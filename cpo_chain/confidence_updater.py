
import sqlite3
import fcntl
import time
import logging
from pathlib import Path
from typing import Optional
from .edgar_fetcher import EdgarFetcher
from .news_fetcher import CompositeNewsFetcher
from .company_ticker_mapper import CompanyTickerMapper

logger = logging.getLogger("confidence_updater")
LOCK_FILE = str(Path(__file__).resolve().parent.parent / ".confidence_updater.lock")

class ConfidenceUpdater:
    BATCH_SIZE = 20

    def __init__(self, db_path, edgar: EdgarFetcher,
                 news: CompositeNewsFetcher, mapper: CompanyTickerMapper):
        self.db_path = db_path
        self.edgar = edgar
        self.news = news
        self.mapper = mapper

    def run(self, limit=50, dry_run=False, offset=0) -> dict:
        """
        冪等執行：讀取現有 edgar_score/news_score，
        若新計算結果 > 舊值才更新（不會重複累加）
        回傳 {"updated": N, "skipped": M, "errors": E}
        """
        # 取得 file lock，防止 backfill + 排程同時執行
        try:
            lock_fd = open(LOCK_FILE, "a")  # "a" 不 truncate
        except OSError as e:
            logger.error(f"Cannot open lock file: {e}")
            return {"updated": 0, "skipped": 0, "errors": 0, "status": "locked"}
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.warning("Another confidence update is already running. Skipping...")
            lock_fd.close()
            return {"updated": 0, "skipped": 0, "errors": 0, "status": "locked"}

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        
        # Ensure company ticker map is fresh
        self.mapper.load_or_refresh(conn)

        try:
            # 撈 relations（不限 confidence，讓 scorer 決定是否更新）
            # 我們會撈出所有的 relations，但優先處理那些還沒被 edgar 或 news score 提升過的
            # 或者我們可以根據 limit 每次跑一部分。
            # 為了支持 resume，我們可以查 confidence_audit 來看看哪些已經跑過了，
            # 或者乾脆每次跑 limit 筆，反正它是冪等的。
            rows = conn.execute("""
                SELECT r.id, r.edgar_score, r.news_score, r.confidence,
                       fe.name AS from_name, te.name AS to_name
                FROM industry_relations r
                JOIN industry_entities fe ON r.from_company_id = fe.id
                JOIN industry_entities te ON r.to_company_id = te.id
                WHERE r.id > ?
                ORDER BY r.id ASC
                LIMIT ?
            """, (offset, limit)).fetchall()

            updated = skipped = errors = 0
            for i, row in enumerate(rows):
                try:
                    logger.info(f"Processing relation {row['id']}: {row['from_name']} -> {row['to_name']}")
                    
                    # === Edgar ===
                    edgar_hits = self.edgar.search_relation(row["from_name"], row["to_name"])
                    new_edgar = self.edgar.calc_edgar_score(edgar_hits)

                    # === News ===
                    new_news, news_source = self.news.boost_score(
                        row["from_name"], row["to_name"], conn
                    )

                    # 冪等：只在分數更高時更新
                    # 也可以決定如果現有分數為 0，則即使新分數為 0 也記錄一次 audit status 'no_match'
                    # 但計畫中提到 "只在 score_new > score_old 時 UPDATE"
                    edgar_changed = new_edgar > row["edgar_score"]
                    news_changed = new_news > row["news_score"]

                    if not dry_run and (edgar_changed or news_changed):
                        # 從 DB 讀出真實 base_score（不同 row 可能不同）
                        base_row = conn.execute(
                            "SELECT base_score FROM industry_relations WHERE id = ?", (row["id"],)
                        ).fetchone()
                        base_score = base_row["base_score"] if base_row else 0.5
                        new_conf = min(1.0, base_score + max(new_edgar, row["edgar_score"]) + max(new_news, row["news_score"]))

                        conn.execute("""
                            UPDATE industry_relations
                            SET edgar_score = MAX(edgar_score, ?),
                                news_score = MAX(news_score, ?),
                                confidence = MAX(confidence, ?)
                            WHERE id = ?
                        """, (new_edgar, new_news, new_conf, row["id"]))
                        
                        if edgar_changed:
                            conn.execute("""
                                INSERT INTO confidence_audit
                                (relation_id, source, boost_value, status, snippet)
                                VALUES (?, 'edgar', ?, 'success', ?)
                            """, (row["id"], new_edgar - row["edgar_score"], f"Found {len(edgar_hits)} filings"))
                            
                        if news_changed:
                            _VALID_SOURCES = {"edgar", "google_news", "yahoo_rss"}
                            audit_source = news_source if news_source in _VALID_SOURCES else "google_news"
                            conn.execute("""
                                INSERT INTO confidence_audit
                                (relation_id, source, boost_value, status, snippet)
                                VALUES (?, ?, ?, 'success', ?)
                            """, (row["id"], audit_source, new_news - row["news_score"], f"Found news via {news_source}"))
                            
                        updated += 1
                    else:
                        skipped += 1
                        # 如果是 dry_run 且會改變，我們也算 updated
                        if dry_run and (edgar_changed or news_changed):
                            updated += 1

                    # 分批 commit
                    if not dry_run and (i + 1) % self.BATCH_SIZE == 0:
                        conn.commit()

                except Exception as e:
                    logger.error(f"Error processing relation {row['id']}: {e}")
                    errors += 1
                    if not dry_run:
                        try:
                            conn.execute("""
                                INSERT INTO confidence_audit(relation_id, source, boost_value, status, snippet)
                                VALUES (?, 'edgar', 0.0, 'api_error', ?)
                            """, (row["id"], str(e)[:200]))
                        except Exception:
                            pass

            if not dry_run:
                conn.commit()

            last_id = rows[-1]["id"] if rows else offset
            return {"updated": updated, "skipped": skipped, "errors": errors, "last_id": last_id}
        finally:
            conn.close()
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
