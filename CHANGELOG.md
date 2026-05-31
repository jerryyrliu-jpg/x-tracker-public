# Changelog

All notable changes to X-Tracker are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [4.7.1] — 2026-05-31

### Security (Round 5 — Opus 4.7 review)

**MEDIUM**
- `discord_bot.py`: Temp file leak fixed in `_run_daily_summary_for_account` — `return` on timeout now inside outer `try/finally` so daily automated runs never orphan temp files

**LOW**
- `discord_bot.py`: Temp file leak fixed in `on_message $ticker` handler — same `try/finally` pattern applied
- `discord_bot.py`: `/resumex` subprocess now uses `sys.executable` instead of hardcoded `venv/bin/python` — consistent across environments
- `discord_bot.py`: `_run_cpo_update` `print()` calls replaced with `logging.warning()` / `logging.info()` for uniform log routing
- `monitor_active.py`: Version string corrected from `v3.4` to `v4.7.0`

### Fixed
- `launchd/com.xtracker.discord.plist`, `launchd/com.xtracker.monitor.plist`: Log paths moved from `Desktop/…/logs/` to `~/Library/Logs/xtracker/` — resolves launchd exit code 78 (EX_CONFIG) caused by macOS TCC blocking Desktop folder access before Python spawns
- launchd plist files added to repo under `launchd/` for version control
- `cpo_chain/batch_embed.py`: `CAST(id AS INTEGER)` in `NOT IN` subquery — fixes type mismatch between `tweets.id` (TEXT) and `tweet_embeddings.tweet_id` (INTEGER)
- `cpo_chain/batch_embed.py`: `INSERT OR REPLACE` replaced with `DELETE` + `INSERT` for virtual table safety
- `cpo_chain/batch_embed.py`: Per-row `except Exception` added to embedding insert loop

### Data
- `themes/USCI_Report.md`: Regenerated 2026-05-25 — higher-confidence edges (⚠️ 0.55 → ✅ 0.95), new sub-industries: 1.6T Optical Transceivers, CPO / NVIDIA Ecosystem, CPO / 1.6T Transceiver

---

## [4.7.0] — 2026-05-24

### Security (4 rounds of Opus 4.7 review)

**Round 1 — CRITICAL / HIGH**
- `llm_url.py`: SSRF — `_SSRFSafeTransport` resolves DNS once, validates all IPs, pins connection to validated IP (eliminates TOCTOU DNS rebinding gap)
- `llm_url.py`: SSRF — `_validate_url` blocks private, loopback, link-local (GCP/AWS metadata 169.254.0.0/16), reserved, multicast IPs
- `llm_url.py`: Prompt injection — `_sanitize_user_content()` strips `</?PAGE_CONTENT>` isolation tags from tweet/reply text before embedding in Gemini prompt
- `llm_url.py`: Prompt injection — `<PAGE_CONTENT>` isolation block wraps all user-supplied content in both prompt builders
- `llm_url.py`: Content-Type allowlist — rejects non-text responses (`application/json`, `application/pdf`, etc.)
- `llm_url.py`: Byte-cap streaming — `aiter_bytes()` stops at 300 KB to prevent memory exhaustion

**Round 2 — HIGH / MEDIUM**
- `llm_url.py`: `_SSRFSafeTransport` validates port on every redirect hop (catches port-smuggling via redirects)
- `llm_url.py`: `_SSRFSafeTransport` prefers IPv4 over IPv6 when pinning resolved address
- `llm_url.py`: `_SSRFSafeTransport` brackets IPv6 addresses correctly in URLs
- `llm_url.py`: URL allowlist — only `http`/`https` schemes, ports `None/80/443`

**Round 3 — HIGH / MEDIUM / LOW**
- `llm_url.py`: CSS selector changed from `href*=` (contains) to `href$=` (ends-with) for exact tweet-ID matching — prevents false positives on retweet/quote URLs
- `llm_url.py`: `_fetch_tweet` `finally` block wraps `page.close()` and `context.close()` in individual `try/except` to prevent cleanup exceptions masking original errors
- `llm_url.py`: `_run_gemini` removes CLI fallback when SDK fails if `GEMINI_API_KEY` is set — same key would fail identically; CLI only used when no key present
- `llm_url.py`: `resp.charset_encoding` read moved inside `async with` block (was read after stream context exited)
- `llm_url.py`: `await resp.aclose()` called before `break` in byte-cap loop
- `llm_url.py`: Magic timeout values replaced with named constants (`_PAGE_LOAD_TIMEOUT_MS`, `_TWEET_SELECTOR_TIMEOUT_MS`, `_REPLY_WAIT_TIMEOUT_MS`)
- `discord_bot.py`: URL length check (> 2048) added before `defer()` in `/llm`
- `discord_bot.py`: `/pausex` `pkill` uses absolute path (`SCRAPER_BASE / script`) instead of relative script name
- `discord_bot.py`: `print()` in `/llm` error paths replaced with `logging.warning()`

**Round 4 — MEDIUM / LOW**
- `llm_url.py`: URL sanitized with `_ISOLATION_TAG_RE.sub()` before embedding in Gemini prompts — `</PAGE_CONTENT>` in URL path can no longer escape isolation tag
- `llm_url.py`: `_validate_url` DNS call wrapped in `asyncio.to_thread` in `main()` to avoid blocking the event loop
- `discord_bot.py`: Temp file leak fixed in `/llm`, `/summary`, `/analyze`, `$summary_test` — `return` on timeout now inside outer `try/finally` block so cleanup always runs
- `discord_bot.py`: `/resumex` `pkill` updated to absolute path (Round 3 only fixed `/pausex`)
- `discord_bot.py`: `/supply` `is_stale` check uses `datetime.now(gen_at.tzinfo)` to handle timezone-aware cache timestamps — prevents `TypeError` crash
- `discord_bot.py`: URL in `/llm` summary header escaped with `discord.utils.escape_markdown()`
- `discord_bot.py`: `accounts.yaml` reads in `_run_monthly_summary()` and `scheduled_summary()` protected by `_accounts_yaml_lock` (write already used lock)

### Fixed
- `llm_url.py`: Extract main tweet BEFORE scrolling — X removes top articles from DOM during scroll (virtual rendering); old code scrolled first and found nothing
- `llm_url.py`: Auto-resolve `/i/status/<id>` URL format — detects post-navigation canonical URL and updates `author`/`tweet_id` accordingly
- `discord_bot.py`: `/llm` always reads output JSON regardless of subprocess exit code — specific error messages from `llm_url.py` were previously silently lost when `returncode != 0`

### Tests
- Added 70 unit tests for `llm_url.py` covering: URL validation (17), content sanitization (6), HTML stripping (6), prompt builders (15), SSRF transport (10), generic fetcher (5), port rejection (3), regex (4), Gemini CLI suppression (3), URL isolation tag sanitization (2) — up from 0

---

## [4.6.4] — 2026-05-04

### Security (Round 3)
- `extract_universal.py` timeout guard
- `monthly_summary.py`: TWEET_DATA isolation + timeout + `GEMINI_MODEL` env var
- `export_universal.py`: parameterized SQL
- `/chain`: `escape_markdown` on company names
- `/account`: error messages redact account details
- Dashboard: subprocess `text=True`, `get_running_loop`, HTML atomic write
- `EDGAR_USER_AGENT` moved to env var

---

## [4.6.3] — 2026-05-04

### Security (Round 2)
- `$summary_test`: owner-only + cooldown + timeout
- 9 `proc.communicate()` calls wrapped in `asyncio.wait_for`
- Atomic `_try_cooldown` (no race between check and set)
- `EntityResolver` commit fix
- `INSERT OR IGNORE` deduplication
- Cache TTL decoupled from analysis window
- `argparse` moved into function scope
- Bare `except:` → `except Exception:`

---

## [4.6.2] — 2026-05-04

### Security (Round 1)
- `httpx` log filtered to prevent webhook URL leaking
- `/pausex` `/resumex` restricted to bot owner
- Gemini prompt wrapped in `TWEET_DATA` isolation block
- `EXTRACTION_PROMPT` brace-escaped
- 60-second rate limiting on all heavy commands
- SQL queries parameterized throughout
- `</script>` XSS injection fixed in HTML export

---

## [4.6.1] — 2026-04-30

### Fixed
- Missing `import os` in monthly_summary
- Monthly stamp written only on success
- Gemini SDK: check `candidates` before accessing `text`
- Logger unified across modules
- `_DEFAULT_ACCOUNT` constant extracted
- `encoding='utf-8'` added to all file I/O
- FTS5 `OperationalError` logged instead of crashing

---

## [4.6.0] — 2026-04-30

### Added
- P-2: Gemini SDK/CLI toggle via `GEMINI_BACKEND` env var
- P-1: FTS5 trigger-based incremental sync (INSERT trigger → no full rebuild)
- F-3: Monthly summary scheduler (auto-runs on 1st of month)

---

## [4.5.1] — 2026-04-30

### Fixed
- Immutable cache objects (copy-on-write)
- Defensive float conversion + clamp for confidence scores
- JSON boundary detection with `find`/`rfind`
- CPO regen timeout guard
- KeepAlive dict usage

---

## [4.5.0] — 2026-04-29

### Added
- A-3: Confidence scores (0.0–1.0) — scales K-line arrow size in dashboard
- D-2: Dashboard directly imports `analyze_topic()` (no subprocess overhead)
- P-4: launchd plist for auto-start and crash recovery

---

## [4.4.1] — 2026-04-29

### Fixed
- Meta-refresh tag in dashboard
- `_run_gemini()` extracted as shared helper
- `tempfile.mkstemp` for safe temp file creation
- Per-account output cap

---

## [4.4.0] — 2026-04-29

### Added
- Multi-account search with `--account all`
- 5-level sentiment (StrongBullish / Bullish / Neutral / Bearish / StrongBearish)
- Dashboard auto-refresh via meta tag

---

## [4.3.0] — 2026-04-28

### Fixed / Improved
- Cache key collision (was overwriting across tickers)
- Typing indicator while Gemini processes
- FTS5 porter tokenizer for better CJK/mixed recall
- `/stats` enriched with per-account last-scraped time
- Gemini prompt includes current date for temporal context

---

## [4.2.1] — 2026-04-22

### Fixed
- `await` missing on async fallback call
- `escape_markdown` on ticker in Discord output
- XSS protection in HTML graph export

---

## [4.2.0] — 2026-04-21

### Added
- Daily summary scheduler (3 messages: per-account + overview)
- `/account enable/disable` slash command

---

## [4.1.0] — 2026-04-19

### Added
- `accounts.yaml` `enabled` flag per account
- Monitor hot-reload on config change
- CPO Network D3.js interactive graph

---

## [3.7.x] — 2026-04-17

### Added
- USCI DB schema (48 CPO companies, 106 supply relations)
- `/chain` slash command — upstream/midstream/downstream panorama
- `/supply` slash command — company-level relation query

---

## [3.6.0] — 2026-04-04

### Added
- Multi-account scraping
- `/summary` slash command

---

## [3.5.0] — 2026-04-03

### Added
- `restart_chrome.sh` script
- FTS5 porter tokenizer
- `Path(__file__)` for portable paths

---

## [3.4.0] — 2026-03-31

### Added
- Active Polling every ~2 hours with jitter
- Self-healing: 3 failures → auto-restart Chrome
- PID lock to prevent duplicate monitor processes
- Metrics (uptime, scrape count, last success)
