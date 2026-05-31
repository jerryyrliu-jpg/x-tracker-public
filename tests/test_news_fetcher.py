
import pytest
import time
from datetime import datetime, timedelta
from cpo_chain.news_fetcher import GoogleNewsRSSFetcher, YahooRSSFetcher, CompositeNewsFetcher

class MockEntry:
    def __init__(self, title, link, published_parsed):
        self.title = title
        self.link = link
        self.published_parsed = published_parsed
        self.summary = ""
    def get(self, key, default=None):
        return getattr(self, key, default)

class MockFeed:
    def __init__(self, entries, bozo=False):
        self.entries = entries
        self.bozo = bozo
    def get(self, key, default=None):
        return getattr(self, key, default)
    def __setitem__(self, key, value):
        setattr(self, key, value)
    def __getitem__(self, key):
        return getattr(self, key)

def _mock_requests_get(url, **kwargs):
    """Return a mock response whose content is the URL bytes so feedparser mock can distinguish feeds."""
    resp = type("Resp", (), {"content": url.encode()})()
    return resp


def test_google_news_found(monkeypatch):
    fetcher = GoogleNewsRSSFetcher()
    now = time.localtime()
    mock_entries = [MockEntry("NVIDIA and TSMC supply chain news", "http://example.com/1", now)]

    monkeypatch.setattr("cpo_chain.news_fetcher.requests.get", _mock_requests_get)
    monkeypatch.setattr("feedparser.parse", lambda content: MockFeed(mock_entries))

    score, status = fetcher.boost_score("NVIDIA", "TSMC")
    assert score == 0.10
    assert status == "success"

def test_google_news_bozo_fallback(monkeypatch):
    class MockMapper:
        def get_ticker(self, conn, name): return "NVDA"

    mapper = MockMapper()
    composite = CompositeNewsFetcher(mapper=mapper)

    now = time.localtime()
    yahoo_entries = [MockEntry("NVDA and TSMC collaboration", "http://example.com/2", now)]
    yahoo_entries[0].summary = "TSMC is a key partner"

    def mock_parse(content):
        url_str = content.decode() if isinstance(content, bytes) else str(content)
        if "news.google.com" in url_str:
            return MockFeed([], bozo=True)
        return MockFeed(yahoo_entries)

    monkeypatch.setattr("cpo_chain.news_fetcher.requests.get", _mock_requests_get)
    monkeypatch.setattr("feedparser.parse", mock_parse)

    score, source = composite.boost_score("NVIDIA", "TSMC", conn="mock_conn")
    assert score == 0.10
    assert source == "yahoo_rss"

def test_all_fail(monkeypatch):
    composite = CompositeNewsFetcher()

    monkeypatch.setattr("cpo_chain.news_fetcher.requests.get", _mock_requests_get)
    monkeypatch.setattr("feedparser.parse", lambda content: MockFeed([], bozo=True))

    score, status = composite.boost_score("X", "Y")
    assert score == 0.0
    assert status == "no_match"
