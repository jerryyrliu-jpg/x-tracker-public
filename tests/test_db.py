import sqlite3, sys, os, tempfile
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
