import sqlite3
import time
from datetime import datetime, timezone, timedelta

import pytest

from cpo_chain.db import init_usci_tables
from cpo_chain.news_article_fetcher import NewsArticleFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_usci_tables(conn)
    return conn


class MockEntry:
    """Minimal feedparser entry mimic."""

    def __init__(self, title, link, published_parsed, summary=""):
        self.title = title
        self.link = link
        self.published_parsed = published_parsed
        self.summary = summary

    def get(self, key, default=None):
        return getattr(self, key, default)


class MockFeed:
    def __init__(self, entries, bozo=False):
        self.entries = entries
        self.bozo = bozo

    def get(self, key, default=None):
        return getattr(self, key, default)


def _recent_parsed():
    """time.struct_time for now (UTC)."""
    return time.gmtime()


def _sec_payload(form="8-K", filing_date=None, accession="0001234567-24-000001", cik="0001234567"):
    """Build a minimal EDGAR submissions JSON payload."""
    if filing_date is None:
        filing_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "filings": {
            "recent": {
                "form": [form],
                "filingDate": [filing_date],
                "accessionNumber": [accession],
            }
        }
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fetch_google_news_saves_articles(monkeypatch):
    """Recent entries returned by feedparser are saved to news_articles."""
    conn = make_db()
    fetcher = NewsArticleFetcher()

    entries = [
        MockEntry("NVIDIA supply chain deal", "http://example.com/1", _recent_parsed()),
        MockEntry("NVIDIA supplier contract news", "http://example.com/2", _recent_parsed()),
    ]
    monkeypatch.setattr("feedparser.parse", lambda url: MockFeed(entries))

    count = fetcher.fetch_google_news(conn, "NVIDIA")

    assert count == 2
    rows = conn.execute("SELECT url, source FROM news_articles").fetchall()
    assert len(rows) == 2
    assert all(row[1] == "google_news" for row in rows)


def test_fetch_google_news_bozo_returns_zero(monkeypatch):
    """When feedparser returns a bozo feed, fetch_google_news returns 0."""
    conn = make_db()
    fetcher = NewsArticleFetcher()

    monkeypatch.setattr("feedparser.parse", lambda url: MockFeed([], bozo=True))

    count = fetcher.fetch_google_news(conn, "NVIDIA")

    assert count == 0
    rows = conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()
    assert rows[0] == 0


def test_fetch_sec_8k_uses_submissions_api(monkeypatch):
    """fetch_sec_8k hits the EDGAR API and stores articles with correct URL format."""
    conn = make_db()
    fetcher = NewsArticleFetcher()

    cik_str = "0001045810"
    payload = _sec_payload(cik=cik_str)

    class MockResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr("requests.get", lambda *a, **kw: MockResp())

    count = fetcher.fetch_sec_8k(conn, "NVIDIA", cik_str)

    assert count > 0
    row = conn.execute("SELECT url, source FROM news_articles LIMIT 1").fetchone()
    assert "sec.gov/Archives/edgar" in row[0]
    assert row[1] == "sec_8k"


def test_duplicate_url_ignored(monkeypatch):
    """Inserting the same article twice: first call saves 1, second call saves 0."""
    conn = make_db()
    fetcher = NewsArticleFetcher()

    article = {
        "url": "http://example.com/dup",
        "source": "google_news",
        "title": "Duplicate article",
        "summary": "",
        "published_at": int(datetime.now(timezone.utc).timestamp()),
    }

    first = fetcher._save_articles(conn, [article])
    second = fetcher._save_articles(conn, [article])

    assert first == 1
    assert second == 0
    total = conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0]
    assert total == 1


def test_fetch_sec_8k_cutoff_filters_old(monkeypatch):
    """8-K filings older than the cutoff are not saved."""
    conn = make_db()
    fetcher = NewsArticleFetcher()

    old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    payload = _sec_payload(filing_date=old_date)

    class MockResp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr("requests.get", lambda *a, **kw: MockResp())

    count = fetcher.fetch_sec_8k(conn, "NVIDIA", "0001045810", days=30)

    assert count == 0
    total = conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0]
    assert total == 0
