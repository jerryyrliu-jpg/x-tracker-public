import json
import re
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


def _extract_graph_nodes(html: str) -> dict[str, dict]:
    match = re.search(r"const data = (\{.*?\});\s*const selectedState =", html, re.S)
    assert match, "graph payload not found in generated HTML"
    payload = json.loads(match.group(1))
    return {node["name"]: node for node in payload["tiers"]}


def _extract_graph_data(html: str) -> dict:
    match = re.search(r"const data = (\{.*?\});\s*const selectedState =", html, re.S)
    assert match, "graph payload not found in generated HTML"
    return json.loads(match.group(1))


def _extract_named_links(html: str) -> set[tuple[str, str, str]]:
    data = _extract_graph_data(html)
    id_to_name = {node["id"]: node["name"] for node in data["tiers"]}
    return {
        (id_to_name.get(link["source"]), id_to_name.get(link["target"]), link.get("role") or "")
        for link in data["links"]
    }


def _extract_html_segment(html: str, start_anchor: str, end_anchor: str) -> str:
    start = html.index(start_anchor)
    end = html.index(end_anchor, start)
    return html[start:end]


def _extract_data_json(html: str) -> str:
    marker = "const data = "
    start = html.index(marker) + len(marker)
    end = html.index(";\n        const selectedState", start)
    return html[start:end]


def test_generated_html_contains_detail_panel_shell(tmp_path):
    html = _render_graph_html(tmp_path)

    assert 'id="detail-panel"' in html
    assert 'id="detail-empty"' in html
    assert 'id="detail-content"' in html
    assert "const selectedState" in html


def test_detail_panel_is_independently_scrollable(tmp_path):
    # .detail-panel is pinned to height: 100vh with no overflow rule, and
    # <body> sets overflow: hidden -- content taller than the viewport (a
    # node with many upstream/downstream evidence cards) becomes completely
    # unreachable, not just scrolled, regardless of window size.
    html = _render_graph_html(tmp_path)
    style_block = _extract_html_segment(html, "<style>", "</style>")
    detail_panel_rule = _extract_html_segment(style_block, ".detail-panel {", "}")

    assert "overflow-y: auto" in detail_panel_rule or "overflow-y:auto" in detail_panel_rule


def test_generated_html_includes_inline_favicon_to_avoid_local_404(tmp_path):
    html = _render_graph_html(tmp_path)

    assert '<link rel="icon" href="data:,">' in html


def test_generated_html_writes_favicon_into_output_directory(tmp_path):
    _render_graph_html(tmp_path)

    favicon_path = tmp_path / "cpo_chain" / "output" / "favicon.ico"
    assert favicon_path.exists()
    assert favicon_path.stat().st_size > 0


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
    assert "detail-panel__taxonomy" in html
    assert "function renderNodeTaxonomy" in html


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
        "function applyFilters(role, domain)",
        "bindDetailPanelEvents();",
    )

    assert "function applyFilters(role, domain)" in html
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
        "function nodeMatchesActiveFilters(nodeDatum, role, domain, infra)",
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

    assert "applyBaseState(currentRole, currentDomain, currentInfra);" in html


def test_generated_html_filter_changes_reset_detail_panel(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "hideDetailPanel();" in html


def test_generated_html_contains_active_filter_summary_and_clear_action(tmp_path):
    html = _render_graph_html(tmp_path)

    assert 'id="active-filters"' in html
    assert 'id="clear-filters"' in html
    assert "active-filter-badge" in html
    assert "function renderActiveFilters()" in html
    assert "clearFiltersBtn.addEventListener(\"click\"" in html
    assert ".clear-filters {" in html
    assert "box-shadow:" in html


def test_generated_html_render_active_filters_updates_panel_copy(tmp_path):
    html = _render_graph_html(tmp_path)
    filter_block = _extract_html_segment(
        html,
        "function renderActiveFilters()",
        "function applyFilters(",
    )

    assert 'const activeFiltersEl = document.getElementById("active-filters");' in html
    assert 'const clearFiltersBtn = document.getElementById("clear-filters");' in html
    assert 'const activeFilters = [];' in filter_block
    assert 'activeFilters.push(`Role: ${roleLabel}`);' in filter_block
    assert 'activeFilters.push(`Domain: ${domainLabel}`);' in filter_block
    assert 'activeFilters.push(`Infra: ${infraLabel}`);' in filter_block
    assert 'activeFiltersEl.innerHTML = activeFilters.length' in filter_block
    assert 'activeFilters.map(item => `<span class="active-filter-badge">${escapeHtml(item)}</span>`).join("")' in filter_block
    assert "clearFiltersBtn.hidden = activeFilters.length === 0;" in filter_block


def test_generated_html_uses_typed_taxonomy_badges(tmp_path):
    html = _render_graph_html(tmp_path)

    assert ".taxonomy-badge--role" in html
    assert ".taxonomy-badge--domain" in html
    assert ".taxonomy-badge--infra" in html
    assert "function renderTaxonomyRow(label, values, type)" in html
    assert 'class="taxonomy-badge taxonomy-badge--${type}"' in html


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


def test_generated_html_exports_role_and_domain_tags(tmp_path):
    html = _render_graph_html(tmp_path)

    assert '"role_tags"' in html
    assert '"domain_tags"' in html


def test_generated_html_applies_domain_override_for_agility_robotics(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "Agility Robotics", None, "US", None),
        ],
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, 2, "Technology Partner", 0.8, "General Tech", "active"),
    )
    conn.commit()
    conn.close()

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
    html = html_path.read_text(encoding="utf-8")

    assert '"name": "Agility Robotics"' in html
    assert '"domain_tags": ["robotics", "embodied_ai"]' in html


def test_generated_html_preserves_existing_role_tags_when_override_adds_domain_tags(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "Ayar Labs", None, "US", "photonics"),
        ],
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 2, 1, "Optical I/O Supplier", 0.8, "CPO", "active"),
    )
    conn.commit()
    conn.close()

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
    html = html_path.read_text(encoding="utf-8")

    assert '"name": "Ayar Labs"' in html
    assert '"role_tags": ["photonics"]' in html
    assert '"domain_tags": ["embodied_ai"]' in html


def test_generated_html_exports_infra_tags_and_node_kind(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "Oracle", None, "US", None),
            (3, "Hyperscalers", None, "Global", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "data center provider", 0.8, "Data Center", "active"),
            (2, 1, 3, "ASIC supplier", 0.8, "AI Server", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    assert nodes["Oracle"]["infra_tags"] == ["cloud_hosting"]
    assert nodes["Oracle"]["node_kind"] == "company"
    assert nodes["Hyperscalers"]["infra_tags"] == ["market_bucket"]
    assert nodes["Hyperscalers"]["node_kind"] == "market_bucket"


def test_generated_html_preserves_role_domain_when_infra_tags_are_added(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "Nebius", None, "NL", None),
        ],
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 2, 1, "GPU cloud provider", 0.8, "AI Server", "active"),
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    assert nodes["Nebius"]["role_tags"] == ["neocloud"]
    assert nodes["Nebius"]["domain_tags"] == []
    assert nodes["Nebius"]["infra_tags"] == ["cloud_hosting"]
    assert nodes["Nebius"]["node_kind"] == "company"


def test_generated_html_renders_infrastructure_filter_group(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "Infrastructure / Ecosystem" in html
    assert 'data-group="infra"' in html
    assert 'data-infra="all"' in html
    assert 'data-infra="power_infra"' in html
    assert 'data-infra="telecom"' in html
    assert 'data-infra="cloud_hosting"' in html
    assert 'data-infra="market_bucket"' in html


def test_generated_html_tracks_current_infra_state(tmp_path):
    html = _render_graph_html(tmp_path)

    assert 'let currentInfra = "all";' in html
    assert "applyBaseState(currentRole, currentDomain, currentInfra)" in html
    assert 'const nextInfra = group === "infra" ? chip.dataset.infra : currentInfra;' in html


def test_generated_html_infra_filter_preserves_tier0_and_one_hop_behavior(tmp_path):
    html = _render_graph_html(tmp_path)
    apply_base_state_block = _extract_html_segment(
        html,
        "function applyBaseState(role, domain, infra) {",
        "function setBaseFilterState(",
    )

    assert (
        "const matchedNodes = nodes.filter(n => nodeMatchesActiveFilters(n, role, domain, infra));"
        in apply_base_state_block
    )
    assert (
        "const tierZeroIds = new Set(nodes.filter(n => n.tier === 0).map(n => n.id));"
        in apply_base_state_block
    )
    assert "const visibleNodeIds = new Set([...tierZeroIds, ...matchedNodeIds]);" in apply_base_state_block
    assert (
        "if (matchedNodeIds.has(link.source.id) || matchedNodeIds.has(link.target.id)) {"
        in apply_base_state_block
    )
    assert "visibleNodeIds.add(link.source.id);" in apply_base_state_block
    assert "visibleNodeIds.add(link.target.id);" in apply_base_state_block


def test_generated_html_includes_market_bucket_styling_hooks(tmp_path):
    html = _render_graph_html(tmp_path)

    assert ".node.market-bucket circle" in html
    assert ".node.market-bucket text" in html
    assert 'classed("market-bucket", d => d.node_kind === "market_bucket")' in html


def test_generated_html_frontend_nodes_include_node_kind_and_infra_tags(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "Hyperscalers", None, "Global", None),
        ],
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, 2, "ASIC supplier", 0.8, "AI Server", "active"),
    )
    conn.commit()
    conn.close()

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
    html = html_path.read_text(encoding="utf-8")
    nodes_block = _extract_html_segment(
        html,
        "const nodes = data.tiers.map(d => ({",
        "const nodeById = new Map(",
    )

    assert 'infra_tags: Array.isArray(d.infra_tags) ? d.infra_tags : []' in nodes_block
    assert 'node_kind: d.node_kind || "company"' in nodes_block


def test_generated_html_contains_role_and_domain_filter_groups(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "Supply Chain Role" in html
    assert "Application / End Market" in html
    assert 'data-filter-group="role"' in html
    assert 'data-filter-group="domain"' in html
    assert 'data-role="all"' in html
    assert 'data-domain="all"' in html


def test_generated_html_contains_dual_filter_state_variables(tmp_path):
    html = _render_graph_html(tmp_path)

    assert 'let currentRole = "all";' in html
    assert 'let currentDomain = "all";' in html
    assert "function applyFilters(" in html


def test_generated_html_dual_filter_keeps_tier_zero_anchor_nodes_visible(tmp_path):
    html = _render_graph_html(tmp_path)

    assert "nodeDatum.tier === 0" in html or "n.tier === 0" in html or "n.tier==0" in html


def test_generated_html_search_restores_dual_filter_state_and_respects_active_filters(tmp_path):
    html = _render_graph_html(tmp_path)
    search_block = _extract_html_segment(
        html,
        'searchInput.addEventListener("input", () => {',
        'resultsDiv.querySelectorAll(".result-item")',
    )

    assert "nodeMatchesActiveFilters(n, currentRole, currentDomain, currentInfra)" in search_block
    assert '(n.role_tags || []).join(" ")' in search_block
    assert '(n.domain_tags || []).join(" ")' in search_block
    assert '(n.infra_tags || []).join(" ")' in search_block
    assert "applyBaseState(currentRole, currentDomain, currentInfra)" in search_block


def test_generated_html_filter_keeps_one_hop_neighbors_of_matched_nodes(tmp_path):
    html = _render_graph_html(tmp_path)
    apply_base_state_block = _extract_html_segment(
        html,
        "function applyBaseState(role, domain, infra)",
        "function clearSelection()",
    )

    assert "const matchedNodeIds = new Set" in apply_base_state_block
    assert "const visibleNodeIds = new Set([...tierZeroIds, ...matchedNodeIds]);" in apply_base_state_block
    assert "visibleNodeIds.add(link.source.id);" in apply_base_state_block
    assert "visibleNodeIds.add(link.target.id);" in apply_base_state_block


def test_generated_html_filter_highlights_matched_nodes_and_edges_more_clearly(tmp_path):
    html = _render_graph_html(tmp_path)
    apply_base_state_block = _extract_html_segment(
        html,
        "function applyBaseState(role, domain, infra)",
        "function clearSelection()",
    )

    assert '.node.highlighted circle' in html
    assert '.link.highlighted' in html
    assert "classed(\"highlighted\", n => matchedNodeIds.has(n.id))" in apply_base_state_block
    assert "classed(\"highlighted\", l => matchedNodeIds.has(l.source.id) && matchedNodeIds.has(l.target.id))" in apply_base_state_block


def test_generated_html_filter_softens_visible_neighbors_that_do_not_match_active_filters(tmp_path):
    html = _render_graph_html(tmp_path)
    apply_base_state_block = _extract_html_segment(
        html,
        "function applyBaseState(role, domain, infra)",
        "function clearSelection()",
    )

    assert '.node.context-node circle' in html
    assert '.node.context-node text' in html
    assert '.link.context-link' in html
    assert "const contextNodeIds = new Set([...visibleNodeIds].filter(id => !matchedNodeIds.has(id)));" in apply_base_state_block
    assert 'classed("context-node", n => contextNodeIds.has(n.id))' in apply_base_state_block
    assert (
        'classed("context-link", l => visibleNodeIds.has(l.source.id) && visibleNodeIds.has(l.target.id) && !(matchedNodeIds.has(l.source.id) && matchedNodeIds.has(l.target.id)))'
        in apply_base_state_block
    )


def test_generated_html_uses_thinner_default_links_but_keeps_emphasis_states(tmp_path):
    html = _render_graph_html(tmp_path)

    assert '.link { stroke: #adb5bd; stroke-opacity: 0.5; stroke-width: 0.65px;' in html
    assert '.marker { fill: #adb5bd; opacity: 0.78; }' in html
    assert '.link.highlighted { stroke: #f08c4a; stroke-opacity: 0.95; stroke-width: 2px; }' in html
    assert '.link.neighborhood-edge { opacity: 0.82; stroke-width: 1.6px; }' in html
    assert '.link.neighborhood-focus { opacity: 1; stroke: #f08c4a; stroke-width: 2.6px; }' in html
    assert '.attr("markerWidth",4).attr("markerHeight",4)' in html


def test_generated_html_uses_stronger_active_chip_emphasis(tmp_path):
    html = _render_graph_html(tmp_path)

    assert '.chip.active {' in html
    assert 'font-weight: 700;' in html
    assert 'border-color: rgba(33,37,41,0.24);' in html
    assert 'box-shadow: 0 2px 6px rgba(33,37,41,0.10);' in html
    assert 'transform: translateY(-1px);' in html


def test_generated_html_applies_additional_high_value_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "MediaTek", None, "TW", None),
            (3, "Anduril", None, "US", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "Chip Partner", 0.8, "AI", "active"),
            (2, 3, 1, "Defense AI Partner", 0.8, "General Tech", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    html = html_path.read_text(encoding="utf-8")

    assert '"name": "MediaTek"' in html
    assert '"role_tags": ["ai_chip"]' in html
    assert '"name": "Anduril"' in html
    assert '"domain_tags": ["aerospace"]' in html


def test_generated_html_applies_alias_and_clear_industry_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "ASTS", None, "US", None),
            (3, "Boston Dynamics", None, "US", None),
            (4, "Micron Technology", "MU", "US", None),
            (5, "Sivers Photonics", None, "SE", None),
            (6, "Win Semi", None, "TW", None),
            (7, "Xintec", None, "TW", None),
            (8, "Tesla", "TSLA", "US", None),
            (9, "Innolight", None, "CN", None),
            (10, "IREN", None, "AU", None),
            (11, "Samsung Electro-Mechanics", None, "KR", None),
            (12, "Compeq", None, "TW", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "satellite connectivity partner", 0.8, "Wireless Infrastructure", "active"),
            (2, 3, 1, "robotics platform user", 0.8, "Robotics", "active"),
            (3, 4, 1, "HBM supplier", 0.8, "HBM", "active"),
            (4, 5, 1, "laser supplier", 0.8, "Silicon Photonics", "active"),
            (5, 6, 1, "foundry partner", 0.8, "Semiconductor", "active"),
            (6, 7, 1, "packaging/test partner", 0.8, "CPO", "active"),
            (7, 8, 1, "Optimus humanoid platform", 0.8, "General Tech", "active"),
            (8, 9, 1, "optical transceiver components supplier", 0.8, "Optical Networking", "active"),
            (9, 10, 1, "GPU cloud contract provider", 0.8, "AI Server", "active"),
            (10, 11, 1, "glass substrate supplier", 0.8, "Semiconductor", "active"),
            (11, 12, 1, "PCB supplier", 0.8, "Aerospace", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    assert "ASTS" not in nodes  # alias-merged to canonical name
    assert nodes["AST SpaceMobile"]["domain_tags"] == ["aerospace"]
    assert nodes["Boston Dynamics"]["domain_tags"] == ["robotics", "embodied_ai"]
    assert nodes["Micron Technology"]["role_tags"] == ["hbm"]
    assert nodes["Sivers Photonics"]["role_tags"] == ["photonics"]
    assert nodes["Win Semi"]["role_tags"] == ["foundry"]
    assert nodes["Xintec"]["role_tags"] == ["packaging"]
    assert nodes["Tesla"]["domain_tags"] == ["embodied_ai"]
    assert nodes["Innolight"]["role_tags"] == ["photonics"]
    assert nodes["IREN"]["role_tags"] == ["neocloud"]
    assert nodes["Samsung Electro-Mechanics"]["role_tags"] == ["material"]
    assert nodes["Compeq"]["domain_tags"] == ["aerospace"]


def test_generated_html_applies_batch6_cloud_and_infra_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "AWS", None, "US", None),
            (3, "Amazon Web Services", None, "US", None),
            (4, "Alphabet (Google Cloud)", None, "US", None),
            (5, "Iris Energy", "IREN", "AU", None),
            (6, "Nebius Group", "NBIS", "NL", None),
            (7, "SK Telecom", None, "KR", None),
            (8, "Ericsson", None, "SE", None),
            (9, "Nokia", "NOK", "FI", None),
            (10, "Bloom Energy", None, "US", None),
            (11, "Fluence Energy", None, "US", None),
            (12, "Power Integrations", None, "US", None),
            (13, "Motivair", None, "US", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "cloud partner", 0.8, "AI Infrastructure", "active"),
            (2, 3, 1, "cloud partner", 0.8, "AI Infrastructure", "active"),
            (3, 4, 1, "cloud partner", 0.8, "AI Infrastructure", "active"),
            (4, 5, 1, "GPU cloud operator", 0.8, "AI Infrastructure", "active"),
            (5, 6, 1, "GPU cloud operator", 0.8, "AI Infrastructure", "active"),
            (6, 7, 1, "telco partner", 0.8, "Wireless Infrastructure", "active"),
            (7, 8, 1, "networking partner", 0.8, "Wireless Infrastructure", "active"),
            (8, 9, 1, "networking partner", 0.8, "Wireless Infrastructure", "active"),
            (9, 10, 1, "power infrastructure partner", 0.8, "Data Center Power", "active"),
            (10, 11, 1, "power infrastructure partner", 0.8, "Data Center Power", "active"),
            (11, 12, 1, "power infrastructure component supplier", 0.8, "Data Center Power", "active"),
            (12, 13, 1, "thermal management supplier", 0.8, "Data Center Power", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    assert nodes["AWS"]["infra_tags"] == ["cloud_hosting"]
    assert nodes["Amazon Web Services"]["infra_tags"] == ["cloud_hosting"]
    assert nodes["Alphabet (Google Cloud)"]["infra_tags"] == ["cloud_hosting"]
    assert nodes["Iris Energy"]["role_tags"] == ["neocloud"]
    assert nodes["Iris Energy"]["infra_tags"] == ["cloud_hosting"]
    assert nodes["Nebius Group"]["role_tags"] == ["neocloud"]
    assert nodes["Nebius Group"]["infra_tags"] == ["cloud_hosting"]
    assert nodes["SK Telecom"]["infra_tags"] == ["telecom"]
    assert nodes["Ericsson"]["infra_tags"] == ["telecom"]
    assert nodes["Nokia"]["infra_tags"] == ["telecom"]
    assert nodes["Bloom Energy"]["infra_tags"] == ["power_infra"]
    assert nodes["Fluence Energy"]["infra_tags"] == ["power_infra"]
    assert nodes["Power Integrations"]["infra_tags"] == ["power_infra"]
    assert nodes["Motivair"]["infra_tags"] == ["power_infra"]


def test_generated_html_applies_batch6_market_bucket_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "AI Data Centers", None, "Global", None),
            (3, "Datacenter Customers", None, "Global", None),
            (4, "Power Infrastructure Market", None, "Global", None),
            (5, "Optical Companies", None, "Global", None),
            (6, "Packaging Companies", None, "Global", None),
            (7, "Photonics Supply Chain", None, "Global", None),
            (8, "Semiconductor Industry", None, "Global", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "market demand", 0.8, "AI Infrastructure", "active"),
            (2, 3, 1, "market demand", 0.8, "AI Infrastructure", "active"),
            (3, 4, 1, "market context", 0.8, "Data Center Power", "active"),
            (4, 5, 1, "peer group", 0.8, "Silicon Photonics", "active"),
            (5, 6, 1, "peer group", 0.8, "Packaging", "active"),
            (6, 7, 1, "ecosystem group", 0.8, "Silicon Photonics", "active"),
            (7, 8, 1, "industry context", 0.8, "Semiconductor", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    assert nodes["AI Data Centers"]["infra_tags"] == ["market_bucket"]
    assert nodes["AI Data Centers"]["node_kind"] == "market_bucket"
    assert nodes["Datacenter Customers"]["infra_tags"] == ["market_bucket"]
    assert nodes["Datacenter Customers"]["node_kind"] == "market_bucket"
    assert nodes["Power Infrastructure Market"]["infra_tags"] == ["market_bucket"]
    assert nodes["Power Infrastructure Market"]["node_kind"] == "market_bucket"
    assert nodes["Optical Companies"]["infra_tags"] == ["market_bucket"]
    assert nodes["Optical Companies"]["node_kind"] == "market_bucket"
    assert nodes["Packaging Companies"]["infra_tags"] == ["market_bucket"]
    assert nodes["Packaging Companies"]["node_kind"] == "market_bucket"
    assert nodes["Photonics Supply Chain"]["infra_tags"] == ["market_bucket"]
    assert nodes["Photonics Supply Chain"]["node_kind"] == "market_bucket"
    assert nodes["Semiconductor Industry"]["infra_tags"] == ["market_bucket"]
    assert nodes["Semiconductor Industry"]["node_kind"] == "market_bucket"


def test_generated_html_applies_batch7_alias_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "Alphabet (GOOGL)", None, "US", None),
            (3, "Coherent (COHR)", None, "US", None),
            (4, "FOCI Fiber Optic Communications", None, "CN", None),
            (5, "Innolight Technology", None, "CN", None),
            (6, "Lite-On", None, "TW", None),
            (7, "Lite-On Technology", None, "TW", None),
            (8, "NBIS", None, "NL", None),
                (9, "Nokia (Internal Facility)", None, "FI", None),
                (10, "Supermicro", None, "US", None),
                (11, "ASE Technology Holding", None, "TW", None),
                (12, "Applied Materials", None, "US", None),
                (13, "ASE Group", None, "TW", None),
                (14, "Nippon Chemical Industry", None, "JP", None),
            ],
        )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "hyperscale partner", 0.8, "Cloud", "active"),
            (2, 3, 1, "optical component supplier", 0.8, "Silicon Photonics", "active"),
            (3, 4, 1, "fiber optic supplier", 0.8, "Silicon Photonics", "active"),
            (4, 5, 1, "optical transceiver supplier", 0.8, "Optical Networking", "active"),
            (5, 6, 1, "optical module partner", 0.8, "CPO", "active"),
            (6, 7, 1, "optical module partner", 0.8, "CPO", "active"),
            (7, 8, 1, "gpu cloud operator", 0.8, "AI Infrastructure", "active"),
            (8, 9, 1, "network equipment partner", 0.8, "Wireless Infrastructure", "active"),
            (9, 10, 1, "ai server supplier", 0.8, "AI Server", "active"),
            (10, 11, 1, "advanced packaging partner", 0.8, "Packaging", "active"),
            (11, 12, 1, "process equipment supplier", 0.8, "Packaging", "active"),
            (12, 13, 1, "memory supplier", 0.8, "HBM", "active"),
            (13, 14, 1, "red phosphorous supplier", 0.8, "Silicon Photonics", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    # canonical_alias_name() strips the trailing " (TICKER)" parenthetical
    # unconditionally (ticker-suffix stripping is safe at any scope)
    assert "Alphabet (GOOGL)" not in nodes
    assert nodes["Alphabet"]["role_tags"] == ["hyperscaler"]
    assert "Coherent (COHR)" not in nodes
    assert nodes["Coherent"]["role_tags"] == ["photonics"]
    # FOCI is merged via the explicit alias map (curated, deterministic)
    assert "FOCI Fiber Optic Communications" not in nodes
    assert nodes["FOCI"]["role_tags"] == ["photonics"]  # override survives via original-name tag resolution
    assert nodes["Innolight Technology"]["role_tags"] == ["photonics"]
    # "Lite-On" and "Lite-On Technology" are distinct DB rows and must stay
    # distinct: no prefix-shortening heuristic is applied graph-wide.
    assert nodes["Lite-On"]["role_tags"] == ["photonics"]
    assert nodes["Lite-On Technology"]["role_tags"] == ["photonics"]
    assert nodes["NBIS"]["role_tags"] == ["neocloud"]
    assert nodes["NBIS"]["infra_tags"] == ["cloud_hosting"]
    assert nodes["Nokia (Internal Facility)"]["infra_tags"] == ["telecom"]
    assert nodes["Supermicro"]["role_tags"] == ["ai_server"]
    assert nodes["ASE Technology Holding"]["role_tags"] == ["packaging"]
    assert nodes["Applied Materials"]["role_tags"] == ["packaging"]
    assert nodes["ASE Group"]["role_tags"] == ["packaging"]
    assert nodes["Nippon Chemical Industry"]["role_tags"] == ["material"]


def test_generated_html_applies_batch8_group_bucket_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "All Businesses", None, "Global", None),
            (3, "AI Chip Manufacturers", None, "Global", None),
            (4, "Epiwafer manufacturers", None, "Global", None),
            (5, "Glass substrate manufacturers", None, "Global", None),
            (6, "Global Silicon Photonics Leader", None, "Global", None),
            (7, "Major Hyperscale Customer", None, "Global", None),
            (8, "Major Silicon Photonics Customer", None, "Global", None),
            (9, "Photonics Industry", None, "Global", None),
            (10, "Silicon Photonics Manufacturers", None, "Global", None),
            (11, "Space Applications", None, "Global", None),
            (12, "upstream semi supply chain companies", None, "Global", None),
            (13, "US big tech", None, "US", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "business segment", 0.8, "Enterprise", "active"),
            (2, 3, 1, "peer group", 0.8, "AI Chip", "active"),
            (3, 4, 1, "materials peer group", 0.8, "Semiconductor", "active"),
            (4, 5, 1, "substrate peer group", 0.8, "Packaging", "active"),
            (5, 6, 1, "ecosystem leader", 0.8, "Silicon Photonics", "active"),
            (6, 7, 1, "customer bucket", 0.8, "Cloud", "active"),
            (7, 8, 1, "customer bucket", 0.8, "Silicon Photonics", "active"),
            (8, 9, 1, "industry grouping", 0.8, "Photonics", "active"),
            (9, 10, 1, "manufacturer grouping", 0.8, "Silicon Photonics", "active"),
            (10, 11, 1, "application grouping", 0.8, "Aerospace", "active"),
            (11, 12, 1, "supply chain grouping", 0.8, "Semiconductor", "active"),
            (12, 13, 1, "customer grouping", 0.8, "Cloud", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    for name in [
        "All Businesses",
        "AI Chip Manufacturers",
        "Epiwafer manufacturers",
        "Glass substrate manufacturers",
        "Global Silicon Photonics Leader",
        "Major Hyperscale Customer",
        "Major Silicon Photonics Customer",
        "Photonics Industry",
        "Silicon Photonics Manufacturers",
        "Space Applications",
        "upstream semi supply chain companies",
        "US big tech",
    ]:
        assert nodes[name]["infra_tags"] == ["market_bucket"]
        assert nodes[name]["node_kind"] == "market_bucket"


def test_generated_html_applies_batch9_conservative_group_bucket_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "Liquid cooling pump manufacturers", None, "Global", None),
            (3, "Server rack manufacturers", None, "Global", None),
            (4, "pluggable optical transceiver companies", None, "Global", None),
            (5, "SiPh Upstream Partners", None, "Global", None),
            (6, "Robotic supply chain", None, "Global", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "cooling supplier group", 0.8, "Data Center Cooling", "active"),
            (2, 3, 1, "rack supplier group", 0.8, "AI Infrastructure", "active"),
            (3, 4, 1, "optical supplier group", 0.8, "Optical Networking", "active"),
            (4, 5, 1, "ecosystem partner group", 0.8, "Silicon Photonics", "active"),
            (5, 6, 1, "robotics ecosystem group", 0.8, "Robotics", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    for name in [
        "Liquid cooling pump manufacturers",
        "Server rack manufacturers",
        "pluggable optical transceiver companies",
        "SiPh Upstream Partners",
        "Robotic supply chain",
    ]:
        assert nodes[name]["infra_tags"] == ["market_bucket"]
        assert nodes[name]["node_kind"] == "market_bucket"


def test_generated_html_applies_batch10_abstract_group_bucket_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "Three Global Memory Giants", None, "Global", None),
            (3, "Unknown Optical Transceiver Company", None, "Global", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "memory peer group", 0.8, "HBM", "active"),
            (2, 3, 1, "optical supplier bucket", 0.8, "Optical Networking", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    for name in [
        "Three Global Memory Giants",
        "Unknown Optical Transceiver Company",
    ]:
        assert nodes[name]["infra_tags"] == ["market_bucket"]
        assert nodes[name]["node_kind"] == "market_bucket"


def test_generated_html_applies_batch12_aerospace_domain_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (1, "Anduril", "ANDR", "US", None),
            (2, "Electro Optic Systems", None, "AU", None),
            (3, "BAE Systems", "BAE", "UK", None),
            (4, "Raytheon", None, "US", None),
            (5, "SpektreWorks", None, "US", None),
            (6, "Gen5", None, "US", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 6, "counter-drone system provider", 0.8, "General Tech", "active"),
            (2, 3, 2, "command and control provider (AI brain) for counter-drone system", 0.8, "General Tech", "active"),
            (3, 4, 1, "defense contract supplier", 0.8, "Wireless Infrastructure", "active"),
            (4, 5, 1, "drone component/integration provider for LUCAS", 0.8, "General Tech", "active"),
        ],
    )
    conn.commit()
    conn.close()

    output_dir = tmp_path / "cpo_chain" / "output"
    output_dir.mkdir(parents=True)
    keywords_path = tmp_path / "cpo_chain" / "keywords.yaml"
    keywords_path.parent.mkdir(parents=True, exist_ok=True)
    keywords_path.write_text("root_tickers:\n  - ANDR\n  - BAE\n", encoding="utf-8")
    html_path = output_dir / "index.html"

    module.generate_graph_html(
        base_dir=tmp_path,
        db_path=db_path,
        html_path=html_path,
        keywords_path=keywords_path,
    )
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    for name in [
        "Electro Optic Systems",
        "BAE Systems",
        "Raytheon",
        "SpektreWorks",
    ]:
        assert nodes[name]["domain_tags"] == ["aerospace"]


def test_generated_html_applies_batch13_moderate_aerospace_domain_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (1, "BAE Systems", "BAE", "UK", None),
            (2, "Electro Optic Systems", None, "AU", None),
            (3, "Gen5", None, "US", None),
            (4, "Lockheed Martin", "LMT", "US", None),
            (5, "AMPG", None, "US", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 1, 2, "command and control provider (AI brain) for counter-drone system", 0.8, "General Tech", "active"),
            (2, 2, 3, "counter-drone system provider", 0.8, "General Tech", "active"),
            (3, 5, 4, "defense contract supplier", 0.8, "Wireless Infrastructure", "active"),
        ],
    )
    conn.commit()
    conn.close()

    output_dir = tmp_path / "cpo_chain" / "output"
    output_dir.mkdir(parents=True)
    keywords_path = tmp_path / "cpo_chain" / "keywords.yaml"
    keywords_path.parent.mkdir(parents=True, exist_ok=True)
    keywords_path.write_text("root_tickers:\n  - BAE\n  - LMT\n", encoding="utf-8")
    html_path = output_dir / "index.html"

    module.generate_graph_html(
        base_dir=tmp_path,
        db_path=db_path,
        html_path=html_path,
        keywords_path=keywords_path,
    )
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    # "Gen5" is filtered as a known low-confidence noise entity (see
    # cpo_chain/normalization.PLACEHOLDER_NODES), not rendered under any override
    assert "Gen5" not in nodes
    assert nodes["AMPG"]["domain_tags"] == ["aerospace"]


def test_generated_html_applies_batch14_robotics_domain_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (1, "Amazon", "AMZN", "US", "hyperscaler"),
            (2, "Nextronics", None, "TW", "photonics"),
            (3, "Boston Dynamics", "BOSTON", "US", None),
            (4, "LG", None, "KR", None),
            (5, "LG Innotek", None, "KR", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "robotics supplier", 0.8, "Robotics", "active"),
            (2, 4, 3, "Vision supplier", 0.8, "Robotics", "active"),
            (3, 5, 3, "robotics module/sensor assembly supplier", 0.8, "Wireless Infrastructure", "active"),
        ],
    )
    conn.commit()
    conn.close()

    output_dir = tmp_path / "cpo_chain" / "output"
    output_dir.mkdir(parents=True)
    keywords_path = tmp_path / "cpo_chain" / "keywords.yaml"
    keywords_path.parent.mkdir(parents=True, exist_ok=True)
    keywords_path.write_text("root_tickers:\n  - AMZN\n  - BOSTON\n", encoding="utf-8")
    html_path = output_dir / "index.html"

    module.generate_graph_html(
        base_dir=tmp_path,
        db_path=db_path,
        html_path=html_path,
        keywords_path=keywords_path,
    )
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    assert nodes["Nextronics"]["domain_tags"] == ["robotics"]
    # "LG" and "LG Innotek" are distinct DB rows and must stay distinct nodes:
    # graph-wide alias merging only applies the explicit alias map, not the
    # prefix-shortening heuristic (which would incorrectly conflate them).
    assert nodes["LG"]["domain_tags"] == ["robotics"]
    assert nodes["LG Innotek"]["domain_tags"] == ["robotics"]


def test_generated_html_applies_batch15_low_risk_domain_and_infra_overrides(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (1, "SpaceX", "SPACEX", "US", None),
            (2, "AST SpaceMobile", "ASTS", "US", None),
            (3, "Nokia", "NOK", "FI", None),
            (4, "Orange Belgium", None, "BE", None),
            (5, "Astronics", None, "US", None),
            (6, "ATI", None, "US", None),
            (7, "Dycom Industries", None, "US", None),
            (8, "Blue Origin", None, "US", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 4, 3, "sole supplier of network infrastructure", 0.8, "5G Telecom", "active"),
            (2, 5, 1, "Testing provider", 0.8, "Aerospace", "active"),
            (3, 6, 1, "Titanium sponge provider", 0.8, "Aerospace", "active"),
            (4, 7, 1, "Subassemblies provider", 0.8, "Aerospace", "active"),
            (5, 8, 2, "launch partner / services provider", 0.8, "Wireless Infrastructure", "active"),
        ],
    )
    conn.commit()
    conn.close()

    output_dir = tmp_path / "cpo_chain" / "output"
    output_dir.mkdir(parents=True)
    keywords_path = tmp_path / "cpo_chain" / "keywords.yaml"
    keywords_path.parent.mkdir(parents=True, exist_ok=True)
    keywords_path.write_text("root_tickers:\n  - SPACEX\n  - ASTS\n  - NOK\n", encoding="utf-8")
    html_path = output_dir / "index.html"

    module.generate_graph_html(
        base_dir=tmp_path,
        db_path=db_path,
        html_path=html_path,
        keywords_path=keywords_path,
    )
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    assert nodes["Orange Belgium"]["infra_tags"] == ["telecom"]
    for name in ["Astronics", "ATI", "Dycom Industries", "Blue Origin"]:
        assert nodes[name]["domain_tags"] == ["aerospace"]


def test_generated_html_applies_batch16_navitas_power_infra_override(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (1, "NVIDIA", "NVDA", "US", None),
            (2, "X-Fab", None, "BE", None),
            (3, "Navitas Semiconductor", None, "US", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 3, 2, "silicon foundry partner", 0.8, "Power Semiconductor", "active"),
            (2, 3, 1, "GaN 800V-to-6V Direct Power Board provider", 0.8, "AI Server", "active"),
        ],
    )
    conn.commit()
    conn.close()

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
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    assert nodes["Navitas Semiconductor"]["infra_tags"] == ["power_infra"]


def test_generated_html_applies_batch17_mediacom_telecom_override(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (1, "Applied Optoelectronics", "AAOI", "US", None),
            (2, "Mediacom", None, "US", None),
        ],
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 2, 1, "DOCSIS 4.0 upgrade equipment/technology provider", 0.8, "Wireless Infrastructure", "active"),
    )
    conn.commit()
    conn.close()

    output_dir = tmp_path / "cpo_chain" / "output"
    output_dir.mkdir(parents=True)
    keywords_path = tmp_path / "cpo_chain" / "keywords.yaml"
    keywords_path.parent.mkdir(parents=True, exist_ok=True)
    keywords_path.write_text("root_tickers:\n  - AAOI\n", encoding="utf-8")
    html_path = output_dir / "index.html"

    module.generate_graph_html(
        base_dir=tmp_path,
        db_path=db_path,
        html_path=html_path,
        keywords_path=keywords_path,
    )
    nodes = _extract_graph_nodes(html_path.read_text(encoding="utf-8"))

    assert nodes["Mediacom"]["infra_tags"] == ["telecom"]


def test_generated_html_includes_downstream_targets_of_root_tickers(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
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
            (2, "Agility Robotics", None, "US", "robotics"),
        ],
    )
    conn.execute(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, 2, "Technology Partner", 0.8, "General Tech", "active"),
    )
    conn.commit()
    conn.close()

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
    html = html_path.read_text(encoding="utf-8")

    assert '"name": "NVIDIA"' in html
    assert '"name": "Agility Robotics"' in html


def test_generated_graph_omits_placeholder_nodes(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
    db_path = tmp_path / "tweets.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE industry_entities (
            id INTEGER PRIMARY KEY, name TEXT, ticker TEXT, country TEXT, industry_tags TEXT
        );
        CREATE TABLE industry_relations (
            id INTEGER PRIMARY KEY, from_company_id INTEGER, to_company_id INTEGER,
            role TEXT, confidence REAL, industry_context TEXT, status TEXT
        );
        CREATE TABLE industry_relation_evidence (
            id INTEGER PRIMARY KEY, relation_id INTEGER, tweet_id INTEGER,
            evidence_type TEXT, snippet TEXT, extracted_at TEXT, source TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO industry_entities (id, name, ticker, country, industry_tags) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "NVIDIA", "NVDA", "US", "ai_chip"),
            (2, "Lumentum", "LITE", "US", "photonics,cpo"),
            (3, "Customer C (全球領先光模組業者)", None, "US", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 2, 1, "Laser supplier", 0.9, "CPO", "active"),
            (2, 3, 1, "placeholder supplier", 0.9, "CPO", "active"),
        ],
    )
    conn.commit()
    conn.close()

    output_dir = tmp_path / "cpo_chain" / "output"
    output_dir.mkdir(parents=True)
    keywords_path = tmp_path / "cpo_chain" / "keywords.yaml"
    keywords_path.parent.mkdir(parents=True, exist_ok=True)
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")
    html_path = output_dir / "index.html"

    module.generate_graph_html(
        base_dir=tmp_path, db_path=db_path, html_path=html_path, keywords_path=keywords_path
    )
    html = html_path.read_text(encoding="utf-8")
    nodes = _extract_graph_nodes(html)

    assert "Customer C (全球領先光模組業者)" not in nodes
    assert "Lumentum" in nodes and "NVIDIA" in nodes
    # no link may reference the filtered placeholder
    assert all(
        "Customer C" not in (s or "") and "Customer C" not in (t or "")
        for s, t, _ in _extract_named_links(html)
    )


def test_generated_graph_merges_company_name_aliases_into_one_node(tmp_path):
    module = importlib.import_module("scripts.update_network_html")
    db_path = tmp_path / "tweets.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE industry_entities (
            id INTEGER PRIMARY KEY, name TEXT, ticker TEXT, country TEXT, industry_tags TEXT
        );
        CREATE TABLE industry_relations (
            id INTEGER PRIMARY KEY, from_company_id INTEGER, to_company_id INTEGER,
            role TEXT, confidence REAL, industry_context TEXT, status TEXT
        );
        CREATE TABLE industry_relation_evidence (
            id INTEGER PRIMARY KEY, relation_id INTEGER, tweet_id INTEGER,
            evidence_type TEXT, snippet TEXT, extracted_at TEXT, source TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO industry_entities (id, name, ticker, country, industry_tags) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "NVIDIA", "NVDA", "US", "ai_chip"),
            (2, "ASTS", None, "US", None),
            (3, "AST SpaceMobile", "ASTS", "US", None),
            (4, "Nokia", "NOK", "FI", None),
        ],
    )
    conn.executemany(
        """
        INSERT INTO industry_relations
        (id, from_company_id, to_company_id, role, confidence, industry_context, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            # both variants -> NVIDIA, identical role/context: must dedupe to ONE link
            (1, 2, 1, "satellite partner", 0.8, "Wireless Infrastructure", "active"),
            (2, 3, 1, "satellite partner", 0.8, "Wireless Infrastructure", "active"),
            # distinct link from the other variant: must survive as a unioned edge
            (3, 3, 4, "network partner", 0.8, "Wireless Infrastructure", "active"),
        ],
    )
    conn.commit()
    conn.close()

    output_dir = tmp_path / "cpo_chain" / "output"
    output_dir.mkdir(parents=True)
    keywords_path = tmp_path / "cpo_chain" / "keywords.yaml"
    keywords_path.parent.mkdir(parents=True, exist_ok=True)
    keywords_path.write_text("root_tickers:\n  - NVDA\n", encoding="utf-8")
    html_path = output_dir / "index.html"

    module.generate_graph_html(
        base_dir=tmp_path, db_path=db_path, html_path=html_path, keywords_path=keywords_path
    )
    html = html_path.read_text(encoding="utf-8")
    data = _extract_graph_data(html)
    nodes = {node["name"]: node for node in data["tiers"]}

    # 1. aliases collapse to ONE canonical node
    assert "AST SpaceMobile" in nodes
    assert "ASTS" not in nodes
    assert sum(1 for n in data["tiers"] if n["name"] == "AST SpaceMobile") == 1

    # 2. the merged node carries a ticker recovered from a member row
    assert nodes["AST SpaceMobile"]["ticker"] == "ASTS"

    # 3. links are remapped onto the survivor id, deduped and unioned
    named = _extract_named_links(html)
    assert ("AST SpaceMobile", "NVIDIA", "satellite partner") in named
    # the duplicate satellite-partner edge from the other variant is deduped away
    assert len([1 for s, t, r in named if (s, t) == ("AST SpaceMobile", "NVIDIA")]) == 1
    # the distinct edge from the merged variant survives
    assert ("AST SpaceMobile", "Nokia", "network partner") in named
    # no orphan link endpoint remains for the non-surviving id
    live_ids = {n["id"] for n in data["tiers"]}
    assert all(
        link["source"] in live_ids and link["target"] in live_ids for link in data["links"]
    )
