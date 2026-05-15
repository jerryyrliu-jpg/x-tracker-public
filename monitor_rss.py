import time, subprocess, sys, requests, feedparser
from pathlib import Path
X_ACCOUNT = "aleabitoreddit"
RSS_URL = f"https://nitter.cz/{X_ACCOUNT}/rss"
CHECK_INTERVAL = 600
_BASE = Path(__file__).resolve().parent
STATE_FILE = _BASE / ".last_guid"

def _fetch_feed(url: str):
    resp = requests.get(url, timeout=10, headers={"User-Agent": "x-tracker/1.0"})
    return feedparser.parse(resp.content)

def monitor():
    print(f"📡 RSS Monitor: @{X_ACCOUNT}")
    while True:
        try:
            feed = _fetch_feed(RSS_URL)
            if feed.entries:
                guid = feed.entries[0].guid
                if not isinstance(guid, str) or len(guid) > 200:
                    print("⚠️ RSS: invalid GUID, skipping")
                elif guid != (STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""):
                    print("✨ New tweet! Triggering scraper...")
                    subprocess.run(
                        [sys.executable, str(_BASE / "scraper_playwright.py")],
                        timeout=600,
                    )
                    STATE_FILE.write_text(guid)
                else:
                    print("😴 Sleeping...")
            else:
                print("⚠️ RSS feed empty")
        except Exception as e:
            print(f"❌ Error: {type(e).__name__}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()
