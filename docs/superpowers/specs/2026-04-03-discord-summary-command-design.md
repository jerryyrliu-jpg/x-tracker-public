# Discord `/summary` Command Design

**Date:** 2026-04-03
**Status:** Draft — pending review
**Project:** `/Users/yj/Desktop/PyProjects/X-tracker`
**Context:** Post-v3.5 — adds a `/summary days:N` Discord command that scans all recent tweets and produces a per-ticker sentiment report, without requiring the user to specify a ticker in advance.

---

## 1. Problem

Currently `$TICKER days:N` requires the user to know which ticker to query. There is no way to ask "what has @aleabitoreddit been talking about in the last 3 days?" — the user must already know the topic.

---

## 2. Feature

`/summary [days:N]` — scans all tweets from the last N days, sends them to Gemini in a single call, and returns a report that:

1. Identifies all tickers/assets mentioned
2. Provides per-ticker sentiment (Bullish / Bearish / Neutral) + one-sentence summary
3. Notes any sentiment shifts within the time window (e.g. Day 1 Bullish → Day 3 Bearish)

Default N = 7. Supported values: any integer 1–90.

---

## 3. Architecture

```
Discord user: /summary days:3
    └─ discord_bot.py (@bot.command() "summary")
         └─ query_topic.py run as subprocess (same pattern as $TICKER)
              ├─ get_cache(key="aleabitoreddit:__summary__:3")  → hit → return
              └─ get_recent_tweets(conn, days=3, account)       → list of rows
                   └─ build_all_tickers_prompt(tweets, days)    → prompt str
                        └─ Gemini CLI subprocess (existing pattern)
                             └─ save_cache + write summary to --output JSON file
```

**Invocation pattern:** `discord_bot.py` spawns `query_topic.py --summary --days 3 --output /tmp/bot___summary__.json` as an async subprocess (identical to the existing `$TICKER` handler). The summary string is returned via the same temp JSON file contract: `{"summary": "...", "cached": false}`. No direct import — keeps DB connection management isolated to the subprocess.

No new files. All changes in `query_topic.py` and `discord_bot.py`.

---

## 4. Components

### 4.1 `get_recent_tweets(conn, days, account)` — `query_topic.py`

Fetches all tweets within the time window. Does **not** use FTS5 (no topic to search). Direct SQL query on `tweets` table.

```python
def get_recent_tweets(conn, days: int, account: str = "aleabitoreddit") -> list:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    return conn.execute(
        "SELECT id, created_at, text FROM tweets "
        "WHERE account = ? AND created_at >= ? ORDER BY created_at DESC",
        (account, since),
    ).fetchall()
```

### 4.2 `build_all_tickers_prompt(tweets, days)` — `query_topic.py`

Groups tweets by day (not week — short windows suit day-level granularity). Instructs Gemini to:
1. Identify all mentioned tickers/assets
2. Output per-ticker in a strict format (no extra separators)
3. Group list-type tweets (ETF holdings, watchlists) under the parent ticker with a `成分股：` sub-line — do not promote each component to a top-level entry
4. Note intra-window sentiment shifts
5. Never quote raw tweet text in the output

**Output format (exact template shown to Gemini):**
```
**$TICKER**
情緒：Bullish
主要觀點：一句話摘要。

**$DRAM**
情緒：Bullish
主要觀點：優質記憶體 ETF，涵蓋主要廠商。
成分股：$MU 24.6%、Samsung 24.1%、SK Hynix 23.1%、$SNDK 4.9%、$WDC 4.8%
```

Tickers separated by one blank line. No commas or symbols between fields.

No `---SENTIMENT_JSON---` block — this is a prose summary, not per-tweet labelling.

**Gemini timeout:** 300s (raised from 120s — full-text tweet payloads can be large). `subprocess.TimeoutExpired` is caught and returns `""` gracefully.

### 4.3 `summarize_recent(account, days)` — `query_topic.py`

Orchestrates the full flow: cache check → fetch tweets → build prompt → Gemini call → save cache → return summary string.

Cache key: `f"{account}:__summary__:{days}"` — uses existing `get_cache` / `save_cache`. TTL tied to `days` (same freshness window as topic queries).

### 4.4 `/summary` Discord command — `discord_bot.py`

New `@bot.command()` named `summary`. Parses optional `days:N` argument from the command arguments string (same `DAYS_RE` pattern already defined). Wraps Gemini call in `async with message.channel.typing()`. Chunks response at 1900 chars.

```
/summary          → days=7 (default)
/summary days:1   → days=1
/summary days:3   → days=3
```

---

## 5. Error Handling

| Condition | Response |
|-----------|----------|
| No tweets in window | `最近 {days} 天無推文資料。` |
| Gemini call fails | `分析失敗，請稍後再試。` |
| days > 90 | Clamp to 90 (same as `parse_ticker_message`) |
| days < 1 | Clamp to 1 |
| Gemini returns empty stdout (returncode=0) | `分析失敗，請稍後再試。` |

---

## 6. Caching

Cache key: `aleabitoreddit:__summary__:N` — stored in existing `query_cache` table. No schema change. TTL = N days (same logic as topic queries). Each `days` value is an independent cache entry: `/summary days:7` and `/summary days:3` do not share a cache slot. Use `--force` flag to bypass cache.

---

## 7. Testing

- `test_get_recent_tweets_returns_all_in_window` — inserts 3 tweets (2 in window, 1 outside), asserts 2 returned
- `test_get_recent_tweets_excludes_other_accounts` — asserts account filter works
- `test_build_all_tickers_prompt_includes_today` — asserts today's date in prompt
- `test_build_all_tickers_prompt_groups_by_day` — asserts day-level keys in prompt JSON
- `test_summary_discord_command_parse_days` — unit test `parse_days_from_args("days:3")` → 3
- `test_summarize_recent_empty_gemini_response` — mock Gemini returning empty stdout with returncode 0; asserts function returns empty string (caller handles gracefully)

---

## 8. Out of Scope

- Multi-account aggregation (single account only, same as existing queries)
- Structured JSON output per ticker (prose summary is sufficient)
- Automatic scheduled `/summary` (manual invocation only)
