# Discord `/summary` Command Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/summary days:N` Discord bot command that scans all tweets from the last N days, identifies every mentioned ticker, and returns a per-ticker sentiment + trend summary in one Gemini call.

**Architecture:** Two files change. `query_topic.py` gets three new functions (`get_recent_tweets`, `build_all_tickers_prompt`, `summarize_recent`) and a `--summary` CLI flag added to `main()`. `discord_bot.py` gets a `parse_days_from_args()` helper and a new `@bot.command() summary`. The bot invokes `query_topic.py --summary` as a subprocess (same pattern as `$TICKER`), reading the result from a temp JSON file.

**Tech Stack:** Python 3.14, SQLite, discord.py, Gemini CLI (`gemini-2.5-flash-lite`), pytest

**Spec:** `docs/superpowers/specs/2026-04-03-discord-summary-command-design.md`

---

## Chunk 1: query_topic.py — data, prompt, orchestration

### Task 1: get_recent_tweets, build_all_tickers_prompt, summarize_recent, main() --summary flag

**Files:**
- Modify: `query_topic.py` (after `build_prompt`, before `get_cache`)
- Create: `tests/test_summary.py`

---

- [ ] **Step 1: Write failing tests**

Create `tests/test_summary.py`:

```python
import sys, os, tempfile
from pathlib import Path
from datetime import datetime, timedelta
sys.path.insert(0, str(Path(__file__).parent.parent))

def make_fresh_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def test_get_recent_tweets_returns_all_in_window():
    """Returns only tweets within the time window."""
    from scraper import init_db
    from query_topic import get_recent_tweets
    db_path = make_fresh_db()
    try:
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        old = (datetime.now() - timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "testuser", recent, "LITE is bullish"),
        )
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("2", "testuser", recent, "TSLA looks weak"),
        )
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("3", "testuser", old, "old tweet"),
        )
        conn.commit()
        rows = get_recent_tweets(conn, days=7, account="testuser")
        conn.close()
        assert len(rows) == 2
        ids = {r[0] for r in rows}
        assert ids == {"1", "2"}
    finally:
        os.unlink(db_path)


def test_get_recent_tweets_excludes_other_accounts():
    """Account filter is applied."""
    from scraper import init_db
    from query_topic import get_recent_tweets
    db_path = make_fresh_db()
    try:
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "targetuser", recent, "LITE bullish"),
        )
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("2", "otheraccount", recent, "TSLA bearish"),
        )
        conn.commit()
        rows = get_recent_tweets(conn, days=7, account="targetuser")
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "1"
    finally:
        os.unlink(db_path)


def test_build_all_tickers_prompt_includes_today():
    """Prompt contains today's date."""
    from query_topic import build_all_tickers_prompt
    tweets = [("1", datetime.now().isoformat(), "LITE is bullish")]
    prompt = build_all_tickers_prompt(tweets, days=3)
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in prompt


def test_build_all_tickers_prompt_groups_by_day():
    """Prompt contains day-level grouping keys (YYYY-MM-DD format)."""
    from query_topic import build_all_tickers_prompt
    today_str = datetime.now().strftime("%Y-%m-%d")
    tweets = [("1", f"{today_str}T10:00:00", "LITE is bullish")]
    prompt = build_all_tickers_prompt(tweets, days=3)
    assert today_str in prompt, "Prompt should contain day key like 2026-04-03"


def test_summarize_recent_empty_gemini_response(monkeypatch):
    """summarize_recent returns '' when Gemini exits 0 but stdout is empty."""
    from scraper import init_db
    import query_topic
    db_path = make_fresh_db()
    try:
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "testuser", recent, "LITE is bullish"),
        )
        conn.commit()
        conn.close()

        # Patch DB_PATH to point at test DB
        monkeypatch.setattr(query_topic, "DB_PATH", db_path)

        import subprocess
        original_run = subprocess.run
        def mock_run(cmd, **kwargs):
            class FakeResult:
                returncode = 0
                stdout = ""
                stderr = ""
            return FakeResult()
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = query_topic.summarize_recent(account="testuser", days=7, force=True)
        assert result == "", "Empty Gemini stdout should return empty string"
    finally:
        os.unlink(db_path)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/yj/Desktop/PyProjects/X-tracker
venv/bin/python -m pytest tests/test_summary.py -v
```

Expected: 5 FAILED (`get_recent_tweets`, `build_all_tickers_prompt`, and `summarize_recent` not defined)

- [ ] **Step 3: Add get_recent_tweets and build_all_tickers_prompt to query_topic.py**

Insert after `build_prompt` (around line 83), before `analyze_topic_weighted`:

```python
def get_recent_tweets(conn, days: int, account: str = "aleabitoreddit") -> list:
    """Fetch all tweets within the last `days` days for `account`. No topic filter."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    return conn.execute(
        "SELECT id, created_at, text FROM tweets "
        "WHERE account = ? AND created_at >= ? ORDER BY created_at DESC",
        (account, since),
    ).fetchall()


def build_all_tickers_prompt(tweets: list, days: int) -> str:
    """Build Gemini prompt for all-tickers summary, grouped by day."""
    today = datetime.now().strftime("%Y-%m-%d")

    day_groups: dict = defaultdict(list)
    for r in tweets:
        tid, created_at, text = r[0], r[1], r[2]
        day_key = created_at[:10]  # YYYY-MM-DD
        day_groups[day_key].append({"id": tid, "text": text})

    grouped_json: dict = {}
    for day in sorted(day_groups):
        grouped_json[day] = day_groups[day]

    return (
        f"今日：{today}（最近 {days} 天推文分析）\n\n"
        f"數據（依日分組）：{json.dumps(grouped_json, ensure_ascii=False)}\n\n"
        "請：\n"
        "1. 找出所有被提及的標的（股票、加密貨幣等）\n"
        "2. 每個標的輸出：\n"
        "   - 情緒：Bullish / Bearish / Neutral\n"
        "   - 一句主要觀點摘要\n"
        "3. 若同一標的在不同天有不同立場，請註明轉變\n\n"
        "繁體中文總結。"
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
venv/bin/python -m pytest tests/test_summary.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Add summarize_recent to query_topic.py**

Insert after `build_all_tickers_prompt`, before `analyze_topic_weighted`:

```python
def summarize_recent(account: str = "aleabitoreddit", days: int = 7, force: bool = False) -> str:
    """Summarize all tickers mentioned in recent tweets. Returns summary string or '' on failure."""
    cache_topic = "__summary__"

    if not force:
        cached = get_cache(cache_topic, account=account, days=days)
        if cached:
            return cached.get("summary", "")

    conn = get_db_conn(DB_PATH)
    try:
        tweets = get_recent_tweets(conn, days=days, account=account)
    finally:
        conn.close()

    if not tweets:
        return ""

    prompt = build_all_tickers_prompt(tweets, days)
    cmd = ["gemini", "--model", "gemini-2.5-flash-lite", "-p", prompt]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if res.returncode != 0 or not res.stdout.strip():
        print(f"Gemini summary failed: {res.stderr}", file=sys.stderr)
        return ""

    summary = res.stdout.strip()
    save_cache(cache_topic, {"summary": summary, "cached": False}, account=account, days=days)
    return summary
```

- [ ] **Step 6: Update main() to add --summary flag**

In `main()`, change the argparse setup and add the `--summary` path.

Change:
```python
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("topic")
    parser.add_argument("--account", default="aleabitoreddit")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    conn = get_db_conn(DB_PATH)
```

To:
```python
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", nargs="?", default=None)
    parser.add_argument("--account", default="aleabitoreddit")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--summary", action="store_true",
                        help="Summarize all tickers in recent tweets (no topic required)")
    args = parser.parse_args()

    if args.summary:
        days = max(1, min(args.days, 90))
        print(f"[Live Analysis] 正在分析最近 {days} 天所有標的...", file=sys.stderr)
        summary = summarize_recent(account=args.account, days=days, force=args.force)
        if not summary:
            print(f"最近 {days} 天無推文資料或分析失敗。", file=sys.stderr)
            return
        result_data = {"summary": summary, "cached": False}
        if args.output:
            with open(args.output, "w") as f:
                json.dump(result_data, f)
        print(summary)
        return

    if not args.topic:
        print("Error: topic is required unless --summary is used.", file=sys.stderr)
        sys.exit(1)

    conn = get_db_conn(DB_PATH)
```

- [ ] **Step 7: Run full test suite**

```bash
venv/bin/python -m pytest tests/ -v
```

Expected: all PASSED (23 + 5 new = 28 total)

- [ ] **Step 8: Commit**

```bash
git add query_topic.py tests/test_summary.py
git commit -m "feat: add get_recent_tweets, build_all_tickers_prompt, summarize_recent, --summary flag (query_topic.py)"
```

---

## Chunk 2: discord_bot.py — /summary command

### Task 2: parse_days_from_args helper and /summary bot command

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py` (append new tests)

---

- [ ] **Step 1: Write failing tests**

Append to `tests/test_discord_bot.py`:

```python
def test_parse_days_from_args_present():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("days:3") == 3

def test_parse_days_from_args_absent():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("") == 7  # default for /summary is 7

def test_parse_days_from_args_clamps_upper():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("days:999") == 90

def test_parse_days_from_args_clamps_lower():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("days:0") == 1

def test_parse_days_from_args_invalid_ignored():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("days:abc") == 7
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
venv/bin/python -m pytest tests/test_discord_bot.py -v
```

Expected: 5 new tests FAILED (`parse_days_from_args` not defined)

- [ ] **Step 3: Add parse_days_from_args and /summary command to discord_bot.py**

Add `parse_days_from_args` after `parse_ticker_message`:

```python
def parse_days_from_args(args_str: str, default: int = 7) -> int:
    """Parse optional days:N from bot command argument string.

    Returns days (clamped 1–90), defaulting to `default` if absent or invalid.
    """
    m = DAYS_RE.search(args_str)
    if m:
        val = m.group(1)
        if val.isdigit():
            return max(1, min(int(val), 90))
    return default
```

Add `/summary` command after the `stats` command:

```python
@bot.command()
async def summary(ctx, *, args: str = ""):
    days = parse_days_from_args(args)
    out_file = f"/tmp/bot___summary__{days}.json"
    cmd = [
        sys.executable,
        str(SCRAPER_BASE / "query_topic.py"),
        "--summary",
        "--days", str(days),
        "--output", out_file,
    ]

    async with ctx.typing():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(SCRAPER_BASE),
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0 and os.path.exists(out_file):
            try:
                with open(out_file) as f:
                    res = json.load(f)
                summary_text = res.get("summary", "")
                if summary_text:
                    for i in range(0, len(summary_text), 1900):
                        await ctx.send(summary_text[i : i + 1900])
                else:
                    await ctx.send(f"分析失敗，請稍後再試。")
            finally:
                if os.path.exists(out_file):
                    os.unlink(out_file)
        else:
            if stderr:
                print(f"Error in /summary: {stderr.decode()}")
            await ctx.send(f"最近 {days} 天無推文資料或分析失敗。")
```

Note: out_file uses `{days}` suffix (`/tmp/bot___summary__7.json`) — this is an intentional improvement over the spec's stated path (`/tmp/bot___summary__.json`). The suffix prevents a race condition if two users invoke `/summary days:7` and `/summary days:3` concurrently.

- [ ] **Step 4: Run tests**

```bash
venv/bin/python -m pytest tests/test_discord_bot.py -v
```

Expected: all PASSED (6 original + 5 new = 11 total)

- [ ] **Step 5: Run full test suite**

```bash
venv/bin/python -m pytest tests/ -v
```

Expected: all PASSED (28 + 5 new = 33 total)

- [ ] **Step 6: Restart bot**

```bash
pkill -f "discord_bot.py" 2>/dev/null; sleep 1
cd /Users/yj/Desktop/PyProjects/X-tracker
nohup venv/bin/python discord_bot.py > discord_bot.log 2>&1 &
sleep 3 && cat discord_bot.log
```

Expected: `Shard ID None has connected to Gateway`

Then manually send `/summary days:1` in Discord and confirm a response is returned (summary text or "無推文資料" if no tweets in the last 1 day).

- [ ] **Step 7: Commit**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: Discord /summary command — all-tickers sentiment report for last N days"
```

---

## Final Step: Push

```bash
cd /Users/yj/Desktop/PyProjects/X-tracker
git push
```

---

*Plan covers: Discord `/summary days:N` command — `get_recent_tweets`, `build_all_tickers_prompt`, `summarize_recent`, `--summary` CLI flag, `parse_days_from_args`, `@bot.command() summary`.*
