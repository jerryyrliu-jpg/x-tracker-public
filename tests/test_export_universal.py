import sqlite3
from cpo_chain import export_universal


def test_normalize_industry_context_merges_known_aliases():
    assert export_universal.normalize_industry_context("AI_Server") == "AI Server"
    assert export_universal.normalize_industry_context("AI Server") == "AI Server"
    assert export_universal.normalize_industry_context("Advanced_Packaging") == "Advanced Packaging"
    assert export_universal.normalize_industry_context("CPO/silicon photonics") == "CPO / Silicon Photonics"
    assert export_universal.normalize_industry_context("CPO / Silicon Photonics") == "CPO / Silicon Photonics"


def test_normalize_industry_context_none_and_empty_return_other():
    assert export_universal.normalize_industry_context(None) == "Other"
    assert export_universal.normalize_industry_context("") == "Other"
    assert export_universal.normalize_industry_context("   ") == "Other"


def test_normalize_industry_context_acronyms():
    assert export_universal.normalize_industry_context("hbm") == "HBM"
    assert export_universal.normalize_industry_context("ocs") == "OCS"
    assert export_universal.normalize_industry_context("leo") == "LEO"


def test_normalize_industry_context_nvidia_brand():
    result = export_universal.normalize_industry_context("nvidia cpo")
    assert result == "NVIDIA CPO"


def test_normalize_industry_context_unit_suffix():
    result = export_universal.normalize_industry_context("400g transceiver")
    assert result == "400G Transceiver"


def test_normalize_industry_context_title_case_fallback():
    result = export_universal.normalize_industry_context("liquid cooling")
    assert result == "Liquid Cooling"


def test_should_export_context_requires_links():
    tiers_data = [{"id": 1, "name": "NVIDIA", "ticker": "NVDA", "tier": 0}]

    assert export_universal.should_export_context(tiers_data, []) is False
    assert export_universal.should_export_context(tiers_data, [{"source": 1, "target": 2, "role": "partner"}]) is True


def test_canonical_company_name_merges_only_when_shorter_name_exists():
    known_names = {"Coherent", "Coherent (COHR)", "Micron", "Micron Technology", "O-Net Technologies"}

    assert export_universal.canonical_company_name("Coherent (COHR)", known_names) == "Coherent"
    assert export_universal.canonical_company_name("Micron Technology", known_names) == "Micron"
    assert export_universal.canonical_company_name("O-Net Technologies", known_names) == "O-Net Technologies"


def test_canonical_company_name_empty_known_names():
    assert export_universal.canonical_company_name("NVIDIA (NVDA)", set()) == "NVIDIA"


def test_canonical_company_name_empty_string():
    assert export_universal.canonical_company_name("", set()) == ""


def test_format_conf_md_high_confidence():
    result = export_universal.format_conf_md({"confidence": 0.9, "edgar_score": 0, "news_score": 0})
    assert "✅" in result
    assert "0.90" in result


def test_format_conf_md_medium_confidence():
    result = export_universal.format_conf_md({"confidence": 0.7, "edgar_score": 0, "news_score": 0})
    assert "📄" in result


def test_format_conf_md_low_confidence():
    result = export_universal.format_conf_md({"confidence": 0.3, "edgar_score": 0, "news_score": 0})
    assert "⚠️" in result


def test_format_conf_md_missing_confidence():
    result = export_universal.format_conf_md({})
    assert isinstance(result, str)
    assert len(result) > 0


def _make_test_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE industry_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
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
    conn.commit()
    return conn


def test_get_context_groups_empty_db_returns_default():
    conn = _make_test_db()
    result = export_universal._get_context_groups(conn)
    assert result == {"CPO": ["CPO"]}


def test_get_context_groups_groups_by_canonical():
    conn = _make_test_db()
    conn.execute("INSERT INTO industry_relations (from_company_id, to_company_id, role, industry_context) VALUES (1, 2, 'supplier', 'AI Server')")
    conn.execute("INSERT INTO industry_relations (from_company_id, to_company_id, role, industry_context) VALUES (2, 3, 'supplier', 'AI_Server')")
    conn.commit()
    result = export_universal._get_context_groups(conn)
    assert "AI Server" in result
    assert len(result["AI Server"]) == 2


def test_get_chain_data_empty_tickers_returns_empty():
    conn = _make_test_db()
    result = export_universal.get_chain_data(conn, [])
    assert result == []


def test_get_chain_data_missing_ticker_returns_empty():
    conn = _make_test_db()
    result = export_universal.get_chain_data(conn, ["NONEXISTENT"])
    assert result == []


def test_get_chain_data_returns_root_node():
    conn = _make_test_db()
    conn.execute("INSERT INTO industry_entities (name, ticker) VALUES ('NVIDIA', 'NVDA')")
    conn.commit()
    result = export_universal.get_chain_data(conn, ["NVDA"], ["CPO"])
    assert len(result) == 1
    assert result[0]["ticker"] == "NVDA"
    assert result[0]["tier"] == 0


def test_get_all_links_empty_node_ids_returns_empty():
    conn = _make_test_db()
    result = export_universal.get_all_links(conn, [])
    assert result == []


def test_get_all_links_returns_active_relations():
    conn = _make_test_db()
    conn.execute("INSERT INTO industry_entities (name, ticker) VALUES ('A Corp', 'ACORP')")
    conn.execute("INSERT INTO industry_entities (name, ticker) VALUES ('B Corp', 'BCORP')")
    conn.execute("""
        INSERT INTO industry_relations (from_company_id, to_company_id, role, industry_context, status)
        VALUES (1, 2, 'supplier', 'CPO', 'active')
    """)
    conn.commit()
    result = export_universal.get_all_links(conn, [1, 2], ["CPO"])
    assert len(result) == 1
    assert result[0]["source"] == 1
    assert result[0]["target"] == 2
