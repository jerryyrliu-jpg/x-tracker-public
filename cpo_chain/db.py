import sqlite3
import logging

logger = logging.getLogger(__name__)

def get_conn(db_path) -> sqlite3.Connection:
    """Get a database connection with WAL mode and standard settings."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_usci_tables(conn: sqlite3.Connection):
    """Initialize USCI (Universal Supply Chain Intelligence) tables."""
    # Enforce WAL mode and timeout
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")

    # 1. Industry Entities Table (formerly cpo_companies)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS industry_entities (
        id              INTEGER PRIMARY KEY,
        ticker          TEXT,                    -- Stock ticker (optional)
        name            TEXT NOT NULL UNIQUE,    -- Standardized entity name
        country         TEXT,                    -- TW / US / JP / KR / EU
        sector          TEXT,                    -- Industry sector
        industry_tags   TEXT,                    -- JSON array: ["CPO", "AI", "Cooling"]
        created_at      TEXT DEFAULT (datetime('now'))
    );
    """)

    # 2. Industry Entity Aliases (formerly cpo_company_aliases)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS industry_entity_aliases (
        alias       TEXT PRIMARY KEY,
        company_id  INTEGER,
        status      TEXT DEFAULT 'active' CHECK(status IN ('active', 'needs_review')),
        FOREIGN KEY(company_id) REFERENCES industry_entities(id)
    );
    """)

    # 3. Industry Relations Table (formerly cpo_supply_relations)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS industry_relations (
        id              INTEGER PRIMARY KEY,
        from_company_id INTEGER NOT NULL,
        to_company_id   INTEGER NOT NULL,
        role            TEXT NOT NULL,
        role_category   TEXT NOT NULL CHECK(role_category IN ('upstream', 'midstream', 'downstream', 'equipment', 'material')),
        confidence      REAL DEFAULT 0.8,
        evidence_score  INTEGER DEFAULT 1,
        status          TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'disputed')),
        first_seen      TEXT DEFAULT (datetime('now')),
        last_confirmed  TEXT DEFAULT (datetime('now')),
        industry_context TEXT DEFAULT 'CPO',
        confidence_reason TEXT,
        UNIQUE(from_company_id, to_company_id, role, industry_context),
        FOREIGN KEY(from_company_id) REFERENCES industry_entities(id),
        FOREIGN KEY(to_company_id) REFERENCES industry_entities(id)
    );
    """)

    # 4. Industry Relation Evidence Table (formerly cpo_relation_evidence)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS industry_relation_evidence (
        relation_id     INTEGER,
        tweet_id        TEXT,
        evidence_type   TEXT DEFAULT 'support' CHECK(evidence_type IN ('support', 'refute')),
        snippet         TEXT, -- Extract of the tweet confirming the relation
        extracted_at    TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (relation_id, tweet_id),
        FOREIGN KEY(relation_id) REFERENCES industry_relations(id)
    );
    """)

    # 5. Industry Extract Log (formerly cpo_extract_log)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS industry_extract_log (
        tweet_id    TEXT PRIMARY KEY,
        processed_at TEXT DEFAULT (datetime('now')),
        relations_found INTEGER DEFAULT 0
    );
    """)

    # 6. Industry Run Metrics Table (formerly cpo_run_metrics)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS industry_run_metrics (
        run_id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date             TEXT DEFAULT (datetime('now')),
        total_tweets         INTEGER DEFAULT 0,
        api_calls            INTEGER DEFAULT 0,
        rate_limit_hits      INTEGER DEFAULT 0,
        new_entities_created INTEGER DEFAULT 0
    );
    """)

    # Create Indices
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_from ON industry_relations(from_company_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_to ON industry_relations(to_company_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_status ON industry_relations(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_context ON industry_relations(industry_context);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_tweet ON industry_relation_evidence(tweet_id);")

    # --- Confidence Booster 擴充（冪等，兼容舊 DB）---
    _add_column_if_missing(conn, "industry_relations", "base_score",  "REAL DEFAULT 0.5")
    _add_column_if_missing(conn, "industry_relations", "edgar_score", "REAL DEFAULT 0.0")
    _add_column_if_missing(conn, "industry_relations", "news_score",  "REAL DEFAULT 0.0")
    _add_column_if_missing(conn, "industry_relation_evidence", "source", "TEXT DEFAULT 'twitter'")

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

    # 7. News Articles Table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS news_articles (
        id           INTEGER PRIMARY KEY,
        url          TEXT NOT NULL UNIQUE,
        source       TEXT NOT NULL CHECK(source IN ('google_news','sec_8k')),
        title        TEXT,
        summary      TEXT,
        published_at INTEGER,
        fetched_at   INTEGER DEFAULT (strftime('%s','now')),
        processed    INTEGER DEFAULT 0
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_processed ON news_articles(processed, source);")

    conn.commit()
    logger.info("USCI database tables initialized.")


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str):
    existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition};")

# For backwards compatibility if any scripts still call init_cpo_tables
def init_cpo_tables(conn):
    init_usci_tables(conn)
