import os
import sqlite3
import re
import networkx as nx
from pyvis.network import Network
from pathlib import Path

def build_correlation_graph(db_path, output_html):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT text FROM tweets").fetchall()
    conn.close()

    co_occurrences = {}
    ticker_counts = {}

    for row in rows:
        text = row[0]
        # 修改 Regex 以確保更能抓到標的 (支援 $LITE 或純大寫 LITE 在特定情境下)
        tickers = re.findall(r'\$([A-Z]{1,6})\b', text)
        tickers = list(set([t.upper() for t in tickers]))
        
        for t in tickers:
            ticker_counts[t] = ticker_counts.get(t, 0) + 1
            
        for i in range(len(tickers)):
            for j in range(i+1, len(tickers)):
                t1, t2 = sorted([tickers[i], tickers[j]])
                co_occurrences[(t1, t2)] = co_occurrences.get((t1, t2), 0) + 1

    G = nx.Graph()
    for t, count in ticker_counts.items():
        if count >= 2: # 至少被提到兩次才顯示
            G.add_node(t, size=15 + count*2, title=f"{t}: {count} 次提及", label=t, color="#97c2fc")
            
    for (t1, t2), weight in co_occurrences.items():
        if t1 in G.nodes and t2 in G.nodes:
            G.add_edge(t1, t2, value=weight, title=f"關聯強度: {weight}")

    # CDN 模式實作：這能解決 Streamlit 渲染問題
    net = Network(height="600px", width="100%", bgcolor="#ffffff", font_color="black", notebook=True, cdn_resources='remote')
    net.from_nx(G)
    
    # 物理引擎設定
    net.set_options("""
    var options = {
      "physics": {
        "forceAtlas2Based": {
          "gravitationalConstant": -50,
          "centralGravity": 0.01,
          "springLength": 100,
          "springConstant": 0.08
        },
        "maxVelocity": 50,
        "solver": "forceAtlas2Based",
        "timestep": 0.35,
        "stabilization": { "iterations": 150 }
      }
    }
    """)
    net.save_graph(str(output_html))

if __name__ == "__main__":
    base = Path(os.getcwd())
    build_correlation_graph(base / "tweets.db", base / "graph.html")
