import sqlite3, sys, os, tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

def make_fresh_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name

def test_init_db_creates_query_cache():
    db_path = make_fresh_db()
    try:
        from scraper import init_db
        conn = init_db(db_path=db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='query_cache'"
        ).fetchone()
        conn.close()
        assert row is not None, "query_cache table should be created by init_db()"
    finally:
        os.unlink(db_path)

def test_init_db_creates_tweets_fts():
    db_path = make_fresh_db()
    try:
        from scraper import init_db
        conn = init_db(db_path=db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tweets_fts'"
        ).fetchone()
        conn.close()
        assert row is not None
    finally:
        os.unlink(db_path)

def test_init_db_default_path_unchanged():
    import scraper
    assert scraper.DB_PATH is not None


def test_fts_search_finds_tweet():
    db_path = make_fresh_db()
    try:
        from scraper import init_db, sync_fts
        from query_topic import search_tweets_fts
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "testuser", recent, "AAPL is looking bullish today"),
        )
        conn.commit()
        sync_fts(conn)
        rows = search_tweets_fts(conn, "testuser", "AAPL", days=7)
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "1"
    finally:
        os.unlink(db_path)


def test_fts_search_excludes_other_accounts():
    db_path = make_fresh_db()
    try:
        from scraper import init_db, sync_fts
        from query_topic import search_tweets_fts
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "targetuser", recent, "TSLA breakout pattern"),
        )
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("2", "otheraccount", recent, "TSLA looks weak"),
        )
        conn.commit()
        sync_fts(conn)
        rows = search_tweets_fts(conn, "targetuser", "TSLA", days=7)
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "1"
    finally:
        os.unlink(db_path)


def test_fts_porter_tokenizer_stems_words():
    """FTS5 with porter tokenizer: 'buying' should match 'buy'."""
    db_path = make_fresh_db()
    try:
        from scraper import init_db, sync_fts
        from query_topic import search_tweets_fts
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "testuser", recent, "I am buying LITE stock today"),
        )
        conn.commit()
        sync_fts(conn)
        rows = search_tweets_fts(conn, "testuser", "buy", days=7)
        conn.close()
        assert len(rows) == 1, "porter tokenizer: 'buy' should match 'buying'"
    finally:
        os.unlink(db_path)
