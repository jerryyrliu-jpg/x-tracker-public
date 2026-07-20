import json
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


def test_export_all_writes_report_to_output_dir(monkeypatch, tmp_path):
    conn = sqlite3.connect(":memory:")
    keywords_path = tmp_path / "keywords.yaml"
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")

    monkeypatch.setattr(export_universal, "KEYWORDS_PATH", keywords_path)
    monkeypatch.setattr(export_universal, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(export_universal, "get_db_conn", lambda _: conn)
    monkeypatch.setattr(export_universal.usci_db, "init_usci_tables", lambda _: None)
    monkeypatch.setattr(export_universal, "_get_context_groups", lambda _: {"CPO": ["CPO"]})
    monkeypatch.setattr(
        export_universal,
        "_build_chain_runtime_cache",
        lambda *_args, **_kwargs: {
            "CPO": {
                "aliases": ["CPO"],
                "tiers": [{"id": 1, "name": "NVIDIA", "ticker": "NVDA", "tier": 0}],
                "links": [{"source": 1, "target": 2, "role": "supplier", "confidence": 0.9}],
            }
        },
    )
    monkeypatch.setattr(export_universal, "_build_rooted_report_sections", lambda *_args, **_kwargs: [])

    export_universal.export_all()

    assert (tmp_path / "USCI_Report.md").exists()
    assert (tmp_path / "usci_tiers_cache.json").exists()
    assert not (export_universal.BASE_DIR / "themes" / "USCI_Report.md").exists()


def test_export_all_writes_chain_runtime_cache_contract(monkeypatch, tmp_path):
    conn = sqlite3.connect(":memory:")
    keywords_path = tmp_path / "keywords.yaml"
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")

    monkeypatch.setattr(export_universal, "KEYWORDS_PATH", keywords_path)
    monkeypatch.setattr(export_universal, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(export_universal, "get_db_conn", lambda _: conn)
    monkeypatch.setattr(export_universal.usci_db, "init_usci_tables", lambda _: None)
    monkeypatch.setattr(export_universal, "_get_context_groups", lambda _: {"AI Server": ["AI Server", "AI_Server"]})
    monkeypatch.setattr(
        export_universal,
        "_build_chain_runtime_cache",
        lambda *_args, **_kwargs: {
            "AI Server": {
                "aliases": ["AI Server", "AI_Server"],
                "tiers": [{"id": 1, "name": "NVIDIA", "ticker": "NVDA", "tier": 0}],
                "links": [{"source": 1, "target": 2, "role": "supplier", "confidence": 0.9}],
            }
        },
    )
    monkeypatch.setattr(export_universal, "_build_rooted_report_sections", lambda *_args, **_kwargs: [])

    export_universal.export_all()

    payload = json.loads((tmp_path / "usci_tiers_cache.json").read_text(encoding="utf-8"))
    ai_server = payload["industries"]["AI Server"]
    assert ai_server["aliases"] == ["AI Server", "AI_Server"]
    assert ai_server["tiers"][0]["name"] == "NVIDIA"
    assert "generated_at" in payload["metadata"]


def test_export_all_includes_non_rooted_context_in_chain_runtime_cache(monkeypatch, tmp_path):
    conn = _make_test_db()
    keywords_path = tmp_path / "keywords.yaml"
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (1, 'Supplier A', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (2, 'Boeing', 'BA')")
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (1, 2, 'supplier', 'Aerospace', 0.9, 'active')
        """
    )
    conn.commit()

    monkeypatch.setattr(export_universal, "KEYWORDS_PATH", keywords_path)
    monkeypatch.setattr(export_universal, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(export_universal, "get_db_conn", lambda _: conn)
    monkeypatch.setattr(export_universal.usci_db, "init_usci_tables", lambda _: None)
    monkeypatch.setattr(export_universal, "_get_context_groups", lambda _: {"Aerospace": ["Aerospace"]})

    export_universal.export_all()

    payload = json.loads((tmp_path / "usci_tiers_cache.json").read_text(encoding="utf-8"))
    aerospace = payload["industries"]["Aerospace"]
    assert aerospace["aliases"] == ["Aerospace"]
    assert aerospace["tiers"]
    assert all(isinstance(item["tier"], int) for item in aerospace["tiers"])
    assert isinstance(aerospace["links"], list)


def test_export_all_assigns_stable_runtime_tiers_for_non_rooted_context(monkeypatch, tmp_path):
    conn = _make_test_db()
    keywords_path = tmp_path / "keywords.yaml"
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (1, 'Supplier A', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (2, 'Boeing', 'BA')")
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (1, 2, 'supplier', 'Aerospace', 0.9, 'active')
        """
    )
    conn.commit()

    monkeypatch.setattr(export_universal, "KEYWORDS_PATH", keywords_path)
    monkeypatch.setattr(export_universal, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(export_universal, "get_db_conn", lambda _: conn)
    monkeypatch.setattr(export_universal.usci_db, "init_usci_tables", lambda _: None)
    monkeypatch.setattr(export_universal, "_get_context_groups", lambda _: {"Aerospace": ["Aerospace"]})

    export_universal.export_all()

    payload = json.loads((tmp_path / "usci_tiers_cache.json").read_text(encoding="utf-8"))
    tiers = {item["name"]: item["tier"] for item in payload["industries"]["Aerospace"]["tiers"]}
    assert tiers["Boeing"] == 0
    assert tiers["Supplier A"] == 1


def test_assign_runtime_tiers_does_not_collapse_cycle_component_to_tier_zero():
    tiers = export_universal._assign_runtime_tiers(
        ["A", "B", "C", "D"],
        [
            {"source_name": "A", "target_name": "B"},
            {"source_name": "B", "target_name": "A"},
            {"source_name": "C", "target_name": "D"},
        ],
    )

    assert tiers["D"] == 0
    assert tiers["C"] == 1
    assert tiers["A"] > 0
    assert tiers["B"] > 0


def test_build_chain_runtime_cache_aliases_include_canonical_key(monkeypatch):
    conn = _make_test_db()
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (1, 'Ayar Labs', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (2, 'NVIDIA', 'NVDA')")
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (1, 2, 'supplier', 'CPO/Silicon Photonics', 0.9, 'active')
        """
    )
    conn.commit()

    industries = export_universal._build_chain_runtime_cache(
        conn,
        {"CPO / Silicon Photonics": ["CPO/Silicon Photonics"]},
    )

    assert industries["CPO / Silicon Photonics"]["aliases"] == [
        "CPO / Silicon Photonics",
        "CPO/Silicon Photonics",
    ]


def test_build_chain_runtime_cache_excludes_market_bucket_nodes():
    conn = _make_test_db()
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (1, 'Ayar Labs', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (2, 'NVIDIA', 'NVDA')")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (3, 'Hyperscalers', NULL)")
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (1, 2, 'supplier', 'CPO', 0.9, 'active')
        """
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (2, 3, 'customer bucket', 'CPO', 0.9, 'active')
        """
    )
    conn.commit()

    industries = export_universal._build_chain_runtime_cache(conn, {"CPO": ["CPO"]})
    names = {item["name"] for item in industries["CPO"]["tiers"]}
    links = industries["CPO"]["links"]

    assert names == {"Ayar Labs", "NVIDIA"}
    assert all(link["source_name"] in names for link in links)
    assert all(link["target_name"] in names for link in links)


def test_build_chain_runtime_cache_excludes_placeholder_synthetic_nodes():
    conn = _make_test_db()
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (1, 'Ayar Labs', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (2, 'NVIDIA', 'NVDA')")
    conn.execute(
        "INSERT INTO industry_entities (id, name, ticker) VALUES (3, 'Customer C (全球領先光模組業者)', NULL)"
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (1, 2, 'supplier', 'CPO', 0.9, 'active')
        """
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (2, 3, 'customer', 'CPO', 0.9, 'active')
        """
    )
    conn.commit()

    industries = export_universal._build_chain_runtime_cache(conn, {"CPO": ["CPO"]})
    names = {item["name"] for item in industries["CPO"]["tiers"]}

    assert "Customer C (全球領先光模組業者)" not in names


def test_build_chain_runtime_cache_applies_context_specific_runtime_exclusions():
    conn = _make_test_db()
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (1, 'Sivers Semiconductors', 'SIVE')")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (2, 'AT&T', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (3, 'Boeing', NULL)")
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (1, 2, 'supplier', 'Wireless Infrastructure', 0.9, 'active')
        """
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (1, 3, 'supplier', 'Wireless Infrastructure', 0.9, 'active')
        """
    )
    conn.commit()

    industries = export_universal._build_chain_runtime_cache(
        conn,
        {"Wireless Infrastructure": ["Wireless Infrastructure"]},
    )
    names = {item["name"] for item in industries["Wireless Infrastructure"]["tiers"]}

    assert "Sivers Semiconductors" in names
    assert "AT&T" in names
    assert "Boeing" not in names


def test_build_chain_runtime_cache_merges_known_company_aliases():
    conn = _make_test_db()
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (1, 'FOCI', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (2, 'Foci', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (3, 'NVIDIA', 'NVDA')")
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (1, 3, 'supplier', 'CPO', 0.9, 'active')
        """
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (2, 3, 'supplier', 'CPO', 0.9, 'active')
        """
    )
    conn.commit()

    industries = export_universal._build_chain_runtime_cache(conn, {"CPO": ["CPO"]})
    names = [item["name"] for item in industries["CPO"]["tiers"]]

    assert names.count("FOCI") == 1
    assert "Foci" not in names


def test_build_chain_runtime_cache_prunes_nodes_without_remaining_links_after_cleanup():
    conn = _make_test_db()
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (1, 'AMPG', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (2, 'Lockheed Martin', NULL)")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (3, 'Sivers Semiconductors', 'SIVE')")
    conn.execute("INSERT INTO industry_entities (id, name, ticker) VALUES (4, 'AT&T', NULL)")
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (1, 2, 'supplier', 'Wireless Infrastructure', 0.9, 'active')
        """
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (from_company_id, to_company_id, role, industry_context, confidence, status)
        VALUES (3, 4, 'supplier', 'Wireless Infrastructure', 0.9, 'active')
        """
    )
    conn.commit()

    industries = export_universal._build_chain_runtime_cache(
        conn,
        {"Wireless Infrastructure": ["Wireless Infrastructure"]},
    )
    names = {item["name"] for item in industries["Wireless Infrastructure"]["tiers"]}

    assert "AMPG" not in names
    assert names == {"AT&T", "Sivers Semiconductors"}


def test_validate_runtime_section_warns_for_orphan_nodes():
    warnings = export_universal._validate_runtime_section(
        "CPO",
        tiers=[
            {"id": 1, "name": "Ayar Labs", "ticker": None, "tier": 1},
            {"id": 2, "name": "NVIDIA", "ticker": "NVDA", "tier": 0},
            {"id": 3, "name": "Orphan Co", "ticker": None, "tier": 2},
        ],
        links=[
            {"source_name": "Ayar Labs", "target_name": "NVIDIA", "role": "supplier"},
        ],
    )

    assert warnings == ["[runtime-qc][CPO] orphan nodes: Orphan Co"]


def test_validate_runtime_section_warns_for_excluded_nodes_leaking_into_runtime_graph():
    warnings = export_universal._validate_runtime_section(
        "Wireless Infrastructure",
        tiers=[
            {"id": 1, "name": "Sivers Semiconductors", "ticker": "SIVE", "tier": 1},
            {"id": 2, "name": "Boeing", "ticker": None, "tier": 0},
        ],
        links=[
            {"source_name": "Sivers Semiconductors", "target_name": "Boeing", "role": "supplier"},
        ],
    )

    assert warnings == ["[runtime-qc][Wireless Infrastructure] excluded nodes leaked: Boeing"]


def test_validate_runtime_section_warns_for_duplicate_names():
    warnings = export_universal._validate_runtime_section(
        "CPO",
        tiers=[
            {"id": 1, "name": "FOCI", "ticker": None, "tier": 1},
            {"id": 2, "name": "FOCI", "ticker": None, "tier": 0},
        ],
        links=[
            {"source_name": "FOCI", "target_name": "NVIDIA", "role": "supplier"},
        ],
    )

    assert warnings == ["[runtime-qc][CPO] duplicate tier names: FOCI"]


def test_export_all_prints_runtime_qc_warnings_but_still_writes_cache(monkeypatch, tmp_path, capsys):
    conn = sqlite3.connect(":memory:")
    keywords_path = tmp_path / "keywords.yaml"
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")

    monkeypatch.setattr(export_universal, "KEYWORDS_PATH", keywords_path)
    monkeypatch.setattr(export_universal, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(export_universal, "get_db_conn", lambda _: conn)
    monkeypatch.setattr(export_universal.usci_db, "init_usci_tables", lambda _: None)
    monkeypatch.setattr(export_universal, "_get_context_groups", lambda _: {"CPO": ["CPO"]})
    monkeypatch.setattr(
        export_universal,
        "_build_chain_runtime_cache",
        lambda *_args, **_kwargs: {
            "CPO": {
                "aliases": ["CPO"],
                "tiers": [
                    {"id": 1, "name": "NVIDIA", "ticker": "NVDA", "tier": 0},
                    {"id": 2, "name": "Orphan Co", "ticker": None, "tier": 1},
                ],
                "links": [],
            }
        },
    )
    monkeypatch.setattr(export_universal, "_build_rooted_report_sections", lambda *_args, **_kwargs: [])

    export_universal.export_all()

    captured = capsys.readouterr()
    assert "[runtime-qc][CPO] orphan nodes: NVIDIA, Orphan Co" in captured.out
    assert (tmp_path / "usci_tiers_cache.json").exists()


def test_collect_runtime_qc_groups_warnings_by_context_and_omits_clean_contexts():
    industries = {
        "CPO": {
            "tiers": [
                {"id": 1, "name": "NVIDIA", "ticker": "NVDA", "tier": 0},
                {"id": 2, "name": "Orphan Co", "ticker": None, "tier": 1},
            ],
            "links": [{"source_name": "NVIDIA", "target_name": "Ayar Labs"}],
        },
        "AI Server": {
            "tiers": [
                {"id": 3, "name": "NVIDIA", "ticker": "NVDA", "tier": 0},
                {"id": 4, "name": "Foxconn", "ticker": "2317", "tier": 1},
            ],
            "links": [{"source_name": "Foxconn", "target_name": "NVIDIA"}],
        },
    }

    qc = export_universal._collect_runtime_qc(industries)

    assert "AI Server" not in qc  # clean context is omitted
    assert qc["CPO"] == ["[runtime-qc][CPO] orphan nodes: Orphan Co"]


def test_load_qc_history_returns_empty_for_missing_file(tmp_path):
    assert export_universal._load_qc_history(tmp_path / "nope.json") == []


def test_load_qc_history_returns_empty_for_non_object_json(tmp_path):
    array_payload = tmp_path / "usci_runtime_qc.json"
    array_payload.write_text("[1, 2, 3]", encoding="utf-8")
    assert export_universal._load_qc_history(array_payload) == []


def test_load_qc_history_returns_empty_when_runs_field_is_not_a_list(tmp_path):
    bad_shape = tmp_path / "usci_runtime_qc.json"
    bad_shape.write_text(json.dumps({"runs": "not-a-list"}), encoding="utf-8")
    assert export_universal._load_qc_history(bad_shape) == []


def test_load_qc_history_returns_empty_for_corrupt_file(tmp_path):
    bad = tmp_path / "usci_runtime_qc.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    assert export_universal._load_qc_history(bad) == []


def test_append_qc_run_writes_self_describing_payload(tmp_path):
    qc_path = tmp_path / "usci_runtime_qc.json"

    export_universal._append_qc_run(
        qc_path,
        {"CPO": ["[runtime-qc][CPO] orphan nodes: Orphan Co"]},
        "2026-07-19T17:00:00",
    )

    payload = json.loads(qc_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == export_universal.RUNTIME_QC_SCHEMA_VERSION
    assert payload["generated_at"] == "2026-07-19T17:00:00"
    assert len(payload["runs"]) == 1
    run = payload["runs"][0]
    assert run["generated_at"] == "2026-07-19T17:00:00"
    assert run["total_warnings"] == 1
    assert run["contexts"]["CPO"] == ["[runtime-qc][CPO] orphan nodes: Orphan Co"]


def test_append_qc_run_records_clean_runs_for_traceability(tmp_path):
    qc_path = tmp_path / "usci_runtime_qc.json"

    export_universal._append_qc_run(qc_path, {}, "2026-07-19T17:00:00")

    payload = json.loads(qc_path.read_text(encoding="utf-8"))
    assert payload["runs"][0]["total_warnings"] == 0
    assert payload["runs"][0]["contexts"] == {}


def test_append_qc_run_bounds_history_to_max_runs(tmp_path):
    qc_path = tmp_path / "usci_runtime_qc.json"

    for i in range(35):
        export_universal._append_qc_run(
            qc_path, {}, f"2026-07-19T00:{i:02d}:00", max_runs=30
        )

    payload = json.loads(qc_path.read_text(encoding="utf-8"))
    assert len(payload["runs"]) == 30
    assert payload["runs"][0]["generated_at"] == "2026-07-19T00:05:00"   # oldest kept
    assert payload["runs"][-1]["generated_at"] == "2026-07-19T00:34:00"  # newest appended


def test_append_qc_run_swallows_write_failures_without_raising(tmp_path):
    unwritable_path = tmp_path / "missing_dir" / "usci_runtime_qc.json"

    export_universal._append_qc_run(unwritable_path, {}, "2026-07-19T17:00:00")

    assert not unwritable_path.exists()


def test_diff_qc_runs_reports_newly_appeared_warnings():
    previous = {"CPO": ["[runtime-qc][CPO] orphan nodes: A"]}
    current = {
        "CPO": ["[runtime-qc][CPO] orphan nodes: A", "[runtime-qc][CPO] orphan nodes: B"],
    }

    diff = export_universal.diff_qc_runs(previous, current)

    assert diff["new"] == {"CPO": ["[runtime-qc][CPO] orphan nodes: B"]}
    assert diff["resolved"] == []


def test_diff_qc_runs_reports_resolved_contexts():
    previous = {"CPO": ["[runtime-qc][CPO] orphan nodes: A"]}
    current = {}

    diff = export_universal.diff_qc_runs(previous, current)

    assert diff["new"] == {}
    assert diff["resolved"] == ["CPO"]


def test_diff_qc_runs_silent_when_unchanged():
    previous = {"CPO": ["[runtime-qc][CPO] orphan nodes: A"]}
    current = {"CPO": ["[runtime-qc][CPO] orphan nodes: A"]}

    diff = export_universal.diff_qc_runs(previous, current)

    assert diff["new"] == {}
    assert diff["resolved"] == []


def test_diff_qc_runs_ignores_brand_new_context_with_no_prior_history():
    # A context appearing for the first time still reports as "new" -- there
    # was no prior run to have already surfaced it.
    diff = export_universal.diff_qc_runs({}, {"AI Server": ["[runtime-qc][AI Server] orphan nodes: X"]})

    assert diff["new"] == {"AI Server": ["[runtime-qc][AI Server] orphan nodes: X"]}
    assert diff["resolved"] == []


def test_diff_qc_runs_does_not_rereport_already_flagged_entities_in_a_growing_combined_warning():
    # _validate_runtime_section always joins ALL flagged names for one context
    # into a SINGLE combined warning string (e.g. "orphan nodes: A, B"), never
    # one string per entity. Diffing whole strings means a growing list (A, B
    # -> A, B, C) looks like an entirely new warning, re-alerting on A and B
    # even though they were already flagged and remain unresolved (not new).
    previous = {"CPO": ["[runtime-qc][CPO] orphan nodes: A, B"]}
    current = {"CPO": ["[runtime-qc][CPO] orphan nodes: A, B, C"]}

    diff = export_universal.diff_qc_runs(previous, current)

    assert diff["new"] == {"CPO": ["[runtime-qc][CPO] orphan nodes: C"]}
    assert diff["resolved"] == []


def test_diff_qc_runs_reports_nothing_when_combined_warning_is_unchanged():
    previous = {"CPO": ["[runtime-qc][CPO] orphan nodes: A, B"]}
    current = {"CPO": ["[runtime-qc][CPO] orphan nodes: A, B"]}

    diff = export_universal.diff_qc_runs(previous, current)

    assert diff["new"] == {}
    assert diff["resolved"] == []


def test_diff_qc_runs_reports_all_entities_when_combined_warning_shrinks_but_stays_present():
    # A member dropping out of the combined list produces no "new" entry
    # (nothing was added) -- shrinking silently is acceptable per the
    # low-noise design; only growth must not re-surface existing members.
    previous = {"CPO": ["[runtime-qc][CPO] orphan nodes: A, B, C"]}
    current = {"CPO": ["[runtime-qc][CPO] orphan nodes: A, B"]}

    diff = export_universal.diff_qc_runs(previous, current)

    assert diff["new"] == {}
    assert diff["resolved"] == []


def test_export_all_continues_when_qc_persistence_fails(monkeypatch, tmp_path):
    conn = sqlite3.connect(":memory:")
    keywords_path = tmp_path / "keywords.yaml"
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")

    monkeypatch.setattr(export_universal, "KEYWORDS_PATH", keywords_path)
    monkeypatch.setattr(export_universal, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(export_universal, "get_db_conn", lambda _: conn)
    monkeypatch.setattr(export_universal.usci_db, "init_usci_tables", lambda _: None)
    monkeypatch.setattr(export_universal, "_get_context_groups", lambda _: {"CPO": ["CPO"]})
    monkeypatch.setattr(
        export_universal,
        "_build_chain_runtime_cache",
        lambda *_a, **_k: {"CPO": {"aliases": ["CPO"], "tiers": [], "links": []}},
    )
    monkeypatch.setattr(export_universal, "_build_rooted_report_sections", lambda *_a, **_k: [])

    # Make the QC file path a directory so writing to it raises OSError (IsADirectoryError)
    qc_path = tmp_path / export_universal.RUNTIME_QC_FILENAME
    qc_path.mkdir()

    export_universal.export_all()  # must not raise

    assert (tmp_path / "usci_tiers_cache.json").exists()
    assert (tmp_path / "USCI_Report.md").exists()


def test_export_all_persists_runtime_qc_history_without_bloating_cache(monkeypatch, tmp_path, capsys):
    conn = sqlite3.connect(":memory:")
    keywords_path = tmp_path / "keywords.yaml"
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")

    monkeypatch.setattr(export_universal, "KEYWORDS_PATH", keywords_path)
    monkeypatch.setattr(export_universal, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(export_universal, "get_db_conn", lambda _: conn)
    monkeypatch.setattr(export_universal.usci_db, "init_usci_tables", lambda _: None)
    monkeypatch.setattr(export_universal, "_get_context_groups", lambda _: {"CPO": ["CPO"]})
    monkeypatch.setattr(
        export_universal,
        "_build_chain_runtime_cache",
        lambda *_a, **_k: {
            "CPO": {
                "aliases": ["CPO"],
                "tiers": [
                    {"id": 1, "name": "NVIDIA", "ticker": "NVDA", "tier": 0},
                    {"id": 2, "name": "Orphan Co", "ticker": None, "tier": 1},
                ],
                "links": [],
            }
        },
    )
    monkeypatch.setattr(export_universal, "_build_rooted_report_sections", lambda *_a, **_k: [])

    export_universal.export_all()

    # 1. stdout behavior is preserved
    captured = capsys.readouterr()
    assert "[runtime-qc][CPO] orphan nodes: NVIDIA, Orphan Co" in captured.out

    # 2. runtime cache contract is untouched (no QC bloat under industries or new siblings)
    cache_payload = json.loads((tmp_path / "usci_tiers_cache.json").read_text(encoding="utf-8"))
    assert set(cache_payload) == {"metadata", "industries"}
    assert "runtime_qc" not in cache_payload

    # 3. QC history is persisted to its own self-describing file
    qc_payload = json.loads((tmp_path / "usci_runtime_qc.json").read_text(encoding="utf-8"))
    assert qc_payload["schema_version"] == export_universal.RUNTIME_QC_SCHEMA_VERSION
    assert qc_payload["runs"][-1]["contexts"]["CPO"] == [
        "[runtime-qc][CPO] orphan nodes: NVIDIA, Orphan Co"
    ]


def test_export_all_accumulates_qc_runs_across_repeated_exports(monkeypatch, tmp_path):
    conn = sqlite3.connect(":memory:")
    keywords_path = tmp_path / "keywords.yaml"
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")

    monkeypatch.setattr(export_universal, "KEYWORDS_PATH", keywords_path)
    monkeypatch.setattr(export_universal, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(export_universal, "get_db_conn", lambda _: conn)
    monkeypatch.setattr(export_universal.usci_db, "init_usci_tables", lambda _: None)
    monkeypatch.setattr(export_universal, "_get_context_groups", lambda _: {"CPO": ["CPO"]})
    monkeypatch.setattr(
        export_universal,
        "_build_chain_runtime_cache",
        lambda *_a, **_k: {
            "CPO": {"aliases": ["CPO"], "tiers": [], "links": []}
        },
    )
    monkeypatch.setattr(export_universal, "_build_rooted_report_sections", lambda *_a, **_k: [])

    export_universal.export_all()
    export_universal.export_all()

    qc_payload = json.loads((tmp_path / "usci_runtime_qc.json").read_text(encoding="utf-8"))
    assert len(qc_payload["runs"]) == 2
