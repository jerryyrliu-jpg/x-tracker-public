import importlib
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from cpo_chain import extract_universal


def test_load_sqlite_vec_returns_none_when_missing(monkeypatch):
    real_import_module = importlib.import_module

    def fake_import_module(name, package=None):
        if name == "sqlite_vec":
            raise ModuleNotFoundError("sqlite_vec")
        return real_import_module(name, package)

    monkeypatch.setattr(extract_universal.importlib, "import_module", fake_import_module)

    assert extract_universal.load_sqlite_vec() is None


# ── call_gemini tests ─────────────────────────────────────────────────────────

async def test_call_gemini_empty_output_raises(monkeypatch):
    monkeypatch.setattr(extract_universal, "run_text_prompt", lambda *a, **kw: "")
    with pytest.raises(Exception):
        await extract_universal.call_gemini([{"id": 1, "text": "hello"}])


async def test_call_gemini_no_json_block_raises(monkeypatch):
    monkeypatch.setattr(extract_universal, "run_text_prompt", lambda *a, **kw: "no json here at all")
    with pytest.raises(Exception):
        await extract_universal.call_gemini([{"id": 1, "text": "hello"}])


async def test_call_gemini_valid_json_returns_relations(monkeypatch):
    relations_payload = [
        {
            "from_entity": "NVIDIA",
            "to_entity": "TSMC",
            "role": "chip_supplier",
            "role_category": "upstream",
            "industry_context": "CPO",
            "evidence_type": "support",
            "confidence": 0.9,
            "confidence_reason": "direct mention",
        }
    ]
    output = json.dumps({"relations": relations_payload})
    monkeypatch.setattr(extract_universal, "run_text_prompt", lambda *a, **kw: output)

    result = await extract_universal.call_gemini([{"id": 1, "text": "NVIDIA uses TSMC"}])
    assert len(result) == 1
    assert result[0]["from_entity"] == "NVIDIA"


# ── process_batch tests ───────────────────────────────────────────────────────

def _make_relation(**overrides):
    base = {
        "from_entity": "NVIDIA",
        "to_entity": "TSMC",
        "role": "chip_supplier",
        "role_category": "upstream",
        "industry_context": "CPO",
        "evidence_type": "support",
        "confidence": 0.9,
        "confidence_reason": "confirmed",
    }
    return {**base, **overrides}


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE industry_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            ticker TEXT,
            status TEXT DEFAULT 'active'
        )
    """)
    conn.execute("""
        CREATE TABLE industry_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_company_id INTEGER,
            to_company_id INTEGER,
            role TEXT,
            role_category TEXT,
            industry_context TEXT,
            confidence REAL DEFAULT 0.5,
            edgar_score REAL DEFAULT 0,
            news_score REAL DEFAULT 0,
            evidence_score REAL DEFAULT 0,
            confidence_reason TEXT,
            base_score REAL DEFAULT 0.5,
            status TEXT DEFAULT 'active',
            last_confirmed TEXT,
            UNIQUE(from_company_id, to_company_id, role, industry_context)
        )
    """)
    conn.execute("""
        CREATE TABLE industry_relation_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            relation_id INTEGER,
            tweet_id INTEGER,
            evidence_type TEXT,
            snippet TEXT
        )
    """)
    conn.execute("INSERT INTO industry_entities (name, ticker) VALUES ('NVIDIA', 'NVDA')")
    conn.execute("INSERT INTO industry_entities (name, ticker) VALUES ('TSMC', 'TSM')")
    conn.commit()
    return conn


def _make_resolver(from_id=1, to_id=2):
    resolver = MagicMock()
    resolver.resolve.side_effect = [
        (from_id, "NVIDIA", "ok"),
        (to_id, "TSMC", "ok"),
    ] * 10
    return resolver


async def test_process_batch_low_confidence_filtered(monkeypatch):
    rel = _make_relation(confidence=0.4)

    async def fake_gemini(b):
        return [rel]

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    result = await extract_universal.process_batch(
        [{"id": 1, "text": "test"}], _make_resolver(), conn, dry_run=True
    )
    assert result == []
    assert conn.execute("SELECT COUNT(*) FROM industry_relations").fetchone()[0] == 0


async def test_process_batch_invalid_role_category_filtered(monkeypatch):
    rel = _make_relation(role_category="invalid_category")

    async def fake_gemini(b):
        return [rel]

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    result = await extract_universal.process_batch(
        [{"id": 1, "text": "test"}], _make_resolver(), conn, dry_run=True
    )
    assert result == []


async def test_process_batch_self_link_skipped(monkeypatch):
    rel = _make_relation()

    async def fake_gemini(b):
        return [rel]

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    resolver = _make_resolver(from_id=1, to_id=1)
    await extract_universal.process_batch(
        [{"id": 1, "text": "test"}], resolver, conn
    )
    assert conn.execute("SELECT COUNT(*) FROM industry_relations").fetchone()[0] == 0


async def test_process_batch_dry_run_skips_db_writes(monkeypatch):
    rel = _make_relation()

    async def fake_gemini(b):
        return [rel]

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    await extract_universal.process_batch(
        [{"id": 1, "text": "test"}], _make_resolver(), conn, dry_run=True
    )
    assert conn.execute("SELECT COUNT(*) FROM industry_relations").fetchone()[0] == 0


async def test_process_batch_inserts_relation(monkeypatch):
    rel = _make_relation()

    async def fake_gemini(b):
        return [rel]

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    await extract_universal.process_batch(
        [{"id": 1, "text": "test"}], _make_resolver(), conn
    )
    assert conn.execute("SELECT COUNT(*) FROM industry_relations").fetchone()[0] == 1


async def test_process_batch_duplicate_rel_key_inserted_once(monkeypatch):
    rel = _make_relation()

    async def fake_gemini(b):
        return [rel, rel]

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    resolver = MagicMock()
    resolver.resolve.side_effect = [
        (1, "NVIDIA", "ok"), (2, "TSMC", "ok"),
        (1, "NVIDIA", "ok"), (2, "TSMC", "ok"),
    ]
    await extract_universal.process_batch(
        [{"id": 1, "text": "test"}], resolver, conn
    )
    assert conn.execute("SELECT COUNT(*) FROM industry_relations").fetchone()[0] == 1
