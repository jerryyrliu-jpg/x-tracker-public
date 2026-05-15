
import feedparser
import requests
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Protocol, Optional
from urllib.parse import quote_plus

logger = logging.getLogger("news_fetcher")


def _fetch_feed(url: str, timeout: int = 10):
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "x-tracker/1.0"})
        return feedparser.parse(resp.content)
    except Exception as e:
        logger.warning("Feed fetch error: %s", type(e).__name__)
        result = feedparser.parse("")
        result["bozo"] = True
        result["bozo_exception"] = e
        return result

class NewsFetcher(Protocol):
    def fetch(self, company_a: str, company_b: str) -> list[dict]: ...
    def boost_score(self, company_a: str, company_b: str) -> tuple[float, str]: ...

class GoogleNewsRSSFetcher:
    RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    def fetch(self, company_a: str, company_b: str, days: int = 30) -> list[dict]:
        query = quote_plus(f'"{company_a[:80]}" "{company_b[:80]}" supply chain')
        url = self.RSS_URL.format(query=query)
        feed = _fetch_feed(url)

        if feed.get("bozo"):
            logger.warning(f"Google News RSS bozo: {feed.get('bozo_exception')}")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        results = []
        for entry in feed.entries:
            try:
                import calendar
                pub = datetime.fromtimestamp(calendar.timegm(entry.published_parsed), tz=timezone.utc)
                if pub >= cutoff:
                    results.append({"title": entry.title, "link": entry.link, "published": pub.isoformat()})
            except Exception as e:
                logger.debug(f"Error parsing news entry: {e}")
                continue
        return results

    def boost_score(self, company_a: str, company_b: str) -> tuple[float, str]:
        try:
            results = self.fetch(company_a, company_b)
            if results:
                return 0.10, "success"
            return 0.0, "no_match"
        except Exception as e:
            logger.error(f"Google News RSS error: {e}")
            return 0.0, "api_error"

class YahooRSSFetcher:
    """Fallback only — 不穩定，僅在 Google News feed.bozo=True 時使用"""
    RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"

    def fetch(self, ticker_a: str, company_b: str, days: int = 30) -> list[dict]:
        url = self.RSS_URL.format(ticker=ticker_a)
        feed = _fetch_feed(url)
        if feed.get("bozo"):
            logger.warning(f"Yahoo RSS bozo: {feed.get('bozo_exception')}")
            return []
            
        import calendar
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        results = []
        for entry in feed.entries:
            try:
                pub = datetime.fromtimestamp(calendar.timegm(entry.published_parsed), tz=timezone.utc)
                if pub >= cutoff:
                    text = (entry.title + " " + entry.get("summary", "")).lower()
                    if company_b.lower() in text:
                        results.append({"title": entry.title, "link": entry.link, "published": pub.isoformat()})
            except Exception as e:
                logger.debug(f"Error parsing Yahoo RSS entry: {e}")
                continue
        return results

    def boost_score(self, ticker_a: str, company_b: str) -> tuple[float, str]:
        try:
            results = self.fetch(ticker_a, company_b)
            if results:
                return 0.10, "success"
            return 0.0, "no_match"
        except Exception as e:
            logger.error(f"Yahoo RSS error: {e}")
            return 0.0, "api_error"

class CompositeNewsFetcher:
    """先試 Google News，失敗或 bozo 時 fallback Yahoo"""
    def __init__(self, mapper=None):
        self.google = GoogleNewsRSSFetcher()
        self.yahoo = YahooRSSFetcher()
        self.mapper = mapper  # CompanyTickerMapper，供 Yahoo fallback 用

    def boost_score(self, company_a: str, company_b: str, conn: Optional[sqlite3.Connection] = None) -> tuple[float, str]:
        # 1. 嘗試 Google News（單次 fetch，不重複）
        results = self.google.fetch(company_a, company_b)
        google_bozo = not results and _fetch_feed(
            self.google.RSS_URL.format(query=quote_plus(f'"{company_a}" "{company_b}" supply chain'))
        ).get("bozo", False)

        if results:
            return 0.10, "google_news"

        # 2. Fallback Yahoo 只在 Google feed 損壞（bozo）時使用
        if google_bozo and self.mapper and conn:
            ticker_a = self.mapper.get_ticker(conn, company_a)
            if ticker_a:
                score, status = self.yahoo.boost_score(ticker_a, company_b)
                if status == "success":
                    return score, "yahoo_rss"

        return 0.0, "no_match"
