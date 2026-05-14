# X-Tracker v4.6 (Serenity)

基於 Playwright CDP 的多帳號推特追蹤與 AI 投資分析系統。透過 Gemini 深度分析推文情感趨勢，整合 CPO 供應鏈知識圖譜，透過 Discord Bot 與 Streamlit Dashboard 呈現。

---

## 🏗 架構

```
Chrome (CDP 9222)
    └─ scraper_playwright.py   ← DOM 擷取；FTS5 trigger 自動同步；--account arg
         ↑ 每帳號每 ~2hr 輪詢
monitor_active.py              ← PID lock、metrics、self-healing、heartbeat
                                  ├─ 遍歷 accounts.yaml 所有帳號
                                  └─ 每月 1 日 09:00+ 自動執行月報

tweets.db (SQLite WAL + FTS5 porter + USCI schema)
    ├─ query_topic.py          ← analyze_topic() → Gemini SDK/CLI → JSON cache
    │    ↑ imported by dashboard; called by discord_bot subprocess
    └─ discord_bot.py          ← $TICKER [days:N]、/stats、/supply、/chain、/analyze

dashboard.py (Streamlit)
    ├─ Tab1: K 線圖 + 情感箭頭（信心分數縮放）+ force_refresh
    └─ Tab2: D3.js CPO 供應鏈知識圖譜

utils.py                       ← DB、Discord、logger、PIDLock、Metrics
accounts.yaml                  ← 多帳號設定（aleabitoreddit、CKCapitalxx、gbstocks）

cpo_chain/output/index.html    ← D3.js CPO Network（搜尋 + 4 類別篩選）
scripts/update_network_html.py ← 從 USCI DB 重新產生 index.html

launchd (開機自啟 + crash 自動重啟):
~/Library/LaunchAgents/com.xtracker.discord.plist
~/Library/LaunchAgents/com.xtracker.monitor.plist
```

**資料現況**：3 帳號 · SQLite WAL · FTS5 porter tokenizer · USCI DB（48 CPO 公司，106 供應關係）· 3 天 Gemini cache

---

## 🚀 核心功能

1. **多帳號爬取** — `accounts.yaml` 驅動，monitor 遍歷所有 enabled 帳號；scraper 接受 `--account` 參數
2. **Playwright CDP** — 連結真實瀏覽器，繞過 Cloudflare 403 / Bot 偵測
3. **Active Polling** — 每 ~2hr 主動輪詢，隨機抖動 ±5–15 分鐘，3 次失敗自動重啟 Chrome
4. **Gemini 分析** — `GEMINI_BACKEND=sdk|cli|auto`；`GEMINI_API_KEY` 設定後自動切換 SDK（無 subprocess overhead）
5. **5 級情感 + 信心分數** — StrongBullish / Bullish / Neutral / Bearish / StrongBearish；confidence 0.0–1.0 縮放 K 線箭頭大小
6. **多帳號搜尋** — `--account all` 跨帳號分析，prompt 按帳號分組
7. **FTS5 Trigger 同步** — INSERT 觸發器自動維護 FTS index，無需 full rebuild
8. **Discord Bot**：
   - `$TICKER [days:N]` — 跨帳號情感分析（含信心分數）
   - `/stats` — 每帳號推文數 + 最後爬取時間
   - `/chain` — CPO 供應鏈上中下游全景
   - `/analyze` — slash command 版查詢
   - `/pausex` / `/resumex` — 暫停 / 恢復 monitor（Chrome 資源管理）
9. **Streamlit Dashboard** — K 線圖直接 import `analyze_topic()`（無 subprocess）；auto-refresh meta tag；force_refresh checkbox
10. **月報排程** — monitor 每月 1 日 09:00+ 自動執行 `monthly_summary.py`，`.last_monthly_summary` 防重複

---

## ⚙️ 設定

### `.env`
```
GEMINI_API_KEY=...          # Gemini SDK 模式（設定後自動啟用）
GEMINI_BACKEND=auto         # sdk | cli | auto（預設 auto）
GEMINI_MODEL=gemini-2.5-flash-lite
MONTHLY_SUMMARY_TIMEOUT=600 # 月報逾時秒數（預設 600）
EDGAR_USER_AGENT=x-tracker your@email.com  # SEC EDGAR 必填 User-Agent
```

### `accounts.yaml`

Set webhook URLs via environment variables (never hardcode them):

```yaml
accounts:
  aleabitoreddit:
    enabled: true
    discord_webhook_env: DISCORD_WEBHOOK_ALEABITOREDDIT
  CKCapitalxx:
    enabled: true
    discord_webhook_env: DISCORD_WEBHOOK_CKCAPITALXX
  gbstocks:
    enabled: true
    discord_webhook_env: DISCORD_WEBHOOK_GBSTOCKS
```

Export each variable in your shell or `.env` file:
```bash
export DISCORD_WEBHOOK_ALEABITOREDDIT="https://discord.com/api/webhooks/..."
```

---

## 🛠 快速啟動

### 方式 A — launchd（推薦，開機自啟）
```bash
launchctl load ~/Library/LaunchAgents/com.xtracker.discord.plist
launchctl load ~/Library/LaunchAgents/com.xtracker.monitor.plist
# 查看狀態
launchctl list | grep xtracker
# 查看 log
tail -f logs/monitor_active.log
```

### 方式 B — 手動背景執行
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

> ⚠️ Always run with `--server.address 127.0.0.1` to bind to localhost only. Without this flag, Streamlit defaults to all interfaces and exposes the dashboard (and Gemini API costs) to anyone on the network.

---

## 📜 版本記錄

| 版本 | 日期 | 主要變更 |
|------|------|---------|
| v4.6.4 | 2026-05-04 | Security R3：extract_universal timeout、monthly_summary TWEET_DATA 隔離+timeout+GEMINI_MODEL、export_universal SQL parameterized、/chain escape_markdown、/account error redact、dashboard subprocess text=True、get_running_loop、HTML 原子寫入、EDGAR_USER_AGENT env var |
| v4.6.3 | 2026-05-04 | Security R2：$summary_test owner-only+cooldown+timeout、9 處 proc.communicate 加 wait_for、atomic _try_cooldown、EntityResolver commit、INSERT OR IGNORE、cache TTL 解耦、argparse 移入函式、bare except 修正 |
| v4.6.2 | 2026-05-04 | Security R1：httpx log 過濾、/pausex /resumex owner guard、Gemini prompt TWEET_DATA 隔離、EXTRACTION_PROMPT brace escape、60s rate limiting、SQL parameterized、</script> XSS 修正 |
| v4.6.1 | 2026-04-30 | Code review fixes：import os、monthly stamp 成功才寫、SDK candidates check、logger 統一、_DEFAULT_ACCOUNT 常數、encoding='utf-8'、FTS5 OperationalError log |
| v4.6 | 2026-04-30 | P-2 Gemini SDK/CLI toggle；P-1 FTS5 trigger 增量同步；F-3 月報排程 |
| v4.5.1 | 2026-04-30 | Code review fixes：immutable cache、defensive float+clamp、find/rfind JSON、CPO regen timeout、KeepAlive dict |
| v4.5 | 2026-04-29 | A-3 信心分數（confidence 0.0–1.0）；D-2 dashboard 直接 import analyze_topic()；P-4 launchd plist |
| v4.4.1 | 2026-04-29 | Code review fixes：meta-refresh、_run_gemini() 提取、tempfile、per-account cap |
| v4.4 | 2026-04-29 | 多帳號搜尋（--account all）；5 級情感；Dashboard auto-refresh |
| v4.3 | 2026-04-28 | P0/P1 全部：cache key、typing indicator、FTS5 porter、/stats 強化、Gemini 日期上下文 |
| v4.2.1 | 2026-04-22 | Code review fixes：await fallback、escape_markdown、XSS 防護 |
| v4.2 | 2026-04-21 | 每日摘要（3 msgs）；/account enable/disable |
| v4.1 | 2026-04-19 | accounts.yaml enabled flag；monitor hot-reload；CPO Network D3.js |
| v3.7.x | 2026-04-17 | CPO 供應鏈：USCI DB schema、48 公司 106 關係、/chain 指令 |
| v3.6 | 2026-04-04 | 多帳號爬取；/summary 指令 |
| v3.5 | 2026-04-03 | restart_chrome.sh；FTS5 porter；Path(__file__) |
| v3.4 | 2026-03-31 | Active Polling；self-healing；jitter；metrics |

---

*Powered by Playwright · Gemini · Discord.py · Streamlit · SQLite FTS5*
