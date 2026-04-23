import json, re, sqlite3, yaml
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "tweets.db"
HTML = BASE / "cpo_chain" / "output" / "index.html"
KEYWORDS = BASE / "cpo_chain" / "keywords.yaml"

with open(KEYWORDS, encoding="utf-8") as f:
    config = yaml.safe_load(f)
root_tickers = config.get("root_tickers", ["NVDA"])
tickers_str = ",".join(f"'{t}'" for t in root_tickers)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

tiers_data = conn.execute(f"""
WITH RECURSIVE hierarchy(id, name, ticker, country, level, path) AS (
  SELECT c.id, c.name, c.ticker, c.country, 0, CAST(c.id AS TEXT)
  FROM industry_entities c WHERE c.ticker IN ({tickers_str})
  UNION ALL
  SELECT c.id, c.name, c.ticker, c.country, h.level+1, h.path||','||c.id
  FROM industry_entities c
  JOIN (SELECT DISTINCT from_company_id, to_company_id FROM industry_relations
        WHERE status='active' AND industry_context='CPO') r ON c.id=r.from_company_id
  JOIN hierarchy h ON r.to_company_id=h.id
  WHERE h.level<5 AND ','||h.path||',' NOT LIKE '%,'||c.id||',%'
)
SELECT id, name, ticker, country, MIN(level) as tier
FROM hierarchy GROUP BY id,name,ticker ORDER BY tier,name
""").fetchall()

tiers_list = [dict(r) for r in tiers_data]
node_ids = [r["id"] for r in tiers_list]
links = []
if node_ids:
    ids_str = ",".join(str(i) for i in node_ids)
    links = [dict(r) for r in conn.execute(f"""
        SELECT from_company_id as source, to_company_id as target, role, confidence
        FROM industry_relations WHERE status='active' AND industry_context='CPO'
        AND from_company_id IN ({ids_str}) AND to_company_id IN ({ids_str})
    """).fetchall()]
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

html = HTML.read_text(encoding="utf-8")
new_line = f"        const data = {json.dumps(data, ensure_ascii=False)};"
html_new = re.sub(r"        const data = \{.*?\};", new_line, html, flags=re.DOTALL)
HTML.write_text(html_new, encoding="utf-8")
print(f"index.html updated: {len(tiers_list)} companies, {len(links)} links")
