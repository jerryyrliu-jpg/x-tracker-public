
import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "tweets.db"

def migrate():
    print(f"Migrating {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    # 1. Add columns to industry_relations
    existing_columns = [row[1] for row in conn.execute("PRAGMA table_info(industry_relations)").fetchall()]
    
    if "base_score" not in existing_columns:
        print("Adding base_score to industry_relations...")
        conn.execute("ALTER TABLE industry_relations ADD COLUMN base_score REAL DEFAULT 0.5;")
    
    if "edgar_score" not in existing_columns:
        print("Adding edgar_score to industry_relations...")
        conn.execute("ALTER TABLE industry_relations ADD COLUMN edgar_score REAL DEFAULT 0.0;")
        
    if "news_score" not in existing_columns:
        print("Adding news_score to industry_relations...")
        conn.execute("ALTER TABLE industry_relations ADD COLUMN news_score REAL DEFAULT 0.0;")

    # 2. Create confidence_audit table
    print("Creating confidence_audit table...")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS confidence_audit (
        id           INTEGER PRIMARY KEY,
        relation_id  INTEGER NOT NULL,
        source       TEXT NOT NULL CHECK(source IN ('edgar','google_news','yahoo_rss')),
        url          TEXT,
        snippet      TEXT,
        boost_value  REAL NOT NULL,
        status       TEXT NOT NULL CHECK(status IN ('success','api_error','no_match','rate_limited')),
        boosted_at   INTEGER DEFAULT (strftime('%s','now')),
        FOREIGN KEY(relation_id) REFERENCES industry_relations(id) ON DELETE CASCADE
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_relation ON confidence_audit(relation_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_source_time ON confidence_audit(source, boosted_at);")

    # 3. Create company_ticker_map table
    print("Creating company_ticker_map table...")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS company_ticker_map (
        id           INTEGER PRIMARY KEY,
        company_name TEXT NOT NULL,
        cik          TEXT,
        ticker       TEXT,
        updated_at   INTEGER DEFAULT (strftime('%s','now'))
    );
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ticker_map_name ON company_ticker_map(company_name);")

    # 4. Add source column to industry_relation_evidence
    evidence_columns = [row[1] for row in conn.execute("PRAGMA table_info(industry_relation_evidence)").fetchall()]
    if "source" not in evidence_columns:
        print("Adding source column to industry_relation_evidence...")
        conn.execute("ALTER TABLE industry_relation_evidence ADD COLUMN source TEXT DEFAULT 'twitter';")

    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
