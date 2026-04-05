# X-Tracker v3.6 (Serenity)

基於 Playwright 的工業級推特追蹤與 AI 投資分析系統。透過 CDP 模式與混合 LLM 策略，達成穩定、隱形的社交媒體監控。

## 🚀 核心功能
1. **Playwright CDP Mode**: 連結真實瀏覽器視窗，完美繞過 X 的 Cloudflare 403 與 Bot 偵測。
2. **Active Polling**: 每 60 分鐘主動輪詢，支援隨機時間抖動 (Jitter) 與真人行為模擬。
3. **Hybrid LLM 策略**: 使用 Gemini 3.1 Pro 進行深度分析，Flash Lite 處理即時摘要。
4. **Discord Bot 2.0**:
    - **$TICKER**: 即時查詢特定標的最近 30 天的觀點轉變。
    - **/summary**: 一鍵生成全標的情緒報表（支援 `days:N`）。
    - **Typing Indicator**: 優化用戶互動體驗。
5. **SQLite FTS5**: 支援 Porter Tokenizer 的英文詞幹全文檢索 (如 buying 匹配 buy)。

## 🛠 快速啟動
```bash
source venv/bin/activate
# 啟動主動監控器 (背景執行)
nohup python3 monitor_active.py &
# 啟動 Discord Bot
nohup python3 discord_bot.py &
```

## 📜 Change List

### v3.6 — 2026-04-04
- **新增 `/summary` 指令**: 自動掃描全標的情緒判斷與觀點轉變。
- **並發優化**: 為 temp JSON 加入 message.id，解決 Discord 併發查詢衝突。

### v3.5 — 2026-04-03
- **系統加固**: 實作 `scripts/restart_chrome.sh` 自我修復機制。
- **FTS5 升級**: 導入 Porter Tokenizer，提升英文搜尋召回率。
- **路徑修復**: 全面採用 `Path(__file__)` 正規化，支援 launchd 呼叫。

### v3.4 — 2026-03-31
- **架構轉型**: 棄用 RSS 監控，全面轉向 Active Polling 主動輪詢模式。
- **日誌輪替**: 導入 `RotatingFileHandler` 限制日誌大小至 5MB。

---
*Created by OpenClaw Agents v3.5. Powered by #mymac.*