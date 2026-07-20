import importlib
import json
import sqlite3
import subprocess
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


def test_load_vector_dependencies_raises_cleanly_when_sqlite_vec_missing(monkeypatch):
    real_import_module = importlib.import_module

    def fake_import_module(name, package=None):
        if name == "sqlite_vec":
            raise ModuleNotFoundError("sqlite_vec")
        if name in {"cpo_chain.embedder", "embedder"}:
            class FakeEmbedderModule:
                class UniversalEmbedder:
                    pass
            return FakeEmbedderModule
        return real_import_module(name, package)

    monkeypatch.setattr(extract_universal.importlib, "import_module", fake_import_module)

    with pytest.raises(RuntimeError, match="sqlite_vec is required"):
        extract_universal.load_vector_dependencies()


def test_collect_ocr_text_returns_empty_when_no_backend(monkeypatch):
    monkeypatch.setattr(extract_universal.ocr_utils, "detect_ocr_backend", lambda: None)
    texts = extract_universal._collect_image_ocr_text(["/tmp/missing.jpg"])
    assert texts == []


def test_collect_ocr_text_skips_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_universal.ocr_utils, "detect_ocr_backend", lambda: "mock")
    monkeypatch.setattr(
        extract_universal.ocr_utils,
        "extract_text_from_image",
        lambda path, backend=None: "hello",
    )
    texts = extract_universal._collect_image_ocr_text([str(tmp_path / "missing.jpg")])
    assert texts == []


def test_collect_ocr_text_skips_paths_outside_images_root(tmp_path, monkeypatch):
    outside = tmp_path / "outside.jpg"
    outside.write_text("fake")
    monkeypatch.setattr(extract_universal.ocr_utils, "detect_ocr_backend", lambda: "mock")
    monkeypatch.setattr(
        extract_universal.ocr_utils,
        "extract_text_from_image",
        lambda path, backend=None: "should not be used",
    )
    texts = extract_universal._collect_image_ocr_text([str(outside)])
    assert texts == []


def test_extract_text_from_image_returns_empty_on_timeout(monkeypatch):
    from cpo_chain import ocr_utils

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="tesseract", timeout=20)

    monkeypatch.setattr(ocr_utils.subprocess, "run", fake_run)
    assert ocr_utils.extract_text_from_image("/tmp/test.jpg", backend="tesseract") == ""


def test_build_enriched_tweet_text_includes_ocr_section(monkeypatch):
    monkeypatch.setattr(
        extract_universal,
        "_collect_image_ocr_text",
        lambda paths: ["slide says Agility Robotics supplies humanoid robots"],
    )
    tweet = {"id": "1", "text": "tweet body", "images": json.dumps(["/tmp/a.jpg"])}
    text = extract_universal._build_enriched_tweet_text(tweet)
    assert "[TWEET_TEXT]" in text
    assert "tweet body" in text
    assert "[IMAGE_OCR]" in text
    assert "Agility Robotics" in text


def test_build_enriched_tweet_text_handles_bad_images_json(monkeypatch):
    monkeypatch.setattr(extract_universal, "_collect_image_ocr_text", lambda paths: [])
    tweet = {"id": "1", "text": "tweet body", "images": "not-json"}
    text = extract_universal._build_enriched_tweet_text(tweet)
    assert text == "[TWEET_TEXT]\ntweet body"


def test_build_batch_payload_enriches_text_with_ocr(monkeypatch):
    monkeypatch.setattr(extract_universal, "_collect_image_ocr_text", lambda paths: ["OCR text"])
    row = {"id": "1", "text": "base", "images": json.dumps(["/tmp/a.jpg"])}
    payload = extract_universal._build_batch_payload(row)
    assert payload["id"] == "1"
    assert "[TWEET_TEXT]" in payload["text"]
    assert "[IMAGE_OCR]" in payload["text"]
    assert "OCR text" in payload["text"]


def test_select_tweets_by_ids_returns_requested_rows(tmp_path):
    conn = sqlite3.connect(tmp_path / "tweets.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tweets (id TEXT PRIMARY KEY, text TEXT, images TEXT, created_at TEXT)")
    conn.execute("INSERT INTO tweets (id, text, images, created_at) VALUES ('1', 'a', '[]', '2026-07-02T00:00:00Z')")
    conn.commit()
    rows = extract_universal._select_tweets_by_ids(conn, ["1", "2"])
    assert [row["id"] for row in rows] == ["1"]


def test_mark_extraction_result_writes_relations_found(tmp_path):
    conn = sqlite3.connect(tmp_path / "tweets.db")
    conn.execute(
        "CREATE TABLE industry_extract_log (tweet_id TEXT PRIMARY KEY, processed_at TEXT DEFAULT (datetime('now')), relations_found INTEGER DEFAULT 0)"
    )
    conn.commit()
    extract_universal._mark_extraction_result(conn, "1", 3)
    row = conn.execute("SELECT tweet_id, relations_found FROM industry_extract_log WHERE tweet_id='1'").fetchone()
    assert row == ("1", 3)


def test_record_batch_results_skips_incomplete_tweets(tmp_path):
    conn = sqlite3.connect(tmp_path / "tweets.db")
    conn.execute(
        "CREATE TABLE industry_extract_log (tweet_id TEXT PRIMARY KEY, processed_at TEXT DEFAULT (datetime('now')), relations_found INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE industry_relation_evidence (relation_id INTEGER, tweet_id TEXT, evidence_type TEXT, snippet TEXT)"
    )
    conn.commit()
    batch = [{"id": "1", "_extraction_completed": False}]
    extract_universal._record_batch_results(conn, batch, {"1": 0})
    row = conn.execute("SELECT COUNT(*) FROM industry_extract_log").fetchone()[0]
    assert row == 0


def test_load_requested_tweet_ids_rejects_missing_file(tmp_path):
    args = type(
        "Args",
        (),
        {"tweet_id": [], "tweet_ids_file": str(tmp_path / "missing.txt")},
    )()

    with pytest.raises(ValueError, match="tweet IDs file"):
        extract_universal._load_requested_tweet_ids(args)


def test_load_requested_tweet_ids_rejects_empty_file(tmp_path):
    ids_file = tmp_path / "tweet_ids.txt"
    ids_file.write_text("\n  \n", encoding="utf-8")
    args = type(
        "Args",
        (),
        {"tweet_id": [], "tweet_ids_file": str(ids_file)},
    )()

    with pytest.raises(ValueError, match="empty"):
        extract_universal._load_requested_tweet_ids(args)


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


async def test_process_batch_uses_raw_text_for_evidence_snippet(monkeypatch):
    rel = _make_relation()

    async def fake_gemini(b):
        return [rel]

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    await extract_universal.process_batch(
        [{"id": "1", "text": "[TWEET_TEXT]\nbase\n\n[IMAGE_OCR]\nOCR text", "raw_text": "base raw text"}],
        _make_resolver(),
        conn,
    )
    snippet = conn.execute("SELECT snippet FROM industry_relation_evidence").fetchone()[0]
    assert snippet == "base raw text"


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


async def test_process_batch_inserts_relation_with_enriched_payload(monkeypatch):
    rel = _make_relation()

    async def fake_gemini(batch):
        assert "[TWEET_TEXT]" in batch[0]["text"]
        return [rel]

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    await extract_universal.process_batch(
        [{"id": "1", "text": "[TWEET_TEXT]\nbase\n\n[IMAGE_OCR]\nOCR text"}],
        _make_resolver(),
        conn,
    )
    assert conn.execute("SELECT COUNT(*) FROM industry_relation_evidence").fetchone()[0] == 1


async def test_process_batch_marks_single_failure_as_incomplete(monkeypatch):
    async def fake_gemini(batch):
        raise RuntimeError("llm down")

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    tweet = {"id": "1", "text": "base"}
    result = await extract_universal.process_batch([tweet], _make_resolver(), conn)
    assert result == []
    assert tweet["_extraction_completed"] is False


async def test_process_batch_marks_db_failure_as_incomplete(monkeypatch):
    rel = _make_relation()

    async def fake_gemini(batch):
        return [rel]

    class BrokenConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            if "INSERT INTO industry_relations" in sql:
                raise sqlite3.OperationalError("db write failed")
            if "SELECT id FROM industry_relations" in sql:
                return MagicMock(fetchone=lambda: None)
            raise AssertionError(f"Unexpected SQL: {sql}")

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    tweet = {"id": "1", "text": "base raw text", "raw_text": "base raw text"}
    result = await extract_universal.process_batch([tweet], _make_resolver(), BrokenConn())
    assert result == []
    assert tweet["_extraction_completed"] is False


async def test_process_batch_multiple_tweets_do_not_cross_attach_evidence(monkeypatch):
    calls = {"count": 0}

    async def fake_gemini(batch):
        calls["count"] += 1
        if "tweet one raw" in batch[0]["text"]:
            return [_make_relation(role="role_one")]
        return [_make_relation(role="role_two")]

    monkeypatch.setattr(extract_universal, "call_gemini", fake_gemini)
    conn = _make_db()
    resolver = MagicMock()
    resolver.resolve.side_effect = [
        (1, "NVIDIA", "ok"), (2, "TSMC", "ok"),
        (1, "NVIDIA", "ok"), (2, "TSMC", "ok"),
    ]
    tweets = [
        {"id": "t1", "text": "tweet one raw", "raw_text": "tweet one raw"},
        {"id": "t2", "text": "tweet two raw", "raw_text": "tweet two raw"},
    ]
    await extract_universal.process_batch(tweets, resolver, conn)
    rows = conn.execute(
        "SELECT tweet_id, snippet FROM industry_relation_evidence ORDER BY tweet_id"
    ).fetchall()
    assert rows == [("t1", "tweet one raw"), ("t2", "tweet two raw")]
    assert calls["count"] == 2
