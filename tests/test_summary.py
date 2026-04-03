import sys, os, tempfile
from pathlib import Path
from datetime import datetime, timedelta
sys.path.insert(0, str(Path(__file__).parent.parent))

def make_fresh_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def test_get_recent_tweets_returns_all_in_window():
    """Returns only tweets within the time window."""
    from scraper import init_db
    from query_topic import get_recent_tweets
    db_path = make_fresh_db()
    try:
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        old = (datetime.now() - timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "testuser", recent, "LITE is bullish"),
        )
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("2", "testuser", recent, "TSLA looks weak"),
        )
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("3", "testuser", old, "old tweet"),
        )
        conn.commit()
        rows = get_recent_tweets(conn, days=7, account="testuser")
        conn.close()
        assert len(rows) == 2
        ids = {r[0] for r in rows}
        assert ids == {"1", "2"}
    finally:
        os.unlink(db_path)


def test_get_recent_tweets_excludes_other_accounts():
    """Account filter is applied."""
    from scraper import init_db
    from query_topic import get_recent_tweets
    db_path = make_fresh_db()
    try:
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "targetuser", recent, "LITE bullish"),
        )
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("2", "otheraccount", recent, "TSLA bearish"),
        )
        conn.commit()
        rows = get_recent_tweets(conn, days=7, account="targetuser")
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "1"
    finally:
        os.unlink(db_path)


def test_build_all_tickers_prompt_includes_today():
    """Prompt contains today's date."""
    from query_topic import build_all_tickers_prompt
    tweets = [("1", datetime.now().isoformat(), "LITE is bullish")]
    prompt = build_all_tickers_prompt(tweets, days=3)
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in prompt


def test_build_all_tickers_prompt_groups_by_day():
    """Prompt contains day-level grouping keys (YYYY-MM-DD format)."""
    from query_topic import build_all_tickers_prompt
    today_str = datetime.now().strftime("%Y-%m-%d")
    tweets = [("1", f"{today_str}T10:00:00", "LITE is bullish")]
    prompt = build_all_tickers_prompt(tweets, days=3)
    assert today_str in prompt, "Prompt should contain day key like 2026-04-03"


def test_summarize_recent_empty_gemini_response(monkeypatch):
    """summarize_recent returns '' when Gemini exits 0 but stdout is empty."""
    from scraper import init_db
    import query_topic
    db_path = make_fresh_db()
    try:
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "testuser", recent, "LITE is bullish"),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(query_topic, "DB_PATH", db_path)

        import subprocess
        def mock_run(cmd, **kwargs):
            class FakeResult:
                returncode = 0
                stdout = ""
                stderr = ""
            return FakeResult()
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = query_topic.summarize_recent(account="testuser", days=7, force=True)
        assert result == "", "Empty Gemini stdout should return empty string"
    finally:
        os.unlink(db_path)
