import feedparser, time, subprocess, os, sys
from pathlib import Path
X_ACCOUNT = "aleabitoreddit"
RSS_URL = f"https://nitter.cz/{X_ACCOUNT}/rss"
CHECK_INTERVAL = 600
STATE_FILE = Path(os.getcwd()) / ".last_guid"
def monitor():
    print(f"📡 RSS Monitor: @{X_ACCOUNT}")
    while True:
        try:
            feed = feedparser.parse(RSS_URL)
            if feed.entries:
                guid = feed.entries[0].guid
                if guid != (STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""):
                    print(f"✨ New tweet! Triggering scraper...")
                    subprocess.run([sys.executable, "scraper_playwright.py"])
                    STATE_FILE.write_text(guid)
                else: print("😴 Sleeping...")
            else: print("⚠️ RSS feed empty")
        except Exception as e: print(f"❌ Error: {e}")
        time.sleep(CHECK_INTERVAL)
if __name__ == "__main__": monitor()
