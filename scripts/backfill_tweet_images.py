import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from playwright.async_api import async_playwright

import scraper_playwright
from utils import get_db_conn


DB_PATH = BASE_DIR / "tweets.db"


def _parse_args():
    parser = argparse.ArgumentParser(description="Backfill local image paths for existing tweets with images=[]")
    parser.add_argument("--account", type=str, help="Limit to a single tracked account")
    parser.add_argument("--limit", type=int, default=20, help="Max tweets to inspect")
    parser.add_argument("--tweet-id", action="append", default=[], help="Specific tweet ID to backfill; may be repeated")
    parser.add_argument("--dry-run", action="store_true", help="Inspect targets without updating DB")
    return parser.parse_args()


async def _find_matching_article(page, tweet_id: str):
    articles = await page.query_selector_all("article[data-testid='tweet']")
    for article in articles:
        link = await article.query_selector("a[href*='/status/']")
        href = await link.get_attribute("href") if link else None
        if href and f"/status/{tweet_id}" in href:
            return article
    return articles[0] if articles else None


async def _backfill_rows(rows, dry_run: bool) -> dict:
    summary = {
        "selected": len(rows),
        "updated": 0,
        "no_images_found": 0,
        "download_failed": 0,
        "missing_article": 0,
    }
    conn = get_db_conn(DB_PATH)
    conn.row_factory = None
    browser = None
    page = None
    owned_context = None

    try:
        async with async_playwright() as playwright_api:
            browser = await playwright_api.chromium.connect_over_cdp("http://127.0.0.1:9222")
            has_existing_context = bool(browser.contexts)
            context = browser.contexts[0] if has_existing_context else await browser.new_context()
            if not has_existing_context:
                owned_context = context
            page = await context.new_page()
            await page.route("**/*", scraper_playwright.intercept_route)

            for row in rows:
                tweet_id, account = row["id"], row["account"]
                url = f"https://x.com/{account}/status/{tweet_id}"
                await page.goto(url, wait_until="load", timeout=60000)
                await page.wait_for_selector("article[data-testid='tweet']", timeout=30000)
                article = await _find_matching_article(page, tweet_id)
                if article is None:
                    summary["missing_article"] += 1
                    continue

                image_urls = await scraper_playwright._extract_tweet_image_urls(article)
                if not image_urls:
                    summary["no_images_found"] += 1
                    continue

                image_paths = await scraper_playwright._download_tweet_images(account, tweet_id, image_urls)
                if not image_paths:
                    summary["download_failed"] += 1
                    continue

                if not dry_run:
                    scraper_playwright._update_tweet_images(conn, tweet_id, image_paths)
                summary["updated"] += 1
    finally:
        conn.close()
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if owned_context is not None:
            try:
                await owned_context.close()
            except Exception:
                pass

    return summary


async def main():
    args = _parse_args()
    conn = get_db_conn(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = scraper_playwright._select_tweets_missing_images(
            conn,
            account=args.account,
            limit=max(1, args.limit),
            tweet_ids=list(dict.fromkeys(args.tweet_id or [])),
        )
        rows = [dict(row) for row in rows]
    finally:
        conn.close()

    if not rows:
        print(json.dumps({"selected": 0, "updated": 0, "message": "No tweets with images=[] matched the request."}))
        return

    summary = await _backfill_rows(rows, dry_run=args.dry_run)
    summary["dry_run"] = args.dry_run
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
