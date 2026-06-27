from pathlib import Path
import importlib
import sqlite3


def test_update_network_html_exposes_callable_entrypoint():
    source = Path("scripts/update_network_html.py").read_text(encoding="utf-8")

    assert "def generate_graph_html(" in source
    assert 'if __name__ == "__main__":' in source


def _prepare_graph_db(
    tmp_path: Path,
    *,
    include_evidence: bool = True,
    evidence_snippet: str = "Lumentum is repeatedly discussed as a laser source in CPO builds.",
    extra_evidences: list[tuple[str, str, str]] | None = None,
) -> Path:
    db_path = tmp_path / "tweets.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE industry_entities (
            id INTEGER PRIMARY KEY,
            name TEXT,
            ticker TEXT,
            country TEXT,
            industry_tags TEXT
        );
        CREATE TABLE industry_relations (
            id INTEGER PRIMARY KEY,
            from_company_id INTEGER,
            to_company_id INTEGER,
            role TEXT,
            confidence REAL,
            industry_context TEXT,
            status TEXT
        );
        CREATE TABLE industry_relation_evidence (
            id INTEGER PRIMARY KEY,
            relation_id INTEGER,
            tweet_id INTEGER,
            evidence_type TEXT,
            snippet TEXT,
            extracted_at TEXT,
            source TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO industry_entities (id, name, ticker, country, industry_tags) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "NVIDIA", "NVDA", "US", "ai_chip"),
            (2, "Lumentum", "LITE", "US", "photonics,cpo"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "Laser supplier", 0.95, "CPO", "active"),
        ],
    )
    if include_evidence:
        evidence_rows = [
            (
                1,
                1,
                101,
                "support",
                evidence_snippet,
                "2026-06-01 09:00:00",
                "twitter",
            ),
        ]
        if extra_evidences:
            for idx, (source, snippet, extracted_at) in enumerate(extra_evidences, start=2):
                evidence_rows.append(
                    (
                        idx,
                        1,
                        100 + idx,
                        "support",
                        snippet,
                        extracted_at,
                        source,
                    ),
                )
        conn.executemany(
            """
            INSERT INTO industry_relation_evidence
            (id, relation_id, tweet_id, evidence_type, snippet, extracted_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            evidence_rows,
        )
    conn.commit()
    conn.close()
    return db_path


def _render_graph_html(
    tmp_path: Path,
    *,
    include_evidence: bool = True,
    evidence_snippet: str = "Lumentum is repeatedly discussed as a laser source in CPO builds.",
    extra_evidences: list[tuple[str, str, str]] | None = None,
) -> str:
    module = importlib.import_module("scripts.update_network_html")
    db_path = _prepare_graph_db(
        tmp_path,
        include_evidence=include_evidence,
        evidence_snippet=evidence_snippet,
        extra_evidences=extra_evidences,
    )
    output_dir = tmp_path / "cpo_chain" / "output"
    output_dir.mkdir(parents=True)
    keywords_path = tmp_path / "cpo_chain" / "keywords.yaml"
    keywords_path.parent.mkdir(parents=True, exist_ok=True)
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")
    html_path = output_dir / "index.html"

    module.generate_graph_html(
        base_dir=tmp_path,
        db_path=db_path,
        html_path=html_path,
        keywords_path=keywords_path,
    )
    return html_path.read_text(encoding="utf-8")


def _extract_html_segment(html: str, start_anchor: str, end_anchor: str) -> str:
    start = html.index(start_anchor)
    end = html.index(end_anchor, start)
    return html[start:end]


def test_generated_html_contains_detail_panel_shell(tmp_path):
    html = _render_graph_html(tmp_path)

    assert 'id="detail-panel"' in html
    assert 'id="detail-empty"' in html
    assert 'id="detail-content"' in html
    assert "const selectedState" in html


def test_generated_html_exports_relation_metadata(tmp_path):
    html = _render_graph_html(tmp_path)

    assert '"industry_context": "CPO"' in html
    assert '"country": "US"' in html
    assert '"tags": "photonics,cpo"' in html
    assert "function buildNodeDetails" in html


def test_generated_html_contains_detail_panel_renderers(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "function renderDetailPanel" in html
    assert "function renderRelationCard" in html
    assert "incomingRelations" in html
    assert "outgoingRelations" in html
    assert "detail-panel__title" in html


def test_generated_html_contains_selection_and_clear_rules(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "function applySelectionState" in html
    assert "selectedState.suppressClearUntil" in html
    assert "detail-close" in html
    assert 'svg.on("click"' in html or "svg.on('click'" in html
    assert "pointer-events: auto" in html


def test_generated_html_contains_compare_state_fields(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "primaryNodeId" in html
    assert "secondaryNodeId" in html
    assert "compareMode" in html
    assert "primaryRelationKey" in html
    assert "secondaryRelationKey" in html


def test_generated_html_contains_compare_actions_and_pending_renderer(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "startCompareMode" in html
    assert "exitCompareMode" in html
    assert "clearSecondaryNode" in html
    assert "renderComparePendingPanel" in html
    assert "Select another company to compare" in html
    assert "detail-panel__compare-trigger" in html


def test_generated_html_contains_compare_panel_markup_and_side_keys(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "renderComparePanel" in html
    assert "detail-panel__compare-grid" in html
    assert "detail-panel__compare-column" in html
    assert 'data-panel-side="${side}"' in html or "data-panel-side" in html
    assert "setSelectedRelation(side, relationCard.dataset.relationKey);" in html


def test_generated_html_compare_state_preserves_evidence_toggle_hooks(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "function toggleRelationEvidence" in html
    assert "selectedState.primaryNodeId" in html or "selectedState.secondaryNodeId" in html


def test_generated_html_compare_state_resets_filter_with_primary_and_secondary_nodes(tmp_path):
    html = _render_graph_html(tmp_path)
    apply_filter_block = _extract_html_segment(
        html,
        "function applyFilter(cat)",
        "bindDetailPanelEvents();",
    )

    assert "function applyFilter(cat)" in html
    assert "selectedState.primaryNodeId = null;" in apply_filter_block
    assert "selectedState.secondaryNodeId = null;" in apply_filter_block
    assert 'selectedState.compareMode = "off";' in apply_filter_block
    assert 'selectedState.neighborhoodMode = "off";' in apply_filter_block
    assert "selectedState.neighborhoodRelationKey = null;" in apply_filter_block


def test_generated_html_compare_state_guards_against_self_compare(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "nodeDatum.id === selectedState.primaryNodeId" in html


def test_generated_html_contains_compare_header_pair_summary_hooks(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "detail-panel__compare-summary" in html
    assert "detail-panel__compare-summary-item" in html
    assert "A:" in html
    assert "B:" in html


def test_generated_html_contains_swap_compare_action(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "swapCompareSides" in html
    assert 'data-action="swap-compare"' in html
    assert "Swap A/B" in html


def test_generated_html_contains_compare_active_column_and_badge_hooks(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "detail-panel__compare-badge" in html
    assert "detail-panel__compare-column--active" in html
    assert "selectedState.activeSide" in html


def test_generated_html_swap_compare_mentions_slot_based_clear_b_behavior(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "clear-secondary" in html
    assert "swapCompareSides" in html
    assert (
        "selectedState.primaryNodeId = selectedState.secondaryNodeId;" in html
        or "const nextPrimaryNodeId = selectedState.secondaryNodeId;" in html
    )


def test_generated_html_contains_neighborhood_state_fields(tmp_path):
    html = _render_graph_html(tmp_path)
    selected_state_block = _extract_html_segment(
        html,
        "const selectedState = {",
        "const expandedRelationKeys = new Set();",
    )

    assert 'neighborhoodMode: "off"' in selected_state_block
    assert "neighborhoodRelationKey: null" in selected_state_block


def test_generated_html_contains_neighborhood_status_bar_hooks(tmp_path):
    html = _render_graph_html(tmp_path)
    compare_panel_block = _extract_html_segment(
        html,
        "function renderComparePanel(primaryNode, secondaryNode)",
        "function renderDetailPanel(node)",
    )

    assert "detail-panel__neighborhood" in compare_panel_block
    assert "detail-panel__neighborhood-label" in compare_panel_block
    assert "detail-panel__neighborhood-summary" in compare_panel_block
    assert "detail-panel__neighborhood-meta" in compare_panel_block
    assert "Active:" in compare_panel_block
    assert 'data-action="exit-neighborhood"' in compare_panel_block
    assert "Exit Neighborhood" in compare_panel_block


def test_generated_html_relation_click_enters_neighborhood_mode(tmp_path):
    html = _render_graph_html(tmp_path)
    bind_detail_panel_events_block = _extract_html_segment(
        html,
        "function bindDetailPanelEvents()",
        "function selectNode(nodeDatum)",
    )
    set_selected_relation_block = _extract_html_segment(
        html,
        "function setSelectedRelation(side, relationKey)",
        "function applyBaseState(cat)",
    )

    assert 'const relationCard = event.target.closest(".relation-card");' in bind_detail_panel_events_block
    assert "setSelectedRelation(side, relationCard.dataset.relationKey);" in bind_detail_panel_events_block
    assert 'selectedState.compareMode === "active" && selectedState.secondaryNodeId' in set_selected_relation_block
    assert 'selectedState.neighborhoodMode = "relation";' in set_selected_relation_block
    assert "selectedState.neighborhoodRelationKey = relationKey;" in set_selected_relation_block
    assert "renderDetailPanel(nodeById.get(selectedState.primaryNodeId));" in set_selected_relation_block
    assert "applySelectionState();" in set_selected_relation_block


def test_generated_html_exit_neighborhood_clears_only_neighborhood_state(tmp_path):
    html = _render_graph_html(tmp_path)
    exit_neighborhood_block = _extract_html_segment(
        html,
        "function exitNeighborhoodMode()",
        "function exitCompareMode()",
    )

    assert 'selectedState.neighborhoodMode = "off";' in exit_neighborhood_block
    assert "selectedState.neighborhoodRelationKey = null;" in exit_neighborhood_block
    assert "renderDetailPanel(nodeById.get(selectedState.primaryNodeId));" in exit_neighborhood_block
    assert "applySelectionState();" in exit_neighborhood_block
    assert "selectedState.secondaryNodeId = null;" not in exit_neighborhood_block
    assert 'selectedState.compareMode = "off";' not in exit_neighborhood_block


def test_generated_html_clear_secondary_clears_neighborhood_state(tmp_path):
    html = _render_graph_html(tmp_path)
    clear_secondary_block = _extract_html_segment(
        html,
        "function clearSecondaryNode()",
        "function swapCompareSides()",
    )

    assert "selectedState.secondaryNodeId = null;" in clear_secondary_block
    assert "selectedState.secondaryRelationKey = null;" in clear_secondary_block
    assert 'selectedState.neighborhoodMode = "off";' in clear_secondary_block
    assert "selectedState.neighborhoodRelationKey = null;" in clear_secondary_block
    assert "applySelectionState();" in clear_secondary_block


def test_generated_html_exit_compare_clears_neighborhood_state(tmp_path):
    html = _render_graph_html(tmp_path)
    exit_compare_block = _extract_html_segment(
        html,
        "function exitCompareMode()",
        "function setSelectedRelation(side, relationKey)",
    )

    assert "selectedState.secondaryNodeId = null;" in exit_compare_block
    assert 'selectedState.compareMode = "off";' in exit_compare_block
    assert "selectedState.secondaryRelationKey = null;" in exit_compare_block
    assert 'selectedState.neighborhoodMode = "off";' in exit_compare_block
    assert "selectedState.neighborhoodRelationKey = null;" in exit_compare_block
    assert "applySelectionState();" in exit_compare_block


def test_generated_html_clear_selection_clears_neighborhood_state(tmp_path):
    html = _render_graph_html(tmp_path)
    clear_selection_block = _extract_html_segment(
        html,
        "function clearSelection()",
        "function applySelectionState()",
    )

    assert "selectedState.primaryNodeId = null;" in clear_selection_block
    assert "selectedState.secondaryNodeId = null;" in clear_selection_block
    assert 'selectedState.compareMode = "off";' in clear_selection_block
    assert 'selectedState.neighborhoodMode = "off";' in clear_selection_block
    assert "selectedState.neighborhoodRelationKey = null;" in clear_selection_block
    assert "hideDetailPanel();" in clear_selection_block
    assert "applySelectionState();" in clear_selection_block


def test_generated_html_replacing_compare_b_clears_neighborhood_state(tmp_path):
    html = _render_graph_html(tmp_path)
    replace_secondary_block = _extract_html_segment(
        html,
        'if (selectedState.compareMode === "active") {',
        "selectedState.primaryNodeId = nodeDatum.id;",
    )

    assert "selectedState.secondaryNodeId = nodeDatum.id;" in replace_secondary_block
    assert "selectedState.secondaryRelationKey = null;" in replace_secondary_block
    assert 'selectedState.neighborhoodMode = "off";' in replace_secondary_block
    assert "selectedState.neighborhoodRelationKey = null;" in replace_secondary_block
    assert "applySelectionState();" in replace_secondary_block


def test_generated_html_contains_neighborhood_graph_emphasis_hooks(tmp_path):
    html = _render_graph_html(tmp_path)
    build_neighborhood_state_block = _extract_html_segment(
        html,
        "function buildNeighborhoodState(relationKey)",
        "function applySelectionState()",
    )
    apply_selection_state_block = _extract_html_segment(
        html,
        "function applySelectionState()",
        "simulation.on",
    )

    assert "neighborhood-node" in html
    assert "neighborhood-edge" in html
    assert "neighborhood-dimmed" in html
    assert "focusRelationKey" in build_neighborhood_state_block
    assert "endpointIds" in build_neighborhood_state_block
    assert "neighborhoodNodeIds" in build_neighborhood_state_block
    assert "neighborhoodEdgeKeys" in build_neighborhood_state_block
    assert "buildNeighborhoodState(selectedState.neighborhoodRelationKey)" in apply_selection_state_block
    assert "selectedState.neighborhoodRelationKey" in apply_selection_state_block
    assert 'renderDetailPanel(nodeById.get(selectedState.primaryNodeId));' in apply_selection_state_block


def test_generated_html_relation_keys_use_stable_relation_ids(tmp_path):
    html = _render_graph_html(tmp_path)
    get_relation_key_block = _extract_html_segment(
        html,
        "function getRelationKey(relation)",
        "function toggleRelationEvidence(",
    )
    compare_panel_block = _extract_html_segment(
        html,
        "function renderComparePanel(primaryNode, secondaryNode)",
        "function renderDetailPanel(node)",
    )

    assert "relation.id" in get_relation_key_block
    assert "neighborhoodFocusLink" in compare_panel_block
    assert "neighborhoodSummary" in compare_panel_block
    assert "source.name" in compare_panel_block
    assert "target.name" in compare_panel_block


def test_generated_html_swap_compare_preserves_company_focus_by_flipping_active_side(
    tmp_path,
):
    html = _render_graph_html(tmp_path)

    assert (
        'selectedState.activeSide = selectedState.activeSide === "primary" ? "secondary" : "primary";'
        in html
        or "const nextActiveSide" in html
    )


def test_generated_html_swap_compare_moves_single_relation_key_with_company(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "primaryRelationKey" in html
    assert "secondaryRelationKey" in html
    assert (
        "const nextPrimaryRelationKey" in html
        or "selectedState.primaryRelationKey = selectedState.secondaryRelationKey;" in html
    )


def test_generated_html_swap_compare_preserves_expanded_relation_keys(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "expandedRelationKeys" in html
    assert (
        "const nextExpandedRelationKeys" in html
        or "new Set(expandedRelationKeys)" in html
    )


def test_generated_html_swap_compare_preserves_neighborhood_when_relation_survives(
    tmp_path,
):
    html = _render_graph_html(tmp_path)
    swap_compare_block = _extract_html_segment(
        html,
        "function swapCompareSides()",
        "function exitNeighborhoodMode()",
    )

    assert "selectedState.neighborhoodMode" in swap_compare_block
    assert "selectedState.neighborhoodRelationKey" in swap_compare_block
    assert "survivingRelationKeys" in swap_compare_block
    assert "nextNeighborhoodRelationKey" in swap_compare_block
    assert "survivingRelationKeys.has(selectedState.neighborhoodRelationKey)" in swap_compare_block
    assert "selectedState.neighborhoodRelationKey = nextNeighborhoodRelationKey;" in swap_compare_block


def test_generated_html_swap_compare_can_clear_neighborhood_when_relation_is_invalid(
    tmp_path,
):
    html = _render_graph_html(tmp_path)
    swap_compare_block = _extract_html_segment(
        html,
        "function swapCompareSides()",
        "function exitNeighborhoodMode()",
    )

    assert 'const nextNeighborhoodMode = nextNeighborhoodRelationKey ? "relation" : "off";' in swap_compare_block
    assert "selectedState.neighborhoodMode = nextNeighborhoodMode;" in swap_compare_block


def test_generated_html_contains_relation_card_linkage_and_mobile_styles(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "data-relation-key" in html
    assert "selectedState.primaryRelationKey" in html
    assert "selectedState.secondaryRelationKey" in html
    assert "@media (max-width: 960px)" in html
    assert "detail-panel--open" in html


def test_generated_html_search_click_uses_select_node_flow(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "selectNode(d);" in html


def test_generated_html_clear_selection_restores_current_filter_state(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "applyBaseState(currentCat);" in html


def test_generated_html_filter_changes_reset_detail_panel(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "hideDetailPanel();" in html


def test_generated_html_contains_evidence_card_fields(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "sourceTags" in html
    assert "summary" in html
    assert "snippet" in html
    assert "context" in html


def test_generated_html_renders_source_tags_summary_and_snippet(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "relation-card__sources" in html
    assert "relation-card__summary" in html
    assert "relation-card__snippet" in html
    assert "Twitter" in html


def test_generated_html_falls_back_to_inferred_when_evidence_missing(tmp_path):
    html = _render_graph_html(tmp_path, include_evidence=False)

    assert "Inferred" in html
    assert "Laser supplier" in html


def test_generated_html_evidence_cards_keep_relation_key_linkage(tmp_path):
    html = _render_graph_html(tmp_path)

    assert 'data-relation-key="' in html
    assert 'const side = relationCard.dataset.panelSide || "primary";' in html
    assert "setSelectedRelation(side, relationCard.dataset.relationKey);" in html


def test_generated_html_handles_missing_snippet_without_empty_block(tmp_path):
    html = _render_graph_html(tmp_path, include_evidence=True, evidence_snippet="")

    assert "relation-card__summary" in html
    assert "Twitter" in html


def test_generated_html_contains_evidences_array_for_relation(tmp_path):
    html = _render_graph_html(
        tmp_path,
        extra_evidences=[
            ("news", "Lumentum also appears in supply-chain reporting.", "2026-06-14T09:00:00+08:00"),
            ("twitter", "A second social thread reinforces the same relation.", "2026-06-13T09:00:00+08:00"),
        ],
    )

    assert "evidences" in html
    assert "sourceTag" in html or "source_tag" in html


def test_generated_html_renders_expand_control_for_multi_evidence_relations(tmp_path):
    html = _render_graph_html(
        tmp_path,
        extra_evidences=[
            ("news", "Lumentum also appears in supply-chain reporting.", "2026-06-14T09:00:00+08:00"),
        ],
    )

    assert "View all evidence" in html
    assert "relation-card__toggle" in html


def test_generated_html_hides_expand_control_for_single_evidence_relation(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "View all evidence (1)" not in html


def test_generated_html_extra_evidence_falls_back_to_summary_when_snippet_missing(tmp_path):
    html = _render_graph_html(
        tmp_path,
        extra_evidences=[
            ("news", "", "2026-06-14T09:00:00+08:00"),
        ],
    )

    assert "relation-card__evidence-row" in html
    assert "Relationship noted" in html or "Laser supplier" in html


def test_generated_html_contains_expanded_evidence_container_and_state_hook(tmp_path):
    html = _render_graph_html(
        tmp_path,
        extra_evidences=[
            ("news", "Lumentum also appears in supply-chain reporting.", "2026-06-14T09:00:00+08:00"),
        ],
    )

    assert "expandedRelationKeys" in html
    assert "relation-card__extra-evidences" in html
    assert "toggleRelationEvidence" in html


def test_generated_html_contains_compare_responsive_layout_classes(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "detail-panel__compare-grid" in html
    assert "detail-panel__compare-column" in html
    assert "@media (max-width: 960px)" in html


def test_generated_html_routes_relation_clicks_by_panel_side(tmp_path):
    html = _render_graph_html(tmp_path)

    assert 'const side = relationCard.dataset.panelSide || "primary";' in html
    assert "setSelectedRelation(side, relationCard.dataset.relationKey);" in html


def test_generated_html_filter_reset_clears_compare_state(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "selectedState.primaryNodeId = null;" in html
    assert "selectedState.secondaryNodeId = null;" in html
    assert 'selectedState.compareMode = "off";' in html


def test_generated_html_select_node_ignores_primary_self_compare(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "if (nodeDatum.id === selectedState.primaryNodeId) {" in html
    assert "return;" in html


def test_generated_html_renders_status_and_confidence_badges(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "relation-card__signals" in html
    assert "status-badge" in html
    assert "confidence-badge" in html
    assert "Evidence" in html
    assert "Math.round((Number(relation.confidence || 0)) * 100)" in html


def test_generated_html_marks_inferred_relations_in_status_badge(tmp_path):
    html = _render_graph_html(tmp_path, include_evidence=False)

    assert "Inferred" in html
    assert "status-badge" in html
