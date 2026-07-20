import json
import os
import sqlite3
import yaml
import sys
import argparse
import re
from collections import deque
from collections import Counter
from pathlib import Path
from datetime import datetime

# Setup paths
CPO_CHAIN_DIR = Path(__file__).resolve().parent
BASE_DIR = CPO_CHAIN_DIR.parent
sys.path.append(str(BASE_DIR))

try:
    from utils import get_db_conn
except ImportError:
    def get_db_conn(p): return sqlite3.connect(p)

try:
    from . import db as usci_db
except ImportError:
    import db as usci_db

try:
    from .normalization import (
        PLACEHOLDER_NODES,
        base_company_name,
        canonical_company_name,
    )
except ImportError:
    from normalization import (
        PLACEHOLDER_NODES,
        base_company_name,
        canonical_company_name,
    )

DB_PATH = BASE_DIR / "tweets.db"
KEYWORDS_PATH = CPO_CHAIN_DIR / "keywords.yaml"
OUTPUT_DIR = CPO_CHAIN_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

RUNTIME_QC_FILENAME = "usci_runtime_qc.json"
RUNTIME_QC_SCHEMA_VERSION = 1
RUNTIME_QC_MAX_RUNS = 30

_CONTEXT_ALIASES = {
    "ai server": "AI Server",
    "ai_server": "AI Server",
    "advanced packaging": "Advanced Packaging",
    "advanced_packaging": "Advanced Packaging",
    "cpo/silicon photonics": "CPO / Silicon Photonics",
    "cpo / silicon photonics": "CPO / Silicon Photonics",
}

_RUNTIME_MARKET_BUCKET_NODES = {
    "AI Data Centers",
    "AI Chip Manufacturers",
    "All Businesses",
    "Datacenter Customers",
    "Epiwafer manufacturers",
    "Glass substrate manufacturers",
    "Global Silicon Photonics Leader",
    "HBM",
    "Hyperscalers",
    "Liquid cooling pump manufacturers",
    "Major Hyperscale Customer",
    "Major Silicon Photonics Customer",
    "Optical Companies",
    "Packaging Companies",
    "Photonics Industry",
    "Photonics Supply Chain",
    "Power Infrastructure Market",
    "Robotic supply chain",
    "Semiconductor Industry",
    "Server rack manufacturers",
    "SiPh Upstream Partners",
    "Silicon Photonics Manufacturers",
    "Space Applications",
    "Three Global Memory Giants",
    "Unknown Optical Transceiver Company",
    "US big tech",
    "upstream semi supply chain companies",
    "pluggable optical transceiver companies",
    "CPO Industry",
    "CPO Manufacturers",
    "CPO Market",
}

_RUNTIME_CONTEXT_EXCLUDED_NODES = {
    "CPO": {
        "Data Center/AI Infrastructure Providers",
    },
    "Wireless Infrastructure": {
        "Blue Origin",
        "Boeing",
        "Boston Dynamics",
        "Lockheed Martin",
        "NASA",
        "Raytheon",
    },
}

def _runtime_excluded_names_for_context(canonical_ctx: str) -> set[str]:
    return (
        _RUNTIME_MARKET_BUCKET_NODES
        | PLACEHOLDER_NODES
        | _RUNTIME_CONTEXT_EXCLUDED_NODES.get(canonical_ctx, set())
    )


def normalize_industry_context(context: str) -> str:
    raw = (context or "").strip()
    if not raw:
        return "Other"
    alias_key = re.sub(r"\s+", " ", raw.replace("_", " ")).strip().lower()
    alias_key = re.sub(r"\s*/\s*", " / ", alias_key)
    if alias_key in _CONTEXT_ALIASES:
        return _CONTEXT_ALIASES[alias_key]

    pretty = re.sub(r"\s+", " ", raw.replace("_", " ")).strip()
    pretty = re.sub(r"\s*/\s*", " / ", pretty)
    words = []
    for word in pretty.split(" "):
        upper = word.upper()
        if upper in {"AI", "CPO", "HBM", "OCS", "LEO"}:
            words.append(upper)
        elif word.lower() == "nvidia":
            words.append("NVIDIA")
        elif re.match(r"^\d+(?:\.\d+)?[a-z]$", word, re.IGNORECASE):
            words.append(word[:-1] + word[-1].upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)


def should_export_context(tiers_data: list[dict], links: list[dict]) -> bool:
    return bool(tiers_data and links)


def _get_context_groups(conn: sqlite3.Connection) -> dict[str, list[str]]:
    contexts = [r[0] for r in conn.execute("SELECT DISTINCT industry_context FROM industry_relations").fetchall()]
    if not contexts:
        return {"CPO": ["CPO"]}
    grouped: dict[str, list[str]] = {}
    for raw in contexts:
        canonical = normalize_industry_context(raw)
        grouped.setdefault(canonical, []).append(raw)
    return grouped


def _merge_context_records(tiers_data: list[dict], links: list[dict], canonical_ctx: str):
    known_names = {base_company_name(row["name"]) for row in tiers_data if row.get("name")}
    sorted_names = sorted(known_names, key=len)
    node_map = {
        row["id"]: canonical_company_name(row["name"], sorted_names)
        for row in tiers_data
    }

    merged_tiers: dict[str, dict] = {}
    for row in tiers_data:
        label = node_map[row["id"]]
        current = merged_tiers.get(label)
        next_row = {**row, "name": label}
        if current is None or row["tier"] < current["tier"]:
            merged_tiers[label] = next_row
        elif not current.get("ticker") and next_row.get("ticker"):
            merged_tiers[label]["ticker"] = next_row["ticker"]

    merged_links: list[dict] = []
    seen_links: set[tuple[str, str, str, str]] = set()
    for link in links:
        source_name = node_map.get(link["source"])
        target_name = node_map.get(link["target"])
        if not source_name or not target_name or source_name == target_name:
            continue
        link_key = (source_name, target_name, link.get("role") or "", canonical_ctx)
        if link_key in seen_links:
            continue
        seen_links.add(link_key)
        merged_links.append({**link, "source_name": source_name, "target_name": target_name})

    merged_tier_rows = sorted(merged_tiers.values(), key=lambda item: (item["tier"], item["name"]))
    merged_link_rows = sorted(
        merged_links,
        key=lambda item: (item["source_name"], item["target_name"], item.get("role") or ""),
    )
    return merged_tier_rows, merged_link_rows


def _assign_runtime_tiers(node_names: list[str], links: list[dict]) -> dict[str, int]:
    if not node_names:
        return {}

    outgoing = {name: set() for name in node_names}
    incoming = {name: set() for name in node_names}
    for link in links:
        source_name = link.get("source_name")
        target_name = link.get("target_name")
        if not source_name or not target_name or source_name == target_name:
            continue
        if source_name not in outgoing or target_name not in outgoing:
            continue
        outgoing[source_name].add(target_name)
        incoming[target_name].add(source_name)

    sinks = sorted(name for name in node_names if not outgoing[name])
    if not sinks:
        return {name: 0 for name in sorted(node_names)}

    tiers: dict[str, int] = {}
    queue = deque((name, 0) for name in sinks)
    while queue:
        name, tier = queue.popleft()
        current = tiers.get(name)
        if current is not None and current <= tier:
            continue
        tiers[name] = tier
        for upstream in sorted(incoming[name]):
            queue.append((upstream, tier + 1))

    unresolved = sorted(name for name in node_names if name not in tiers)
    for name in unresolved:
        if name in tiers:
            continue

        component = set()
        queue = deque([name])
        while queue:
            current = queue.popleft()
            if current in component:
                continue
            component.add(current)
            for neighbor in sorted(outgoing[current] | incoming[current]):
                if neighbor not in component and neighbor not in tiers:
                    queue.append(neighbor)

        base_tier = 1
        for idx, component_name in enumerate(sorted(component)):
            tiers[component_name] = base_tier + idx
    return tiers


def _validate_runtime_section(canonical_ctx: str, tiers: list[dict], links: list[dict]) -> list[str]:
    warnings: list[str] = []
    tier_names = [row["name"] for row in tiers if row.get("name")]
    linked_names = {
        name
        for link in links
        for name in (link.get("source_name"), link.get("target_name"))
        if name
    }

    orphan_names = sorted(name for name in set(tier_names) if name not in linked_names)
    if orphan_names:
        warnings.append(f"[runtime-qc][{canonical_ctx}] orphan nodes: {', '.join(orphan_names)}")

    leaked_excluded = sorted(name for name in set(tier_names) if name in _runtime_excluded_names_for_context(canonical_ctx))
    if leaked_excluded:
        warnings.append(f"[runtime-qc][{canonical_ctx}] excluded nodes leaked: {', '.join(leaked_excluded)}")

    duplicate_names = sorted(name for name, count in Counter(tier_names).items() if count > 1)
    if duplicate_names:
        warnings.append(f"[runtime-qc][{canonical_ctx}] duplicate tier names: {', '.join(duplicate_names)}")

    return warnings


def _collect_runtime_qc(industries: dict[str, dict]) -> dict[str, list[str]]:
    """Group runtime QC warnings by canonical context, omitting clean contexts."""
    qc_by_context: dict[str, list[str]] = {}
    for ctx in sorted(industries):
        section = industries[ctx]
        warnings = _validate_runtime_section(
            ctx, section.get("tiers", []), section.get("links", [])
        )
        if warnings:
            qc_by_context[ctx] = warnings
    return qc_by_context


def _load_qc_history(qc_path: Path) -> list[dict]:
    """Return prior runs, tolerating a missing or corrupt self-contained file."""
    if not qc_path.exists():
        return []
    try:
        payload = json.loads(qc_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    runs = payload.get("runs") if isinstance(payload, dict) else None
    return runs if isinstance(runs, list) else []


def _append_qc_run(
    qc_path: Path,
    qc_by_context: dict[str, list[str]],
    generated_at: str,
    max_runs: int = RUNTIME_QC_MAX_RUNS,
) -> None:
    """Append one QC run and rewrite the bounded, self-describing history file."""
    run_record = {
        "generated_at": generated_at,
        "total_warnings": sum(len(v) for v in qc_by_context.values()),
        "contexts": qc_by_context,
    }
    history = _load_qc_history(qc_path)
    bounded = [*history, run_record][-max_runs:]
    payload = {
        "schema_version": RUNTIME_QC_SCHEMA_VERSION,
        "generated_at": generated_at,
        "max_runs": max_runs,
        "runs": bounded,
    }
    try:
        with open(qc_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"[runtime-qc] failed to persist QC history to {qc_path}: {e}")


def _atomize_context_warnings(warnings: list[str]) -> set[str]:
    """Break each combined "kind: name1, name2" warning into one atom per name.

    _validate_runtime_section always joins every flagged name for one
    category into a single string, never one string per name. Diffing whole
    strings would treat a growing list (A, B -> A, B, C) as an entirely new
    warning and re-alert on A and B even though they were already flagged and
    remain unresolved, not new.
    """
    atoms: set[str] = set()
    for warning in warnings:
        prefix, sep, names_csv = warning.partition(": ")
        if not sep:
            atoms.add(warning)
            continue
        for name in names_csv.split(", "):
            atoms.add(f"{prefix}: {name}")
    return atoms


def diff_qc_runs(
    previous_contexts: dict[str, list[str]], current_contexts: dict[str, list[str]]
) -> dict[str, dict[str, list[str]] | list[str]]:
    """Compare two runs' context->warnings maps for low-noise alerting.

    Diffing happens per individual flagged name (see _atomize_context_warnings),
    not per combined warning string. Returns names that are new since
    `previous_contexts` (a context appearing for the first time counts as
    entirely new) and contexts that were warning-flagged before but are clean
    now. A name persisting unchanged across both runs is reported in neither --
    callers should stay silent in that case rather than re-alert every run.
    """
    new_warnings: dict[str, list[str]] = {}
    for ctx, warnings in current_contexts.items():
        already_seen = _atomize_context_warnings(previous_contexts.get(ctx, []))
        added = sorted(_atomize_context_warnings(warnings) - already_seen)
        if added:
            new_warnings[ctx] = added
    resolved = sorted(ctx for ctx in previous_contexts if ctx not in current_contexts)
    return {"new": new_warnings, "resolved": resolved}


def _build_context_runtime_data(conn: sqlite3.Connection, canonical_ctx: str, raw_contexts: list[str]):
    if not raw_contexts:
        return [], []

    context_placeholders = ", ".join("?" * len(raw_contexts))
    sql = f"""
    SELECT
        r.from_company_id as source,
        source_e.name as source_name_raw,
        source_e.ticker as source_ticker,
        r.to_company_id as target,
        target_e.name as target_name_raw,
        target_e.ticker as target_ticker,
        r.role,
        r.industry_context,
        r.confidence,
        r.edgar_score,
        r.news_score
    FROM industry_relations r
    JOIN industry_entities source_e ON source_e.id = r.from_company_id
    JOIN industry_entities target_e ON target_e.id = r.to_company_id
    WHERE r.status = 'active'
      AND r.industry_context IN ({context_placeholders})
      AND r.from_company_id IS NOT NULL
      AND r.to_company_id IS NOT NULL
    ORDER BY source_e.name, target_e.name, r.role
    """
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    raw_links = [dict(row) for row in cursor.execute(sql, raw_contexts).fetchall()]
    if not raw_links:
        return [], []

    node_rows: dict[int, dict] = {}
    for link in raw_links:
        node_rows[link["source"]] = {
            "id": link["source"],
            "name": link["source_name_raw"],
            "ticker": link.get("source_ticker"),
        }
        node_rows[link["target"]] = {
            "id": link["target"],
            "name": link["target_name_raw"],
            "ticker": link.get("target_ticker"),
        }

    known_names = {base_company_name(row["name"]) for row in node_rows.values() if row.get("name")}
    sorted_names = sorted(known_names, key=len)
    node_map = {
        node_id: canonical_company_name(row["name"], sorted_names)
        for node_id, row in node_rows.items()
    }

    merged_links: list[dict] = []
    seen_links: set[tuple[str, str, str, str]] = set()
    for link in raw_links:
        source_name = node_map.get(link["source"])
        target_name = node_map.get(link["target"])
        if not source_name or not target_name or source_name == target_name:
            continue
        link_key = (source_name, target_name, link.get("role") or "", canonical_ctx)
        if link_key in seen_links:
            continue
        seen_links.add(link_key)
        merged_links.append(
            {
                "source": link["source"],
                "target": link["target"],
                "role": link.get("role"),
                "industry_context": canonical_ctx,
                "confidence": link.get("confidence"),
                "edgar_score": link.get("edgar_score"),
                "news_score": link.get("news_score"),
                "source_name": source_name,
                "target_name": target_name,
            }
        )

    excluded_names = _runtime_excluded_names_for_context(canonical_ctx)
    merged_links = [
        link
        for link in merged_links
        if link["source_name"] not in excluded_names and link["target_name"] not in excluded_names
    ]
    linked_names = {
        name
        for link in merged_links
        for name in (link["source_name"], link["target_name"])
    }

    merged_nodes: dict[str, dict] = {}
    for node_id, row in node_rows.items():
        label = node_map[node_id]
        if label in excluded_names:
            continue
        if linked_names and label not in linked_names:
            continue
        current = merged_nodes.get(label)
        next_row = {"id": node_id, "name": label, "ticker": row.get("ticker")}
        if current is None:
            merged_nodes[label] = next_row
        elif not current.get("ticker") and next_row.get("ticker"):
            merged_nodes[label]["ticker"] = next_row["ticker"]
        elif node_id < current["id"]:
            merged_nodes[label]["id"] = node_id

    if not merged_nodes or not merged_links:
        return [], []

    tier_map = _assign_runtime_tiers(list(merged_nodes.keys()), merged_links)
    merged_tiers = []
    for label, row in merged_nodes.items():
        merged_tiers.append({**row, "tier": tier_map.get(label, 0)})

    merged_tiers.sort(key=lambda item: (item["tier"], item["name"]))
    merged_links.sort(key=lambda item: (item["source_name"], item["target_name"], item.get("role") or ""))
    return merged_tiers, merged_links


def _build_chain_runtime_cache(conn: sqlite3.Connection, context_groups: dict[str, list[str]]):
    industries: dict[str, dict] = {}
    for ctx in sorted(context_groups):
        raw_contexts = context_groups[ctx]
        print(f"Processing context: {ctx} ({', '.join(raw_contexts)})...")
        tiers, links = _build_context_runtime_data(conn, ctx, raw_contexts)
        if not tiers or not links:
            continue
        aliases = sorted({ctx, *raw_contexts})
        industries[ctx] = {
            "aliases": aliases,
            "tiers": tiers,
            "links": links,
        }
    return industries


def _build_rooted_report_sections(
    conn: sqlite3.Connection,
    context_groups: dict[str, list[str]],
    root_tickers: list[str],
):
    sections = []
    for ctx in sorted(context_groups):
        raw_contexts = context_groups[ctx]
        tiers_data = get_chain_data(conn, root_tickers, raw_contexts)
        if not tiers_data:
            continue

        node_ids = [row["id"] for row in tiers_data]
        links = get_all_links(conn, node_ids, raw_contexts)
        if not should_export_context(tiers_data, links):
            continue

        merged_tiers, merged_links = _merge_context_records(tiers_data, links, ctx)
        sections.append(
            {
                "context": ctx,
                "tiers": merged_tiers,
                "links": merged_links,
            }
        )
    return sections

def get_chain_data(conn: sqlite3.Connection, root_tickers: list[str], industry_contexts: list[str] | None = None):
    """
    Calculate Tiers starting from root_tickers for a specific industry context.
    """
    if not root_tickers: return []
    industry_contexts = industry_contexts or ["CPO"]
    ticker_placeholders = ", ".join("?" * len(root_tickers))
    context_placeholders = ", ".join("?" * len(industry_contexts))

    sql = f"""
    WITH RECURSIVE
      hierarchy(id, name, ticker, level, path) AS (
        SELECT c.id, c.name, c.ticker, 0, CAST(c.id AS TEXT)
        FROM industry_entities c
        WHERE c.ticker IN ({ticker_placeholders})

        UNION ALL

        SELECT c.id, c.name, c.ticker, h.level + 1, h.path || ',' || c.id
        FROM industry_entities c
        JOIN (
            SELECT DISTINCT from_company_id, to_company_id
            FROM industry_relations
            WHERE status = 'active' AND industry_context IN ({context_placeholders})
        ) r ON c.id = r.from_company_id
        JOIN hierarchy h ON r.to_company_id = h.id
        WHERE h.level < 5
        AND ',' || h.path || ',' NOT LIKE '%,' || c.id || ',%'
      )
    SELECT id, name, ticker, MIN(level) as tier
    FROM hierarchy
    GROUP BY id, name, ticker
    ORDER BY tier ASC, name ASC;
    """
    try:
        cursor = conn.cursor()
        cursor.row_factory = sqlite3.Row
        return [dict(r) for r in cursor.execute(sql, root_tickers + industry_contexts).fetchall()]
    except Exception as e:
        print(f"CTE Error [{','.join(industry_contexts)}]: {e}")
        return []

def get_all_links(conn: sqlite3.Connection, node_ids: list[int], industry_contexts: list[str] | None = None):
    if not node_ids: return []
    industry_contexts = industry_contexts or ["CPO"]
    id_placeholders = ", ".join("?" * len(node_ids))
    context_placeholders = ", ".join("?" * len(industry_contexts))
    sql = f"""
    SELECT from_company_id as source, to_company_id as target, role, industry_context,
           confidence, edgar_score, news_score
    FROM industry_relations
    WHERE status = 'active' AND industry_context IN ({context_placeholders})
    AND from_company_id IN ({id_placeholders})
    AND to_company_id IN ({id_placeholders});
    """
    try:
        cursor = conn.cursor()
        cursor.row_factory = sqlite3.Row
        return [dict(r) for r in cursor.execute(sql, industry_contexts + node_ids + node_ids).fetchall()]
    except Exception as e:
        print(f"Links Error: {e}")
        return []

def format_conf_md(l) -> str:
    conf = l.get('confidence', 0.5)
    edgar = l.get('edgar_score', 0)
    news = l.get('news_score', 0)
    sources = []
    if edgar > 0: sources.append(f"SEC×{max(1, int(edgar/0.15))}")
    if news > 0: sources.append("News×1")
    badge = "✅" if conf >= 0.8 else ("📄" if conf >= 0.6 else "⚠️")
    src_str = f" ({', '.join(sources)})" if sources else ""
    return f"[{badge} {conf:.2f}{src_str}]"

def export_all():
    conn = get_db_conn(DB_PATH)
    usci_db.init_usci_tables(conn)

    with open(KEYWORDS_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    root_tickers = config.get('root_tickers', ['NVDA'])

    context_groups = _get_context_groups(conn)

    industries = _build_chain_runtime_cache(conn, context_groups)

    qc_generated_at = datetime.now().isoformat()
    qc_by_context = _collect_runtime_qc(industries)
    for ctx in sorted(qc_by_context):
        for warning in qc_by_context[ctx]:
            print(warning)
    _append_qc_run(OUTPUT_DIR / RUNTIME_QC_FILENAME, qc_by_context, qc_generated_at)
    print(f"Runtime QC history updated at {OUTPUT_DIR / RUNTIME_QC_FILENAME}")

    universal_cache = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "root_tickers": root_tickers
        },
        "industries": industries
    }
    rooted_sections = _build_rooted_report_sections(conn, context_groups, root_tickers)

    md_path = OUTPUT_DIR / "USCI_Report.md"
    print(f"Generating USCI Report to {md_path}...")

    with open(md_path, 'w', encoding='utf-8') as f_md:
        f_md.write("# Universal Supply Chain Intelligence (USCI) Report\n\n")
        f_md.write(f"> Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        for section in rooted_sections:
            ctx = section["context"]
            merged_tiers = section["tiers"]
            merged_links = section["links"]
            f_md.write(f"## Industry: {ctx}\n\n")
            for t in range(6):
                nodes = sorted(
                    [r for r in merged_tiers if r['tier'] == t],
                    key=lambda item: item["name"],
                )
                if not nodes:
                    continue
                f_md.write(f"### Tier {t}\n")
                for n in nodes:
                    customers = [l for l in merged_links if l['source_name'] == n['name']]
                    if customers:
                        cust_list = [f"{l['target_name']} ({l.get('role') or 'unknown'}) {format_conf_md(l)}" for l in customers]
                        f_md.write(f"- **{n['name']}** -> {', '.join(cust_list)}\n")
                    else:
                        f_md.write(f"- **{n['name']}**\n")
                f_md.write("\n")

    cache_path = OUTPUT_DIR / "usci_tiers_cache.json"
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(universal_cache, f, indent=2, ensure_ascii=False)

    print(f"Universal cache exported to {cache_path}")
    conn.close()

if __name__ == '__main__':
    export_all()
