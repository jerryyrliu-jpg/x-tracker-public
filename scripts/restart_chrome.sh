#!/bin/bash
# X-Tracker v3.4 Self-Healing Script: Restart Chrome (v2 Hardened)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "♻️ Restarting Chrome (9222)..."
pkill -f "Google Chrome.*x_scraper"
sleep 5

# 強力清理 SingletonLock，防止 CDP 連線拒絕
find "$PROJECT_DIR/.profiles/x_scraper" -name "SingletonLock" -delete 2>/dev/null

# 使用 Headless-new 啟動 (Chrome 109+ 推薦)
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROJECT_DIR/.profiles/x_scraper" \
  --headless=old \
  --disable-gpu \
  --remote-allow-origins="*" >> "$PROJECT_DIR/chrome.log" 2>&1 &

sleep 5
echo "✅ Chrome restarted with SingletonLock cleanup."
