import json
import os
import sqlite3
import yaml
import sys
import argparse
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

def get_chain_data(conn: sqlite3.Connection, root_tickers: list[str], industry_context: str = "CPO"):
    """
    Calculate Tiers starting from root_tickers for a specific industry context.
    """
    if not root_tickers: return []
    ticker_placeholders = ", ".join("?" * len(root_tickers))

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
            WHERE status = 'active' AND industry_context = ?
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
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, root_tickers + [industry_context]).fetchall()]
    except Exception as e:
        print(f"CTE Error [{industry_context}]: {e}")
        return []

def get_all_links(conn: sqlite3.Connection, node_ids: list[int], industry_context: str = "CPO"):
    if not node_ids: return []
    id_placeholders = ", ".join("?" * len(node_ids))
    sql = f"""
    SELECT from_company_id as source, to_company_id as target, role, industry_context,
           confidence, edgar_score, news_score
    FROM industry_relations
    WHERE status = 'active' AND industry_context = ?
    AND from_company_id IN ({id_placeholders})
    AND to_company_id IN ({id_placeholders});
    """
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, [industry_context] + node_ids + node_ids).fetchall()]
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
    
    # Get all available industry contexts
    contexts = [r[0] for r in conn.execute("SELECT DISTINCT industry_context FROM industry_relations").fetchall()]
    if not contexts: contexts = ["CPO"]
    
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

        for ctx in sorted(contexts):
            print(f"Processing context: {ctx}...")
            tiers_data = get_chain_data(conn, root_tickers, ctx)
            if not tiers_data: continue
            
            node_ids = [r['id'] for r in tiers_data]
            links = get_all_links(conn, node_ids, ctx)
            
            universal_cache["industries"][ctx] = {
                "tiers": tiers_data,
                "links": links
            }
            
            # Append to MD
            f_md.write(f"## Industry: {ctx}\n\n")
            node_map = {r['id']: r['name'] for r in tiers_data}
            for t in range(6):
                nodes = [r for r in tiers_data if r['tier'] == t]
                if not nodes: continue
                f_md.write(f"### Tier {t}\n")
                for n in nodes:
                    customers = [l for l in links if l['source'] == n['id']]
                    if customers:
                        cust_list = [f"{node_map.get(l['target'], 'Unknown')} ({l['role']}) {format_conf_md(l)}" for l in customers]
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
