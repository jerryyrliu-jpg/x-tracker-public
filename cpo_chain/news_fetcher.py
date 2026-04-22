
import feedparser
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Protocol, Optional

logger = logging.getLogger("news_fetcher")

class NewsFetcher(Protocol):
    def fetch(self, company_a: str, company_b: str) -> list[dict]: ...
    def boost_score(self, company_a: str, company_b: str) -> tuple[float, str]: ...

class GoogleNewsRSSFetcher:
    RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    def fetch(self, company_a: str, company_b: str, days: int = 30) -> list[dict]:
        # query example: "NVIDIA" "TSMC" supply chain
        query = f'"{company_a}" "{company_b}" supply chain'.replace(" ", "+")
        url = self.RSS_URL.format(query=query)
        feed = feedparser.parse(url)
        
        if feed.get("bozo"):  # feed 損壞
            logger.warning(f"Google News RSS bozo: {feed.get('bozo_exception')}")
            return []
            
        cutoff = datetime.now() - timedelta(days=days)
        results = []
        for entry in feed.entries:
            try:
                # entry.published_parsed: (tm_year, tm_mon, tm_mday, tm_hour, tm_min, tm_sec, tm_wday, tm_yday, tm_isdst)
                pub = datetime(*entry.published_parsed[:6])
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
        feed = feedparser.parse(url)
        if feed.get("bozo"):
            logger.warning(f"Yahoo RSS bozo: {feed.get('bozo_exception')}")
            return []
            
        cutoff = datetime.now() - timedelta(days=days)
        results = []
        for entry in feed.entries:
            try:
                pub = datetime(*entry.published_parsed[:6])
                if pub >= cutoff:
                    text = (entry.title + " " + entry.get("summary", "")).lower()
                    if company_b.lower() in text:
                        results.append({"title": entry.title, "link": entry.link, "published": pub.isoformat()})
            except Exception as e:
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
        # 1. 嘗試 Google News
        query_query = f'"{company_a}" "{company_b}" supply chain'.replace(" ", "+")
        url = self.google.RSS_URL.format(query=query_query)
        feed = feedparser.parse(url)
        
        # Check if Google News worked
        if not feed.get("bozo"):
            score, status = self.google.boost_score(company_a, company_b)
            if status == "success":
                return score, "google_news"
            elif status == "no_match":
                # If no match in Google, maybe try Yahoo anyway? 
                # The plan says fallback only when bozo=True.
                pass
        
        # 2. Fallback to Yahoo if Google failed or no match and we have mapper
        if self.mapper and conn:
            ticker_a = self.mapper.get_ticker(conn, company_a)
            if ticker_a:
                score, status = self.yahoo.boost_score(ticker_a, company_b)
                if status == "success":
                    return score, "yahoo_rss"
                    
        return 0.0, "no_match"
