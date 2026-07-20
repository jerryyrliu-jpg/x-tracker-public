import sys, os, tempfile, pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock
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


def test_get_recent_tweets_all_account_returns_cross_account_rows():
    """account='all' should aggregate recent tweets across enabled accounts."""
    from scraper import init_db
    from query_topic import get_recent_tweets
    db_path = make_fresh_db()
    try:
        conn = init_db(db_path=db_path)
        recent = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("1", "acct_a", recent, "LITE bullish"),
        )
        conn.execute(
            "INSERT INTO tweets (id, account, created_at, text) VALUES (?, ?, ?, ?)",
            ("2", "acct_b", recent, "TSLA bearish"),
        )
        conn.commit()
        rows = get_recent_tweets(conn, days=7, account="all")
        conn.close()
        ids = {r[0] for r in rows}
        assert ids == {"1", "2"}
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
        monkeypatch.setattr(
            query_topic,
            "run_text_prompt",
            lambda prompt, **kwargs: "",
        )

        result = query_topic.summarize_recent(account="testuser", days=7, force=True)
        assert result == "", "Empty Gemini stdout should return empty string"
    finally:
        os.unlink(db_path)


def test_summarize_recent_uses_google_api_backend(monkeypatch):
    """Production summaries should use the formal google_api backend."""
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
        seen = {}

        def fake_run_text_prompt(prompt, **kwargs):
            seen.update(kwargs)
            return "summary ok"

        monkeypatch.setattr(query_topic, "run_text_prompt", fake_run_text_prompt)

        result = query_topic.summarize_recent(account="testuser", days=7, force=True)
        assert result == "summary ok"
        assert seen["backend"] == "google_api"
    finally:
        os.unlink(db_path)


def test_monthly_summary_generate_summary_uses_google_api_backend(monkeypatch):
    import monthly_summary

    seen = {}

    def fake_run_text_prompt(prompt, **kwargs):
        del prompt
        seen.update(kwargs)
        return "monthly ok"

    monkeypatch.setattr(monthly_summary, "run_text_prompt", fake_run_text_prompt)

    result = monthly_summary.generate_summary({"username": "u", "display_name": "d"}, "tweets")

    assert result == "monthly ok"
    assert seen["backend"] == "google_api"


def test_analyze_topic_cache_save_error_does_not_fail(monkeypatch):
    import query_topic

    monkeypatch.setattr(query_topic, "get_db_conn", lambda path: MagicMock(close=lambda: None))
    monkeypatch.setattr(query_topic, "search_tweets_fts", lambda conn, account, topic, days: [("1", datetime.now().isoformat(), "NVDA bullish")])
    monkeypatch.setattr(query_topic, "_run_gemini", lambda prompt: "summary text\n---SENTIMENT_JSON---\n{}")
    monkeypatch.setattr(query_topic, "save_cache", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    result = query_topic.analyze_topic("NVDA", force=True)

    assert result is not None
    assert result["summary"] == "summary text"


def test_analyze_topic_empty_summary_does_not_write_cache(monkeypatch):
    import query_topic

    saved = {"called": False}
    monkeypatch.setattr(query_topic, "get_db_conn", lambda path: MagicMock(close=lambda: None))
    monkeypatch.setattr(query_topic, "search_tweets_fts", lambda conn, account, topic, days: [("1", datetime.now().isoformat(), "NVDA bullish")])
    monkeypatch.setattr(query_topic, "_run_gemini", lambda prompt: "---SENTIMENT_JSON---\n{}")
    monkeypatch.setattr(query_topic, "save_cache", lambda *args, **kwargs: saved.update(called=True))

    result = query_topic.analyze_topic("NVDA", force=True)

    assert result is not None
    assert result["summary"] == ""
    assert saved["called"] is False
