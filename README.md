# X-Tracker v2.0

基於 Playwright 的高效能推特追蹤與 AI 投資分析系統。

## 🚀 核心功能
1. **Playwright 抓取**: 模擬真人行為，繞過 Cloudflare 封鎖。
2. **Session 持久化**: 繼承瀏覽器登入狀態，無需重複輸入密碼。
3. **Gemini AI 摘要**: 使用 gemini-3.1-pro-preview 提煉月度投資標的。
4. **Discord 整合**: 自動推送最新推文與分析報表。
5. **FTS5 全文搜尋**: 資料庫支援高速關鍵字檢索。

## 🛠 快速啟動
```bash
source venv/bin/activate
# 每日抓取
python3 scraper_playwright.py
# 生成月度摘要
python3 monthly_summary.py --days 30
```
