import json
import os
import sqlite3
import yaml
import sys
import argparse
import re
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

DB_PATH = BASE_DIR / "tweets.db"
KEYWORDS_PATH = CPO_CHAIN_DIR / "keywords.yaml"
OUTPUT_DIR = CPO_CHAIN_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

_CONTEXT_ALIASES = {
    "ai server": "AI Server",
    "ai_server": "AI Server",
    "advanced packaging": "Advanced Packaging",
    "advanced_packaging": "Advanced Packaging",
    "cpo/silicon photonics": "CPO / Silicon Photonics",
    "cpo / silicon photonics": "CPO / Silicon Photonics",
}


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


def _base_company_name(name: str) -> str:
    return re.sub(r"\s+\([A-Z0-9.\-]+\)$", "", (name or "").strip())


def canonical_company_name(name: str, known_names: set[str] | list[str]) -> str:
    base_name = _base_company_name(name)
    lower_base = base_name.lower()
    for candidate in known_names:
        if lower_base.startswith(candidate.lower() + " "):
            return candidate
    if base_name in known_names:
        return base_name
    return base_name


def _get_context_groups(conn: sqlite3.Connection) -> dict[str, list[str]]:
    contexts = [r[0] for r in conn.execute("SELECT DISTINCT industry_context FROM industry_relations").fetchall()]
    if not contexts:
        return {"CPO": ["CPO"]}
    grouped: dict[str, list[str]] = {}
    for raw in contexts:
        canonical = normalize_industry_context(raw)
        grouped.setdefault(canonical, []).append(raw)
    return grouped

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

    universal_cache = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "root_tickers": root_tickers
        },
        "industries": {}
    }

    md_path = BASE_DIR / "themes" / "USCI_Report.md"
    print(f"Generating USCI Report to {md_path}...")

    with open(md_path, 'w', encoding='utf-8') as f_md:
        f_md.write("# Universal Supply Chain Intelligence (USCI) Report\n\n")
        f_md.write(f"> Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        for ctx in sorted(context_groups):
            raw_contexts = context_groups[ctx]
            print(f"Processing context: {ctx} ({', '.join(raw_contexts)})...")
            tiers_data = get_chain_data(conn, root_tickers, raw_contexts)
            if not tiers_data: continue

            node_ids = [r['id'] for r in tiers_data]
            links = get_all_links(conn, node_ids, raw_contexts)
            if not should_export_context(tiers_data, links):
                continue

            known_names = {_base_company_name(r["name"]) for r in tiers_data if r.get("name")}
            sorted_names = sorted(known_names, key=len)
            node_map = {
                r["id"]: canonical_company_name(r["name"], sorted_names)
                for r in tiers_data
            }

            merged_tiers: dict[str, dict] = {}
            for row in tiers_data:
                label = node_map[row["id"]]
                current = merged_tiers.get(label)
                if current is None or row["tier"] < current["tier"]:
                    merged_tiers[label] = {**row, "name": label}

            merged_links: list[dict] = []
            seen_links: set[tuple[str, str, str, str]] = set()
            for link in links:
                source_name = node_map.get(link["source"])
                target_name = node_map.get(link["target"])
                if not source_name or not target_name or source_name == target_name:
                    continue
                link_key = (source_name, target_name, link.get('role') or '', ctx)
                if link_key in seen_links:
                    continue
                seen_links.add(link_key)
                merged_links.append({**link, "source_name": source_name, "target_name": target_name})

            universal_cache["industries"][ctx] = {
                "tiers": list(merged_tiers.values()),
                "links": merged_links,
            }

            # Append to MD
            f_md.write(f"## Industry: {ctx}\n\n")
            for t in range(6):
                nodes = sorted(
                    [r for r in merged_tiers.values() if r['tier'] == t],
                    key=lambda item: item["name"],
                )
                if not nodes: continue
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
