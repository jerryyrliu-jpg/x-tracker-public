# X-Tracker P0+P1 Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all P0 and P1 optimization items from the design spec — path fixes, cache key compound, Discord UX improvements, FTS5 porter tokenizer, and richer Gemini prompt.

**Architecture:** All changes are incremental patches to existing files. No new files. No schema changes. Each task is independently testable and committable. Cache is flushed once as part of Task 2 deployment.

**Tech Stack:** Python 3.14, SQLite FTS5, discord.py, Gemini CLI (`gemini-2.5-flash-lite`), pytest

**Spec:** `docs/superpowers/specs/2026-04-02-x-tracker-optimization-design.md`

---

## Chunk 1: Infrastructure Fixes (P-4 + P-3)

### Task 1: Fix path resolution in query_topic.py and monthly_summary.py (P-4)

**Files:**
- Modify: `query_topic.py:14`
- Modify: `monthly_summary.py:14`
- Test: `tests/test_paths.py` (new)

**Why:** Both files use `Path(os.getcwd())` which fails when called from a different working directory (e.g., by a launchd agent or any subprocess launched from `~`).

- [ ] **Step 1: Write failing test**

Create `tests/test_paths.py`:

```python
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_query_topic_scraper_base_is_file_relative():
    """SCRAPER_BASE must be relative to __file__, not os.getcwd()."""
    import query_topic
    expected = Path(__file__).resolve().parent.parent
    assert query_topic.SCRAPER_BASE == expected, (
        f"SCRAPER_BASE is {query_topic.SCRAPER_BASE}, expected {expected}. "
        "Fix: use Path(__file__).resolve().parent"
    )

def test_monthly_summary_scraper_base_is_file_relative():
    import monthly_summary
    expected = Path(__file__).resolve().parent.parent
    assert monthly_summary.SCRAPER_BASE == expected
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/yj/Desktop/PyProjects/X-tracker
venv/bin/python -m pytest tests/test_paths.py -v
```

Expected: 2 FAILED (SCRAPER_BASE points to cwd, not __file__ parent)

- [ ] **Step 3: Fix query_topic.py line 14**

Change:
```python
SCRAPER_BASE = Path(os.getcwd())
```
To:
```python
SCRAPER_BASE = Path(__file__).resolve().parent
```

- [ ] **Step 4: Fix monthly_summary.py line 14**

Change:
```python
SCRAPER_BASE = Path(os.getcwd())
```
To:
```python
SCRAPER_BASE = Path(__file__).resolve().parent
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
venv/bin/python -m pytest tests/test_paths.py tests/test_utils.py tests/test_db.py -v
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add query_topic.py monthly_summary.py tests/test_paths.py
git commit -m "fix: use Path(__file__) in query_topic and monthly_summary (P-4)"
```

---

### Task 2: Compound cache key account:topic:days (P-3)

**Files:**
- Modify: `query_topic.py` — `get_cache`, `save_cache`, and call sites in `main()`
- Test: `tests/test_cache_key.py` (new)

**Why:** Current key is just `topic`. After adding `days:N` (Task 4) and multi-account (future), `$LITE days:7` and `$LITE days:30` would hit the same cache entry. Flush existing cache on deploy.

- [ ] **Step 1: Write failing tests**

Create `tests/test_cache_key.py`:

```python
import sys, os, tempfile, sqlite3
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))

def make_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    from scraper import init_db
    conn = init_db(db_path=f.name)
    return f.name, conn

def test_cache_key_includes_account_and_days():
    """Different account/days combos must not share cache."""
    db_path, conn = make_db()
    try:
        from query_topic import save_cache, get_cache
        data = {"summary": "test", "tweets": [], "tweet_count": 0, "cached": False}
        save_cache("LITE", data, account="aleabitoreddit", days=30, conn=conn)

        # Same topic, different days → cache miss
        result = get_cache("LITE", account="aleabitoreddit", days=7, conn=conn)
        assert result is None, "days:7 should not hit days:30 cache entry"

        # Same topic, different account → cache miss
        result2 = get_cache("LITE", account="otheraccount", days=30, conn=conn)
        assert result2 is None, "different account should not hit cache"

        # Same topic+account+days → hit
        result3 = get_cache("LITE", account="aleabitoreddit", days=30, conn=conn)
        assert result3 is not None, "exact match should hit cache"
    finally:
        conn.close()
        os.unlink(db_path)

def test_cache_key_format():
    """Verify the stored key is account:topic:days."""
    db_path, conn = make_db()
    try:
        from query_topic import save_cache
        data = {"summary": "x", "tweets": [], "tweet_count": 0, "cached": False}
        save_cache("TSLA", data, account="testacct", days=14, conn=conn)
        row = conn.execute("SELECT topic FROM query_cache").fetchone()
        assert row[0] == "testacct:TSLA:14"
    finally:
        conn.close()
        os.unlink(db_path)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
venv/bin/python -m pytest tests/test_cache_key.py -v
```

Expected: FAILED (TypeError: unexpected keyword argument 'account')

- [ ] **Step 3: Update get_cache and save_cache signatures**

In `query_topic.py`, replace `get_cache` (lines 65–77):

```python
def get_cache(topic, account="aleabitoreddit", days=3, conn=None):
    """Retrieve cached result. Cache key is account:topic:days."""
    should_close = conn is None
    if conn is None:
        conn = get_db_conn(DB_PATH)
    cache_key = f"{account}:{topic}:{days}"
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    row = conn.execute(
        "SELECT result_json FROM query_cache WHERE topic = ? AND updated_at >= ?",
        (cache_key, cutoff),
    ).fetchone()
    if should_close:
        conn.close()
    return json.loads(row[0]) if row else None
```

Replace `save_cache` (lines 80–91):

```python
def save_cache(topic, result_data, account="aleabitoreddit", days=30, conn=None):
    """Save result to cache. Cache key is account:topic:days."""
    should_close = conn is None
    if conn is None:
        conn = get_db_conn(DB_PATH)
    cache_key = f"{account}:{topic}:{days}"
    conn.execute(
        "INSERT OR REPLACE INTO query_cache (topic, result_json, updated_at) VALUES (?, ?, ?)",
        (cache_key, json.dumps(result_data), datetime.now().isoformat()),
    )
    conn.commit()
    if should_close:
        conn.close()
```

- [ ] **Step 4: Update call sites in main()**

In `main()`, update the cache calls to pass `account` and `days`:

```python
# get_cache call (line ~107):
cached = get_cache(args.topic, account=args.account, days=args.days, conn=conn)

# save_cache call (line ~156):
save_cache(args.topic, result_data, account=args.account, days=args.days)
```

- [ ] **Step 5: Run tests**

```bash
venv/bin/python -m pytest tests/test_cache_key.py tests/test_paths.py tests/test_db.py tests/test_utils.py -v
```

Expected: all PASSED

- [ ] **Step 6: Flush existing cache**

```bash
cd /Users/yj/Desktop/PyProjects/X-tracker
venv/bin/python -c "
from utils import get_db_conn
conn = get_db_conn('tweets.db')
count = conn.execute('SELECT COUNT(*) FROM query_cache').fetchone()[0]
conn.execute('DELETE FROM query_cache')
conn.commit()
conn.close()
print(f'Flushed {count} stale cache entries.')
"
```

- [ ] **Step 7: Commit**

```bash
git add query_topic.py tests/test_cache_key.py
git commit -m "feat: compound cache key account:topic:days (P-3)"
```

---

## Chunk 2: Discord Bot Improvements (Q-4, Q-1, Q-3, F-2)

### Task 3: Discord bot — WAL stats, typing indicator, enriched /stats, days:N (Q-4 + Q-1 + Q-3 + F-2)

**Files:**
- Modify: `discord_bot.py`
- Test: `tests/test_discord_bot.py` (new — unit tests only, no live Discord)

**Why:** Four small, co-located changes in discord_bot.py. Batched into one task to avoid 4 separate PRs for 4 tiny touches.

- [ ] **Step 1: Write tests**

Create `tests/test_discord_bot.py`:

```python
import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_ticker_re_accepts_valid():
    from discord_bot import TICKER_RE
    assert TICKER_RE.match("LITE")
    assert TICKER_RE.match("BRK.B")
    assert TICKER_RE.match("BTC-USD")

def test_ticker_re_rejects_invalid():
    from discord_bot import TICKER_RE
    assert not TICKER_RE.match("")
    assert not TICKER_RE.match("A" * 11)
    assert not TICKER_RE.match("../etc")

def test_parse_days_suffix_present():
    from discord_bot import parse_ticker_message
    ticker, days = parse_ticker_message("LITE days:7")
    assert ticker == "LITE"
    assert days == 7

def test_parse_days_suffix_absent():
    from discord_bot import parse_ticker_message
    ticker, days = parse_ticker_message("LITE")
    assert ticker == "LITE"
    assert days == 30  # default

def test_parse_days_suffix_clamps():
    from discord_bot import parse_ticker_message
    _, days = parse_ticker_message("LITE days:999")
    assert days <= 90  # max allowed

def test_parse_days_suffix_invalid_ignored():
    from discord_bot import parse_ticker_message
    ticker, days = parse_ticker_message("LITE days:abc")
    assert ticker == "LITE"
    assert days == 30
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
venv/bin/python -m pytest tests/test_discord_bot.py -v
```

Expected: FAILED (`parse_ticker_message` not defined)

- [ ] **Step 3: Add parse_ticker_message and update discord_bot.py**

Replace the full content of `discord_bot.py`:

```python
import asyncio, discord, json, os, re, sys
from discord.ext import commands
from dotenv import load_dotenv
from pathlib import Path
from utils import get_db_conn

TICKER_RE = re.compile(r'^[A-Z0-9.\-]{1,10}$')
DAYS_RE = re.compile(r'\bdays:(\d+)\b', re.IGNORECASE)

SCRAPER_BASE = Path(__file__).resolve().parent
load_dotenv(SCRAPER_BASE / ".env")
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)


def parse_ticker_message(raw: str) -> tuple[str, int]:
    """Extract ticker and optional days:N from a message string.

    Returns (ticker_upper, days) where days defaults to 30 and caps at 90.
    days:N is stripped before ticker validation.
    """
    days = 30
    m = DAYS_RE.search(raw)
    if m:
        try:
            days = min(int(m.group(1)), 90)
        except ValueError:
            pass
        raw = DAYS_RE.sub("", raw)
    return raw.strip().upper(), days


@bot.event
async def on_ready():
    print(f"Bot is ready! Logged in as {bot.user}")


@bot.command()
async def stats(ctx):
    conn = get_db_conn(SCRAPER_BASE / "tweets.db")
    total = conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    rows = conn.execute(
        "SELECT account, COUNT(*), MAX(scraped_at) FROM tweets GROUP BY account"
    ).fetchall()
    conn.close()
    lines = [f"📊 **X-Tracker Stats** — 共 {total} 則推文"]
    for account, count, last_scraped in rows:
        ts = last_scraped[:16].replace("T", " ") if last_scraped else "—"
        lines.append(f"  • @{account}: {count} 則 · 最後抓取 {ts}")
    await ctx.send("\n".join(lines))


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)
    if message.content.startswith("$"):
        raw = message.content[1:].strip()
        ticker, days = parse_ticker_message(raw)
        if TICKER_RE.match(ticker):
            safe_ticker = re.sub(r'[^A-Z0-9]', '_', ticker)
            out_file = f"/tmp/bot_{safe_ticker}.json"
            cmd = [
                sys.executable,
                str(SCRAPER_BASE / "query_topic.py"),
                ticker,
                "--days", str(days),
                "--output", out_file,
            ]

            async with message.channel.typing():
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
                    summary = res["summary"]
                    for i in range(0, len(summary), 1900):
                        await message.channel.send(summary[i : i + 1900])
                finally:
                    os.unlink(out_file)
            else:
                if stderr:
                    print(f"Error analyzing {ticker}: {stderr.decode()}")
                await message.channel.send(f"找不到關於 {ticker} 的推文或分析失敗。")


bot.run(TOKEN)
```

- [ ] **Step 4: Run tests**

```bash
venv/bin/python -m pytest tests/test_discord_bot.py -v
```

Expected: all PASSED

- [ ] **Step 5: Run full suite**

```bash
venv/bin/python -m pytest tests/ -v
```

Expected: all PASSED

- [ ] **Step 6: Restart bot to apply changes**

```bash
pkill -f "discord_bot.py" 2>/dev/null; sleep 1
cd /Users/yj/Desktop/PyProjects/X-tracker
nohup venv/bin/python discord_bot.py > discord_bot.log 2>&1 &
sleep 3 && cat discord_bot.log
```

Expected: `Shard ID None has connected to Gateway`

- [ ] **Step 7: Commit**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: Discord bot UX — typing indicator, days:N, enriched /stats, WAL (Q-1/Q-3/Q-4/F-2)"
```

---

## Chunk 3: FTS5 + Prompt (Q-2 + A-1)

### Task 4: FTS5 porter tokenizer (Q-2)

**Files:**
- Modify: `scraper.py` — `init_db()` FTS5 CREATE statement
- Test: `tests/test_db.py` — add tokenizer migration test

**Why:** Default FTS5 tokenizer doesn't stem English words. Adding `tokenize="porter unicode61"` means "buying" matches "buy", "traded" matches "trade" etc. Existing DB needs a one-time migration (DROP + recreate + rebuild).

- [ ] **Step 1: Add test for porter tokenizer**

Append to `tests/test_db.py`:

```python
def test_fts_porter_tokenizer_stems_words():
    """FTS5 with porter tokenizer: 'buying' should match 'buy'."""
    db_path = make_fresh_db()
    try:
        from scraper import init_db, sync_fts
        from query_topic import search_tweets_fts
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "testuser", recent, "I am buying LITE stock today"),
        )
        conn.commit()
        sync_fts(conn)
        rows = search_tweets_fts(conn, "testuser", "buy", days=7)
        conn.close()
        assert len(rows) == 1, "porter tokenizer: 'buy' should match 'buying'"
    finally:
        os.unlink(db_path)
```

- [ ] **Step 2: Run to confirm it fails**

```bash
venv/bin/python -m pytest tests/test_db.py::test_fts_porter_tokenizer_stems_words -v
```

Expected: FAILED (default tokenizer does not stem "buying" → "buy")

- [ ] **Step 3: Update init_db FTS CREATE in scraper.py**

Change (around line 62–66):
```python
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS tweets_fts "
            "USING fts5(text, content='tweets', content_rowid='rowid')"
        )
    except sqlite3.OperationalError:
        pass
```
To:
```python
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS tweets_fts "
            "USING fts5(text, content='tweets', content_rowid='rowid', "
            "tokenize='porter unicode61')"
        )
    except sqlite3.OperationalError:
        pass
```

- [ ] **Step 4: Add migrate_fts_tokenizer helper in scraper.py** (after `sync_fts`):

```python
def migrate_fts_tokenizer(conn):
    """Migrate existing tweets_fts to porter tokenizer if needed.

    Safe to call multiple times — checks tokenizer config before acting.
    """
    row = conn.execute(
        "SELECT value FROM tweets_fts_config WHERE k = 'tokenize'"
    ).fetchone()
    current = row[0] if row else "unicode61"
    if "porter" in current:
        return  # already migrated
    print("Migrating FTS5 tokenizer to porter unicode61...")
    conn.execute("DROP TABLE IF EXISTS tweets_fts")
    conn.execute(
        "CREATE VIRTUAL TABLE tweets_fts "
        "USING fts5(text, content='tweets', content_rowid='rowid', "
        "tokenize='porter unicode61')"
    )
    conn.execute("INSERT INTO tweets_fts(tweets_fts) VALUES('rebuild')")
    conn.commit()
    print("FTS5 migration complete.")
```

- [ ] **Step 5: Run migration on production DB**

```bash
cd /Users/yj/Desktop/PyProjects/X-tracker
venv/bin/python -c "
from utils import get_db_conn
from scraper import migrate_fts_tokenizer
conn = get_db_conn('tweets.db')
migrate_fts_tokenizer(conn)
conn.close()
"
```

Expected: `Migrating FTS5 tokenizer to porter unicode61...` then `FTS5 migration complete.`

- [ ] **Step 6: Run all tests**

```bash
venv/bin/python -m pytest tests/ -v
```

Expected: all PASSED (including new porter tokenizer test)

- [ ] **Step 7: Commit**

```bash
git add scraper.py tests/test_db.py
git commit -m "feat: FTS5 porter unicode61 tokenizer for better search recall (Q-2)"
```

---

### Task 5: Richer Gemini prompt with date context (A-1)

**Files:**
- Modify: `query_topic.py` — `analyze_topic_weighted()`
- Test: `tests/test_prompt.py` (new)

**Why:** Current prompt passes raw tweet JSON with no date anchor. Adding today's date and weekly grouping helps Gemini identify trend direction changes and recent shifts.

- [ ] **Step 1: Write prompt content test**

Create `tests/test_prompt.py`:

```python
import sys, json
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))

def make_fake_tweets(n=3):
    return [
        (str(i), f"2026-04-0{i+1}T10:00:00", f"Tweet {i} about LITE stock")
        for i in range(1, n + 1)
    ]

def test_prompt_includes_today_date():
    from query_topic import build_prompt
    prompt = build_prompt("LITE", make_fake_tweets())
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in prompt, f"Prompt should contain today's date {today}"

def test_prompt_includes_week_grouping():
    from query_topic import build_prompt
    prompt = build_prompt("LITE", make_fake_tweets())
    assert "Week" in prompt or "週" in prompt, "Prompt should group tweets by week"

def test_prompt_includes_sentiment_separator():
    from query_topic import build_prompt
    prompt = build_prompt("LITE", make_fake_tweets())
    assert "---SENTIMENT_JSON---" in prompt

def test_prompt_includes_trend_instruction():
    from query_topic import build_prompt
    prompt = build_prompt("LITE", make_fake_tweets())
    assert "趨勢" in prompt or "trend" in prompt.lower() or "演變" in prompt
```

- [ ] **Step 2: Run to confirm they fail**

```bash
venv/bin/python -m pytest tests/test_prompt.py -v
```

Expected: FAILED (`build_prompt` not defined)

- [ ] **Step 3: Extract build_prompt and update analyze_topic_weighted**

In `query_topic.py`, add `build_prompt` above `analyze_topic_weighted`:

```python
def build_prompt(topic: str, tweets: list) -> str:
    """Build the Gemini analysis prompt with date context and weekly grouping."""
    from collections import defaultdict
    today = datetime.now().strftime("%Y-%m-%d")

    # Group tweets by ISO week
    weeks: dict[str, list[dict]] = defaultdict(list)
    ungrouped = []
    for r in tweets:
        tid, created_at, text = r[0], r[1], r[2]
        entry = {"id": tid, "date": created_at[:10], "text": text}
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            week_label = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
            weeks[week_label].append(entry)
        except Exception:
            ungrouped.append(entry)

    grouped_json = {}
    for week in sorted(weeks):
        grouped_json[week] = weeks[week]
    if ungrouped:
        grouped_json["other"] = ungrouped

    example_id = tweets[0][0] if tweets else "1234567890"
    return (
        f"今日：{today}\n\n"
        f"分析「{topic}」的推文，以時間順序觀察觀點演變與趨勢轉折。繁體中文總結。\n"
        f"數據（依週分組）：{json.dumps(grouped_json, ensure_ascii=False)}\n\n"
        "請：\n"
        "1. 總結各週的主要觀點。\n"
        "2. 明確指出是否有趨勢方向轉變（例如從看多轉為謹慎）。\n"
        "3. 以最新推文為準給出當前立場。\n\n"
        "請在回答最後加上以下分隔符，並輸出每則推文 id 對應的情感標籤 JSON。\n"
        "情感值只能是 Bullish、Bearish 或 Neutral 其中之一。\n"
        "格式範例（請替換為實際的 tweet id）：\n"
        "---SENTIMENT_JSON---\n"
        f'{{"{example_id}": "Bullish", "<其他id>": "Bearish"}}'
    )
```

Update `analyze_topic_weighted` to call `build_prompt`:

```python
def analyze_topic_weighted(topic, tweets):
    """Analyze tweets with Gemini. Returns raw stdout including ---SENTIMENT_JSON--- block."""
    prompt = build_prompt(topic, tweets)
    cmd = ["gemini", "--model", "gemini-2.5-flash-lite", "-p", prompt]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if res.returncode != 0:
        print(f"Error running gemini command: {res.stderr}", file=sys.stderr)
        return ""
    return res.stdout
```

- [ ] **Step 4: Run tests**

```bash
venv/bin/python -m pytest tests/test_prompt.py -v
```

Expected: all PASSED

- [ ] **Step 5: Run full suite**

```bash
venv/bin/python -m pytest tests/ -v
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add query_topic.py tests/test_prompt.py
git commit -m "feat: richer Gemini prompt with date context and weekly grouping (A-1)"
```

---

## Final Step: Push

```bash
cd /Users/yj/Desktop/PyProjects/X-tracker
git push
```

---

*Plan covers: P-4, P-3, Q-4, Q-1, Q-3, F-2, Q-2, A-1 from the optimization spec.*
*P2+ items (F-1 multi-account, A-2 5-class sentiment, D-1 dashboard, P-2 Gemini SDK) are deferred to a separate plan.*
