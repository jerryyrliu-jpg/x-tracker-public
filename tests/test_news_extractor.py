import json
import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from cpo_chain.db import init_usci_tables
from cpo_chain.news_extractor import NewsExtractor, INDUSTRY_CONTEXTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn():
    """In-memory SQLite with full USCI schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_usci_tables(conn)
    return conn


@pytest.fixture
def keywords_path(tmp_path):
    """Minimal keywords.yaml required by EntityResolver."""
    kw = tmp_path / "keywords.yaml"
    kw.write_text("seed_aliases: {}\nroot_tickers: []\n")
    return kw


def _make_extractor(keywords_path, monkeypatch):
    """Create a NewsExtractor with the Gemini model constructor mocked out."""
    mock_model = MagicMock()
    with patch("cpo_chain.news_extractor.genai.GenerativeModel", return_value=mock_model):
        extractor = NewsExtractor(db_path=":memory:", keywords_path=keywords_path)
    # Replace model directly so tests can configure responses
    extractor.model = mock_model
    return extractor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_extract_finds_relation(keywords_path, monkeypatch):
    """Mock Gemini returning 1 relation → list has 1 item."""
    extractor = _make_extractor(keywords_path, monkeypatch)
    payload = {"relations": [{"supplier": "TSMC", "customer": "NVIDIA",
                               "role": "wafer_fab", "industry_context": "CPO"}]}
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(payload)
    extractor.model.generate_content.return_value = mock_resp

    result = extractor.extract_from_article({"title": "TSMC supplies NVIDIA", "summary": ""})
    assert len(result) == 1
    assert result[0]["supplier"] == "TSMC"


def test_extract_empty_response(keywords_path, monkeypatch):
    """Mock Gemini returning empty relations list → empty list."""
    extractor = _make_extractor(keywords_path, monkeypatch)
    mock_resp = MagicMock()
    mock_resp.text = json.dumps({"relations": []})
    extractor.model.generate_content.return_value = mock_resp

    result = extractor.extract_from_article({"title": "No supply chain here", "summary": ""})
    assert result == []


def test_run_marks_processed_1(db_conn, keywords_path, monkeypatch):
    """Article with extracted relation → processed=1."""
    db_conn.execute("""
        INSERT INTO news_articles (id, url, source, title, summary, processed)
        VALUES (1, 'http://example.com/1', 'google_news', 'TSMC supplies NVIDIA', '', 0)
    """)
    db_conn.commit()

    extractor = _make_extractor(keywords_path, monkeypatch)

    # Mock extract_from_article to return 1 relation
    relation = {"supplier": "TSMC", "customer": "NVIDIA",
                 "role": "wafer_fab", "industry_context": "CPO"}
    extractor.extract_from_article = MagicMock(return_value=[relation])

    # Mock resolver.resolve to return stable IDs
    extractor.resolver.resolve = MagicMock(side_effect=[(1, "TSMC", "TSM"), (2, "NVIDIA", "NVDA")])

    extractor.run(db_conn, limit=10)

    row = db_conn.execute("SELECT processed FROM news_articles WHERE id=1").fetchone()
    assert row[0] == 1


def test_run_marks_processed_2(db_conn, keywords_path, monkeypatch):
    """Article with no extracted relations → processed=2."""
    db_conn.execute("""
        INSERT INTO news_articles (id, url, source, title, summary, processed)
        VALUES (2, 'http://example.com/2', 'google_news', 'Market overview', '', 0)
    """)
    db_conn.commit()

    extractor = _make_extractor(keywords_path, monkeypatch)
    extractor.extract_from_article = MagicMock(return_value=[])

    extractor.run(db_conn, limit=10)

    row = db_conn.execute("SELECT processed FROM news_articles WHERE id=2").fetchone()
    assert row[0] == 2


def test_run_marks_processed_3(db_conn, keywords_path, monkeypatch):
    """Article where extract_from_article raises → processed=3."""
    db_conn.execute("""
        INSERT INTO news_articles (id, url, source, title, summary, processed)
        VALUES (3, 'http://example.com/3', 'google_news', 'Error article', '', 0)
    """)
    db_conn.commit()

    extractor = _make_extractor(keywords_path, monkeypatch)
    extractor.extract_from_article = MagicMock(side_effect=Exception("Gemini timeout"))

    extractor.run(db_conn, limit=10)

    row = db_conn.execute("SELECT processed FROM news_articles WHERE id=3").fetchone()
    assert row[0] == 3


def test_existing_relation_gets_news_evidence(db_conn, keywords_path, monkeypatch):
    """Pre-existing relation (from Twitter) gets a news evidence row after run."""
    # Insert two entities and a pre-existing relation
    db_conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (10, 'TSMC', 'TSM')")
    db_conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (11, 'NVIDIA', 'NVDA')")
    db_conn.execute("""
        INSERT INTO industry_relations
            (id, from_company_id, to_company_id, role, role_category, base_score, confidence, industry_context)
        VALUES (99, 10, 11, 'wafer_fab', 'upstream', 0.8, 0.8, 'CPO')
    """)
    # Insert existing Twitter evidence
    db_conn.execute("""
        INSERT INTO industry_relation_evidence (relation_id, tweet_id, snippet, source)
        VALUES (99, 'tweet_abc', 'TSMC manufactures for NVIDIA', 'twitter')
    """)
    db_conn.execute("""
        INSERT INTO news_articles (id, url, source, title, summary, processed)
        VALUES (4, 'http://example.com/4', 'google_news', 'TSMC supplies NVIDIA chips', '', 0)
    """)
    db_conn.commit()

    extractor = _make_extractor(keywords_path, monkeypatch)

    relation = {"supplier": "TSMC", "customer": "NVIDIA",
                 "role": "wafer_fab", "industry_context": "CPO"}
    extractor.extract_from_article = MagicMock(return_value=[relation])
    # Resolve returns the pre-existing entity IDs → INSERT OR IGNORE on relations will skip
    extractor.resolver.resolve = MagicMock(side_effect=[(10, "TSMC", "TSM"), (11, "NVIDIA", "NVDA")])

    extractor.run(db_conn, limit=10)

    row = db_conn.execute("""
        SELECT source FROM industry_relation_evidence
        WHERE relation_id=99 AND tweet_id='http://example.com/4'
    """).fetchone()
    assert row is not None
    assert row[0] == "news"


def test_industry_context_enum_enforced():
    """INDUSTRY_CONTEXTS must contain exactly the expected 6 values."""
    expected = ["CPO", "HBM", "AI_Server", "Liquid_Cooling", "Advanced_Packaging", "Other"]
    assert INDUSTRY_CONTEXTS == expected
