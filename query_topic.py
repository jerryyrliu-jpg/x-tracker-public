import sqlite3, json, sys, os, subprocess, argparse, re
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

try:
    from utils import get_db_conn
    from scraper import init_db
except ImportError:
    print("Error: Could not import get_db_conn from utils or init_db from scraper.", file=sys.stderr)
    sys.exit(1)


SCRAPER_BASE = Path(__file__).resolve().parent
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


def build_prompt(topic: str, tweets: list) -> str:
    """Build the Gemini analysis prompt with date context and weekly grouping."""
    today = datetime.now().strftime("%Y-%m-%d")

    # Group tweets by ISO week
    weeks: dict = defaultdict(list)
    ungrouped = []
    for r in tweets:
        tid, created_at, text = r[0], r[1], r[2]
        entry = {"id": tid, "date": created_at[:10], "text": text}
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            week_label = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
            weeks[week_label].append(entry)
        except Exception:
            ungrouped.append(entry)

    grouped_json: dict = {}
    for week in sorted(weeks):
        grouped_json[week] = weeks[week]
    if ungrouped:
        grouped_json["other"] = ungrouped

    example_id = tweets[0][0] if tweets else "1234567890"
    return (
        f"今日：{today}\n\n"
        f"分析「{topic}」的推文，以時間順序觀察觀點演變與趨勢轉折。繁體中文總結。\n"
        f"數據（依週分組）：{json.dumps(grouped_json, ensure_ascii=False)}\n\n"
        "請：\n"
        "1. 總結各週的主要觀點。\n"
        "2. 明確指出是否有趨勢方向轉變（例如從看多轉為謹慎）。\n"
        "3. 以最新推文為準給出當前立場。\n\n"
        "請在回答最後加上以下分隔符，並輸出每則推文 id 對應的情感標籤 JSON。\n"
        "情感值只能是 Bullish、Bearish 或 Neutral 其中之一。\n"
        "格式範例（請替換為實際的 tweet id）：\n"
        "---SENTIMENT_JSON---\n"
        f'{{"{example_id}": "Bullish", "<其他id>": "Bearish"}}'
    )


def get_recent_tweets(conn, days: int, account: str = "aleabitoreddit") -> list:
    """Fetch all tweets within the last `days` days for `account`. No topic filter."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    return conn.execute(
        "SELECT id, created_at, text FROM tweets "
        "WHERE account = ? AND created_at >= ? ORDER BY created_at DESC",
        (account, since),
    ).fetchall()


def build_all_tickers_prompt(tweets: list, days: int) -> str:
    """Build Gemini prompt for all-tickers summary, grouped by day."""
    today = datetime.now().strftime("%Y-%m-%d")

    day_groups: dict = defaultdict(list)
    for r in tweets:
        tid, created_at, text = r[0], r[1], r[2]
        day_key = created_at[:10]  # YYYY-MM-DD
        day_groups[day_key].append({"id": tid, "text": text})

    grouped_json: dict = {}
    for day in sorted(day_groups):
        grouped_json[day] = day_groups[day]

    return (
        f"今日：{today}（最近 {days} 天推文分析）\n\n"
        f"數據（依日分組）：{json.dumps(grouped_json, ensure_ascii=False)}\n\n"
        "請：\n"
        "1. 找出所有被提及的標的（股票、加密貨幣等）\n"
        "2. 每個標的輸出：\n"
        "   - 情緒：Bullish / Bearish / Neutral\n"
        "   - 一句主要觀點摘要\n"
        "3. 若同一標的在不同天有不同立場，請註明轉變\n\n"
        "繁體中文總結。"
    )


def summarize_recent(account: str = "aleabitoreddit", days: int = 7, force: bool = False) -> str:
    """Summarize all tickers mentioned in recent tweets. Returns summary string or '' on failure."""
    cache_topic = "__summary__"

    if not force:
        cached = get_cache(cache_topic, account=account, days=days)
        if cached:
            return cached.get("summary", "")

    conn = get_db_conn(DB_PATH)
    try:
        tweets = get_recent_tweets(conn, days=days, account=account)
    finally:
        conn.close()

    if not tweets:
        return ""

    prompt = build_all_tickers_prompt(tweets, days)
    cmd = ["gemini", "--model", "gemini-2.5-flash-lite", "-p", prompt]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=300)
    except subprocess.TimeoutExpired:
        print("Gemini summary timed out.", file=sys.stderr)
        return ""
    if res.returncode != 0 or not res.stdout.strip():
        print(f"Gemini summary failed: {res.stderr}", file=sys.stderr)
        return ""

    summary = res.stdout.strip()
    save_cache(cache_topic, {"summary": summary, "cached": False}, account=account, days=days)
    return summary


def analyze_topic_weighted(topic, tweets):
    """Analyze tweets with Gemini. Returns raw stdout including ---SENTIMENT_JSON--- block."""
    prompt = build_prompt(topic, tweets)
    cmd = ["gemini", "--model", "gemini-2.5-flash-lite", "-p", prompt]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=300)
    except subprocess.TimeoutExpired:
        print("Gemini analysis timed out.", file=sys.stderr)
        return ""
    if res.returncode != 0:
        print(f"Error running gemini command: {res.stderr}", file=sys.stderr)
        return ""
    return res.stdout


def get_cache(topic, account="aleabitoreddit", days=30, conn=None):
    """Retrieve cached result. Cache key is account:topic:days.
    Also used as freshness window: entries older than `days` days are ignored."""
    should_close = conn is None
    if conn is None:
        conn = get_db_conn(DB_PATH)
    cache_key = f"{account}:{topic}:{days}"
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    row = conn.execute(
        "SELECT result_json FROM query_cache WHERE topic = ? AND updated_at >= ?",
        (cache_key, cutoff),
    ).fetchone()
    if should_close:
        conn.close()
    return json.loads(row[0]) if row else None


def save_cache(topic, result_data, account="aleabitoreddit", days=30, conn=None):
    """Save result to cache. Cache key is account:topic:days."""
    should_close = conn is None
    if conn is None:
        conn = get_db_conn(DB_PATH)
    cache_key = f"{account}:{topic}:{days}"
    conn.execute(
        "INSERT OR REPLACE INTO query_cache (topic, result_json, updated_at) VALUES (?, ?, ?)",
        (cache_key, json.dumps(result_data), datetime.now().isoformat()),
    )
    conn.commit()
    if should_close:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", nargs="?", default=None)
    parser.add_argument("--summary", action="store_true",
                        help="Summarize all tickers in recent tweets (no topic required)")
    parser.add_argument("--account", default="aleabitoreddit")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.summary:
        days = max(1, min(args.days, 90))
        print(f"[Live Analysis] 正在分析最近 {days} 天所有標的...", file=sys.stderr)
        summary = summarize_recent(account=args.account, days=days, force=args.force)
        if not summary:
            print(f"最近 {days} 天無推文資料或分析失敗。", file=sys.stderr)
            return
        result_data = {"summary": summary, "cached": False}
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(result_data, f)
        print(summary)
        return

    if not args.topic:
        print("Error: topic is required unless --summary is used.", file=sys.stderr)
        sys.exit(1)

    conn = get_db_conn(DB_PATH)
    try:
        # Check cache first (before FTS sync for speed on hits)
        if not args.force:
            cached = get_cache(args.topic, account=args.account, days=args.days, conn=conn)
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

    save_cache(args.topic, result_data, account=args.account, days=args.days)
    if args.output:
        with open(args.output, "w") as f:
            json.dump(result_data, f)
    print(summary)


if __name__ == "__main__":
    main()
