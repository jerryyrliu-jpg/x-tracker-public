import asyncio, json, os, sqlite3, re, httpx
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright
from dotenv import load_dotenv
SCRAPER_BASE = Path(os.getcwd())
load_dotenv(SCRAPER_BASE / ".env")
DB_PATH = SCRAPER_BASE / "tweets.db"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_SERENITY")
async def post_to_discord(tweet):
    if not DISCORD_WEBHOOK: return
    msg = f"**@aleabitoreddit** `{tweet["time"]}`\n{tweet["text"]}\nhttps://x.com/aleabitoreddit/status/{tweet["id"]}"
    async with httpx.AsyncClient() as client: await client.post(DISCORD_WEBHOOK, json={"content": msg})
async def main():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            page = browser.contexts[0].pages[0]
            print(f"🔗 已連線: {await page.title()}")
            await page.goto("https://x.com/aleabitoreddit", wait_until="domcontentloaded")
            # 強制等待 article 標籤出現
            await page.wait_for_selector("article", timeout=30000)
            await asyncio.sleep(5)
            articles = await page.query_selector_all("article")
            print(f"✅ 發現 {len(articles)} 條推文")
            conn = sqlite3.connect(DB_PATH)
            new_count = 0
            for t in articles:
                try:
                    link = await t.query_selector("a[href*=\"/status/\"]")
                    if not link: continue
                    tid = re.search(r"/status/(\d+)", await link.get_attribute("href")).group(1)
                    txt_el = await t.query_selector("[data-testid=\"tweetText\"]")
                    txt = await txt_el.inner_text() if txt_el else ""
                    tm = await (await t.query_selector("time")).get_attribute("datetime")
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR IGNORE INTO tweets (id, account, created_at, text, images, scraped_at) VALUES (?,?,?,?,?,?)", (tid, "aleabitoreddit", tm, txt, "[]", datetime.now().isoformat()))
                    if cursor.rowcount > 0:
                        new_count += 1
                        print(f"✨ 成功捕獲: {tid}")
                        await post_to_discord({"id": tid, "text": txt, "time": tm})
                except: pass
            conn.commit()
            conn.close()
            print(f"🏁 完成，新增 {new_count} 筆。")
        except Exception as e: print(f"❌ 錯誤: {e}")
if __name__ == "__main__": asyncio.run(main())
