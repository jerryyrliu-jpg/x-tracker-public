# X-Tracker v4.8.0

A Playwright CDP-based multi-account X tracking and AI investment analysis system. It uses Gemini to analyze sentiment trends, integrates a CPO supply-chain knowledge graph, and surfaces results through a Discord bot and Streamlit dashboard.

---

## 🏗 Architecture

```
Chrome (CDP 9222)
    └─ scraper_playwright.py   ← DOM extraction; FTS5 trigger sync; `--account` arg
         ↑ each account is polled every ~2hr
monitor_active.py              ← PID lock, metrics, self-healing, heartbeat
                                  ├─ iterates all accounts defined in local accounts.yaml
                                  └─ runs the monthly summary automatically on the 1st at 09:00+

tweets.db (SQLite WAL + FTS5 porter + USCI schema)
    ├─ query_topic.py          ← analyze_topic() → Gemini SDK/CLI → JSON cache
    │    ↑ imported by dashboard; called by discord_bot subprocess
    └─ discord_bot.py          ← $TICKER [days:N], /stats, /supply, /chain, /analyze

dashboard.py (Streamlit)
    ├─ Tab 1: candlestick chart + sentiment arrows (scaled by confidence) + force_refresh
    └─ Tab 2: D3.js CPO supply-chain knowledge graph

utils.py                       ← DB, Discord, logger, PIDLock, metrics
accounts.yaml                  ← user-defined tracked accounts (local copy of accounts.example.yaml)

cpo_chain/output/index.html    ← D3.js CPO network (search + 3 filter axes + tier legend + detail panel)
cpo_chain/output/usci_tiers_cache.json ← canonical runtime cache consumed by `/chain`
cpo_chain/output/USCI_Report.md ← generated human-readable Markdown report; not a runtime data source
cpo_chain/output/usci_runtime_qc.json ← rolling runtime QC history (last 30 export runs); ops/diagnostics artifact, not a runtime data source
archive/USCI_Report.md         ← archived legacy copy; no longer updated by export jobs
scripts/update_network_html.py ← regenerates index.html from the USCI database

launchd (auto-start on login + automatic crash restart):
~/Library/LaunchAgents/com.xtracker.discord.plist
~/Library/LaunchAgents/com.xtracker.monitor.plist
```

**Current snapshot**: SQLite WAL · FTS5 porter tokenizer · USCI graph database · 3-day Gemini cache

Runtime note: the USCI database in `tweets.db` is the extraction source of truth. `/chain` reads the exported `cpo_chain/output/usci_tiers_cache.json`, while the graph HTML is still regenerated from the database. `USCI_Report.md` is an export artifact for human reading only. Runtime QC results from each export (orphan / excluded-leak / duplicate-tier checks) are appended to `cpo_chain/output/usci_runtime_qc.json` as a bounded, self-describing history so issues stay traceable across daily runs; `/chain` never reads this file.

### News modules: two layers, not duplicates

`cpo_chain/news_fetcher.py` and `cpo_chain/news_article_fetcher.py` look similar (same size, both wrap Google News RSS) but sit at different stages of the pipeline and are not interchangeable:

```
cpo_chain/news_article_fetcher.py  ← NewsArticleFetcher: DISCOVERY, per root company
    ├─ fetch_google_news() + fetch_sec_8k()  → persist rows into news_articles table
    ├─ driven by root_tickers in keywords.yaml, not by any existing relation
    └─ feeds news_extractor.py's LLM relation extraction (News Fetch 08:00 UTC → News Extract 08:30 UTC)

cpo_chain/news_fetcher.py          ← CompositeNewsFetcher: CORROBORATION, per relation pair
    ├─ boost_score(company_a, company_b) → ephemeral RSS search, no DB persistence
    ├─ Google News primary, Yahoo RSS fallback only when the Google feed is bozo (broken)
    └─ consumed by confidence_updater.py / scripts/backfill_confidence.py (Confidence Boost 10:00 UTC)
```

In short: `news_article_fetcher.py` finds new articles to extract relations *from*; `news_fetcher.py` checks whether news corroborates a relation that *already exists*, to boost its confidence score. The Google News RSS call in each is deliberately separate (different query shape — single company vs. company pair) rather than shared, so don't consolidate them without preserving that distinction.

---

## 🚀 Core Features

1. **Multi-account crawling** — Driven by `accounts.yaml`; the monitor iterates over enabled accounts, and the scraper accepts the `--account` argument
2. **Playwright CDP** — Connects to a real browser to work through Cloudflare 403 / bot detection
3. **Active polling** — Polls every ~2 hours with random jitter of ±5–15 minutes and automatically restarts Chrome after 3 failures
4. **Gemini analysis** — `GEMINI_BACKEND=sdk|cli|auto`; setting `GEMINI_API_KEY` automatically enables SDK mode without subprocess overhead
5. **5-level sentiment + confidence** — StrongBullish / Bullish / Neutral / Bearish / StrongBearish; confidence 0.0–1.0 scales candlestick arrow size
6. **Multi-account search** — `--account all` performs cross-account analysis with prompts grouped by account
7. **FTS5 trigger sync** — INSERT triggers automatically maintain the FTS index without a full rebuild
8. **Discord Bot**:
   - `$TICKER [days:N]` — cross-account sentiment analysis with confidence scores
   - `/stats` — per-account post counts plus last crawl time
   - `/chain` — full upstream/midstream/downstream CPO supply-chain view, rendered from `cpo_chain/output/usci_tiers_cache.json`
   - `/analyze` — slash-command version of the query flow
   - `/pausex` / `/resumex` — pause and resume the monitor for Chrome resource management
9. **Graph explorer filters** — The D3 graph now supports three independent filter axes: `Supply Chain Role`, `Application / End Market`, and `Infrastructure / Ecosystem`
10. **Curated graph normalization** — The generated graph applies conservative override batches for cloud / hosting, telecom, power infrastructure, alias resolution, and grouped market-bucket nodes
11. **Graph UI polish** — Active filter chips have clearer emphasis, default arrows and links are lighter, and selected / neighborhood highlights use a softer orange accent
12. **Streamlit dashboard** — The candlestick chart imports `analyze_topic()` directly with no subprocess; includes auto-refresh meta tags and a `force_refresh` checkbox
13. **Monthly summary schedule** — The monitor automatically runs `monthly_summary.py` on the 1st of each month at 09:00+, guarded by `.last_monthly_summary` to prevent duplicate runs

---

## ⚙️ Setup

### `.env`
```
DISCORD_BOT_TOKEN=...       # Required for discord_bot.py login
DISCORD_GUILD_ID=...        # Optional: guild-scoped slash command sync
DISCORD_WEBHOOK_SERENITY=...# Required for monitor alerts and tweet/webhook delivery
GEMINI_API_KEY=...          # Gemini SDK mode (enables automatically when set)
GEMINI_BACKEND=auto         # sdk | cli | auto (default: auto)
GEMINI_MODEL=gemini-2.5-flash-lite
MONTHLY_SUMMARY_TIMEOUT=600 # Monthly summary timeout in seconds (default: 600)
EDGAR_USER_AGENT=x-tracker your@email.com  # Required SEC EDGAR user agent
```

`discord_bot.py` uses `DISCORD_BOT_TOKEN`, while `monitor_active.py`, `monthly_summary.py`, and webhook-based alerts require `DISCORD_WEBHOOK_SERENITY`. If tweets are still landing in `tweets.db` but nothing appears in Discord, the webhook variable is the first thing to verify.

### `accounts.example.yaml`

Copy this file to `accounts.yaml` locally before running the app.
Replace the sample keys with your real X usernames before running, and set `enabled: true` only for the accounts you want to track. Keep webhook URLs in environment variables.

```bash
cp accounts.example.yaml accounts.yaml
```

Your copied `accounts.yaml` should reference environment variable names, never hardcoded webhook URLs. The exact sample shape is defined in `accounts.example.yaml`.

Export each variable in your shell or `.env` file:
```bash
export DISCORD_WEBHOOK_SAMPLE_ACCOUNT_1="https://discord.com/api/webhooks/..."
export DISCORD_WEBHOOK_SAMPLE_ACCOUNT_2="https://discord.com/api/webhooks/..."
```

---

## 🛠 Quick Start

### Option A - launchd (recommended, auto-start on login)
```bash
launchctl load ~/Library/LaunchAgents/com.xtracker.discord.plist
launchctl load ~/Library/LaunchAgents/com.xtracker.monitor.plist
# Check status
launchctl list | grep xtracker
# Check logs
tail -f logs/monitor_active.log
```

### Option B - run manually in the background
```bash
source venv/bin/activate
nohup python3 monitor_active.py > logs/monitor_active.log 2>&1 &
nohup python3 discord_bot.py > logs/discord_bot.log 2>&1 &
```

### Dashboard
```bash
source venv/bin/activate
streamlit run dashboard.py --server.address 127.0.0.1
```

> ⚠️ Always run with `--server.address 127.0.0.1` to bind to localhost only. Without this flag, Streamlit defaults to all interfaces and exposes the dashboard, and potentially Gemini API costs, to anyone on the network.

### USCI Manual Backfill
```bash
source venv/bin/activate
python3 cpo_chain/extract_universal.py --tweet-id 2072367082248040621
```

Process multiple IDs from a file:

```bash
python3 cpo_chain/extract_universal.py --tweet-ids-file /path/to/tweet_ids.txt
```

`--tweet-ids-file` must point to a readable UTF-8 text file with one tweet ID per non-empty line.

If local image files already exist in `tweets.images`, the extractor appends OCR text before sending the tweet to the LLM. Playwright-ingested tweets now persist downloaded tweet images under `images/<account>/<tweet_id>/`, so newly scraped image tweets can participate in OCR-backed extraction. Legacy rows that still store `images=[]` remain text-only until a separate backfill is run.

To backfill image paths for existing `images=[]` rows, run:

```bash
venv/bin/python scripts/backfill_tweet_images.py --account aleabitoreddit --limit 20
```

### Common Maintenance Commands

Manual operations that aren't on any schedule — run these directly when needed:

```bash
# Rebuild the /chain runtime cache + USCI_Report.md from the current DB state
python3 -m cpo_chain.export_universal

# Regenerate the dashboard's D3 graph (cpo_chain/output/index.html) from the current DB state
python3 scripts/update_network_html.py

# Backfill EDGAR/News confidence scores for relations that haven't been scored yet
# (creates a tweets.db.bak.<timestamp> backup first unless --dry-run)
python3 scripts/backfill_confidence.py --limit 500 --offset 0

# Embed tweets that don't yet have a sqlite-vec row, for /chain and extract_universal --vector
# recall. Not scheduled anywhere — run manually after a large backfill or when vector recall
# quality looks stale.
python3 -m cpo_chain.batch_embed

# Self-heal a wedged Chrome CDP connection (also runs automatically after 3 scraper failures)
scripts/restart_chrome.sh

# Preview what a public-repo sync would publish, without writing anything
python3 scripts/check_public_sync.py --paths-file /tmp/xtracker-public-manifest.txt
```

`cpo_chain/batch_embed.py` specifically: it embeds any `tweets` row missing a matching `tweet_embeddings` row (via `UniversalEmbedder`, local `nomic-embed-text`), in batches of 50, and is the only way those vectors get backfilled — nothing schedules it automatically, so semantic recall quality degrades silently over time unless someone runs it after tweet volume grows significantly.

### Version String Maintenance

Two version markers exist and are updated independently — there is no single source of truth, so bump both together when cutting a release:

- `README.md`'s title (`# X-Tracker vX.Y.Z`, line 1) and the Version History table below.
- `discord_bot.py`'s Discord presence string (`bot.change_presence(activity=discord.Game(name="..."))`).

Both had drifted out of sync with actual shipped work before v4.8.0 (README said v4.6.4, the presence string still said `V3.7_LOCAL_ACTIVE`) — bumped together as part of that release. Treat any presence string or README version that predates the most recent `git log` entries as stale, not authoritative.

---

## 📜 Version History

| Version | Date | Main Changes |
|------|------|---------|
| v4.8.0 | 2026-07-19 | USCI runtime QC persistence + low-noise Discord alerting (`usci_runtime_qc.json`, diffed against the prior run so unresolved issues don't re-notify); shared `cpo_chain/normalization.py` between `/chain` and the dashboard graph (alias merging, placeholder filtering); Wikidata entity resolution hardened with throttling, 429 retry/backoff, and result caching (zero prior test coverage, now 11 tests); graph detail panel made independently scrollable. Note: versions between v4.6.4 and v4.8.0 were not tracked in this table — substantial unlogged work landed in that window (USCI schema migration, multi-account support, public-sync tooling); treat this table as incomplete before v4.8.0. |
| v4.6.3 | 2026-05-04 | Security R2: `$summary_test` owner-only + cooldown + timeout, `proc.communicate` with `wait_for` in 9 places, atomic `_try_cooldown`, `EntityResolver` commit, `INSERT OR IGNORE`, decoupled cache TTL, moved `argparse` into a function, fixed bare `except` |
| v4.6.2 | 2026-05-04 | Security R1: `httpx` log filtering, `/pausex` and `/resumex` owner guard, Gemini prompt TWEET_DATA isolation, `EXTRACTION_PROMPT` brace escaping, 60-second rate limiting, parameterized SQL, `</script>` XSS fix |
| v4.6.1 | 2026-04-30 | Code review fixes: import `os`, only write the monthly stamp after success, SDK candidate checks, unified logger, `_DEFAULT_ACCOUNT` constant, `encoding='utf-8'`, FTS5 OperationalError logging |
| v4.6 | 2026-04-30 | P-2 Gemini SDK/CLI toggle; P-1 incremental FTS5 trigger sync; F-3 monthly summary schedule |
| v4.5.1 | 2026-04-30 | Code review fixes: immutable cache, defensive float clamping, `find`/`rfind` JSON handling, CPO regeneration timeout, `KeepAlive` dict |
| v4.5 | 2026-04-29 | A-3 confidence scores (0.0–1.0); D-2 dashboard imports `analyze_topic()` directly; P-4 launchd plist |
| v4.4.1 | 2026-04-29 | Code review fixes: meta refresh, extracted `_run_gemini()`, `tempfile`, per-account cap |
| v4.4 | 2026-04-29 | Multi-account search (`--account all`); 5-level sentiment; dashboard auto-refresh |
| v4.3 | 2026-04-28 | All P0/P1 items: cache key, typing indicator, FTS5 porter, improved `/stats`, Gemini date context |
| v4.2.1 | 2026-04-22 | Code review fixes: await fallback, `escape_markdown`, XSS protection |
| v4.2 | 2026-04-21 | Daily summary (3 messages); `/account` enable/disable |
| v4.1 | 2026-04-19 | `accounts.yaml` enabled flag; monitor hot reload; CPO Network D3.js |
| v3.7.x | 2026-04-17 | CPO supply chain: USCI DB schema, 48 companies, 106 relationships, `/chain` command |
| v3.6 | 2026-04-04 | Multi-account crawling; `/summary` command |
| v3.5 | 2026-04-03 | `restart_chrome.sh`; FTS5 porter; `Path(__file__)` |
| v3.4 | 2026-03-31 | Active polling; self-healing; jitter; metrics |

---

## Public Sync

For public repository publication rules and the release gate, see:

- `CHANGELOG.md`
- `docs/public-sync-policy.md`
- `docs/public-release-checklist.md`

Recommended preflight flow:

```bash
python3 scripts/prepare_public_sync.py --write-manifest /tmp/xtracker-public-manifest.txt
python3 scripts/check_public_sync.py --paths-file /tmp/xtracker-public-manifest.txt
python3 scripts/sync_public_repo.py --manifest /tmp/xtracker-public-manifest.txt --target-root /path/to/x-tracker-public-clone
scripts/release_public.sh --target-root /path/to/x-tracker-public-clone
```

*Powered by Playwright · Gemini · Discord.py · Streamlit · SQLite FTS5*
