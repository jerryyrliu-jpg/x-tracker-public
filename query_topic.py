import sqlite3, json, sys, os, subprocess, argparse, re
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

try:
    from utils import get_db_conn
    from scraper import init_db
except ImportError:
    print("Error: Could not import get_db_conn from utils or init_db from scraper.", file=sys.stderr)
    sys.exit(1)


SCRAPER_BASE = Path(os.getcwd())
load_dotenv(SCRAPER_BASE / ".env")
DB_PATH = SCRAPER_BASE / "tweets.db"


def search_tweets_fts(conn, account: str, topic: str, days: int) -> list:
    """Search tweets using FTS5 virtual table. Falls back to LIKE on exception."""
    since_date = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        query = (
            "SELECT t.id, t.created_at, t.text "
            "FROM tweets_fts JOIN tweets t ON t.rowid = tweets_fts.rowid "
            "WHERE tweets_fts MATCH ? AND t.account = ? AND t.created_at >= ? "
            "ORDER BY t.created_at DESC"
        )
        # Strip FTS5 special characters to avoid syntax errors (e.g. BTC-USD → BTC USD)
        fts_topic = re.sub(r'[^\w\s]', ' ', topic).strip()
        rows = conn.execute(query, (fts_topic, account, since_date)).fetchall()
        return rows
    except sqlite3.OperationalError as e:
        print(f"FTS5 search failed ({e}), falling back to LIKE search.", file=sys.stderr)
        query = (
            "SELECT id, created_at, text FROM tweets "
            "WHERE account = ? AND text LIKE ? AND created_at >= ? "
            "ORDER BY created_at DESC"
        )
        rows = conn.execute(query, (account, f"%{topic}%", since_date)).fetchall()
        return rows


def analyze_topic_weighted(topic, tweets):
    """Analyze tweets with Gemini. Returns raw stdout including ---SENTIMENT_JSON--- block."""
    tweet_list = [{"id": r[0], "date": r[1], "text": r[2]} for r in tweets]
    example_id = tweet_list[0]["id"] if tweet_list else "1234567890"
    prompt = (
        f"分析「{topic}」推文。以最新推文為準，觀察觀點演變。繁體中文總結。\n"
        f"數據：{json.dumps(tweet_list, ensure_ascii=False)}\n\n"
        "請在回答最後加上以下分隔符，並輸出每則推文 id 對應的情感標籤 JSON。\n"
        "情感值只能是 Bullish、Bearish 或 Neutral 其中之一。\n"
        "格式範例（請替換為實際的 tweet id）：\n"
        "---SENTIMENT_JSON---\n"
        f'{{"{example_id}": "Bullish", "<其他id>": "Bearish"}}'
    )
    cmd = ["gemini", "--model", "gemini-2.5-flash-lite", "-p", prompt]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if res.returncode != 0:
        print(f"Error running gemini command: {res.stderr}", file=sys.stderr)
        return ""
    return res.stdout


def get_cache(topic, days=3, conn=None):
    """Retrieve cached result. Creates its own conn if none provided."""
    should_close = conn is None
    if conn is None:
        conn = get_db_conn(DB_PATH)
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    row = conn.execute(
        "SELECT result_json FROM query_cache WHERE topic = ? AND updated_at >= ?",
        (topic, cutoff),
    ).fetchone()
    if should_close:
        conn.close()
    return json.loads(row[0]) if row else None


def save_cache(topic, result_data, conn=None):
    """Save result to cache. Creates its own conn if none provided."""
    should_close = conn is None
    if conn is None:
        conn = get_db_conn(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO query_cache (topic, result_json, updated_at) VALUES (?, ?, ?)",
        (topic, json.dumps(result_data), datetime.now().isoformat()),
    )
    conn.commit()
    if should_close:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("topic")
    parser.add_argument("--account", default="aleabitoreddit")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    conn = get_db_conn(DB_PATH)
    try:
        # Check cache first (before FTS sync for speed on hits)
        if not args.force:
            cached = get_cache(args.topic, conn=conn)
            if cached:
                print(f"[Cache Hit] 讀取 {args.topic} 的快取數據。")
                if args.output:
                    with open(args.output, "w") as f:
                        json.dump(cached, f)
                print(cached.get("summary", ""))
                return

        tweets = search_tweets_fts(conn, args.account, args.topic, args.days)
    finally:
        conn.close()

    if not tweets:
        print(f"No tweets found for '{args.topic}' in last {args.days} days.", file=sys.stderr)
        return

    print(f"[Live Analysis] 正在呼叫 Gemini 分析 {args.topic}...")
    full_output = analyze_topic_weighted(args.topic, tweets)
    if not full_output:
        print("Gemini analysis failed.", file=sys.stderr)
        return

    parts = full_output.split("---SENTIMENT_JSON---")
    summary = parts[0].strip()
    s_map = {}
    if len(parts) > 1:
        m = re.search(r"\{.*\}", parts[1].strip(), re.DOTALL)
        if m:
            try:
                s_map = json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

    result_data = {
        "tweets": [
            {
                "id": r[0],
                "created_at": r[1],
                "text": r[2],
                "sentiment": s_map.get(str(r[0]), "Neutral"),
            }
            for r in tweets
        ],
        "summary": summary,
        "tweet_count": len(tweets),
        "cached": False,
    }

    save_cache(args.topic, result_data)
    if args.output:
        with open(args.output, "w") as f:
            json.dump(result_data, f)
    print(summary)


if __name__ == "__main__":
    main()
