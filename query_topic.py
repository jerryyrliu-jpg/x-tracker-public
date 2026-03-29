#!/usr/bin/env python3
"""
x-tracker topic query — search tweets by topic and analyze with Gemini.

Usage:
  python3 query_topic.py "光通訊"
  python3 query_topic.py "光通訊" --account aleabitoreddit --days 90
  python3 query_topic.py "AI chips" --output /tmp/result.json --ground
"""
import sqlite3
import json
import sys
import os
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, timedelta

import yaml
from dotenv import load_dotenv

SCRAPER_BASE = Path(os.environ.get("SCRAPER_DIR", "~/scraper")).expanduser()
load_dotenv(SCRAPER_BASE / "config.env")

DB_PATH = SCRAPER_BASE / "tweets.db"


def load_account_config(account_name: str) -> dict:
    with open(SCRAPER_BASE / "accounts.yaml") as f:
        data = yaml.safe_load(f)
    accounts = data.get("accounts", {})
    if account_name not in accounts:
        print(f"Error: account '{account_name}' not in accounts.yaml. Available: {list(accounts.keys())}")
        sys.exit(1)
    cfg = accounts[account_name]
    cfg["username"] = account_name
    return cfg


def search_tweets(account: str, topic: str, days: int) -> list:
    conn = sqlite3.connect(DB_PATH)
    since_date = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        rows = conn.execute(
            "SELECT t.id, t.created_at, t.text FROM tweets_fts f "
            "JOIN tweets t ON t.rowid = f.rowid "
            "WHERE f.text MATCH ? AND t.account = ? AND t.created_at >= ? "
            "ORDER BY t.created_at ASC",
            (topic, account, since_date)
        ).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            "SELECT id, created_at, text FROM tweets "
            "WHERE account = ? AND created_at >= ? AND text LIKE ? "
            "ORDER BY created_at ASC",
            (account, since_date, f"%{topic}%")
        ).fetchall()
    conn.close()
    return rows


def analyze_topic(account_cfg: dict, topic: str, tweets: list, ground: bool = False) -> str:
    display = account_cfg.get("display_name", account_cfg["username"])
    username = account_cfg["username"]
    tweets_text = "\n---\n".join(f"[{r[1]}] {r[2]}" for r in tweets)
    prompt = f"""
你是一個投資分析助理。以下是 @{username} ({display}) 提到「{topic}」的推文（共 {len(tweets)} 篇）：

---
{tweets_text}
---

請根據以上內容，產生：

## 🎯 主題：{topic}

### 相關標的
| 標的代號 | 公司名 | 方向 | 理由摘要 | 期權策略 | 最後提及時間 |
|----------|--------|------|----------|----------|------------|

### @{username} 觀點演變
- [時間軸摘要：觀點是否有轉變？何時轉變？]

### 關鍵推文（最具參考價值的 3 則）
1. [時間] [推文摘要]
2. ...

注意：標的代號請用美股 Ticker，若不確定填公司名。使用繁體中文。
"""
    cmd = ["gemini", "--model", "gemini-2.0-flash"]
    if ground:
        cmd.append("--search")
    try:
        result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            return f"Gemini error: {result.stderr}"
        return result.stdout
    except Exception as e:
        return f"Error calling gemini: {e}"


def main():
    parser = argparse.ArgumentParser(description="Search tweets by topic and analyze with Gemini")
    parser.add_argument("topic", help="Search topic or ticker")
    parser.add_argument("--account", help="Account username (from accounts.yaml)")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", help="Output JSON file path")
    parser.add_argument("--ground", action="store_true", help="Enable Gemini search grounding")
    args = parser.parse_args()

    with open(SCRAPER_BASE / "accounts.yaml") as f:
        all_accounts = list(yaml.safe_load(f).get("accounts", {}).keys())
    account_name = args.account or all_accounts[0]
    account_cfg = load_account_config(account_name)

    tweets = search_tweets(account_name, args.topic, args.days)
    if not tweets:
        print(f"No tweets found for '{args.topic}' from @{account_name} in last {args.days} days.")
        return

    print(f"Found {len(tweets)} tweets from @{account_name}. Analyzing...")
    summary = analyze_topic(account_cfg, args.topic, tweets, args.ground)
    print(summary)

    if args.output:
        result_data = {
            "account": account_name,
            "topic": args.topic,
            "tweet_count": len(tweets),
            "tweets": [{"id": r[0], "created_at": r[1], "text": r[2]} for r in tweets],
            "summary": summary,
            "generated_at": datetime.now().isoformat(),
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
