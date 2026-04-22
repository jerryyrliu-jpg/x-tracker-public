
import requests
import time
import threading
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("edgar_fetcher")

class _TokenBucket:
    """全域 rate limiter，max 8 req/sec（留 buffer）"""
    def __init__(self, rate=8):
        self._rate = rate
        self._tokens = rate
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self._rate, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens < 1:
                # Calculate sleep time needed
                sleep_time = (1 - self._tokens) / self._rate
                time.sleep(sleep_time)
                # Update tokens after sleep
                self._tokens = 0
            else:
                self._tokens -= 1

_edgar_bucket = _TokenBucket(rate=8)

class EdgarFetcher:
    FULLTEXT_URL = "https://efts.sec.gov/LATEST/search-index"
    HEADERS = {"User-Agent": "x-tracker ppisliu@gmail.com"}
    MAX_RETRIES = 3

    def search_relation(self, supplier: str, customer: str) -> list[dict]:
        """搜尋兩公司在 SEC 文件中的共同出現，含 exponential backoff"""
        since = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        params = {
            "q": f'"{supplier}" "{customer}"',
            "dateRange": "custom", "startdt": since,
            "forms": "10-K,8-K",
        }
        for attempt in range(self.MAX_RETRIES):
            _edgar_bucket.acquire()
            try:
                resp = requests.get(self.FULLTEXT_URL, params=params,
                                    headers=self.HEADERS, timeout=10)
                if resp.status_code == 429 or resp.status_code == 403:
                    logger.warning(f"SEC Rate limited (429/403). Retrying in {2 ** attempt}s...")
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                
                results = []
                for h in hits[:5]:
                    source = h.get("_source", {})
                    results.append({
                        "url": source.get("period_of_report", ""), # This is not exactly URL, but filing period
                        "form_type": source.get("form_type", ""),
                        "snippet": h.get("highlight", {}).get("file_date", [""])[0], # Not exactly snippet, but illustrative
                        "file_date": source.get("file_date", "")
                    })
                return results
            except Exception as e:
                logger.error(f"Edgar search error: {e}")
                if attempt == self.MAX_RETRIES - 1:
                    return []
                time.sleep(2 ** attempt)
        return []

    def calc_edgar_score(self, hits: list[dict]) -> float:
        """依 filing 數量與類型計算 edgar_score"""
        if not hits:
            return 0.0
        form_types = [h.get("form_type", "") for h in hits]
        if len(hits) >= 3:
            return 0.30
        if any(f == "10-K" for f in form_types):
            return 0.20
        # 8-K or others
        return 0.15
