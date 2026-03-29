import sqlite3, json, sys, os, subprocess, argparse
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

SCRAPER_BASE = Path(os.getcwd())
load_dotenv(SCRAPER_BASE / ".env")
DB_PATH = SCRAPER_BASE / "tweets.db"

def search_tweets(account, topic, days):
    conn = sqlite3.connect(DB_PATH)
    since_date = (datetime.now() - timedelta(days=days)).isoformat()
    query = "SELECT id, created_at, text FROM tweets WHERE account = ? AND text LIKE ? AND created_at >= ? ORDER BY created_at DESC"
    rows = conn.execute(query, (account, f"%{topic}%", since_date)).fetchall()
    conn.close()
    return rows

def analyze_topic_weighted(topic, tweets):
    tweet_list = [{"id": r[0], "date": r[1], "text": r[2]} for r in tweets]
    prompt = f"分析「{topic}」推文。以最新推文為準，觀察觀點演變。繁體中文總結。數據：{json.dumps(tweet_list, ensure_ascii=False)}\n---SENTIMENT_JSON---\n"
    cmd = ["gemini", "--model", "gemini-2.5-flash-lite", "-p", prompt]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.stdout

def get_cache(topic, days=3):
    conn = sqlite3.connect(DB_PATH)
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    row = conn.execute("SELECT result_json FROM query_cache WHERE topic = ? AND updated_at >= ?", (topic, cutoff)).fetchone()
    conn.close()
    return json.loads(row[0]) if row else None

def save_cache(topic, result_data):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO query_cache (topic, result_json, updated_at) VALUES (?, ?, ?)", 
                 (topic, json.dumps(result_data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("topic")
    parser.add_argument("--account", default="aleabitoreddit")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    
    # 檢查快取
    if not args.force:
        cached = get_cache(args.topic)
        if cached:
            print(f"📦 [Cache Hit] 讀取 {args.topic} 的快取數據。")
            if args.output:
                with open(args.output, "w") as f: json.dump(cached, f)
            print(cached["summary"])
            return

    tweets = search_tweets(args.account, args.topic, args.days)
    if not tweets: return
    
    print(f"🔍 [Live Analysis] 正在呼叫 Gemini 分析 {args.topic}...")
    full_output = analyze_topic_weighted(args.topic, tweets)
    parts = full_output.split("---SENTIMENT_JSON---")
    summary = parts[0].strip()
    s_map = {}
    try: s_map = json.loads(parts[1].strip())
    except: pass

    result_data = {
        "tweets": [{"id": r[0], "created_at": r[1], "text": r[2], "sentiment": s_map.get(r[0], "Neutral")} for r in tweets],
        "summary": summary,
        "tweet_count": len(tweets),
        "cached": True
    }
    
    save_cache(args.topic, result_data)
    if args.output:
        with open(args.output, "w") as f: json.dump(result_data, f)
    print(summary)

if __name__ == "__main__": main()
