import sys, os, tempfile, sqlite3
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))

def make_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    from scraper import init_db
    conn = init_db(db_path=f.name)
    return f.name, conn

def test_cache_key_includes_account_and_days():
    """Different account/days combos must not share cache."""
    db_path, conn = make_db()
    try:
        from query_topic import save_cache, get_cache
        data = {"summary": "test", "tweets": [], "tweet_count": 0, "cached": False}
        save_cache("LITE", data, account="aleabitoreddit", days=30, conn=conn)

        # Same topic, different days → cache miss
        result = get_cache("LITE", account="aleabitoreddit", days=7, conn=conn)
        assert result is None, "days:7 should not hit days:30 cache entry"

        # Same topic, different account → cache miss
        result2 = get_cache("LITE", account="otheraccount", days=30, conn=conn)
        assert result2 is None, "different account should not hit cache"

        # Same topic+account+days → hit
        result3 = get_cache("LITE", account="aleabitoreddit", days=30, conn=conn)
        assert result3 is not None, "exact match should hit cache"
    finally:
        conn.close()
        os.unlink(db_path)

def test_cache_key_format():
    """Verify the stored key is account:topic:days."""
    db_path, conn = make_db()
    try:
        from query_topic import save_cache
        data = {"summary": "x", "tweets": [], "tweet_count": 0, "cached": False}
        save_cache("TSLA", data, account="testacct", days=14, conn=conn)
        row = conn.execute("SELECT topic FROM query_cache").fetchone()
        assert row[0] == "testacct:TSLA:14"
    finally:
        conn.close()
        os.unlink(db_path)
