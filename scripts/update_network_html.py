import base64
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cpo_chain.normalization import (
    PLACEHOLDER_NODES,
    canonical_alias_name,
)


FAVICON_ICO_BASE64 = (
    "AAABAAEAEBAAAAAAIABdAAAAFgAAAIlQTkcNChoKAAAADUlIRFIAAAAQAAAAEAgGAAAAH/P/"
    "YQAAACRJREFUeJxj/P//PwMlgIki3aMGgAETA4WAadQAhtEwYKA8DABjmgMd1bXWdwAAAABJ"
    "RU5ErkJggg=="
)


def _source_label(raw: str | None) -> str:
    mapping = {
        "twitter": "Twitter",
        "google_news": "Google News",
        "yahoo_rss": "Yahoo",
        "sec_8k": "SEC",
        "edgar": "SEC",
    }
    if not raw:
        return "Inferred"
    return mapping.get(raw, raw.replace("_", " ").title())


def _summarize_relation(role: str | None, context: str | None) -> str:
    if role and context:
        return f"{role} in {context}"
    if role:
        return role
    return context or "Relationship noted"


def _evidence_rank(item: dict) -> tuple[int, int, str]:
    return (
        1 if item.get("snippet") else 0,
        1 if item.get("raw_source") else 0,
        item.get("extracted_at") or "",
    )


def _normalize_relation_evidence(row: dict) -> dict:
    snippet = (row.get("snippet") or "").strip()
    source_tag = _source_label(row.get("evidence_source"))
    summary = _summarize_relation(row.get("role"), row.get("industry_context"))
    return {
        "source_tag": source_tag,
        "summary": summary,
        "snippet": snippet,
        "raw_source": row.get("evidence_source"),
        "extracted_at": row.get("extracted_at") or "",
    }


ROLE_TAG_MAP = {
    "ai_chip": {"ai_chip"},
    "hbm": {"hbm"},
    "ai_server": {"ai_server", "odm"},
    "neocloud": {"neocloud"},
    "photonics": {"photonics", "cpo"},
    "hyperscaler": {"hyperscaler"},
    "material": {"material", "inp", "substrate", "wafer"},
    "foundry": {"foundry"},
    "packaging": {"packaging", "test"},
}

ENTITY_CATEGORY_OVERRIDES = {
    "Agility Robotics": {"domain_tags": ["robotics", "embodied_ai"]},
    "AI Data Centers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "AI Chip Manufacturers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "All Businesses": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "AST SpaceMobile": {"domain_tags": ["aerospace"]},
    "ASTS": {"domain_tags": ["aerospace"]},
    "ATI": {"domain_tags": ["aerospace"]},
    "AEVA Technologies": {"domain_tags": ["robotics"]},
    "Aeva": {"domain_tags": ["robotics"]},
    "ASE Technology Holding": {"role_tags": ["packaging"]},
    "ASE Group": {"role_tags": ["packaging"]},
    "AMPG": {"domain_tags": ["aerospace"]},
    "AWS": {"infra_tags": ["cloud_hosting"]},
    "Amazon Web Services": {"infra_tags": ["cloud_hosting"]},
    "Anduril": {"domain_tags": ["aerospace"]},
    "Applied Materials": {"role_tags": ["packaging"]},
    "Alphabet (GOOGL)": {"role_tags": ["hyperscaler"]},
    "Alphabet (Google Cloud)": {"infra_tags": ["cloud_hosting"]},
    "BAE Systems": {"domain_tags": ["aerospace"]},
    "Blue Origin": {"domain_tags": ["aerospace"]},
    "Boston Dynamics": {"domain_tags": ["robotics", "embodied_ai"]},
    "Coherent (COHR)": {"role_tags": ["photonics"]},
    "Compeq": {"domain_tags": ["aerospace"]},
    "Datacenter Customers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "OpenAI": {"domain_tags": ["model_lab"]},
    "Anthropic": {"domain_tags": ["model_lab"]},
    "SpaceX": {"domain_tags": ["aerospace"]},
    "Starlink": {"domain_tags": ["aerospace"]},
    "Boeing": {"domain_tags": ["aerospace"]},
    "Lockheed Martin": {"domain_tags": ["aerospace"]},
    "NASA": {"domain_tags": ["aerospace"]},
    "US Air Force": {"domain_tags": ["aerospace"]},
    "US Navy": {"domain_tags": ["aerospace"]},
    "Ayar Labs": {"domain_tags": ["embodied_ai"]},
    "Alchip": {"role_tags": ["ai_chip"]},
    "Bloom Energy": {"infra_tags": ["power_infra"]},
    "Astronics": {"domain_tags": ["aerospace"]},
    "Celestial AI": {"role_tags": ["photonics"]},
    "Delta Electronics": {"role_tags": ["ai_server"]},
    "Dycom Industries": {"domain_tags": ["aerospace"]},
    "Electro Optic Systems": {"domain_tags": ["aerospace"]},
    "Ericsson": {"infra_tags": ["telecom"]},
    "Epiwafer manufacturers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "FOCI Fiber Optic Communications": {"role_tags": ["photonics"]},
    "Fluence Energy": {"infra_tags": ["power_infra"]},
    "Glass substrate manufacturers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Global Silicon Photonics Leader": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Iris Energy": {"role_tags": ["neocloud"], "infra_tags": ["cloud_hosting"]},
    "Jabil": {"role_tags": ["packaging"]},
    "Innolight": {"role_tags": ["photonics"]},
    "Innolight Technology": {"role_tags": ["photonics"]},
    "IREN": {"role_tags": ["neocloud"], "infra_tags": ["cloud_hosting"]},
    "LG": {"domain_tags": ["robotics"]},
    "LG Innotek": {"domain_tags": ["robotics"]},
    "Liquid cooling pump manufacturers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Lightmatter": {"role_tags": ["photonics"]},
    "Lightelligence": {"role_tags": ["photonics"]},
    "MediaTek": {"role_tags": ["ai_chip"]},
    "Mediacom": {"infra_tags": ["telecom"]},
    "Micron Technology": {"role_tags": ["hbm"]},
    "Lite-On": {"role_tags": ["photonics"]},
    "Lite-On Technology": {"role_tags": ["photonics"]},
    "NBIS": {"role_tags": ["neocloud"], "infra_tags": ["cloud_hosting"]},
    "Orange Belgium": {"infra_tags": ["telecom"]},
    "Oracle": {"infra_tags": ["cloud_hosting"]},
    "TeraWulf": {"infra_tags": ["cloud_hosting"]},
    "Samsung Electro-Mechanics": {"role_tags": ["material"]},
    "Tesla": {"domain_tags": ["embodied_ai"]},
    "Wiwynn": {"role_tags": ["ai_server"]},
    "Dell": {"role_tags": ["ai_server"]},
    "Apple": {"domain_tags": ["embodied_ai"]},
    "Motivair": {"infra_tags": ["power_infra"]},
    "Major Hyperscale Customer": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Major Silicon Photonics Customer": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Navitas Semiconductor": {"infra_tags": ["power_infra"]},
    "Nebius": {"role_tags": ["neocloud"], "infra_tags": ["cloud_hosting"]},
    "Nebius Group": {"role_tags": ["neocloud"], "infra_tags": ["cloud_hosting"]},
    "Nextronics": {"domain_tags": ["robotics"]},
    "Nokia": {"infra_tags": ["telecom"]},
    "Nokia (Internal Facility)": {"infra_tags": ["telecom"]},
    "Nippon Chemical Industry": {"role_tags": ["material"]},
    "Optical Companies": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "PENG": {"role_tags": ["ai_server"]},
    "Penguin Solutions": {"role_tags": ["ai_server"]},
    "OpenLight": {"role_tags": ["photonics"]},
    "Packaging Companies": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Photonics Industry": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Photonics Supply Chain": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "SiLC Technologies": {"role_tags": ["photonics"]},
    "Sivers": {"role_tags": ["photonics"]},
    "Sivers Photonics": {"role_tags": ["photonics"]},
    "POET Technologies (POET)": {"role_tags": ["photonics"]},
    "Power Infrastructure Market": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Power Integrations": {"infra_tags": ["power_infra"]},
    "Raytheon": {"domain_tags": ["aerospace"]},
    "Robotic supply chain": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Semiconductor Industry": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Server rack manufacturers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "SK Telecom": {"infra_tags": ["telecom"]},
    "SiPh Upstream Partners": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Silicon Photonics Manufacturers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Space Applications": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "SpektreWorks": {"domain_tags": ["aerospace"]},
    "Supermicro": {"role_tags": ["ai_server"]},
    "US big tech": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Win Semi": {"role_tags": ["foundry"]},
    "X-Fab": {"role_tags": ["foundry"]},
    "X-FAB": {"role_tags": ["foundry"]},
    "Xintec": {"role_tags": ["packaging"]},
    "upstream semi supply chain companies": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "pluggable optical transceiver companies": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "AT&T": {"infra_tags": ["telecom"]},
    "Verizon": {"infra_tags": ["telecom"]},
    "T-Mobile": {"infra_tags": ["telecom"]},
    "Schneider Electric": {"infra_tags": ["power_infra"]},
    "Siemens": {"infra_tags": ["power_infra"]},
    "Vertiv": {"infra_tags": ["power_infra"]},
    "Eaton": {"infra_tags": ["power_infra"]},
    "Hyperscalers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "HBM": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "CPO Industry": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "CPO Manufacturers": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "CPO Market": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Three Global Memory Giants": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
    "Unknown Optical Transceiver Company": {"infra_tags": ["market_bucket"], "node_kind": "market_bucket"},
}


def _split_tags(raw_tags: str | None) -> list[str]:
    if not raw_tags:
        return []
    return [part.strip().lower() for part in raw_tags.split(",") if part.strip()]


def _derive_role_tags(raw_tags: str | None) -> list[str]:
    raw_parts = set(_split_tags(raw_tags))
    matches: list[str] = []
    for category, needles in ROLE_TAG_MAP.items():
        if raw_parts.intersection(needles):
            matches.append(category)
    return matches


def _merge_unique(existing: list[str], additions: list[str]) -> list[str]:
    merged: list[str] = []
    for value in [*existing, *additions]:
        if value and value not in merged:
            merged.append(value)
    return merged


def _resolve_node_categories(
    name: str, raw_tags: str | None
) -> tuple[list[str], list[str], list[str], str]:
    role_tags = _derive_role_tags(raw_tags)
    domain_tags: list[str] = []
    infra_tags: list[str] = []
    node_kind = "company"
    override = ENTITY_CATEGORY_OVERRIDES.get(name, {})
    role_tags = _merge_unique(role_tags, override.get("role_tags", []))
    domain_tags = _merge_unique(domain_tags, override.get("domain_tags", []))
    infra_tags = _merge_unique(infra_tags, override.get("infra_tags", []))
    node_kind = override.get("node_kind", node_kind)
    return role_tags, domain_tags, infra_tags, node_kind


def _merge_graph_nodes(nodes: list[dict]) -> tuple[list[dict], dict[int, int]]:
    """Collapse alias-equivalent nodes into one D3 node each.

    Uses canonical_alias_name() (explicit alias map + ticker-suffix stripping
    only), not canonical_company_name()'s prefix-shortening heuristic: the
    graph merges across the ENTIRE multi-context node set at once, and that
    heuristic can conflate genuinely distinct entities that happen to share a
    name prefix (e.g. a parent company and a separately-tracked subsidiary or
    facility row) when applied at that scope.

    Returns the merged node list plus an id remap (every original node id ->
    the surviving canonical node id). Tags are unioned from members, each
    already resolved on its ORIGINAL name, so ENTITY_CATEGORY_OVERRIDES keeps
    applying after a rename.
    """
    canonical_of = {n["id"]: canonical_alias_name(n["name"]) for n in nodes}

    groups: dict[str, list[dict]] = {}
    for node in nodes:
        groups.setdefault(canonical_of[node["id"]], []).append(node)

    merged: list[dict] = []
    id_remap: dict[int, int] = {}
    for label, members in groups.items():
        ordered = sorted(members, key=lambda n: (n["tier"], n["id"]))
        survivor = ordered[0]
        ticker = next(
            (m["ticker"] for m in ordered if m.get("ticker")), survivor.get("ticker")
        )
        role_tags: list[str] = []
        domain_tags: list[str] = []
        infra_tags: list[str] = []
        node_kind = "company"
        for member in ordered:
            role_tags = _merge_unique(role_tags, member.get("role_tags", []))
            domain_tags = _merge_unique(domain_tags, member.get("domain_tags", []))
            infra_tags = _merge_unique(infra_tags, member.get("infra_tags", []))
            if member.get("node_kind") and member["node_kind"] != "company":
                node_kind = member["node_kind"]
        merged.append(
            {
                **survivor,
                "name": label,
                "ticker": ticker,
                "role_tags": role_tags,
                "domain_tags": domain_tags,
                "infra_tags": infra_tags,
                "node_kind": node_kind,
            }
        )
        for member in members:
            id_remap[member["id"]] = survivor["id"]

    merged.sort(key=lambda n: (n["tier"], n["name"]))
    return merged, id_remap


def _remap_and_dedupe_links(links: list[dict], id_remap: dict[int, int]) -> list[dict]:
    """Point every link at its survivor node id; drop self-loops and duplicates."""
    remapped: list[dict] = []
    seen: set[tuple[int, int, str, str]] = set()
    for link in sorted(links, key=lambda item: item.get("id", 0)):
        source = id_remap.get(link["source"], link["source"])
        target = id_remap.get(link["target"], link["target"])
        if source == target:
            continue
        key = (source, target, link.get("role") or "", link.get("context") or "")
        if key in seen:
            continue
        seen.add(key)
        remapped.append({**link, "source": source, "target": target})
    return remapped


def generate_graph_html(
    base_dir: Path | None = None,
    db_path: Path | None = None,
    html_path: Path | None = None,
    keywords_path: Path | None = None,
) -> None:
    base_dir = base_dir or Path(__file__).resolve().parent.parent
    db_path = db_path or base_dir / "tweets.db"
    html_path = html_path or base_dir / "cpo_chain" / "output" / "index.html"
    keywords_path = keywords_path or base_dir / "cpo_chain" / "keywords.yaml"

    with open(keywords_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    root_tickers = config.get("root_tickers", ["NVDA"])

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    ticker_placeholders = ",".join("?" * len(root_tickers))
    root_rows = conn.execute(
        f"""
        SELECT id, name, ticker, country
        FROM industry_entities
        WHERE ticker IN ({ticker_placeholders})
        """,
        root_tickers,
    ).fetchall()
    active_edges = conn.execute(
        """
        SELECT DISTINCT from_company_id, to_company_id
        FROM industry_relations
        WHERE status='active'
        """
    ).fetchall()

    adjacency: dict[int, set[int]] = {}
    for source_id, target_id in active_edges:
        adjacency.setdefault(source_id, set()).add(target_id)
        adjacency.setdefault(target_id, set()).add(source_id)

    tiers_by_id: dict[int, int] = {}
    frontier = [row["id"] for row in root_rows]
    for root_id in frontier:
        tiers_by_id[root_id] = 0

    max_depth = 5
    for level in range(1, max_depth + 1):
        next_frontier: list[int] = []
        for node_id in frontier:
            for neighbor_id in adjacency.get(node_id, ()):
                if neighbor_id in tiers_by_id:
                    continue
                tiers_by_id[neighbor_id] = level
                next_frontier.append(neighbor_id)
        if not next_frontier:
            break
        frontier = next_frontier

    tiers_data = []
    if tiers_by_id:
        id_placeholders = ",".join("?" * len(tiers_by_id))
        entity_rows = conn.execute(
            f"""
            SELECT id, name, ticker, country
            FROM industry_entities
            WHERE id IN ({id_placeholders})
            """,
            list(tiers_by_id.keys()),
        ).fetchall()
        tiers_data = sorted(
            (
                {
                    "id": row["id"],
                    "name": row["name"],
                    "ticker": row["ticker"],
                    "country": row["country"],
                    "tier": tiers_by_id[row["id"]],
                }
                for row in entity_rows
            ),
            key=lambda row: (row["tier"], row["name"]),
        )

    tiers_data = [row for row in tiers_data if row["name"] not in PLACEHOLDER_NODES]

    tags_map = {
        row[0]: (row[1] or "")
        for row in conn.execute("SELECT id, industry_tags FROM industry_entities").fetchall()
    }

    tiers_list = []
    for row in tiers_data:
        raw_tags = tags_map.get(row["id"], "")
        role_tags, domain_tags, infra_tags, node_kind = _resolve_node_categories(
            row["name"], raw_tags
        )
        tiers_list.append(
            dict(row)
            | {
                "tags": raw_tags,
                "role_tags": role_tags,
                "domain_tags": domain_tags,
                "infra_tags": infra_tags,
                "node_kind": node_kind,
            }
        )
    enriched_nodes = tiers_list  # nodes with tags resolved on ORIGINAL names

    tiers_list, id_remap = _merge_graph_nodes(enriched_nodes)
    node_ids = list(id_remap.keys())  # ALL member ids, so links from non-survivors are fetched
    relation_evidence_map: dict[int, list[dict]] = {}
    links = []
    if node_ids:
        id_placeholders = ",".join("?" * len(node_ids))
        evidence_rows = [
            dict(row)
            for row in conn.execute(
                f"""
        SELECT
            r.id AS relation_id,
            r.from_company_id AS source,
            r.to_company_id AS target,
            e.snippet,
            e.extracted_at,
            e.source AS evidence_source,
            e.evidence_type,
            r.role,
            r.industry_context
        FROM industry_relations r
        LEFT JOIN industry_relation_evidence e ON e.relation_id = r.id
        WHERE r.status='active'
          AND r.from_company_id IN ({id_placeholders})
          AND r.to_company_id IN ({id_placeholders})
    """,
                node_ids + node_ids,
            ).fetchall()
        ]
        for row in evidence_rows:
            key = row["relation_id"]
            relation_evidence_map.setdefault(key, []).append(_normalize_relation_evidence(row))

        for items in relation_evidence_map.values():
            items.sort(key=_evidence_rank, reverse=True)

        raw_links = [
            dict(row)
            for row in conn.execute(
                f"""
        SELECT
            id,
            from_company_id as source,
            to_company_id as target,
            role,
            confidence,
            industry_context
        FROM industry_relations
        WHERE status='active'
          AND from_company_id IN ({id_placeholders})
          AND to_company_id IN ({id_placeholders})
    """,
                node_ids + node_ids,
            ).fetchall()
        ]
        for row in raw_links:
            evidences = relation_evidence_map.get(row["id"], [])
            primary = evidences[0] if evidences else {
                "source_tag": "Inferred",
                "summary": _summarize_relation(row.get("role"), row.get("industry_context")),
                "snippet": "",
                "raw_source": None,
                "extracted_at": "",
            }
            links.append(
                {
                    **row,
                    "source_tags": [item["source_tag"] for item in evidences[:3]] or [primary["source_tag"]],
                    "summary": primary["summary"],
                    "snippet": primary["snippet"],
                    "raw_source": primary["raw_source"],
                    "evidences": evidences,
                    "context": row.get("industry_context") or "",
                }
            )
    links = _remap_and_dedupe_links(links, id_remap)
    conn.close()

    data = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "root_tickers": root_tickers,
            "total_companies": len(tiers_list),
            "total_links": len(links),
        },
        "tiers": tiers_list,
        "links": links,
    }

    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>CPO Network Visualization</title>
    <link rel="icon" href="data:,">
    <meta http-equiv="Content-Security-Policy" content="default-src 'self' https://d3js.org; script-src 'self' 'unsafe-inline' https://d3js.org; style-src 'self' 'unsafe-inline';">
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background: #f8f9fa; overflow: hidden; }}
        #chart {{ width: 100vw; height: 100vh; }}
        .node circle {{ stroke: #fff; stroke-width: 1.5px; cursor: grab; transition: opacity 0.2s; }}
        .node circle:active {{ cursor: grabbing; }}
        .node text {{ font-size: 11px; font-weight: 500; pointer-events: auto; cursor: pointer; fill: #495057; text-shadow: 0 1px 2px rgba(255,255,255,0.9); transition: opacity 0.2s; }}
        .link {{ stroke: #adb5bd; stroke-opacity: 0.5; stroke-width: 0.65px; fill: none; transition: opacity 0.2s; }}
        .marker {{ fill: #adb5bd; opacity: 0.78; }}
        .node.dimmed circle, .node.dimmed text {{ opacity: 0.08; }}
        .link.dimmed {{ opacity: 0.04; }}
        .node.highlighted circle {{ stroke: #ffc107; stroke-width: 3px; }}
        .link.highlighted {{ stroke: #f08c4a; stroke-opacity: 0.95; stroke-width: 2px; }}
        .node.context-node circle, .node.context-node text {{ opacity: 0.34; }}
        .link.context-link {{ opacity: 0.16; stroke-opacity: 0.22; stroke-width: 0.85px; }}
        .node.market-bucket circle {{ fill-opacity: 0.55; stroke-opacity: 0.6; }}
        .node.market-bucket text {{ fill: #6c757d; }}
        .node.neighborhood-node circle, .node.neighborhood-node text {{ opacity: 1; }}
        .node.neighborhood-focus circle {{ stroke: #f08c4a; stroke-width: 4px; }}
        .link.neighborhood-edge {{ opacity: 0.82; stroke-width: 1.6px; }}
        .link.neighborhood-focus {{ opacity: 1; stroke: #f08c4a; stroke-width: 2.6px; }}
        .node.neighborhood-dimmed circle, .node.neighborhood-dimmed text {{ opacity: 0.05; }}
        .link.neighborhood-dimmed {{ opacity: 0.03; }}

        /* Search + Filter panel */
        #panel {{
            position: absolute; top: 16px; left: 16px;
            background: rgba(255,255,255,0.96);
            border: 1px solid #dee2e6; border-radius: 10px;
            padding: 14px 16px; width: 260px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            z-index: 10;
        }}
        #panel h2 {{ margin: 0 0 10px; font-size: 14px; color: #212529; }}
        #search {{
            width: 100%; padding: 7px 10px; border: 1px solid #ced4da;
            border-radius: 6px; font-size: 13px; outline: none;
            margin-bottom: 10px;
        }}
        #search:focus {{ border-color: #0d6efd; box-shadow: 0 0 0 2px rgba(13,110,253,0.15); }}
        .filter-label {{ font-size: 11px; color: #6c757d; margin-bottom: 6px; }}
        .chips {{ display: flex; flex-wrap: wrap; gap: 5px; }}
        .chip {{
            padding: 4px 10px; border-radius: 20px; font-size: 11px; cursor: pointer;
            border: 1px solid transparent; transition: all 0.15s; user-select: none;
        }}
        .chip:hover {{ filter: brightness(0.92); }}
        .chip.active {{
            font-weight: 700;
            border-color: rgba(33,37,41,0.24);
            box-shadow: 0 2px 6px rgba(33,37,41,0.10);
            transform: translateY(-1px);
        }}
        .chip-all    {{ background: #e9ecef; color: #495057; }}
        .chip-ai     {{ background: #6610f2; color: #fff; }}
        .chip-hbm    {{ background: #0d6efd; color: #fff; }}
        .chip-server {{ background: #0a3622; color: #d1e7dd; }}
        .chip-cloud  {{ background: #20c997; color: #fff; }}
        .chip-photo  {{ background: #fd7e14; color: #fff; }}
        .chip-hyper  {{ background: #dc3545; color: #fff; }}
        .chip-mat    {{ background: #fff3cd; color: #856404; }}
        .chip-fab    {{ background: #cfe2ff; color: #084298; }}
        .chip-pkg    {{ background: #f8d7da; color: #842029; }}
        .active-filters {{
            margin: 10px 0 8px;
            font-size: 11px;
            line-height: 1.45;
            color: #495057;
            min-height: 16px;
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            align-items: center;
        }}
        .active-filter-badge {{
            display: inline-flex;
            align-items: center;
            padding: 3px 8px;
            border-radius: 999px;
            background: #eef2ff;
            color: #364fc7;
            font-size: 11px;
            font-weight: 700;
            border: 1px solid rgba(54,79,199,0.12);
        }}
        .clear-filters {{
            width: 100%;
            margin-bottom: 10px;
            border: 1px solid rgba(213,53,69,0.28);
            background: #fff5f5;
            color: #c92a2a;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            padding: 7px 10px;
            cursor: pointer;
            box-shadow: 0 2px 8px rgba(201,42,42,0.08);
            transition: transform 0.15s, box-shadow 0.15s, filter 0.15s;
        }}
        .clear-filters:hover {{ filter: brightness(0.98); transform: translateY(-1px); }}
        .clear-filters[hidden] {{ display: none; }}

        #results {{
            margin-top: 10px; max-height: 180px; overflow-y: auto;
            border-top: 1px solid #e9ecef; padding-top: 8px;
            display: none;
        }}
        .result-item {{
            padding: 4px 6px; border-radius: 4px; font-size: 12px; cursor: pointer;
            display: flex; align-items: center; gap: 6px;
        }}
        .result-item:hover {{ background: #f1f3f5; }}
        .result-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
        .result-name {{ font-weight: 500; color: #212529; }}
        .result-ticker {{ color: #6c757d; font-size: 11px; }}

        /* Legend */
        #legend {{
            position: absolute; top: 16px; right: 16px;
            background: rgba(255,255,255,0.96); padding: 12px 14px;
            border: 1px solid #dee2e6; border-radius: 10px; font-size: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        }}
        #legend strong {{ display: block; margin-bottom: 6px; font-size: 12px; }}
        .legend-item {{ display: flex; align-items: center; margin-bottom: 4px; gap: 7px; }}
        .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
        #info {{
            position: absolute; bottom: 14px; left: 16px;
            font-size: 11px; color: #6c757d;
        }}
        #tooltip {{
            position: absolute; background: rgba(33,37,41,0.9); color: #fff;
            padding: 6px 10px; border-radius: 6px; font-size: 12px;
            pointer-events: none; display: none; max-width: 240px; line-height: 1.5;
            z-index: 20;
        }}
        .detail-panel {{
            position: absolute;
            top: 0;
            right: 0;
            width: 360px;
            height: 100vh;
            background: rgba(255,255,255,0.98);
            border-left: 1px solid #dee2e6;
            box-shadow: -8px 0 24px rgba(0,0,0,0.06);
            padding: 24px 20px;
            z-index: 15;
            overflow-y: auto;
            overflow-x: hidden;
            -webkit-overflow-scrolling: touch;
        }}
        .detail-panel.is-empty .detail-content {{ display: none; }}
        .detail-empty {{ color: #6c757d; font-size: 13px; line-height: 1.6; }}
        .detail-panel__title {{ font-size: 22px; font-weight: 700; color: #212529; }}
        .detail-panel__subtitle {{ margin-top: 6px; font-size: 13px; color: #6c757d; }}
        .detail-panel__stats {{ margin-top: 8px; font-size: 12px; color: #495057; }}
        .detail-panel__section {{ margin-top: 20px; }}
        .detail-panel__taxonomy {{
            display: grid;
            gap: 10px;
            margin-top: 14px;
        }}
        .detail-panel__taxonomy-row {{
            display: grid;
            gap: 6px;
        }}
        .detail-panel__taxonomy-label {{
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            color: #6c757d;
        }}
        .detail-panel__taxonomy-badges {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }}
        .detail-panel__taxonomy-empty {{
            font-size: 12px;
            color: #adb5bd;
        }}
        .taxonomy-badge {{
            display: inline-flex;
            align-items: center;
            padding: 3px 8px;
            border-radius: 999px;
            background: #f1f3f5;
            color: #495057;
            font-size: 11px;
            font-weight: 600;
        }}
        .taxonomy-badge--role {{ background: #fff3cd; color: #7a5200; }}
        .taxonomy-badge--domain {{ background: #e8f7f0; color: #0f6b4b; }}
        .taxonomy-badge--infra {{ background: #e7f1ff; color: #0b57d0; }}
        .detail-panel__actions {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            margin-top: 10px;
        }}
        .detail-panel__compare-summary {{
            display: grid;
            gap: 6px;
            margin-top: 12px;
        }}
        .detail-panel__compare-summary-item {{
            font-size: 12px;
            color: #495057;
        }}
        .detail-panel__action {{
            border: 1px solid #d0d7de;
            background: #fff;
            color: #1f2328;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            padding: 6px 10px;
            cursor: pointer;
        }}
        .detail-panel__compare-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
            margin-top: 20px;
        }}
        .detail-panel__compare-column {{
            min-width: 0;
            border: 1px solid #e5e7eb;
            border-radius: 14px;
            padding: 12px;
            background: #fafbfc;
        }}
        .detail-panel__compare-column--active {{
            border-color: #f59f00;
            background: #fff8e1;
            box-shadow: 0 0 0 1px rgba(245, 159, 0, 0.18);
        }}
        .detail-panel__compare-column-header {{
            display: grid;
            gap: 6px;
        }}
        .detail-panel__compare-column--active .detail-panel__compare-column-header {{
            padding: 10px;
            border-radius: 12px;
            background: rgba(245, 159, 0, 0.12);
        }}
        .detail-panel__compare-label {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .detail-panel__compare-badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 28px;
            padding: 4px 10px;
            border-radius: 999px;
            background: #1f2937;
            color: #fff;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.06em;
        }}
        .detail-panel__compare-pending {{
            border: 1px dashed #cbd5e1;
            border-radius: 14px;
            padding: 14px;
            background: #f8fafc;
        }}
        .detail-empty-state {{ color: #6c757d; font-size: 12px; }}
        .relation-card {{
            width: 100%;
            margin-top: 10px;
            padding: 12px;
            text-align: left;
            border: 1px solid #dee2e6;
            border-radius: 10px;
            background: #fff;
            cursor: pointer;
            transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
        }}
        .relation-card:hover {{
            border-color: #adb5bd;
        }}
        .relation-card.is-active {{
            border-color: #0d6efd;
            background: #eef5ff;
            box-shadow: 0 0 0 2px rgba(13,110,253,0.12);
        }}
        .relation-card__name {{ font-weight: 600; color: #212529; }}
        .relation-card__meta {{ margin-top: 4px; font-size: 12px; color: #6c757d; }}
        .relation-card__badges {{ display: flex; gap: 8px; margin-top: 8px; }}
        .confidence-badge, .context-badge {{
            display: inline-flex;
            align-items: center;
            padding: 2px 8px;
            border-radius: 999px;
            background: #f1f3f5;
            color: #495057;
            font-size: 11px;
        }}
        .relation-card__sources {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 8px;
        }}
        .relation-card__summary {{
            margin-top: 10px;
            font-size: 12px;
            line-height: 1.5;
            color: #212529;
        }}
        .relation-card__signals {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 8px;
        }}
        .relation-card__snippet {{
            margin-top: 8px;
            font-size: 11px;
            line-height: 1.45;
            color: #6c757d;
        }}
        .relation-card__toggle {{
            display: inline-flex;
            align-items: center;
            margin-top: 10px;
            color: #0d6efd;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            text-align: left;
        }}
        .relation-card__toggle:focus-visible {{
            outline: 2px solid #86b7fe;
            outline-offset: 2px;
            border-radius: 4px;
        }}
        .relation-card__extra-evidences {{
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px dashed #d7dee7;
            display: grid;
            gap: 10px;
        }}
        .relation-card__evidence-row {{
            display: grid;
            gap: 6px;
        }}
        .evidence-date {{
            font-size: 11px;
            color: #6c757d;
        }}
        .status-badge {{
            display: inline-flex;
            align-items: center;
            padding: 2px 8px;
            border-radius: 999px;
            background: #fff3cd;
            color: #856404;
            font-size: 11px;
        }}
        .source-badge {{
            display: inline-flex;
            align-items: center;
            padding: 2px 8px;
            border-radius: 999px;
            background: #eef2f6;
            color: #495057;
            font-size: 11px;
        }}
        @media (max-width: 960px) {{
            #panel, #legend {{
                max-width: calc(100vw - 24px);
            }}
            .detail-panel__compare-grid {{
                grid-template-columns: 1fr;
            }}
            .detail-panel {{
                width: 100%;
                max-width: 100%;
                transform: translateX(100%);
                transition: transform 0.25s ease;
            }}
            .detail-panel.detail-panel--open {{
                transform: translateX(0);
            }}
        }}
    </style>
</head>
<body>
    <div id="panel">
        <h2>🔍 CPO Supply Chain Explorer</h2>
        <input id="search" type="text" placeholder="搜尋公司名稱或 ticker..." />
        <div class="filter-label">Supply Chain Role</div>
        <div class="chips" data-filter-group="role" data-group="role">
            <span class="chip chip-all active" data-filter-group="role" data-group="role" data-role="all">全部</span>
            <span class="chip chip-ai" data-filter-group="role" data-group="role" data-role="ai_chip">AI 晶片</span>
            <span class="chip chip-hbm" data-filter-group="role" data-group="role" data-role="hbm">HBM 記憶體</span>
            <span class="chip chip-server" data-filter-group="role" data-group="role" data-role="ai_server">AI Server</span>
            <span class="chip chip-cloud" data-filter-group="role" data-group="role" data-role="neocloud">Neocloud</span>
            <span class="chip chip-photo" data-filter-group="role" data-group="role" data-role="photonics">Photonics</span>
            <span class="chip chip-hyper" data-filter-group="role" data-group="role" data-role="hyperscaler">Hyperscaler</span>
            <span class="chip chip-mat" data-filter-group="role" data-group="role" data-role="material">原材料</span>
            <span class="chip chip-fab" data-filter-group="role" data-group="role" data-role="foundry">晶圓代工</span>
            <span class="chip chip-pkg" data-filter-group="role" data-group="role" data-role="packaging">封裝/測試</span>
        </div>
        <div class="filter-label">Application / End Market</div>
        <div class="chips" data-filter-group="domain" data-group="domain">
            <span class="chip chip-all active" data-filter-group="domain" data-group="domain" data-domain="all">全部</span>
            <span class="chip chip-server" data-filter-group="domain" data-group="domain" data-domain="robotics">Robotics</span>
            <span class="chip chip-cloud" data-filter-group="domain" data-group="domain" data-domain="embodied_ai">Embodied AI</span>
            <span class="chip chip-fab" data-filter-group="domain" data-group="domain" data-domain="aerospace">Aerospace</span>
            <span class="chip chip-ai" data-filter-group="domain" data-group="domain" data-domain="model_lab">Model Lab</span>
        </div>
        <div class="filter-label">Infrastructure / Ecosystem</div>
        <div class="chips" data-filter-group="infra" data-group="infra">
            <span class="chip chip-all active" data-filter-group="infra" data-group="infra" data-infra="all">All</span>
            <span class="chip chip-server" data-filter-group="infra" data-group="infra" data-infra="power_infra">Power Infra</span>
            <span class="chip chip-cloud" data-filter-group="infra" data-group="infra" data-infra="telecom">Telecom</span>
            <span class="chip chip-fab" data-filter-group="infra" data-group="infra" data-infra="cloud_hosting">Cloud / Hosting</span>
            <span class="chip chip-ai" data-filter-group="infra" data-group="infra" data-infra="market_bucket">Market Buckets</span>
        </div>
        <div id="active-filters" class="active-filters">Filters: All</div>
        <button id="clear-filters" class="clear-filters" type="button" hidden>Clear filters</button>
        <div id="results"></div>
    </div>

    <div id="legend">
        <strong>Tier 層級</strong>
        <div class="legend-item"><div class="legend-dot" style="background:#dc3545"></div>Tier 0 — Hyperscaler</div>
        <div class="legend-item"><div class="legend-dot" style="background:#fd7e14"></div>Tier 1 — 直接供應商</div>
        <div class="legend-item"><div class="legend-dot" style="background:#0d6efd"></div>Tier 2 — 二級供應商</div>
        <div class="legend-item"><div class="legend-dot" style="background:#6610f2"></div>Tier 3 — 三級供應商</div>
        <div class="legend-item"><div class="legend-dot" style="background:#20c997"></div>Tier 4+</div>
        <hr style="margin:8px 0;border-color:#dee2e6">
        <div style="font-size:11px;color:#6c757d">滑鼠懸停查看關係<br>點擊節點高亮連線</div>
    </div>

    <div id="info">Generated: {gen_time} | Companies: {len(tiers_list)} | Relations: {len(links)}</div>
    <div id="tooltip"></div>
    <div id="chart"></div>
    <aside id="detail-panel" class="detail-panel is-empty">
        <div id="detail-empty" class="detail-empty">Select a company to inspect its relationships.</div>
        <div id="detail-content" class="detail-content" hidden></div>
    </aside>

    <script>
        const data = {data_json};
        const selectedState = {{
            primaryNodeId: null,
            secondaryNodeId: null,
            compareMode: "off",
            primaryRelationKey: null,
            secondaryRelationKey: null,
            activeSide: "primary",
            neighborhoodMode: "off",
            neighborhoodRelationKey: null,
            suppressClearUntil: 0,
        }};
        const expandedRelationKeys = new Set();

        const tierColors = ["#dc3545","#fd7e14","#0d6efd","#6610f2","#20c997","#6c757d"];
        const width = window.innerWidth, height = window.innerHeight;
        const detailPanel = document.getElementById("detail-panel");
        const detailEmpty = document.getElementById("detail-empty");
        const detailContent = document.getElementById("detail-content");

        const nodes = data.tiers.map(d => ({{
            ...d,
            role_tags: Array.isArray(d.role_tags) ? d.role_tags : [],
            domain_tags: Array.isArray(d.domain_tags) ? d.domain_tags : [],
            infra_tags: Array.isArray(d.infra_tags) ? d.infra_tags : [],
            node_kind: d.node_kind || "company",
        }}));
        const nodeById = new Map(nodes.map(d => [d.id, d]));
        const links = data.links.map(d => ({{
            id: d.id,
            source: nodeById.get(d.source),
            target: nodeById.get(d.target),
            role: d.role,
            confidence: d.confidence,
            industry_context: d.industry_context,
            sourceTags: d.source_tags || ["Inferred"],
            summary: d.summary || d.role || "Relationship noted",
            snippet: d.snippet || "",
            context: d.context || d.industry_context || "",
            status: (d.source_tags || ["Inferred"]).includes("Inferred") ? "Inferred" : "Evidence",
            evidences: (d.evidences || []).map(item => ({{
                sourceTag: item.source_tag || "Inferred",
                summary: item.summary || "Relationship noted",
                snippet: item.snippet || "",
                extractedAt: item.extracted_at || "",
            }})),
        }})).filter(d => d.source && d.target);

        const linksBySource = new Map();
        const linksByTarget = new Map();
        links.forEach(l => {{
            if (!linksBySource.has(l.source.id)) linksBySource.set(l.source.id, []);
            if (!linksByTarget.has(l.target.id)) linksByTarget.set(l.target.id, []);
            linksBySource.get(l.source.id).push(l);
            linksByTarget.get(l.target.id).push(l);
        }});

        function buildNodeDetails(node) {{
            const incomingRelations = (linksByTarget.get(node.id) || []);
            const outgoingRelations = (linksBySource.get(node.id) || []);
            return {{
                ...node,
                degree: incomingRelations.length + outgoingRelations.length,
                incomingRelations,
                outgoingRelations,
                neighborIds: new Set([
                    ...incomingRelations.map(l => l.source.id),
                    ...outgoingRelations.map(l => l.target.id),
                ]),
            }};
        }}

        function getRelationKey(relation) {{
            return `relation-${{relation.id}}`;
        }}

        function toggleRelationEvidence(relationKey, event) {{
            if (event) {{
                event.preventDefault();
                event.stopPropagation();
            }}
            if (expandedRelationKeys.has(relationKey)) {{
                expandedRelationKeys.delete(relationKey);
            }} else {{
                expandedRelationKeys.add(relationKey);
            }}
            if (selectedState.primaryNodeId) {{
                renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
            }}
        }}

        function renderRelationCard(relation, direction, side = "primary") {{
            const other = direction === "incoming" ? relation.source : relation.target;
            const relationKey = getRelationKey(relation);
            const activeRelationKey = side === "secondary"
                ? selectedState.secondaryRelationKey
                : selectedState.primaryRelationKey;
            const activeClass = activeRelationKey === relationKey ? " is-active" : "";
            const sourceTags = (relation.sourceTags || ["Inferred"])
                .map(tag => `<span class="source-badge">${{tag}}</span>`)
                .join("");
            const summary = relation.summary || relation.role || "Relationship noted";
            const confidence = `${{Math.round((Number(relation.confidence || 0)) * 100)}}%`;
            const snippet = relation.snippet
                ? `<div class="relation-card__snippet">${{escapeHtml(relation.snippet)}}</div>`
                : "";
            const evidences = relation.evidences || [];
            const hasMultipleEvidences = evidences.length > 1;
            const isExpanded = expandedRelationKeys.has(relationKey);
            const extraEvidences = isExpanded
                ? evidences.slice(1).map(item => {{
                    const text = item.snippet || item.summary || "";
                    const date = item.extractedAt
                        ? `<span class="evidence-date">${{item.extractedAt.slice(0, 10)}}</span>`
                        : "";
                    return `
                        <div class="relation-card__evidence-row">
                            <div class="relation-card__sources">
                                <span class="source-badge">${{escapeHtml(item.sourceTag || "Inferred")}}</span>
                                ${{date}}
                            </div>
                            ${{text ? `<div class="relation-card__snippet">${{escapeHtml(text)}}</div>` : ""}}
                        </div>
                    `;
                }}).join("")
                : "";
            const toggle = hasMultipleEvidences
                ? `
                    <span
                        class="relation-card__toggle"
                        role="button"
                        tabindex="0"
                        onclick="toggleRelationEvidence('${{relationKey}}', event)"
                        onkeydown="if (event.key === 'Enter' || event.key === ' ') toggleRelationEvidence('${{relationKey}}', event)"
                    >${{isExpanded ? "Collapse" : `View all evidence (${{evidences.length}})`}}</span>
                `
                : "";
            return `
                <button class="relation-card${{activeClass}}" type="button" data-panel-side="${{side}}" data-relation-key="${{relationKey}}">
                    <div class="relation-card__name">${{escapeHtml(other.name)}}</div>
                    <div class="relation-card__meta">${{escapeHtml(relation.role)}}</div>
                    <div class="relation-card__sources">${{sourceTags}}</div>
                    <div class="relation-card__signals">
                        <span class="status-badge">${{relation.status}}</span>
                        <span class="confidence-badge">${{confidence}}</span>
                    </div>
                    <div class="relation-card__summary">${{escapeHtml(summary)}}</div>
                    ${{snippet}}
                    ${{toggle}}
                    ${{extraEvidences ? `<div class="relation-card__extra-evidences">${{extraEvidences}}</div>` : ""}}
                </button>
            `;
        }}

        function renderSinglePanelSections(details, side) {{
            return `
                <section class="detail-panel__section">
                    <h3>Upstream</h3>
                    ${{details.incomingRelations.map(r => renderRelationCard(r, "incoming", side)).join("") || '<div class="detail-empty-state">No upstream relations.</div>'}}
                </section>
                <section class="detail-panel__section">
                    <h3>Downstream</h3>
                    ${{details.outgoingRelations.map(r => renderRelationCard(r, "outgoing", side)).join("") || '<div class="detail-empty-state">No downstream relations.</div>'}}
                </section>
            `;
        }}

        function renderTaxonomyRow(label, values, type) {{
            if (!values.length) {{
                return `
                    <div class="detail-panel__taxonomy-row">
                        <div class="detail-panel__taxonomy-label">${{label}}</div>
                        <div class="detail-panel__taxonomy-empty">None</div>
                    </div>
                `;
            }}
            return `
                <div class="detail-panel__taxonomy-row">
                    <div class="detail-panel__taxonomy-label">${{label}}</div>
                    <div class="detail-panel__taxonomy-badges">
                        ${{values.map(value => `<span class="taxonomy-badge taxonomy-badge--${{type}}">${{escapeHtml(value)}}</span>`).join("")}}
                    </div>
                </div>
            `;
        }}

        function renderNodeTaxonomy(details) {{
            return `
                <section class="detail-panel__taxonomy">
                    ${{renderTaxonomyRow("Role", details.role_tags || [], "role")}}
                    ${{renderTaxonomyRow("Domain", details.domain_tags || [], "domain")}}
                    ${{renderTaxonomyRow("Infrastructure", details.infra_tags || [], "infra")}}
                </section>
            `;
        }}

        function renderCompareColumn(details, side, label) {{
            const activeClass = selectedState.activeSide === side
                ? " detail-panel__compare-column--active"
                : "";
            return `
                <section class="detail-panel__compare-column${{activeClass}}" data-panel-side="${{side}}">
                    <div class="detail-panel__compare-column-header">
                        <div class="detail-panel__compare-label">
                            <span class="detail-panel__compare-badge">${{label}}</span>
                        </div>
                        <div class="detail-panel__title">${{escapeHtml(details.name)}}</div>
                        <div class="detail-panel__subtitle">${{escapeHtml(details.ticker || "N/A")}} · Tier ${{details.tier}}</div>
                        <div class="detail-panel__stats">Connections ${{details.degree}}</div>
                    </div>
                    ${{renderNodeTaxonomy(details)}}
                    ${{renderSinglePanelSections(details, side)}}
                </section>
            `;
        }}

        function renderSinglePanel(node) {{
            const details = buildNodeDetails(node);
            detailEmpty.hidden = true;
            detailContent.hidden = false;
            detailPanel.classList.remove("is-empty");
            detailPanel.classList.add("detail-panel--open");
            detailContent.innerHTML = `
                <div class="detail-panel__header">
                    <button id="detail-close" class="detail-panel__close" type="button">×</button>
                    <div class="detail-panel__title">${{escapeHtml(details.name)}}</div>
                    <div class="detail-panel__subtitle">${{escapeHtml(details.ticker || "N/A")}} · Tier ${{details.tier}}</div>
                    <div class="detail-panel__stats">Connections ${{details.degree}}</div>
                    <div class="detail-panel__actions">
                        <button class="detail-panel__action detail-panel__compare-trigger" type="button" data-action="start-compare">Compare</button>
                    </div>
                </div>
                ${{renderNodeTaxonomy(details)}}
                ${{renderSinglePanelSections(details, "primary")}}
            `;
            detailContent.scrollTop = 0;
        }}

        function renderComparePendingPanel(node) {{
            const details = buildNodeDetails(node);
            detailEmpty.hidden = true;
            detailContent.hidden = false;
            detailPanel.classList.remove("is-empty");
            detailPanel.classList.add("detail-panel--open");
            detailContent.innerHTML = `
                <div class="detail-panel__header">
                    <button id="detail-close" class="detail-panel__close" type="button">×</button>
                    <div class="detail-panel__title">${{escapeHtml(details.name)}}</div>
                    <div class="detail-panel__subtitle">${{escapeHtml(details.ticker || "N/A")}} · Tier ${{details.tier}}</div>
                    <div class="detail-panel__stats">Connections ${{details.degree}}</div>
                    <div class="detail-panel__actions">
                        <button class="detail-panel__action" type="button" data-action="exit-compare">Exit Compare</button>
                    </div>
                </div>
                ${{renderNodeTaxonomy(details)}}
                <section class="detail-panel__section">
                    <div class="detail-panel__compare-pending">
                        <div class="detail-panel__compare-pending-title">Compare Ready</div>
                        <div class="detail-panel__compare-pending-copy">Select another company to compare.</div>
                    </div>
                </section>
                ${{renderSinglePanelSections(details, "primary")}}
            `;
            detailContent.scrollTop = 0;
        }}

        function renderComparePanel(primaryNode, secondaryNode) {{
            const primary = buildNodeDetails(primaryNode);
            const secondary = buildNodeDetails(secondaryNode);
            const neighborhoodFocusLink = selectedState.neighborhoodRelationKey
                ? links.find(linkItem => getRelationKey(linkItem) === selectedState.neighborhoodRelationKey)
                : null;
            const neighborhoodSummary = neighborhoodFocusLink
                ? `${{escapeHtml(neighborhoodFocusLink.source.name)}} -> ${{escapeHtml(neighborhoodFocusLink.target.name)}}`
                : selectedState.neighborhoodRelationKey;
            detailEmpty.hidden = true;
            detailContent.hidden = false;
            detailPanel.classList.remove("is-empty");
            detailPanel.classList.add("detail-panel--open");
            detailContent.innerHTML = `
                <div class="detail-panel__header">
                    <button id="detail-close" class="detail-panel__close" type="button">×</button>
                    <div class="detail-panel__title">Compare Companies</div>
                    <div class="detail-panel__compare-summary">
                        <div class="detail-panel__compare-summary-item"><strong>A:</strong> ${{escapeHtml(primary.name)}}</div>
                        <div class="detail-panel__compare-summary-item"><strong>B:</strong> ${{escapeHtml(secondary.name)}}</div>
                    </div>
                    ${{selectedState.neighborhoodMode === "relation" && selectedState.neighborhoodRelationKey ? `
                        <div class="detail-panel__neighborhood">
                            <div class="detail-panel__neighborhood-label">Neighborhood Focus</div>
                            <div class="detail-panel__neighborhood-summary">${{escapeHtml(neighborhoodSummary)}}</div>
                            <div class="detail-panel__neighborhood-meta">Active: ${{selectedState.activeSide === "secondary" ? "B" : "A"}}</div>
                            <button class="detail-panel__action" type="button" data-action="exit-neighborhood">Exit Neighborhood</button>
                        </div>
                    ` : ""}}
                    <div class="detail-panel__actions">
                        <button class="detail-panel__action" type="button" data-action="clear-secondary">Clear B</button>
                        <button class="detail-panel__action" type="button" data-action="swap-compare">Swap A/B</button>
                        <button class="detail-panel__action" type="button" data-action="exit-compare">Exit Compare</button>
                    </div>
                </div>
                <div class="detail-panel__compare-grid">
                    ${{renderCompareColumn(primary, "primary", "A")}}
                    ${{renderCompareColumn(secondary, "secondary", "B")}}
                </div>
            `;
            detailContent.scrollTop = 0;
        }}

        function renderDetailPanel(node) {{
            if (!node) {{
                hideDetailPanel();
                return;
            }}
            if (selectedState.compareMode === "pending") {{
                renderComparePendingPanel(node);
                return;
            }}
            if (selectedState.compareMode === "active" && selectedState.secondaryNodeId) {{
                renderComparePanel(
                    nodeById.get(selectedState.primaryNodeId),
                    nodeById.get(selectedState.secondaryNodeId),
                );
                return;
            }}
            renderSinglePanel(node);
        }}

        function hideDetailPanel() {{
            detailPanel.classList.add("is-empty");
            detailPanel.classList.remove("detail-panel--open");
            detailEmpty.hidden = false;
            detailContent.hidden = true;
        }}

        function startCompareMode() {{
            if (!selectedState.primaryNodeId) return;
            selectedState.compareMode = "pending";
            selectedState.secondaryNodeId = null;
            selectedState.secondaryRelationKey = null;
            selectedState.activeSide = "primary";
            renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
            applySelectionState();
        }}

        function clearSecondaryNode() {{
            selectedState.secondaryNodeId = null;
            selectedState.secondaryRelationKey = null;
            selectedState.compareMode = selectedState.primaryNodeId ? "pending" : "off";
            selectedState.activeSide = "primary";
            selectedState.neighborhoodMode = "off";
            selectedState.neighborhoodRelationKey = null;
            if (selectedState.primaryNodeId) {{
                renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
            }} else {{
                hideDetailPanel();
            }}
            applySelectionState();
        }}

        function swapCompareSides() {{
            if (selectedState.compareMode !== "active" || !selectedState.secondaryNodeId) return;

            const nextPrimaryNodeId = selectedState.secondaryNodeId;
            const nextSecondaryNodeId = selectedState.primaryNodeId;
            const nextPrimaryRelationKey = selectedState.secondaryRelationKey;
            const nextSecondaryRelationKey = selectedState.primaryRelationKey;
            const nextActiveSide = selectedState.activeSide === "primary" ? "secondary" : "primary";
            const nextExpandedRelationKeys = new Set(expandedRelationKeys);
            const survivingRelationKeys = new Set(
                [nextPrimaryRelationKey, nextSecondaryRelationKey].filter(Boolean)
            );
            const nextNeighborhoodRelationKey = survivingRelationKeys.has(selectedState.neighborhoodRelationKey)
                ? selectedState.neighborhoodRelationKey
                : null;
            const nextNeighborhoodMode = nextNeighborhoodRelationKey ? "relation" : "off";

            selectedState.primaryNodeId = nextPrimaryNodeId;
            selectedState.secondaryNodeId = nextSecondaryNodeId;
            selectedState.primaryRelationKey = nextPrimaryRelationKey;
            selectedState.secondaryRelationKey = nextSecondaryRelationKey;
            selectedState.activeSide = nextActiveSide;
            selectedState.neighborhoodMode = nextNeighborhoodMode;
            selectedState.neighborhoodRelationKey = nextNeighborhoodRelationKey;
            expandedRelationKeys.clear();
            nextExpandedRelationKeys.forEach(key => expandedRelationKeys.add(key));

            renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
            applySelectionState();
        }}

        function exitNeighborhoodMode() {{
            selectedState.neighborhoodMode = "off";
            selectedState.neighborhoodRelationKey = null;
            renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
            applySelectionState();
        }}

        function exitCompareMode() {{
            selectedState.secondaryNodeId = null;
            selectedState.compareMode = "off";
            selectedState.secondaryRelationKey = null;
            selectedState.activeSide = "primary";
            selectedState.neighborhoodMode = "off";
            selectedState.neighborhoodRelationKey = null;
            if (selectedState.primaryNodeId) {{
                renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
            }} else {{
                hideDetailPanel();
            }}
            applySelectionState();
        }}

        function setSelectedRelation(side, relationKey) {{
            selectedState.activeSide = side;
            if (side === "secondary") {{
                selectedState.secondaryRelationKey = relationKey;
            }} else {{
                selectedState.primaryRelationKey = relationKey;
            }}
            if (selectedState.compareMode === "active" && selectedState.secondaryNodeId) {{
                selectedState.neighborhoodMode = "relation";
                selectedState.neighborhoodRelationKey = relationKey;
            }}
            renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
            applySelectionState();
        }}

        function nodeMatchesActiveFilters(nodeDatum, role, domain, infra) {{
            if (nodeDatum.tier === 0) return true;
            const roleMatch = role === "all" || nodeDatum.role_tags.includes(role);
            const domainMatch = domain === "all" || nodeDatum.domain_tags.includes(domain);
            const infraMatch = infra === "all" || nodeDatum.infra_tags.includes(infra);
            return roleMatch && domainMatch && infraMatch;
        }}

        function applyBaseState(role, domain, infra) {{
            const matchedNodes = nodes.filter(n => nodeMatchesActiveFilters(n, role, domain, infra));
            const matchedNodeIds = new Set(matchedNodes.map(n => n.id));
            const tierZeroIds = new Set(nodes.filter(n => n.tier === 0).map(n => n.id));
            const visibleNodeIds = new Set([...tierZeroIds, ...matchedNodeIds]);
            links.forEach(link => {{
                if (matchedNodeIds.has(link.source.id) || matchedNodeIds.has(link.target.id)) {{
                    visibleNodeIds.add(link.source.id);
                    visibleNodeIds.add(link.target.id);
                }}
            }});
            const contextNodeIds = new Set([...visibleNodeIds].filter(id => !matchedNodeIds.has(id)));

            node.classed("dimmed", n => !visibleNodeIds.has(n.id))
                .classed("highlighted", n => matchedNodeIds.has(n.id))
                .classed("context-node", n => contextNodeIds.has(n.id))
                .classed("neighborhood-focus", false)
                .classed("neighborhood-node", false)
                .classed("neighborhood-dimmed", false);
            link.classed("dimmed", l => !(visibleNodeIds.has(l.source.id) && visibleNodeIds.has(l.target.id)))
                .classed("highlighted", l => matchedNodeIds.has(l.source.id) && matchedNodeIds.has(l.target.id))
                .classed("context-link", l => visibleNodeIds.has(l.source.id) && visibleNodeIds.has(l.target.id) && !(matchedNodeIds.has(l.source.id) && matchedNodeIds.has(l.target.id)))
                .classed("neighborhood-focus", false)
                .classed("neighborhood-edge", false)
                .classed("neighborhood-dimmed", false);
        }}

        function clearSelection() {{
            selectedState.primaryNodeId = null;
            selectedState.secondaryNodeId = null;
            selectedState.compareMode = "off";
            selectedState.primaryRelationKey = null;
            selectedState.secondaryRelationKey = null;
            selectedState.activeSide = "primary";
            selectedState.neighborhoodMode = "off";
            selectedState.neighborhoodRelationKey = null;
            expandedRelationKeys.clear();
            hideDetailPanel();
            applySelectionState();
        }}

        function buildNeighborhoodState(relationKey) {{
            const focusLink = links.find(linkItem => getRelationKey(linkItem) === relationKey);
            if (!focusLink) return null;

            const endpointIds = new Set([focusLink.source.id, focusLink.target.id]);
            const neighborhoodNodeIds = new Set(endpointIds);
            const neighborhoodEdgeKeys = new Set([relationKey]);

            links.forEach(linkItem => {{
                const linkKey = getRelationKey(linkItem);
                if (endpointIds.has(linkItem.source.id) || endpointIds.has(linkItem.target.id)) {{
                    neighborhoodNodeIds.add(linkItem.source.id);
                    neighborhoodNodeIds.add(linkItem.target.id);
                    neighborhoodEdgeKeys.add(linkKey);
                }}
            }});

            return {{
                focusRelationKey: relationKey,
                endpointIds,
                neighborhoodNodeIds,
                neighborhoodEdgeKeys,
            }};
        }}

        function applySelectionState() {{
            if (!selectedState.primaryNodeId) {{
                applyBaseState(currentRole, currentDomain, currentInfra);
                return;
            }}
            const primaryDetails = buildNodeDetails(nodeById.get(selectedState.primaryNodeId));
            const secondaryDetails = selectedState.secondaryNodeId
                ? buildNodeDetails(nodeById.get(selectedState.secondaryNodeId))
                : null;
            const activeRelationKey = selectedState.activeSide === "secondary"
                ? selectedState.secondaryRelationKey
                : selectedState.primaryRelationKey;
            const compareNodeIds = new Set([
                primaryDetails.id,
                ...primaryDetails.neighborIds,
                ...(secondaryDetails ? [secondaryDetails.id, ...secondaryDetails.neighborIds] : []),
            ]);
            if (selectedState.neighborhoodMode === "relation" && selectedState.neighborhoodRelationKey) {{
                const neighborhoodState = buildNeighborhoodState(selectedState.neighborhoodRelationKey);
                if (!neighborhoodState) {{
                    selectedState.neighborhoodMode = "off";
                    selectedState.neighborhoodRelationKey = null;
                    renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
                }} else {{
                    node
                        .classed("context-node", false)
                        .classed("neighborhood-focus", d => neighborhoodState.endpointIds.has(d.id))
                        .classed("neighborhood-node", d => neighborhoodState.neighborhoodNodeIds.has(d.id))
                        .classed("neighborhood-dimmed", d => !neighborhoodState.neighborhoodNodeIds.has(d.id))
                        .classed("highlighted", d => neighborhoodState.endpointIds.has(d.id))
                        .classed("dimmed", false);
                    link
                        .classed("context-link", false)
                        .classed("neighborhood-focus", l => getRelationKey(l) === neighborhoodState.focusRelationKey)
                        .classed("neighborhood-edge", l => neighborhoodState.neighborhoodEdgeKeys.has(getRelationKey(l)))
                        .classed("neighborhood-dimmed", l => !neighborhoodState.neighborhoodEdgeKeys.has(getRelationKey(l)))
                        .classed("highlighted", l => getRelationKey(l) === neighborhoodState.focusRelationKey)
                        .classed("dimmed", false);
                    return;
                }}
            }}
            node
                .classed("context-node", false)
                .classed("neighborhood-focus", false)
                .classed("neighborhood-node", false)
                .classed("neighborhood-dimmed", false)
                .classed("highlighted", d => {{
                    if (activeRelationKey) {{
                        return links.some(l =>
                            getRelationKey(l) === activeRelationKey &&
                            (l.source.id === d.id || l.target.id === d.id)
                        );
                    }}
                    return compareNodeIds.has(d.id);
                }})
                .classed("dimmed", d => !compareNodeIds.has(d.id));
            link
                .classed("context-link", false)
                .classed("neighborhood-focus", false)
                .classed("neighborhood-edge", false)
                .classed("neighborhood-dimmed", false)
                .classed("highlighted", l => {{
                    if (activeRelationKey) return getRelationKey(l) === activeRelationKey;
                    if (!secondaryDetails) {{
                        return l.source.id === primaryDetails.id || l.target.id === primaryDetails.id;
                    }}
                    return (
                        l.source.id === primaryDetails.id ||
                        l.target.id === primaryDetails.id ||
                        l.source.id === secondaryDetails.id ||
                        l.target.id === secondaryDetails.id
                    );
                }})
                .classed("dimmed", l => {{
                    if (activeRelationKey) return getRelationKey(l) !== activeRelationKey;
                    if (!secondaryDetails) {{
                        return l.source.id !== primaryDetails.id && l.target.id !== primaryDetails.id;
                    }}
                    return (
                        l.source.id !== primaryDetails.id &&
                        l.target.id !== primaryDetails.id &&
                        l.source.id !== secondaryDetails.id &&
                        l.target.id !== secondaryDetails.id
                    );
                }});
        }}

        function bindDetailPanelEvents() {{
            detailContent.addEventListener("click", event => {{
                const closeBtn = event.target.closest("#detail-close");
                if (closeBtn) {{
                    clearSelection();
                    return;
                }}
                const actionBtn = event.target.closest("[data-action]");
                if (actionBtn) {{
                    const action = actionBtn.dataset.action;
                    if (action === "start-compare") startCompareMode();
                    if (action === "clear-secondary") clearSecondaryNode();
                    if (action === "swap-compare") swapCompareSides();
                    if (action === "exit-neighborhood") exitNeighborhoodMode();
                    if (action === "exit-compare") exitCompareMode();
                    return;
                }}
                const relationCard = event.target.closest(".relation-card");
                if (!relationCard) return;
                const side = relationCard.dataset.panelSide || "primary";
                setSelectedRelation(side, relationCard.dataset.relationKey);
            }});
        }}

        function selectNode(nodeDatum) {{
            if (!selectedState.primaryNodeId) {{
                expandedRelationKeys.clear();
                selectedState.primaryNodeId = nodeDatum.id;
                selectedState.secondaryNodeId = null;
                selectedState.compareMode = "off";
                selectedState.primaryRelationKey = null;
                selectedState.secondaryRelationKey = null;
                selectedState.activeSide = "primary";
                applySelectionState();
                renderDetailPanel(nodeDatum);
                return;
            }}

            if (selectedState.compareMode === "pending") {{
                if (nodeDatum.id === selectedState.primaryNodeId) {{
                    return;
                }}
                expandedRelationKeys.clear();
                selectedState.secondaryNodeId = nodeDatum.id;
                selectedState.secondaryRelationKey = null;
                selectedState.compareMode = "active";
                selectedState.activeSide = "secondary";
                applySelectionState();
                renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
                return;
            }}

            if (selectedState.compareMode === "active") {{
                if (nodeDatum.id === selectedState.primaryNodeId) {{
                    return;
                }}
                expandedRelationKeys.clear();
                selectedState.secondaryNodeId = nodeDatum.id;
                selectedState.secondaryRelationKey = null;
                selectedState.activeSide = "secondary";
                selectedState.neighborhoodMode = "off";
                selectedState.neighborhoodRelationKey = null;
                applySelectionState();
                renderDetailPanel(nodeById.get(selectedState.primaryNodeId));
                return;
            }}

            expandedRelationKeys.clear();
            selectedState.primaryNodeId = nodeDatum.id;
            selectedState.primaryRelationKey = null;
            selectedState.activeSide = "primary";
            applySelectionState();
            renderDetailPanel(nodeDatum);
        }}

        const svg = d3.select("#chart").append("svg")
            .attr("width", width).attr("height", height)
            .call(d3.zoom().scaleExtent([0.1, 4]).on("zoom", e => container.attr("transform", e.transform)));

        const container = svg.append("g");

        svg.append("defs").append("marker")
            .attr("id","arrowhead").attr("viewBox","0 -5 10 10")
            .attr("refX",22).attr("refY",0).attr("orient","auto")
            .attr("markerWidth",4).attr("markerHeight",4)
            .append("svg:path").attr("d","M 0,-5 L 10,0 L 0,5").attr("class","marker");

        const simulation = d3.forceSimulation(nodes)
            .force("link", d3.forceLink(links).id(d => d.id).distance(110))
            .force("charge", d3.forceManyBody().strength(-220))
            .force("center", d3.forceCenter(width/2, height/2))
            .force("collision", d3.forceCollide().radius(38));

        const link = container.append("g").selectAll("line")
            .data(links).enter().append("line")
            .attr("class","link").attr("marker-end","url(#arrowhead)");

        const node = container.append("g").selectAll("g")
            .data(nodes).enter().append("g").attr("class","node")
            .classed("market-bucket", d => d.node_kind === "market_bucket")
            .call(d3.drag()
                .on("start", (e,d) => {{ selectedState.suppressClearUntil = Date.now() + 250; if(!e.active) simulation.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
                .on("drag",  (e,d) => {{ d.fx=e.x; d.fy=e.y; }})
                .on("end",   (e,d) => {{ if(!e.active) simulation.alphaTarget(0); d.fx=null; d.fy=null; }}));

        node.append("circle")
            .attr("r", d => d.tier===0 ? 11 : 7)
            .attr("fill", d => tierColors[Math.min(d.tier,5)]);

        node.append("text").attr("dx",13).attr("dy",".35em")
            .text(d => d.ticker ? `${{d.name}} (${{d.ticker}})` : d.name);

        // Tooltip
        const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
        function escapeHtml(s) {{
            if (s == null) return '';
            return String(s)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }}
        const tooltip = d3.select("#tooltip");
        node.on("mouseover", (e, d) => {{
            const inLinks  = (linksByTarget.get(d.id) || []).map(l=>`← ${{esc(l.source.name)}}`);
            const outLinks = (linksBySource.get(d.id) || []).map(l=>`→ ${{esc(l.target.name)}}`);
            const lines = [
                `<strong>${{esc(d.name)}}</strong>`,
                d.ticker ? `Ticker: ${{esc(d.ticker)}}` : "",
                `Tier: ${{d.tier}}`,
                d.tags ? `Tags: ${{esc(d.tags)}}` : "",
                inLinks.length  ? `<br>⬅ 客戶: ${{inLinks.slice(0,3).join(", ")}}${{inLinks.length>3?" …":""}}` : "",
                outLinks.length ? `<br>➡ 供應: ${{outLinks.slice(0,3).join(", ")}}${{outLinks.length>3?" …":""}}` : "",
            ].filter(Boolean);
            tooltip.style("display","block").html(lines.join("<br>"))
                .style("left", (e.pageX+14)+"px").style("top", (e.pageY-10)+"px");
        }}).on("mousemove", e => {{
            tooltip.style("left",(e.pageX+14)+"px").style("top",(e.pageY-10)+"px");
        }}).on("mouseout", () => tooltip.style("display","none"));

        node.on("click", (e, d) => {{
            e.stopPropagation();
            selectNode(d);
        }});
        svg.on("click", event => {{
            if (Date.now() < selectedState.suppressClearUntil) return;
            if (event.defaultPrevented) return;
            clearSelection();
        }});

        simulation.on("tick", () => {{
            link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
                .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
            node.attr("transform",d=>`translate(${{d.x}},${{d.y}})`);
        }});

        // ---- Filter logic ----
        let currentRole = "all";
        let currentDomain = "all";
        let currentInfra = "all";
        const activeFiltersEl = document.getElementById("active-filters");
        const clearFiltersBtn = document.getElementById("clear-filters");

        function getChipLabel(group, value) {{
            const selector = `.chip[data-group="${{group}}"][data-${{group}}="${{value}}"], .chip[data-filter-group="${{group}}"][data-${{group}}="${{value}}"]`;
            const chip = document.querySelector(selector);
            return chip ? chip.textContent.trim() : value;
        }}

        function renderActiveFilters() {{
            const activeFilters = [];
            if (currentRole !== "all") {{
                const roleLabel = getChipLabel("role", currentRole);
                activeFilters.push(`Role: ${{roleLabel}}`);
            }}
            if (currentDomain !== "all") {{
                const domainLabel = getChipLabel("domain", currentDomain);
                activeFilters.push(`Domain: ${{domainLabel}}`);
            }}
            if (currentInfra !== "all") {{
                const infraLabel = getChipLabel("infra", currentInfra);
                activeFilters.push(`Infra: ${{infraLabel}}`);
            }}
            activeFiltersEl.innerHTML = activeFilters.length
                ? activeFilters.map(item => `<span class="active-filter-badge">${{escapeHtml(item)}}</span>`).join("")
                : "Filters: All";
            clearFiltersBtn.hidden = activeFilters.length === 0;
        }}

        function setChipState(group, value) {{
            document
                .querySelectorAll(`.chip[data-group="${{group}}"], .chip[data-filter-group="${{group}}"]`)
                .forEach(c => {{
                    const chipValue = c.dataset[group];
                    c.classList.toggle("active", chipValue === value);
                }});
        }}

        function setBaseFilterState(role, domain, infra) {{
            currentRole = role;
            currentDomain = domain;
            currentInfra = infra;
            renderActiveFilters();
            applyBaseState(role, domain, infra);
        }}

        function applyFilters(role, domain) {{
            selectedState.primaryNodeId = null;
            selectedState.secondaryNodeId = null;
            selectedState.compareMode = "off";
            selectedState.primaryRelationKey = null;
            selectedState.secondaryRelationKey = null;
            selectedState.activeSide = "primary";
            selectedState.neighborhoodMode = "off";
            selectedState.neighborhoodRelationKey = null;
            expandedRelationKeys.clear();
            hideDetailPanel();
            setBaseFilterState(role, domain, currentInfra);
        }}

        bindDetailPanelEvents();

        clearFiltersBtn.addEventListener("click", () => {{
            setChipState("role", "all");
            setChipState("domain", "all");
            setChipState("infra", "all");
            document.getElementById("search").value = "";
            document.getElementById("results").style.display = "none";
            currentInfra = "all";
            applyFilters("all", "all");
        }});

        document.querySelectorAll(".chip").forEach(chip => {{
            chip.addEventListener("click", () => {{
                const group = chip.dataset.group || chip.dataset.filterGroup;
                setChipState(group, chip.dataset[group]);
                const nextRole = group === "role" ? chip.dataset.role : currentRole;
                const nextDomain = group === "domain" ? chip.dataset.domain : currentDomain;
                const nextInfra = group === "infra" ? chip.dataset.infra : currentInfra;
                currentInfra = nextInfra;
                applyFilters(nextRole, nextDomain);
                document.getElementById("search").value = "";
                document.getElementById("results").style.display = "none";
            }});
        }});

        // ---- Search logic ----
        const searchInput = document.getElementById("search");
        const resultsDiv  = document.getElementById("results");

        searchInput.addEventListener("input", () => {{
            const q = searchInput.value.trim().toLowerCase();
            if (!q) {{
                resultsDiv.style.display = "none";
                applyBaseState(currentRole, currentDomain, currentInfra);
                if (selectedState.primaryNodeId) {{
                    applySelectionState();
                }}
                return;
            }}

            const matches = nodes.filter(n => {{
                if (!nodeMatchesActiveFilters(n, currentRole, currentDomain, currentInfra)) return false;
                const roleText = (n.role_tags || []).join(" ");
                const domainText = (n.domain_tags || []).join(" ");
                const infraText = (n.infra_tags || []).join(" ");
                return `${{n.name}} ${{n.ticker || ""}} ${{n.tags || ""}} ${{roleText}} ${{domainText}} ${{infraText}}`
                    .toLowerCase()
                    .includes(q);
            }});

            // Highlight matches
            const matchIds = new Set(matches.map(n=>n.id));
            node.classed("dimmed", n => !matchIds.has(n.id)).classed("highlighted", false).classed("context-node", false);
            link.classed("dimmed", l => !matchIds.has(l.source.id) && !matchIds.has(l.target.id)).classed("context-link", false);

            // Show list
            if (matches.length===0) {{
                resultsDiv.innerHTML = `<div style="color:#6c757d;font-size:12px;padding:4px 6px">找不到結果</div>`;
            }} else {{
                resultsDiv.innerHTML = matches.map(n => {{
                    const color = tierColors[Math.min(n.tier,5)];
                    return `<div class="result-item" data-id="${{n.id}}">
                        <div class="result-dot" style="background:${{color}}"></div>
                        <span class="result-name">${{esc(n.name)}}</span>
                        <span class="result-ticker">${{esc(n.ticker||"")}}</span>
                    </div>`;
                }}).join("");
                resultsDiv.querySelectorAll(".result-item").forEach(el => {{
                    el.addEventListener("click", () => {{
                        const nid = +el.dataset.id;
                        const d = nodeById.get(nid);
                        if (!d) return;
                        svg.transition().duration(600).call(
                            d3.zoom().transform,
                            d3.zoomIdentity.translate(width/2 - d.x, height/2 - d.y)
                        );
                        selectNode(d);
                    }});
                }});
            }}
            resultsDiv.style.display = "block";
        }});
    </script>
</body>
</html>"""

    html_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(html_path) + ".tmp"
    Path(tmp_path).write_text(html_content, encoding="utf-8")
    os.replace(tmp_path, html_path)
    favicon_path = html_path.parent / "favicon.ico"
    favicon_path.write_bytes(base64.b64decode(FAVICON_ICO_BASE64))
    print(f"index.html updated: {len(tiers_list)} companies, {len(links)} links")


if __name__ == "__main__":
    generate_graph_html()
