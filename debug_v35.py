import asyncio
import os
import random
import sys
from playwright.async_api import async_playwright
from pathlib import Path
from datetime import datetime

async def debug_screenshot():
    async with async_playwright() as p:
        try:
            # 連線至現有的 CDP 瀏覽器
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            
            # 導航
            print(f"[{datetime.now().isoformat()}] 📸 Navigating to account page...")
            try:
                await page.goto("https://x.com/aleabitoreddit", wait_until="domcontentloaded", timeout=30000)
                print("✅ Navigation (domcontentloaded) finished.")
            except Exception as e:
                print(f"⚠️ Navigation warning: {e}")
            
            # 等待渲染
            await asyncio.sleep(10) 
            
            screenshot_path = "/Users/yj/Desktop/PyProjects/X-tracker/debug_v35.png"
            await page.screenshot(path=screenshot_path)
            print(f"✅ Screenshot saved to: {screenshot_path}")
            
            # 檢查頁面內容
            title = await page.title()
            articles = await page.query_selector_all("article[data-testid='tweet']")
            print(f"📄 Page Title: {title}")
            print(f"🐦 Tweets found on page: {len(articles)}")
            
            if articles:
                txt_el = await articles[0].query_selector("[data-testid='tweetText']")
                if txt_el:
                    txt = await txt_el.inner_text()
                    print(f"🐦 First Tweet Text: {txt[:100]}...")
                else:
                    print("🐦 First article has no tweetText")
            
            # 看看有沒有什麼奇怪的文字
            body_text = await page.inner_text("body")
            if "Something went wrong" in body_text:
                print("🚨 Page says: 'Something went wrong. Try reloading.'")
            if ("Log in" in body_text and "Sign up" in body_text) or "Join X today" in body_text:
                print("🚨 LOGIN WALL DETECTED!")
            
            await page.close()
        except Exception as e:
            print(f"❌ Error during debug: {e}")

if __name__ == "__main__":
    asyncio.run(debug_screenshot())
