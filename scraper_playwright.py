import asyncio
import json
import os
import re
import httpx
import sys
import random
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright
from dotenv import load_dotenv
from utils import get_db_conn
from scraper import _ensure_fts_triggers

SCRAPER_BASE = Path(__file__).resolve().parent
load_dotenv(SCRAPER_BASE / ".env")

DB_PATH = SCRAPER_BASE / "tweets.db"
IMAGES_ROOT = SCRAPER_BASE / "images"

from utils import load_account_config as _load_cfg


def _parse_account_arg() -> tuple[str, str]:
    """Parse --account from argv without consuming other flags. Returns (account, webhook)."""
    import argparse as _argparse
    _parser = _argparse.ArgumentParser(add_help=False)
    _parser.add_argument("--account", default="aleabitoreddit")
    _args, _ = _parser.parse_known_args()
    account = _args.account
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', account):
        print(f"Invalid account name: {account!r}", file=sys.stderr)
        sys.exit(1)
    try:
        _cfg = _load_cfg(account, SCRAPER_BASE)
        webhook = _cfg.get("discord_webhook") or os.environ.get("DISCORD_WEBHOOK_SERENITY", "")
    except Exception as e:
        print(f"Warning: could not load account config for {account}: {e}", file=sys.stderr)
        webhook = os.environ.get("DISCORD_WEBHOOK_SERENITY", "")
    return account, webhook


ACCOUNT, DISCORD_WEBHOOK = _parse_account_arg()


def _build_tweet_image_dir(account: str, tweet_id: str) -> Path:
    return IMAGES_ROOT / account / tweet_id


def _filter_tweet_image_urls(urls: list[str]) -> list[str]:
    kept: list[str] = []
    for url in urls:
        if not url.startswith("https://"):
            continue
        if "pbs.twimg.com/media/" not in url:
            continue
        kept.append(url)
    return list(dict.fromkeys(kept))


async def _extract_tweet_image_urls(tweet_el) -> list[str]:
    raw_urls: list[str] = []
    for img in await tweet_el.query_selector_all("img"):
        src = await img.get_attribute("src")
        if src:
            raw_urls.append(src)
    return _filter_tweet_image_urls(raw_urls)


def _image_suffix_from_url(url: str) -> str:
    match = re.search(r"\.(jpg|jpeg|png|webp)(?:$|[?&])", url, re.IGNORECASE)
    if match:
        return "." + match.group(1).lower()
    return ".jpg"


async def _download_tweet_images(account: str, tweet_id: str, urls: list[str]) -> list[str]:
    if not urls:
        return []

    out_dir = _build_tweet_image_dir(account, tweet_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []

    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for idx, url in enumerate(urls, start=1):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception as e:
                print(f"⚠️ Image download error for {tweet_id}: {e}", file=sys.stderr)
                continue

            out_path = out_dir / f"{idx}{_image_suffix_from_url(url)}"
            out_path.write_bytes(resp.content)
            saved_paths.append(str(out_path.resolve()))

    return saved_paths


def _select_tweets_missing_images(conn, account: str | None = None, limit: int = 20, tweet_ids: list[str] | None = None):
    params: list[str | int] = []
    where = ["COALESCE(images, '[]') IN ('', '[]')"]
    if account:
        where.append("account = ?")
        params.append(account)
    if tweet_ids:
        placeholders = ",".join("?" for _ in tweet_ids)
        where.append(f"id IN ({placeholders})")
        params.extend(tweet_ids)
    params.append(limit)
    query = f"""
        SELECT id, account, created_at
        FROM tweets
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
        LIMIT ?
    """
    return conn.execute(query, params).fetchall()


def _update_tweet_images(conn, tweet_id: str, image_paths: list[str]) -> None:
    conn.execute(
        "UPDATE tweets SET images = ?, scraped_at = ? WHERE id = ?",
        (json.dumps(image_paths, ensure_ascii=False), datetime.now().isoformat(), tweet_id),
    )
    conn.commit()


def _base_result() -> dict:
    return {
        "status": "error",
        "new_count": 0,
        "message": "",
        "timestamp": datetime.now().isoformat(),
    }


def _is_cdp_connection_error(exc: Exception) -> bool:
    message = str(exc)
    markers = (
        "ECONNREFUSED",
        "connect_over_cdp",
        "retrieving websocket url",
        "Connection closed while reading from the driver",
    )
    return any(marker in message for marker in markers)


def _is_retryable_playwright_error(exc: Exception) -> bool:
    message = str(exc)
    markers = (
        "TargetClosedError",
        "Execution context was destroyed",
        "Target page, context or browser has been closed",
    )
    return any(marker in message for marker in markers)


async def _wait_for_cdp_ready(timeout_secs: int = 15) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_secs
    while asyncio.get_running_loop().time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get("http://127.0.0.1:9222/json/version")
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


async def _restart_cdp_chrome() -> bool:
    restart_script = SCRAPER_BASE / "scripts" / "restart_chrome.sh"
    if not restart_script.exists():
        return False
    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(restart_script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(SCRAPER_BASE),
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    if proc.returncode != 0:
        return False
    return await _wait_for_cdp_ready()

async def post_to_discord(tweet):
    if not DISCORD_WEBHOOK:
        print(f"⚠️ Discord: No webhook for @{ACCOUNT}", file=sys.stderr)
        return
    msg = f"**@{ACCOUNT}** `{tweet['time']}`\n{tweet['text']}\nhttps://x.com/{ACCOUNT}/status/{tweet['id']}"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(DISCORD_WEBHOOK, json={"content": msg, "allowed_mentions": {"parse": []}}, timeout=15)
            if r.status_code not in (200, 204):
                print(f"⚠️ Discord HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️ Discord Post Error: {e}", file=sys.stderr)

async def intercept_route(route):
    """Block high-cost nonessential assets while allowing tweet images for OCR."""
    exts = [".mp4", ".woff", ".woff2", ".otf", ".ttf"]
    if any(route.request.url.lower().endswith(ext) for ext in exts):
        await route.abort()
    elif route.request.resource_type in ["media", "font"]:
        await route.abort()
    elif "google-analytics" in route.request.url or "analytics.twitter.com" in route.request.url:
        await route.abort()
    else:
        await route.continue_()

async def human_like_scroll(page):
    """模擬人類隨機滾動行為"""
    steps = random.randint(4, 7)
    for _ in range(steps):
        scroll_amount = random.randint(300, 700)
        await page.mouse.wheel(0, scroll_amount)
        await asyncio.sleep(random.uniform(0.5, 1.5))

async def _scrape_once(playwright_api) -> dict:
    result = _base_result()
    browser = None
    page = None
    owned_context = None

    try:
        browser = await playwright_api.chromium.connect_over_cdp("http://127.0.0.1:9222")
        has_existing_context = bool(browser.contexts)
        context = browser.contexts[0] if has_existing_context else await browser.new_context()
        if not has_existing_context:
            owned_context = context
        page = await context.new_page()

        await page.route("**/*", intercept_route)

        await asyncio.sleep(random.uniform(1.0, 3.0))
        url = f"https://x.com/{ACCOUNT}"
        await page.goto(url, wait_until="load", timeout=60000)

        try:
            await page.wait_for_selector("article[data-testid='tweet']", timeout=30000)
        except Exception:
            result["status"] = "potential_structure_change"
            result["message"] = "Cannot find tweet elements"
            return result

        await human_like_scroll(page)
        await asyncio.sleep(random.uniform(1.0, 2.0))

        articles = await page.query_selector_all("article[data-testid='tweet']")

        conn = get_db_conn(DB_PATH)
        _ensure_fts_triggers(conn)
        new_count = 0
        try:
            for t in articles[:10]:
                try:
                    link_el = await t.query_selector("a[href*='/status/']")
                    if not link_el:
                        continue
                    href = await link_el.get_attribute("href")
                    tid_match = re.search(r"/status/(\d+)", href)
                    if not tid_match:
                        continue
                    tid = tid_match.group(1)

                    txt_el = await t.query_selector("[data-testid='tweetText']")
                    txt = await txt_el.inner_text() if txt_el else ""

                    time_el = await t.query_selector("time")
                    tm = await time_el.get_attribute("datetime") if time_el else datetime.now().isoformat()
                    try:
                        image_urls = await _extract_tweet_image_urls(t)
                        image_paths = await _download_tweet_images(ACCOUNT, tid, image_urls)
                    except Exception as e:
                        print(f"⚠️ Image persistence error for {tid}: {e}", file=sys.stderr)
                        image_paths = []
                    images_json = json.dumps(image_paths, ensure_ascii=False)
                    print(f"   - [ID:{tid}] {tm} | {txt[:30]}...", file=sys.stderr)

                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT OR IGNORE INTO tweets (id, account, created_at, text, images, scraped_at) VALUES (?,?,?,?,?,?)",
                        (tid, ACCOUNT, tm, txt, images_json, datetime.now().isoformat())
                    )

                    if cursor.rowcount > 0:
                        print("     🆕 NEW TWEET!", file=sys.stderr)
                        new_count += 1
                        await post_to_discord({"id": tid, "text": txt, "time": tm})
                except Exception as e:
                    print(f"⚠️ Tweet parse error: {e}", file=sys.stderr)
                    continue

            conn.commit()
        finally:
            conn.close()

        result["status"] = "success"
        result["new_count"] = new_count
        return result
    finally:
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


async def _run_scrape_with_retries(playwright_factory=None, sleep_fn=None) -> dict:
    if playwright_factory is None:
        playwright_factory = async_playwright
    if sleep_fn is None:
        sleep_fn = asyncio.sleep

    last_error = ""
    for attempt in range(3):
        async with playwright_factory() as playwright_api:
            try:
                return await _scrape_once(playwright_api)
            except Exception as exc:
                last_error = str(exc)
                if attempt == 0 and _is_cdp_connection_error(exc) and await _restart_cdp_chrome():
                    continue
                if attempt < 2 and _is_retryable_playwright_error(exc):
                    await sleep_fn(2)
                    continue
                result = _base_result()
                result["message"] = last_error
                return result

    result = _base_result()
    result["message"] = last_error
    return result


async def scrape():
    result = await _run_scrape_with_retries()
    print(json.dumps(result))
    if result["status"] == "success":
        sys.exit(0)
    if result["status"] == "potential_structure_change":
        sys.exit(2)
    sys.exit(1)

if __name__ == "__main__":
    asyncio.run(scrape())
