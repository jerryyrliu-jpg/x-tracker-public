import feedparser
import requests
import sqlite3
import time
import calendar
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
from .company_ticker_mapper import CompanyTickerMapper

logger = logging.getLogger("news_article_fetcher")


class NewsArticleFetcher:
    GOOGLE_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    def fetch_google_news(self, conn: sqlite3.Connection, company: str, days: int = 7) -> int:
        """Fetch supply chain news for company from Google News RSS. Returns count of new articles saved."""
        query = quote_plus(f'"{company}" supply chain OR supplier OR contract')
        feed = feedparser.parse(self.GOOGLE_RSS.format(query=query))
        if feed.get("bozo"):
            logger.warning("Google News RSS bozo for %s: %s", company, feed.get("bozo_exception"))
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        articles = []
        for entry in feed.entries:
            try:
                pub = datetime.fromtimestamp(calendar.timegm(entry.published_parsed), tz=timezone.utc)
                if pub < cutoff:
                    continue
                articles.append({
                    "url": entry.link,
                    "source": "google_news",
                    "title": entry.title[:200],
                    "summary": entry.get("summary", "")[:500],
                    "published_at": int(pub.timestamp()),
                })
            except Exception:
                continue
        return self._save_articles(conn, articles)

    def fetch_sec_8k(self, conn: sqlite3.Connection, company: str, cik: str, days: int = 30) -> int:
        """Fetch 8-K filings from EDGAR submissions API. Returns count of new articles saved."""
        url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "x-tracker ppisliu@gmail.com"},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"SEC submissions fetch error for {company}: {e}")
            return 0

        cik_int = int(cik)  # numeric CIK for Archive paths (no leading zeros)
        data = resp.json()
        filings = data.get("filings", {}).get("recent", {})
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        articles = []
        filing_dates = filings.get("filingDate", [])
        accession_numbers = filings.get("accessionNumber", [])
        for i, form in enumerate(filings.get("form", [])):
            if form != "8-K":
                continue
            if i >= len(filing_dates) or i >= len(accession_numbers):
                continue
            filing_date = filing_dates[i]
            if filing_date < cutoff:
                continue
            adsh = accession_numbers[i]
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                f"{adsh.replace('-', '')}/{adsh}-index.htm"
            )
            articles.append({
                "url": filing_url,
                "source": "sec_8k",
                "title": f"{company} 8-K: {filing_date}",
                "summary": "",
                "published_at": int(
                    datetime.strptime(filing_date, "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                ),
            })
        return self._save_articles(conn, articles)

    def _save_articles(self, conn: sqlite3.Connection, articles: list[dict]) -> int:
        saved = 0
        for a in articles:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO news_articles
                    (url, source, title, summary, published_at)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (a["url"], a["source"], a["title"], a["summary"], a.get("published_at")),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    saved += 1
            except Exception as e:
                logger.error(f"Error saving article: {e}")
        conn.commit()
        return saved

    def run(self, conn: sqlite3.Connection, root_companies: list[str]) -> dict:
        """
        Fetch news for all root companies.
        Returns {"google_news": N, "sec_8k": K}
        """
        mapper = CompanyTickerMapper()
        mapper.load_or_refresh(conn)  # ensures company_ticker_map is populated
        results = {"google_news": 0, "sec_8k": 0}
        for company in root_companies:
            results["google_news"] += self.fetch_google_news(conn, company)
            cik = mapper.get_cik(conn, company)
            if cik:
                results["sec_8k"] += self.fetch_sec_8k(conn, company, cik)
            time.sleep(0.5)
        return results
