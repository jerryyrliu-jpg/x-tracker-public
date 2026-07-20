from cpo_chain import normalization


def test_placeholder_nodes_contains_known_junk_entity():
    assert "Customer C (全球領先光模組業者)" in normalization.PLACEHOLDER_NODES


def test_placeholder_nodes_contains_gen5_low_confidence_noise_entity():
    assert "Gen5" in normalization.PLACEHOLDER_NODES


def test_company_name_aliases_map_known_variants():
    assert normalization.COMPANY_NAME_ALIASES["ASTS"] == "AST SpaceMobile"
    assert normalization.COMPANY_NAME_ALIASES["FOCI Fiber Optic Communications"] == "FOCI"
    assert normalization.COMPANY_NAME_ALIASES["Foci"] == "FOCI"


def test_base_company_name_strips_ticker_parenthetical():
    assert normalization.base_company_name("NVIDIA (NVDA)") == "NVIDIA"
    assert normalization.base_company_name("Coherent (COHR)") == "Coherent"
    assert normalization.base_company_name("AST SpaceMobile") == "AST SpaceMobile"
    assert normalization.base_company_name("") == ""


def test_canonical_company_name_prefers_explicit_alias_map():
    assert normalization.canonical_company_name("ASTS", set()) == "AST SpaceMobile"
    assert (
        normalization.canonical_company_name("FOCI Fiber Optic Communications", set())
        == "FOCI"
    )


def test_canonical_company_name_merges_to_shorter_known_name():
    known = {"Micron", "Micron Technology"}
    assert normalization.canonical_company_name("Micron Technology", known) == "Micron"


def test_canonical_company_name_keeps_name_without_shorter_match():
    assert normalization.canonical_company_name("O-Net Technologies", {"O-Net Technologies"}) == "O-Net Technologies"


def test_canonical_company_name_breaks_equal_length_ties_deterministically():
    # "Lite-On" and "LITE-ON" tie in length; regardless of set insertion/iteration
    # order, the same (len, name)-sorted candidate must always win.
    known = {"Lite-On", "LITE-ON"}
    result_a = normalization.canonical_company_name("Lite-On Technology", known)
    result_b = normalization.canonical_company_name("Lite-On Technology", set(reversed(list(known))))
    assert result_a == result_b == "LITE-ON"  # sorted by (len, name): "LITE-ON" < "Lite-On"


def test_canonical_alias_name_applies_explicit_alias_map_only():
    assert normalization.canonical_alias_name("ASTS") == "AST SpaceMobile"
    assert normalization.canonical_alias_name("FOCI Fiber Optic Communications") == "FOCI"
    assert normalization.canonical_alias_name("Foci") == "FOCI"


def test_canonical_alias_name_strips_ticker_suffix_without_alias_entry():
    assert normalization.canonical_alias_name("Alphabet (GOOGL)") == "Alphabet"


def test_canonical_alias_name_does_not_merge_unrelated_prefix_pairs():
    # Unlike canonical_company_name(), this must NOT collapse a parent/subsidiary
    # or facility-suffixed name into a shorter sibling name just because one is
    # a prefix of the other -- that heuristic is unsafe across a whole graph.
    assert normalization.canonical_alias_name("Nokia (Internal Facility)") == "Nokia (Internal Facility)"
    assert normalization.canonical_alias_name("ASE Technology Holding") == "ASE Technology Holding"
    assert normalization.canonical_alias_name("LG Innotek") == "LG Innotek"
