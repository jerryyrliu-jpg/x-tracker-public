#!/usr/bin/env python3
import sqlite3
import sys
import os
import subprocess
import argparse
import httpx
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
import yaml

SCRAPER_BASE = Path(os.getcwd())
load_dotenv(SCRAPER_BASE / ".env")
DB_PATH = SCRAPER_BASE / "tweets.db"


def load_account_config(account_name: str) -> dict:
    with open(SCRAPER_BASE / "accounts.yaml") as f:
        data = yaml.safe_load(f)
    accounts = data.get("accounts", {})
    if account_name not in accounts:
        available = list(accounts.keys())
        print(f"Error: account '{account_name}' not found. Available: {available}")
        sys.exit(1)
    cfg = accounts[account_name]
    cfg["username"] = account_name
    cfg["discord_webhook"] = os.environ.get(cfg.get("discord_webhook_env", ""), "")
    return cfg


def get_tweets(account: str, days: int):
    conn = sqlite3.connect(DB_PATH)
    since_date = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT created_at, text FROM tweets WHERE account = ? AND created_at >= ? ORDER BY created_at ASC",
        (account, since_date)
    ).fetchall()
    conn.close()
    return rows


def generate_summary(account_cfg: dict, tweets_text: str):
    display = account_cfg.get("display_name", account_cfg["username"])
    username = account_cfg["username"]
    prompt = f"""
你是一位專業的投資分析師。請分析以下 @{username} ({display}) 的推文，並生成月度投資摘要。

推文內容：
{tweets_text}

請產出以下格式：
1. 📈 看多標的表格 (標的 | 理由摘要 | 提及次數 | 期權策略)
2. 📉 看空標的表格 (標的 | 理由摘要 | 提及次數 | 期權策略)
3. 🎯 重點關注 (2-3 個主軸)
4. ⚠️ 風險提示

注意：
- 僅使用繁體中文。
- 若推文中無特定方向，請標註為中性或觀察。
- Discord 訊息上限為 2000 字元。
"""
    try:
        # 使用 subprocess 呼叫 Gemini CLI
        result = subprocess.run(
            ["gemini", "--model", "gemini-3.1-pro-preview"], # 預設使用 2.0-flash
            input=prompt,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        if result.returncode != 0:
            print(f"Gemini error: {result.stderr}")
            return None
        return result.stdout
    except Exception as e:
        print(f"Error calling gemini: {e}")
        return None

async def send_discord(webhook: str, content: str):
    if not webhook:
        print("Discord Webhook not set, skip.")
        return
    async with httpx.AsyncClient() as client:
        chunks = []
        while len(content) > 1900:
            idx = content.rfind("\n", 0, 1900)
            if idx == -1:
                idx = 1900
            chunks.append(content[:idx])
            content = content[idx:].strip()
        chunks.append(content)
        for chunk in chunks:
            if chunk:
                await client.post(webhook, json={"content": chunk})
                await asyncio.sleep(1)


async def main():
    parser = argparse.ArgumentParser(description="Generate monthly tweet summary → Discord")
    parser.add_argument("--account", help="Account username (from accounts.yaml)")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(SCRAPER_BASE / "accounts.yaml") as f:
        all_accounts = list(yaml.safe_load(f).get("accounts", {}).keys())
    account_name = args.account or all_accounts[0]
    account_cfg = load_account_config(account_name)

    rows = get_tweets(account_name, args.days)
    if not rows:
        print(f"No tweets found for @{account_name} in last {args.days} days.")
        return

    tweets_text = "\n---\n".join(f"[{r[0]}] {r[1]}" for r in rows)
    print(f"Analyzing {len(rows)} tweets for @{account_name}...")

    summary = generate_summary(account_cfg, tweets_text)
    if not summary:
        print("Failed to generate summary.")
        return

    if args.dry_run:
        print("=== DRY RUN ===")
        print(summary)
    else:
        await send_discord(account_cfg["discord_webhook"], summary)
        print("Summary sent to Discord.")

if __name__ == "__main__":
    asyncio.run(main())
