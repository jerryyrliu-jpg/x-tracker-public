
import pytest
import sqlite3
import os
from cpo_chain.confidence_updater import ConfidenceUpdater

class MockEdgar:
    def __init__(self, score=0.2): self.score = score
    def search_relation(self, a, b): return [{"form_type": "10-K"}] if self.score > 0 else []
    def calc_edgar_score(self, hits): return self.score

class MockNews:
    def __init__(self, score=0.1): self.score = score
    def boost_score(self, a, b, conn=None): return self.score, "google_news"

class MockMapper:
    def load_or_refresh(self, conn): pass
    def get_ticker(self, conn, name): return "TKR"

@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "test_tweets.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE industry_entities (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE industry_relations (id INTEGER PRIMARY KEY, from_company_id INTEGER, to_company_id INTEGER, base_score REAL DEFAULT 0.5, edgar_score REAL DEFAULT 0.0, news_score REAL DEFAULT 0.0, confidence REAL DEFAULT 0.5)")
    conn.execute("CREATE TABLE confidence_audit (id INTEGER PRIMARY KEY, relation_id INTEGER, source TEXT CHECK(source IN ('edgar','google_news','yahoo_rss')), boost_value REAL, status TEXT, snippet TEXT)")
    conn.execute("CREATE TABLE company_ticker_map (id INTEGER PRIMARY KEY, company_name TEXT, updated_at INTEGER)")
    
    conn.execute("INSERT INTO industry_entities (id, name) VALUES (1, 'NVIDIA'), (2, 'TSMC')")
    conn.execute("INSERT INTO industry_relations (id, from_company_id, to_company_id) VALUES (1, 1, 2)")
    conn.commit()
    conn.close()
    return str(db_path)

def test_run_updates_score(mock_db):
    edgar = MockEdgar(0.2)
    news = MockNews(0.1)
    mapper = MockMapper()
    updater = ConfidenceUpdater(mock_db, edgar, news, mapper)
    
    result = updater.run(limit=10)
    assert result["updated"] == 1
    
    conn = sqlite3.connect(mock_db)
    row = conn.execute("SELECT edgar_score, news_score, confidence FROM industry_relations WHERE id=1").fetchone()
    assert row[0] == pytest.approx(0.2)
    assert row[1] == pytest.approx(0.1)
    assert row[2] == pytest.approx(0.8) # 0.5 + 0.2 + 0.1
    
    # Audit check
    audits = conn.execute("SELECT source, boost_value FROM confidence_audit").fetchall()
    assert len(audits) == 2
    conn.close()

def test_run_is_idempotent(mock_db):
    edgar = MockEdgar(0.2)
    news = MockNews(0.1)
    mapper = MockMapper()
    updater = ConfidenceUpdater(mock_db, edgar, news, mapper)

    updater.run(limit=10)         # first run
    result = updater.run(limit=10) # second run

    assert result["updated"] == 0
    assert result["skipped"] == 1

    conn = sqlite3.connect(mock_db)
    audit_count = conn.execute("SELECT COUNT(*) FROM confidence_audit").fetchone()[0]
    conn.close()
    assert audit_count == 2  # only 2 rows from first run, not duplicated


def test_confidence_not_decreased(mock_db):
    """已有高 confidence 的 relation，updater 不應降低它"""
    conn = sqlite3.connect(mock_db)
    conn.execute("INSERT INTO industry_entities (id, name) VALUES (3, 'AMD'), (4, 'Samsung')")
    conn.execute("""INSERT INTO industry_relations
        (id, from_company_id, to_company_id, base_score, edgar_score, news_score, confidence)
        VALUES (2, 3, 4, 0.5, 0.3, 0.1, 0.9)""")
    conn.commit()
    conn.close()

    updater = ConfidenceUpdater(mock_db, MockEdgar(0.2), MockNews(0.1), MockMapper())
    updater.run(limit=10)

    conn = sqlite3.connect(mock_db)
    row = conn.execute("SELECT confidence FROM industry_relations WHERE id=2").fetchone()
    conn.close()
    assert row[0] >= 0.9  # MAX(confidence, ?) 保證不降

def test_dry_run_no_db_change(mock_db):
    edgar = MockEdgar(0.2)
    news = MockNews(0.1)
    mapper = MockMapper()
    updater = ConfidenceUpdater(mock_db, edgar, news, mapper)
    
    result = updater.run(limit=10, dry_run=True)
    assert result["updated"] == 1 # Would be updated
    
    conn = sqlite3.connect(mock_db)
    row = conn.execute("SELECT edgar_score, news_score FROM industry_relations WHERE id=1").fetchone()
    assert row[0] == 0.0
    conn.close()
