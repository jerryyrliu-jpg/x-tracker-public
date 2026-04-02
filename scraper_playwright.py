import asyncio
import json
import os
import sqlite3
import re
import httpx
import sys
import random
import signal
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright
from dotenv import load_dotenv

# 使用絕對路徑確保在不同目錄下執行皆正常
SCRAPER_BASE = Path("/Users/yj/Desktop/PyProjects/X-tracker")
load_dotenv(SCRAPER_BASE / ".env")

DB_PATH = SCRAPER_BASE / "tweets.db"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_SERENITY")
ACCOUNT = "aleabitoreddit"

async def post_to_discord(tweet):
    if not DISCORD_WEBHOOK:
        return
    msg = f"**@{ACCOUNT}** \\\`{tweet['time']}\\\`\n{tweet['text']}\nhttps://x.com/{ACCOUNT}/status/{tweet['id']}"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=15)
        except Exception as e:
            print(f"⚠️ Discord Post Error: {e}", file=sys.stderr)

async def intercept_route(route):
    """攔截不必要的資源（圖片、影片、字體），保留 CSS 以利定位"""
    exts = [".png", ".jpg", ".jpeg", ".mp4", ".woff", ".woff2", ".otf", ".ttf"]
    if any(route.request.url.lower().endswith(ext) for ext in exts):
        await route.abort()
    elif route.request.resource_type in ["image", "media", "font"]:
        await route.abort()
    elif "google-analytics" in route.request.url or "analytics.twitter.com" in route.request.url:
        await route.abort()
    else:
        await route.continue_()

async def human_like_scroll(page):
    """模擬人類隨機滾動行為"""
    steps = random.randint(2, 4)
    for _ in range(steps):
        scroll_amount = random.randint(300, 700)
        await page.mouse.wheel(0, scroll_amount)
        await asyncio.sleep(random.uniform(0.5, 1.5))

async def scrape():
    result = {"status": "error", "new_count": 0, "message": "", "timestamp": datetime.now().isoformat()}
    
    async with async_playwright() as p:
        try:
            # 連線至現有的 CDP 瀏覽器
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            # 優先使用現有的 context
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            
            # 設定資源攔截
            await page.route("**/*", intercept_route)
            
            # 導航至帳號頁面
            await asyncio.sleep(random.uniform(1.0, 3.0))
            url = f"https://x.com/{ACCOUNT}"
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # 使用 data-testid 定位推文
            try:
                await page.wait_for_selector("article[data-testid='tweet']", timeout=30000)
            except Exception:
                result["status"] = "potential_structure_change"
                result["message"] = "Cannot find tweet elements"
                print(json.dumps(result))
                await page.close()
                sys.exit(2)
            
            # 執行隨機滾動
            await human_like_scroll(page)
            await asyncio.sleep(random.uniform(1.0, 2.0))
            
            articles = await page.query_selector_all("article[data-testid='tweet']")
            
            conn = sqlite3.connect(DB_PATH)
            new_count = 0
            
            for t in articles[:10]:
                try:
                    link_el = await t.query_selector("a[href*='/status/']")
                    if not link_el: continue
                    href = await link_el.get_attribute("href")
                    tid_match = re.search(r"/status/(\d+)", href)
                    if not tid_match: continue
                    tid = tid_match.group(1)
                    
                    txt_el = await t.query_selector("[data-testid='tweetText']")
                    txt = await txt_el.inner_text() if txt_el else ""
                    
                    time_el = await t.query_selector("time")
                    tm = await time_el.get_attribute("datetime") if time_el else datetime.now().isoformat()
                    
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT OR IGNORE INTO tweets (id, account, created_at, text, images, scraped_at) VALUES (?,?,?,?,?,?)",
                        (tid, ACCOUNT, tm, txt, "[]", datetime.now().isoformat())
                    )
                    
                    if cursor.rowcount > 0:
                        new_count += 1
                        await post_to_discord({"id": tid, "text": txt, "time": tm})
                except Exception:
                    continue
            
            conn.commit()
            if new_count > 0:
                conn.execute("INSERT INTO tweets_fts(tweets_fts) VALUES('rebuild')")
                conn.commit()
            conn.close()

            result["status"] = "success"
            result["new_count"] = new_count
            await page.close()
            
        except Exception as e:
            result["message"] = str(e)
        
        print(json.dumps(result))
        if result["status"] == "success":
            sys.exit(0)
        else:
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(scrape())
