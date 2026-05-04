import json, os, re, sqlite3, yaml
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "tweets.db"
HTML = BASE / "cpo_chain" / "output" / "index.html"
KEYWORDS = BASE / "cpo_chain" / "keywords.yaml"

with open(KEYWORDS, encoding="utf-8") as f:
    config = yaml.safe_load(f)
root_tickers = config.get("root_tickers", ["NVDA"])

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

ticker_placeholders = ",".join("?" * len(root_tickers))
tiers_data = conn.execute(f"""
WITH RECURSIVE hierarchy(id, name, ticker, country, level, path) AS (
  SELECT c.id, c.name, c.ticker, c.country, 0, CAST(c.id AS TEXT)
  FROM industry_entities c WHERE c.ticker IN ({ticker_placeholders})
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
""", root_tickers).fetchall()

# Fetch industry_tags for each entity
tags_map = {r[0]: (r[1] or "") for r in conn.execute("SELECT id, industry_tags FROM industry_entities").fetchall()}

tiers_list = [dict(r) | {"tags": tags_map.get(dict(r)["id"], "")} for r in tiers_data]
node_ids = [r["id"] for r in tiers_list]
links = []
if node_ids:
    id_placeholders = ",".join("?" * len(node_ids))
    links = [dict(r) for r in conn.execute(f"""
        SELECT from_company_id as source, to_company_id as target, role, confidence
        FROM industry_relations WHERE status='active' AND industry_context='CPO'
        AND from_company_id IN ({id_placeholders}) AND to_company_id IN ({id_placeholders})
    """, node_ids + node_ids).fetchall()]
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
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background: #f8f9fa; overflow: hidden; }}
        #chart {{ width: 100vw; height: 100vh; }}
        .node circle {{ stroke: #fff; stroke-width: 1.5px; cursor: grab; transition: opacity 0.2s; }}
        .node circle:active {{ cursor: grabbing; }}
        .node text {{ font-size: 11px; font-weight: 500; pointer-events: none; fill: #495057; text-shadow: 0 1px 2px rgba(255,255,255,0.9); transition: opacity 0.2s; }}
        .link {{ stroke: #adb5bd; stroke-opacity: 0.5; stroke-width: 1px; fill: none; transition: opacity 0.2s; }}
        .marker {{ fill: #adb5bd; }}
        .node.dimmed circle, .node.dimmed text {{ opacity: 0.08; }}
        .link.dimmed {{ opacity: 0.04; }}
        .node.highlighted circle {{ stroke: #ffc107; stroke-width: 3px; }}

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
        .chip.active {{ font-weight: 600; border-color: rgba(0,0,0,0.2); }}
        .chip-all   {{ background: #e9ecef; color: #495057; }}
        .chip-mat   {{ background: #fff3cd; color: #856404; }}
        .chip-fab   {{ background: #cfe2ff; color: #084298; }}
        .chip-comp  {{ background: #d1e7dd; color: #0a3622; }}
        .chip-int   {{ background: #f8d7da; color: #842029; }}

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
    </style>
</head>
<body>
    <div id="panel">
        <h2>🔍 CPO Supply Chain Explorer</h2>
        <input id="search" type="text" placeholder="搜尋公司名稱或 ticker..." />
        <div class="filter-label">分類篩選</div>
        <div class="chips">
            <span class="chip chip-all active" data-cat="all">全部</span>
            <span class="chip chip-mat" data-cat="material">原材料 InP</span>
            <span class="chip chip-fab" data-cat="foundry">製造/代工</span>
            <span class="chip chip-comp" data-cat="component">元器件</span>
            <span class="chip chip-int" data-cat="integration">整合/測試</span>
        </div>
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

    <script>
        const data = {data_json};

        // Category tag matching
        const CAT_TAGS = {{
            material:    ["InP","substrate","materials","wafer","feedstock","epiwafer"],
            foundry:     ["foundry","SiPh","EDA"],
            component:   ["laser","photonics","DSP","optical","AI_chip"],
            integration: ["module","packaging","test","burn-in","fiber","glass","OSAT","metrology","EMS"]
        }};

        function getCategory(tags) {{
            if (!tags) return null;
            const t = tags.toLowerCase();
            if (CAT_TAGS.material.some(k => t.includes(k.toLowerCase()))) return "material";
            if (CAT_TAGS.foundry.some(k => t.includes(k.toLowerCase()))) return "foundry";
            if (CAT_TAGS.component.some(k => t.includes(k.toLowerCase()))) return "component";
            if (CAT_TAGS.integration.some(k => t.includes(k.toLowerCase()))) return "integration";
            return null;
        }}

        const tierColors = ["#dc3545","#fd7e14","#0d6efd","#6610f2","#20c997","#6c757d"];
        const width = window.innerWidth, height = window.innerHeight;

        const nodes = data.tiers.map(d => ({{...d, category: getCategory(d.tags)}}));
        const nodeById = new Map(nodes.map(d => [d.id, d]));
        const links = data.links.map(d => ({{
            source: nodeById.get(d.source),
            target: nodeById.get(d.target),
            role: d.role,
            confidence: d.confidence
        }})).filter(d => d.source && d.target);

        const svg = d3.select("#chart").append("svg")
            .attr("width", width).attr("height", height)
            .call(d3.zoom().scaleExtent([0.1, 4]).on("zoom", e => container.attr("transform", e.transform)));

        const container = svg.append("g");

        svg.append("defs").append("marker")
            .attr("id","arrowhead").attr("viewBox","0 -5 10 10")
            .attr("refX",22).attr("refY",0).attr("orient","auto")
            .attr("markerWidth",6).attr("markerHeight",6)
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
            .call(d3.drag()
                .on("start", (e,d) => {{ if(!e.active) simulation.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
                .on("drag",  (e,d) => {{ d.fx=e.x; d.fy=e.y; }})
                .on("end",   (e,d) => {{ if(!e.active) simulation.alphaTarget(0); d.fx=null; d.fy=null; }}));

        node.append("circle")
            .attr("r", d => d.tier===0 ? 11 : 7)
            .attr("fill", d => tierColors[Math.min(d.tier,5)]);

        node.append("text").attr("dx",13).attr("dy",".35em")
            .text(d => d.ticker ? `${{d.name}} (${{d.ticker}})` : d.name);

        // Tooltip
        const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
        const tooltip = d3.select("#tooltip");
        node.on("mouseover", (e, d) => {{
            const connected = new Set();
            links.forEach(l => {{
                if (l.source.id===d.id) connected.add(l.target.id + ":out:" + l.role);
                if (l.target.id===d.id) connected.add(l.source.id + ":in:" + l.role);
            }});
            const inLinks  = links.filter(l => l.target.id===d.id).map(l=>`← ${{esc(l.source.name)}}`);
            const outLinks = links.filter(l => l.source.id===d.id).map(l=>`→ ${{esc(l.target.name)}}`);
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

        // Click: highlight connected nodes
        let selectedId = null;
        node.on("click", (e, d) => {{
            e.stopPropagation();
            if (selectedId === d.id) {{ selectedId = null; applyFilter(currentCat); return; }}
            selectedId = d.id;
            const connectedIds = new Set([d.id]);
            links.forEach(l => {{
                if (l.source.id===d.id) connectedIds.add(l.target.id);
                if (l.target.id===d.id) connectedIds.add(l.source.id);
            }});
            node.classed("dimmed", n => !connectedIds.has(n.id))
                .classed("highlighted", n => n.id===d.id);
            link.classed("dimmed", l => l.source.id!==d.id && l.target.id!==d.id);
        }});
        svg.on("click", () => {{ selectedId=null; applyFilter(currentCat); }});

        simulation.on("tick", () => {{
            link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
                .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
            node.attr("transform",d=>`translate(${{d.x}},${{d.y}})`);
        }});

        // ---- Filter logic ----
        let currentCat = "all";

        function applyFilter(cat) {{
            currentCat = cat;
            selectedId = null;
            if (cat==="all") {{
                node.classed("dimmed", false).classed("highlighted", false);
                link.classed("dimmed", false);
            }} else {{
                const matchIds = new Set(nodes.filter(n => n.category===cat || n.tier===0).map(n=>n.id));
                node.classed("dimmed", n => !matchIds.has(n.id))
                    .classed("highlighted", false);
                link.classed("dimmed", l => !matchIds.has(l.source.id) && !matchIds.has(l.target.id));
            }}
        }}

        document.querySelectorAll(".chip").forEach(chip => {{
            chip.addEventListener("click", () => {{
                document.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));
                chip.classList.add("active");
                applyFilter(chip.dataset.cat);
                document.getElementById("search").value = "";
                document.getElementById("results").style.display = "none";
            }});
        }});

        // ---- Search logic ----
        const searchInput = document.getElementById("search");
        const resultsDiv  = document.getElementById("results");

        searchInput.addEventListener("input", () => {{
            const q = searchInput.value.trim().toLowerCase();
            if (!q) {{ resultsDiv.style.display="none"; applyFilter(currentCat); return; }}

            const matches = nodes.filter(n =>
                n.name.toLowerCase().includes(q) ||
                (n.ticker && n.ticker.toLowerCase().includes(q)) ||
                (n.tags  && n.tags.toLowerCase().includes(q))
            );

            // Highlight matches
            const matchIds = new Set(matches.map(n=>n.id));
            node.classed("dimmed", n => !matchIds.has(n.id)).classed("highlighted", false);
            link.classed("dimmed", l => !matchIds.has(l.source.id) && !matchIds.has(l.target.id));

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
                        // Pan to node
                        svg.transition().duration(600).call(
                            d3.zoom().transform,
                            d3.zoomIdentity.translate(width/2 - d.x, height/2 - d.y)
                        );
                        // Highlight
                        const connIds = new Set([nid]);
                        links.forEach(l => {{
                            if (l.source.id===nid) connIds.add(l.target.id);
                            if (l.target.id===nid) connIds.add(l.source.id);
                        }});
                        node.classed("dimmed", n => !connIds.has(n.id))
                            .classed("highlighted", n => n.id===nid);
                        link.classed("dimmed", l => l.source.id!==nid && l.target.id!==nid);
                    }});
                }});
            }}
            resultsDiv.style.display = "block";
        }});
    </script>
</body>
</html>"""

tmp_path = str(HTML) + ".tmp"
Path(tmp_path).write_text(html_content, encoding="utf-8")
os.replace(tmp_path, HTML)
print(f"index.html updated: {len(tiers_list)} companies, {len(links)} links")
