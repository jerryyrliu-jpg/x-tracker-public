#!/bin/bash
# X-Tracker v3.4 Self-Healing Script: Restart Chrome CDP

echo "♻️ Restarting Chrome (9222)..."
pkill -f "Google Chrome"
sleep 5
# 開啟帶有遠端偵錯功能且獨立 Profile 的 Chrome
# 注意: 這裡假設路徑與之前 snapshot 紀錄一致
open -a "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="/Users/yj/Desktop/PyProjects/X-tracker/.profiles/x_scraper" --headless
echo "✅ Chrome restarted in headless mode."
