import asyncio
import json
import os
import sqlite3
import re
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright
from dotenv import load_dotenv
import httpx

SCRAPER_BASE = Path(os.getcwd())
load_dotenv(SCRAPER_BASE / '.env')

USER_DATA_DIR = SCRAPER_BASE / '.profiles/x_scraper'
DB_PATH = SCRAPER_BASE / 'tweets.db'
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK_SERENITY')

async def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS tweets (id TEXT PRIMARY KEY, created_at TEXT, text TEXT, images TEXT, scraped_at TEXT)')
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS tweets_fts USING fts5(text, content='tweets', content_rowid='rowid')")
    conn.close()

async def post_to_discord(tweet):
    if not DISCORD_WEBHOOK: return
    content = f"**@aleabitoreddit** `{tweet['time']}`
{tweet['text']}
https://x.com/aleabitoreddit/status/{tweet['id']}"
    if tweet['images']:
        imgs = json.loads(tweet['images'])
        for img in imgs: content += f"
{img}"
    async with httpx.AsyncClient() as client:
        try: await client.post(DISCORD_WEBHOOK, json={'content': content})
        except Exception as e: print(f'Discord Error: {e}')

async def scrape_serenity():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(str(USER_DATA_DIR), headless=True, args=['--disable-blink-features=AutomationControlled'])
        page = await context.new_page()
        print('🚀 Navigating to X...')
        await page.goto('https://x.com/aleabitoreddit', wait_until='networkidle')
        await page.wait_for_selector('article[data-testid="tweet"]', timeout=30000)
        for _ in range(3):
            await page.mouse.wheel(0, 1000)
            await asyncio.sleep(2)
        tweets = await page.query_selector_all('article[data-testid="tweet"]')
        conn = sqlite3.connect(DB_PATH)
        for t in tweets:
            try:
                link = await t.query_selector('a[href*="/status/"]')
                if not link: continue
                href = await link.get_attribute('href')
                tid = re.search(r'/status/(\d+)', href).group(1)
                txt_el = await t.query_selector('[data-testid="tweetText"]')
                txt = await txt_el.inner_text() if txt_el else ''
                tm_el = await t.query_selector('time')
                tm = await tm_el.get_attribute('datetime') if tm_el else ''
                imgs = [await img.get_attribute('src') for img in await t.query_selector_all('img[src*="media"]')]
                cursor = conn.cursor()
                cursor.execute('INSERT OR IGNORE INTO tweets VALUES (?, ?, ?, ?, ?)', (tid, tm, txt, json.dumps(imgs), datetime.now().isoformat()))
                if cursor.rowcount > 0:
                    print(f'✨ New: {tid}')
                    await post_to_discord({'id': tid, 'text': txt, 'time': tm, 'images': json.dumps(imgs)})
                    conn.execute('INSERT INTO tweets_fts(rowid, text) SELECT rowid, text FROM tweets WHERE id = ?', (tid,))
            except Exception as e: print(f'Parse Error: {e}')
        conn.commit()
        conn.close()
        await context.close()

if __name__ == '__main__':
    asyncio.run(init_db())
    asyncio.run(scrape_serenity())
