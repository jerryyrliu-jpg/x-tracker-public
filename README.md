# X-Tracker v4.6.4

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

cpo_chain/output/index.html    ← D3.js CPO network (search + 4 category filters)
scripts/update_network_html.py ← regenerates index.html from the USCI database

launchd (auto-start on login + automatic crash restart):
~/Library/LaunchAgents/com.xtracker.discord.plist
~/Library/LaunchAgents/com.xtracker.monitor.plist
```

**Current snapshot**: SQLite WAL · FTS5 porter tokenizer · USCI graph database · 3-day Gemini cache

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
   - `/chain` — full upstream/midstream/downstream CPO supply-chain view
   - `/analyze` — slash-command version of the query flow
   - `/pausex` / `/resumex` — pause and resume the monitor for Chrome resource management
9. **Streamlit dashboard** — The candlestick chart imports `analyze_topic()` directly with no subprocess; includes auto-refresh meta tags and a `force_refresh` checkbox
10. **Monthly summary schedule** — The monitor automatically runs `monthly_summary.py` on the 1st of each month at 09:00+, guarded by `.last_monthly_summary` to prevent duplicate runs

---

## ⚙️ Setup

### `.env`
```
GEMINI_API_KEY=...          # Gemini SDK mode (enables automatically when set)
GEMINI_BACKEND=auto         # sdk | cli | auto (default: auto)
GEMINI_MODEL=gemini-2.5-flash-lite
MONTHLY_SUMMARY_TIMEOUT=600 # Monthly summary timeout in seconds (default: 600)
EDGAR_USER_AGENT=x-tracker your@email.com  # Required SEC EDGAR user agent
```

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

---

## 📜 Version History

| Version | Date | Main Changes |
|------|------|---------|
| v4.6.4 | 2026-05-04 | Security R3: `extract_universal` timeout, `monthly_summary` TWEET_DATA isolation + timeout + `GEMINI_MODEL`, parameterized `export_universal` SQL, `/chain` markdown escaping, `/account` error redaction, dashboard subprocess `text=True`, `get_running_loop`, atomic HTML writes, `EDGAR_USER_AGENT` env var |
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
