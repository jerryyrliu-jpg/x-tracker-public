# X-Tracker Optimization Design

**Date:** 2026-04-02
**Status:** Draft — pending review
**Project:** `/Users/yj/Desktop/PyProjects/X-tracker`
**Context:** Post-v3.4.1 — system is stable and running. This document identifies optimization opportunities across four dimensions: feature extension, analysis quality, performance/stability, and dashboard.

---

## 1. Current System Architecture

```
Chrome (CDP 9222)
    └─ scraper_playwright.py   ← DOM extraction, INSERT + FTS rebuild (active path)
         ↑ called every 15±2min
monitor_active.py              ← PID lock, metrics, self-healing, heartbeat
    └─ tweets.db (SQLite/FTS5)
         ├─ query_topic.py     ← FTS5 search → Gemini CLI → JSON + cache
         │    ↑ spawned by
         └─ discord_bot.py     ← $TICKER handler, /stats
dashboard.py (Streamlit)       ← K-line chart + co-occurrence graph
utils.py                       ← shared: DB, Discord, logger, PIDLock, Metrics

scraper.py                     ← twscrape-based scraper (legacy, NOT called by monitor)
                                  shares tweets.db; kept for manual/fallback use
```

**Current data:** 398+ tweets from `@aleabitoreddit` · SQLite WAL · FTS5 content table · 3-day Gemini cache

---

## 2. Completed Work (Reference)

| Version | Key changes |
|---------|-------------|
| v3.3 | Bug fixes: FTS query_cache table, async Discord bot, input validation, utils consolidation |
| v3.4 | Active Polling: monitor_active.py, self-healing Chrome restart, jitter, metrics, heartbeat |
| v3.5 | Resource Management: /pausex, /resumex, Chrome profile isolation, async subprocess, fr-string fix |
| v3.4.1 | Hotfix: FTS5 not synced in scraper_playwright.py; all code-review issues patched |

---


---

## 2.5 Resource Governance (v3.5)

To resolve resource contention between X-Tracker and manual Chrome usage, the following governance model is implemented:

**Manual-Override Pattern:**
- **`/pausex`**: Stops `monitor_active.py` and `monitor_rss.py`. Force-kills Chrome instances tied to the `x_scraper` profile using `pkill -f "Google Chrome.*x_scraper"`.
- **`/resumex`**: Restores the environment. Runs `restart_chrome.sh` (Headless-new) and restarts monitor processes using the project's virtual environment Python.
- **Isolation**: By targeting the specific `--user-data-dir` profile string in `pkill`, the system ensures that the user's personal Chrome windows remain untouched during automated restarts.

---
## 3. Optimization Opportunities

### 3.1 Feature Extension

**F-1 · Multi-account Discord queries**

Currently `query_topic.py` defaults `--account aleabitoreddit`. A user typing `$LITE` searches only that account's tweets. The DB schema already has an `account` column — the search layer just needs to lift the constraint.

- **Proposed:** `query_topic.py` accepts `--account all` (or omit flag) → searches across all tracked accounts; results grouped by account in the Gemini prompt.
- **Impact:** Change to `search_tweets_fts`, the prompt template, and the `discord_bot.py` subprocess command (which currently passes no `--account` flag, so defaults to single account). P-3 cache key fix is a prerequisite.

**F-2 · `$TICKER days:N` Discord shorthand**

Currently `--days` is hardcoded to 30. Users may want `$LITE days:7` for recent sentiment only.

- **Proposed:** Parse optional `days:N` suffix from Discord message before passing to `query_topic.py`.
- **Impact:** 5-line change in `discord_bot.py`.

**F-3 · Scheduled monthly summary**

`monthly_summary.py` exists but requires manual invocation. The monitor already has a scheduling loop.

- **Proposed:** Add a `--monthly` mode to `monitor_active.py` that fires `monthly_summary.py` on the 1st of each month at 09:00 local time.
- **Impact:** ~15 lines in `monitor_active.py`; no new files.

---

### 3.2 Analysis Quality

**A-1 · Richer Gemini prompt with date context**

Current prompt passes raw tweet JSON without explicit date framing. The model infers time ordering but has no anchor.

- **Proposed:** Prepend `今日: YYYY-MM-DD` and group tweets by week in the prompt. Instruct model to note trend direction changes explicitly.
- **Impact:** Prompt-only change in `query_topic.py`.

**A-2 · Sentiment granularity**

Current sentiment is 3-class: Bullish / Bearish / Neutral. Edge cases like "cautiously bullish" or "short-term bearish, long-term bullish" collapse to Neutral.

- **Proposed:** Extend to 5-class: `StrongBullish` / `Bullish` / `Neutral` / `Bearish` / `StrongBearish`. Update dashboard K-line annotation colours.
- **Impact:** Prompt change + dashboard colour mapping. Cache key unchanged (backwards compatible).

**A-3 · Confidence score**

Currently the model outputs no confidence signal. A tweet saying "I'm considering $LITE" should carry less weight than "I'm all-in on $LITE."

- **Proposed:** Request a `confidence: 0.0–1.0` field per tweet alongside sentiment in the JSON block. Use confidence to weight the summary.
- **Impact:** Prompt change + JSON parsing update. No schema change.

---

### 3.3 Performance / Stability

**P-1 · FTS5 incremental sync vs. full rebuild**

Current `sync_fts` does a full `rebuild` on every scrape run with new tweets. At 398 tweets this is fast (~1ms), but at 10k+ tweets this becomes measurable.

- **Proposed (short-term):** Keep rebuild. It is correct and simple; at current scale the cost is negligible.
- **Proposed (long-term, >5k tweets):** Switch to trigger-based incremental sync — `INSERT INTO tweets_fts` per new row instead of full rebuild. Requires adding insert/delete triggers to `init_db`.
- **Decision point:** Benchmark at 5k rows. Until then, no change needed.

**P-2 · Gemini CLI subprocess latency**

`query_topic.py` calls `gemini` via `subprocess.run(timeout=120)`. Cold-start of the CLI adds ~2s per call on top of API latency. For the Discord bot this means 10–30s response time with no intermediate feedback.

- **Proposed:** Replace Gemini CLI subprocess with direct Google API call using `google-generativeai` SDK. Eliminates subprocess overhead, enables streaming, and removes dependency on CLI being installed.
- **Note:** Verify that `gemini-2.5-flash-lite` is the correct SDK model ID — CLI shorthands sometimes differ from SDK identifiers.
- **Also proposed:** Add a Discord typing indicator (`async with message.channel.typing()`) while analysis runs — 3-line change, immediate UX win regardless of P-2.
- **Trade-off:** SDK requires `GEMINI_API_KEY` in `.env`. CLI uses user's `gcloud` auth. For personal use, either is fine.

**P-3 · Cache key includes account and days**

Current cache key is just `topic` (e.g., `"LITE"`). Two problems:
1. Adding multi-account support (F-1) would return cached single-account results for all-account queries.
2. Adding `days:N` support (F-2) means `$LITE days:7` and `$LITE days:30` share the same cache entry.

- **Proposed:** Cache key becomes `f"{account}:{topic}:{days}"`. Requires one-time cache flush on deploy.
- **Impact:** 2-line change in `get_cache` / `save_cache`. Required before both F-1 and F-2.

**P-4 · Path resolution fragility + launchd**

`discord_bot.py` and `monitor_active.py` already use `Path(__file__).resolve().parent`. However, `query_topic.py` and `monthly_summary.py` still use `SCRAPER_BASE = Path(os.getcwd())` — if either is called from a non-project working directory (e.g., by a launchd agent whose cwd defaults to `~`), they fail to find `tweets.db` and `.env`. This also means F-3 (scheduled monthly summary) depends on this fix.

- **Proposed:**
  1. Change `SCRAPER_BASE = Path(os.getcwd())` → `Path(__file__).resolve().parent` in `query_topic.py` and `monthly_summary.py`.
  2. Add launchd plists for `discord_bot.py` and `monitor_active.py` (`~/Library/LaunchAgents/com.xtracker.*.plist`).
- **Impact:** 2-line code change in each file + 2 plist files. Prerequisite for F-3.

---

### 3.4 Dashboard

**D-1 · Live data refresh**

Streamlit dashboard requires manual reload to show new tweets. `st.rerun()` with `time.sleep(60)` would auto-refresh, but the current dashboard has no refresh mechanism.

- **Proposed:** Add `st.sidebar` toggle "Auto-refresh (60s)" using `st.rerun()` loop.
- **Impact:** ~10 lines in `dashboard.py`.

**D-2 · Topic search in dashboard**

Dashboard currently shows all tweets. There is no UI to filter by ticker symbol — users must open Discord to run a `$TICKER` query.

- **Proposed:** Add a sidebar text input for topic search that reuses `search_tweets_fts` and displays results + Gemini summary inline.
- **Impact:** Reuse `query_topic.py` functions directly (import, not subprocess). Moderate change to `dashboard.py`.

---

### 3.5 Quick Wins (from review)

**Q-1 · Discord typing indicator** — `async with message.channel.typing()` while Gemini runs. ~3 lines. Eliminates "did it receive my message?" confusion.

**Q-2 · FTS5 porter tokenizer** — Change `init_db` to `USING fts5(text, content='tweets', content_rowid='rowid', tokenize="porter unicode61")`. Improves recall for English tokens (e.g., "buying" matches "buy"). Zero runtime cost; requires one-time FTS rebuild.

**Q-3 · `/stats` enrichment** — Add per-account count and "last scraped" timestamp to the `/stats` Discord command. ~5-line change.

**Q-4 · `discord_bot.py` stats WAL** — `/stats` command uses raw `sqlite3.connect` instead of `utils.get_db_conn`. Minor inconsistency; fix alongside any bot change.

---

## 4. Recommended Priority

| Priority | Item | Effort | Value |
|----------|------|--------|-------|
| P0 | P-4 · `Path(__file__)` in query_topic + monthly_summary | XS | Required before F-3, launchd |
| P0 | P-3 · Cache key `account:topic:days` | XS | Required before F-1 and F-2 |
| P0 | Q-1 · Discord typing indicator | XS | Immediate UX win |
| P0 | Q-4 · `/stats` use `get_db_conn` | XS | Consistency |
| P1 | F-2 · `$TICKER days:N` | XS | UX win |
| P1 | Q-2 · FTS5 porter tokenizer | XS | Better recall |
| P1 | Q-3 · `/stats` enrichment | XS | More useful |
| P1 | A-1 · Richer prompt (date context) | XS | Easy quality win |
| P2 | F-1 · Multi-account search | S | Core feature gap |
| P2 | A-2 · 5-class sentiment | S | Better signal |
| P2 | D-1 · Dashboard auto-refresh | XS | Polish |
| P3 | P-2 · Gemini SDK (remove subprocess) | M | Latency + reliability |
| P3 | F-3 · Scheduled monthly summary | S | Nice-to-have (needs P-4 first) |
| P3 | D-2 · Topic search in dashboard | M | Convenience |
| P3 | A-3 · Confidence score | M | Advanced signal |
| Defer | P-1 · FTS incremental sync | L | Not needed until 5k+ tweets |
| Defer | P-4 · launchd plists | S | Operational (after path fix) |

---

## 5. Out of Scope

- Multi-user / cloud deployment (personal tool)
- Real-time streaming (Playwright CDP polling is sufficient)
- Paid data sources (twscrape / CDP is free)
- LLM fine-tuning on tweet corpus

---

## 6. Open Questions

1. Is multi-account (F-1) a priority, or is `@aleabitoreddit` the only account tracked for the foreseeable future?
2. For Gemini (P-2): preference for keeping CLI (zero-config) vs. SDK (faster, no subprocess)?
3. Dashboard (D-2): is the Streamlit dashboard actively used, or is Discord the primary interface?
