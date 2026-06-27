
import os
import requests
import sqlite3
import time
from difflib import get_close_matches
from pathlib import Path

class CompanyTickerMapper:
    SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    HEADERS = {"User-Agent": os.getenv("EDGAR_USER_AGENT", "x-tracker contact@example.com")}

    def load_or_refresh(self, conn: sqlite3.Connection, max_age_hours=24) -> None:
        """若快取 > 24h 則重新從 SEC 下載並更新 company_ticker_map"""
        row = conn.execute(
            "SELECT MAX(updated_at) FROM company_ticker_map"
        ).fetchone()

        last_update = row[0] if row and row[0] else 0
        if (time.time() - last_update) < max_age_hours * 3600:
            return  # 快取仍有效

        print("Refreshing company ticker map from SEC...")
        resp = requests.get(self.SEC_TICKERS_URL, headers=self.HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()  # {0: {"cik_str": ..., "ticker": ..., "title": ...}, ...}

        rows = []
        for v in data.values():
            # title typically is "NVIDIA CORP", ticker "NVDA", cik_str 1045810
            rows.append((v["title"].lower(), str(v["cik_str"]).zfill(10), v["ticker"]))

        conn.executemany(
            "INSERT OR REPLACE INTO company_ticker_map(company_name, cik, ticker, updated_at) VALUES (?,?,?,?)",
            [(r[0], r[1], r[2], int(time.time())) for r in rows]
        )
        conn.commit()

    def get_ticker(self, conn: sqlite3.Connection, company_name: str) -> str | None:
        """模糊比對公司名稱，回傳 ticker"""
        name_lower = company_name.lower()
        row = conn.execute(
            "SELECT ticker FROM company_ticker_map WHERE company_name = ?", (name_lower,)
        ).fetchone()
        if row:
            return row[0]

        # fuzzy fallback
        # To avoid performance issues, we might want to cache the names in memory or limit the search
        # For now, let's fetch all names and use get_close_matches
        all_names_rows = conn.execute("SELECT company_name FROM company_ticker_map").fetchall()
        all_names = [r[0] for r in all_names_rows]

        matches = get_close_matches(name_lower, all_names, n=1, cutoff=0.85)
        if matches:
            row = conn.execute(
                "SELECT ticker FROM company_ticker_map WHERE company_name = ?", (matches[0],)
            ).fetchone()
            return row[0] if row else None

        return None

    def get_cik(self, conn: sqlite3.Connection, company_name: str) -> str | None:
        """回傳 10-digit zero-padded CIK"""
        name_lower = company_name.lower()
        row = conn.execute(
            "SELECT cik FROM company_ticker_map WHERE company_name = ?", (name_lower,)
        ).fetchone()
        if row:
            return row[0]

        # fuzzy fallback
        all_names_rows = conn.execute("SELECT company_name FROM company_ticker_map").fetchall()
        all_names = [r[0] for r in all_names_rows]
        matches = get_close_matches(name_lower, all_names, n=1, cutoff=0.85)
        if matches:
            row = conn.execute(
                "SELECT cik FROM company_ticker_map WHERE company_name = ?", (matches[0],)
            ).fetchone()
            return row[0] if row else None

        return None
