import difflib
import re
import sqlite3
import time
import yaml
import requests
from pathlib import Path

_ENTITY_ID_RE = re.compile(r'^Q\d+$')
_MAX_ENTITY_NAME_LEN = 80
_WIKIDATA_MAX_RETRIES = 3
_WIKIDATA_MIN_INTERVAL_SECONDS = 1.0

class EntityResolver:
    def __init__(self, db_path: Path, keywords_path: Path,
                 wikidata_min_interval: float = _WIKIDATA_MIN_INTERVAL_SECONDS):
        self.db_path = db_path
        with open(keywords_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
            self.seed_aliases = cfg.get('seed_aliases', {})
            self.root_tickers = cfg.get('root_tickers', [])
        self._db_cache = None  # Cache for known aliases; not thread-safe — use one instance per thread
        self._wikidata_cache: dict[str, dict] = {}  # per-instance memo of _fetch_wikidata results
        self._wikidata_min_interval = wikidata_min_interval
        self._last_wikidata_call = 0.0

    def _throttle_wikidata(self) -> None:
        """Enforce a minimum interval between consecutive Wikidata requests."""
        elapsed = time.monotonic() - self._last_wikidata_call
        wait = self._wikidata_min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_wikidata_call = time.monotonic()

    def _wikidata_get(self, url: str, params: dict | None = None, timeout: int = 5) -> requests.Response:
        """GET with throttling plus bounded retry/backoff on HTTP 429."""
        for attempt in range(_WIKIDATA_MAX_RETRIES + 1):
            self._throttle_wikidata()
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code != 429 or attempt == _WIKIDATA_MAX_RETRIES:
                return resp
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2 ** attempt
            except ValueError:
                delay = 2 ** attempt
            time.sleep(delay)

    def _query_wikidata(self, name: str) -> dict:
        """Query Wikidata for company ticker and industry, memoized per instance."""
        if name in self._wikidata_cache:
            return self._wikidata_cache[name]
        result = self._fetch_wikidata(name)
        self._wikidata_cache[name] = result
        return result

    def _fetch_wikidata(self, name: str) -> dict:
        url = "https://www.wikidata.org/w/api.php"
        params = {
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "format": "json"
        }
        try:
            resp = self._wikidata_get(url, params=params, timeout=5)
            data = resp.json()
            if data.get("search"):
                entity_id = data["search"][0]["id"]
                if not _ENTITY_ID_RE.match(entity_id):
                    return {}
                # Get detailed claims (P249 is ticker symbol, P452 is industry)
                detail_url = f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
                detail_resp = self._wikidata_get(detail_url, timeout=5)
                details = detail_resp.json().get("entities", {}).get(entity_id, {})
                claims = details.get("claims", {})

                ticker = None
                if "P249" in claims:
                    raw_ticker = claims["P249"][0]["mainsnak"]["datavalue"]["value"]
                    if (isinstance(raw_ticker, str) and 1 <= len(raw_ticker) <= 10
                            and re.match(r'^[A-Z$][A-Z0-9.\-]{0,9}$', raw_ticker)):
                        ticker = raw_ticker

                return {"ticker": ticker}
        except Exception as e:
            print(f"Wikidata API Error: {type(e).__name__}")
        return {}

    def resolve(self, conn: sqlite3.Connection, raw_name: str) -> tuple[int, str, str]:
        """
        Resolve raw_name to (company_id, standardized_name, status).
        """
        name = raw_name.strip()[:_MAX_ENTITY_NAME_LEN]

        # 1. Check Static Seed Dictionary
        for standard_name, aliases in self.seed_aliases.items():
            if name.upper() == standard_name.upper() or name.upper() in [a.upper() for a in aliases]:
                res = conn.execute("SELECT id, name FROM industry_entities WHERE name = ?", (standard_name,)).fetchone()

                ticker = None
                all_possible = [standard_name] + aliases
                for a in all_possible:
                    if a in self.root_tickers:
                        ticker = a
                        break
                if not ticker:
                    for a in all_possible:
                        if a.isupper() and 1 <= len(a) <= 5:
                            ticker = a
                            break

                if res:
                    company_id = res[0]
                    if ticker:
                        conn.execute("UPDATE industry_entities SET ticker = ? WHERE id = ? AND ticker IS NULL", (ticker, company_id))
                else:
                    cursor = conn.execute("INSERT INTO industry_entities (name, ticker) VALUES (?, ?)", (standard_name, ticker))
                    company_id = cursor.lastrowid

                for alias in ([standard_name] + aliases):
                    conn.execute("INSERT OR IGNORE INTO industry_entity_aliases (alias, company_id, status) VALUES (?, ?, ?)", (alias, company_id, 'active'))
                    if self._db_cache is not None and alias not in self._db_cache:
                        self._db_cache.append(alias)
                conn.commit()
                return company_id, standard_name, 'active'

        # 2. Check Database Aliases (Exact Match)
        res = conn.execute("""
            SELECT c.id, c.name FROM industry_entities c
            JOIN industry_entity_aliases a ON c.id = a.company_id
            WHERE a.alias = ? OR c.name = ?
        """, (name, name)).fetchone()
        if res:
            return res[0], res[1], 'active'

        # 3. Fuzzy Match
        if self._db_cache is None:
            aliases = [row[0] for row in conn.execute("SELECT alias FROM industry_entity_aliases").fetchall()]
            names = [row[0] for row in conn.execute("SELECT name FROM industry_entities").fetchall()]
            self._db_cache = list(set(aliases + names))

        matches = difflib.get_close_matches(name, self._db_cache, n=1, cutoff=0.8)
        if matches:
            matched_name = matches[0]
            res = conn.execute("""
                SELECT c.id, c.name FROM industry_entities c
                LEFT JOIN industry_entity_aliases a ON c.id = a.company_id
                WHERE a.alias = ? OR c.name = ?
            """, (matched_name, matched_name)).fetchone()
            if res:
                return res[0], res[1], 'active'

        # 4. Wikidata Fallback for New Entities
        wiki_info = self._query_wikidata(name)
        ticker = wiki_info.get("ticker")

        cursor = conn.execute("INSERT INTO industry_entities (name, ticker) VALUES (?, ?)", (name, ticker))
        new_id = cursor.lastrowid
        if self._db_cache is not None:
            self._db_cache.append(name)
        conn.execute("INSERT INTO industry_entity_aliases (alias, company_id, status) VALUES (?, ?, ?)", (name, new_id, 'needs_review'))
        conn.commit()
        return new_id, name, 'needs_review'
