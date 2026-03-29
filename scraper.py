#!/usr/bin/env python3
"""
x-tracker scraper — fetch tweets from a tracked X account and post to Discord.

Usage:
  python3 scraper.py --account aleabitoreddit
  python3 scraper.py --account aleabitoreddit --dry-run
  python3 scraper.py  # uses first account in accounts.yaml
"""
import asyncio
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

import httpx
import yaml
from dotenv import load_dotenv
from twscrape import API

SCRAPER_BASE = Path(os.environ.get("SCRAPER_DIR", "~/scraper")).expanduser()
load_dotenv(SCRAPER_BASE / "config.env")

DB_PATH = SCRAPER_BASE / "tweets.db"
ACCOUNTS_DB = SCRAPER_BASE / "accounts.db"

X_USERNAME = os.environ.get("X_ACCOUNT_USERNAME", "")
X_PASSWORD = os.environ.get("X_ACCOUNT_PASSWORD", "")
X_EMAIL = os.environ.get("X_ACCOUNT_EMAIL", "")
X_EMAIL_PASSWORD = os.environ.get("X_ACCOUNT_EMAIL_PASSWORD", "")


def load_account_config(account_name: str) -> dict:
    config_path = SCRAPER_BASE / "accounts.yaml"
    with open(config_path) as f:
        data = yaml.safe_load(f)
    accounts = data.get("accounts", {})
    if account_name not in accounts:
        print(f"Error: account '{account_name}' not found in accounts.yaml")
        print(f"Available: {list(accounts.keys())}")
        sys.exit(1)
    cfg = accounts[account_name]
    cfg["username"] = account_name
    cfg["discord_webhook"] = os.environ.get(cfg.get("discord_webhook_env", ""), "")
    return cfg


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tweets (
            id TEXT PRIMARY KEY,
            account TEXT NOT NULL,
            created_at TEXT,
            text TEXT,
            images TEXT,
            scraped_at TEXT
        )
    """)
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS tweets_fts "
            "USING fts5(text, content='tweets', content_rowid='rowid')"
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def sync_fts(conn):
    conn.execute("""
        INSERT INTO tweets_fts(rowid, text)
        SELECT rowid, text FROM tweets
        WHERE rowid NOT IN (SELECT rowid FROM tweets_fts)
    """)
    conn.commit()


def load_since_id(account: str) -> str | None:
    state_file = SCRAPER_BASE / f"{account}_state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text()).get("last_id")
        except Exception:
            pass
    return None


def save_since_id(account: str, tweet_id):
    state_file = SCRAPER_BASE / f"{account}_state.json"
    state_file.write_text(json.dumps({
        "last_id": str(tweet_id),
        "updated": datetime.now().isoformat()
    }))


async def download_image(url: str, path: Path):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    return str(path)


async def send_discord(webhook: str, text: str, image_paths: list[str], tweet_url: str):
    if not webhook:
        print("  [skip] Discord webhook not configured")
        return
    content = f"{text}\n{tweet_url}"
    async with httpx.AsyncClient() as client:
        if image_paths:
            opened = []
            try:
                files = {}
                for i, p in enumerate(image_paths[:4]):
                    f = open(p, "rb")
                    opened.append(f)
                    files[f"file{i}"] = (Path(p).name, f, "image/jpeg")
                files["payload_json"] = (None, json.dumps({"content": content}), "application/json")
                await client.post(webhook, files=files)
            finally:
                for f in opened:
                    f.close()
        else:
            await client.post(webhook, json={"content": content})


async def run(account_cfg: dict, dry_run: bool = False):
    account = account_cfg["username"]
    webhook = account_cfg["discord_webhook"]
    images_dir = SCRAPER_BASE / "images" / account

    conn = init_db()
    api = API(str(ACCOUNTS_DB))

    if X_USERNAME and X_PASSWORD:
        await api.pool.add_account(X_USERNAME, X_PASSWORD, X_EMAIL, X_EMAIL_PASSWORD)
        await api.pool.login_all()

    since_id = load_since_id(account)
    user = await api.user_by_login(account)

    new_tweets = []
    async for tweet in api.user_tweets(user.id, limit=100):
        if since_id and str(tweet.id) == str(since_id):
            break
        new_tweets.append(tweet)

    if not new_tweets:
        print(f"[{account}] No new tweets.")
        return

    print(f"[{account}] Found {len(new_tweets)} new tweets")

    for tweet in reversed(new_tweets):
        image_paths = []
        if tweet.media and tweet.media.photos:
            for i, photo in enumerate(tweet.media.photos):
                path = images_dir / f"{tweet.id}_{i}.jpg"
                try:
                    local = await download_image(photo.url, path)
                    image_paths.append(local)
                except Exception as e:
                    print(f"  Image download failed: {e}")

        conn.execute(
            "INSERT OR IGNORE INTO tweets (id, account, created_at, text, images, scraped_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(tweet.id), account, tweet.date.isoformat(),
             tweet.rawContent, json.dumps(image_paths), datetime.now().isoformat())
        )
        conn.commit()

        tweet_url = f"https://x.com/{account}/status/{tweet.id}"
        date_str = tweet.date.strftime("%Y-%m-%d %H:%M ET")
        display_name = account_cfg.get("display_name", account)
        text = f"**@{account}** ({display_name}) `{date_str}`\n{tweet.rawContent}"

        if dry_run:
            print(f"  [dry-run] {date_str}: {tweet.rawContent[:80]}...")
        else:
            await send_discord(webhook, text, image_paths, tweet_url)
            await asyncio.sleep(1)

    sync_fts(conn)
    save_since_id(account, new_tweets[0].id)
    print(f"[{account}] Done. since_id={new_tweets[0].id}")


def main():
    parser = argparse.ArgumentParser(description="Scrape X account tweets → Discord + SQLite")
    parser.add_argument("--account", help="Account username (must exist in accounts.yaml)")
    parser.add_argument("--all", action="store_true", help="Run all accounts in accounts.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print tweets without posting to Discord")
    args = parser.parse_args()

    config_path = SCRAPER_BASE / "accounts.yaml"
    with open(config_path) as f:
        all_accounts = list(yaml.safe_load(f).get("accounts", {}).keys())

    if args.all:
        targets = all_accounts
    elif args.account:
        targets = [args.account]
    else:
        targets = [all_accounts[0]]

    for account_name in targets:
        cfg = load_account_config(account_name)
        asyncio.run(run(cfg, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
