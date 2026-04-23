import streamlit as st
import streamlit.components.v1 as components
import os, json, sqlite3, subprocess, sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objects as go
import yfinance as yf
from dotenv import load_dotenv

SCRAPER_BASE = Path(os.getcwd())
load_dotenv(SCRAPER_BASE / ".env")

def get_accounts():
    try:
        import yaml
        with open(SCRAPER_BASE / "accounts.yaml") as f:
            return yaml.safe_load(f).get("accounts", {})
    except: return {}

st.set_page_config(layout="wide", page_title="X Tracker Dashboard v3.0")
st.title("📈 X Tracker — 專業交易儀表板 v3.0")

accounts = get_accounts()
account_names = list(accounts.keys())

# --- 側邊欄設定 ---
with st.sidebar:
    st.header("🔍 查詢設定")
    account = st.selectbox("追蹤帳號", account_names)
    topic = st.text_input("關鍵字 / 標的", "LITE")
    days = st.slider("查詢天數", 7, 180, 60)
    run_btn = st.button("🚀 開始深度分析", type="primary")

# --- 分頁設計 ---
tab1, tab2 = st.tabs(["📈 個股深度分析", "🕸️ 股市知識圖譜"])

with tab1:
    if run_btn:
        out_file = "/tmp/topic_res_v3.json"
        with st.spinner(f"正在以最新推文為基準分析 {topic} 的觀點演變..."):
            cmd = [sys.executable, "query_topic.py", topic, "--account", account, "--days", str(days), "--output", out_file]
            subprocess.run(cmd, capture_output=True)
            
            if not os.path.exists(out_file):
                st.error("分析失敗。")
            else:
                with open(out_file) as f:
                    result = json.load(f)
                
                col1, col2 = st.columns([3, 2])
                with col1:
                    # 顯示快取狀態
                    if result.get("cached"):
                        st.info("📦 讀取自本地快取。若要取得最新分析，請從終端機使用 --force 參數。")
                    
                    st.markdown(result["summary"])
                    with st.expander("📚 多空訊號詳情"):
                        for t in result["tweets"]:
                            if t['sentiment'] == 'Neutral': continue # 降噪：不顯示中性
                            emoji = "🟢" if t['sentiment'] == 'Bullish' else "🔴"
                            st.write(f"{emoji} **[{t['created_at'][:16]}]** {t['text']}")
                            st.divider()
                
                with col2:
                    st.subheader(f"📊 {topic.upper()} 多空趨勢圖")
                    ticker = topic.upper().strip('$')
                    try:
                        df = yf.download(ticker, start=(datetime.now() - timedelta(days=days+30)).strftime("%Y-%m-%d"), progress=False)
                        
                        if not df.empty:
                            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
                            fig = go.Figure(data=[go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name=ticker)])
                            
                            for t in result['tweets']:
                                if t['sentiment'] == 'Neutral': continue # 移除中性標注
                                ts = pd.Timestamp(t['created_at'][:10])
                                if ts in df.index:
                                    if t['sentiment'] == 'Bullish':
                                        icon, color, y_pos, ay = "▲", "green", df.loc[ts, 'Low'] * 0.98, 40
                                    else: # Bearish
                                        icon, color, y_pos, ay = "▼", "red", df.loc[ts, 'High'] * 1.02, -40
                                    
                                    fig.add_annotation(x=ts, y=y_pos, text=icon, showarrow=True, arrowhead=1, arrowcolor=color, font=dict(color=color, size=18), ay=ay)
                            
                            fig.update_layout(xaxis_rangeslider_visible=False, height=600)
                            st.plotly_chart(fig, use_container_width=True)
                    except Exception as e:
                        st.error(f"K 線圖繪製失敗: {e}")
    else:
        st.info("👆 請在左側輸入標的並點擊「開始深度分析」以繪製 K 線。")

with tab2:
    st.subheader("🕸️ CPO 供應鏈知識圖譜")
    st.markdown("基於推文萃取的 **供應鏈關係 (USCI)**，按 Tier 分層著色。每日自動更新。")

    col_a, col_b = st.columns([1, 4])
    with col_a:
        if st.button("🔄 重新生成圖譜"):
            with st.spinner("正在從資料庫重新匯出供應鏈圖譜..."):
                subprocess.run([sys.executable, "-m", "cpo_chain.export_universal"], cwd=str(SCRAPER_BASE))
                # Update embedded data in index.html
                _update_script = str(SCRAPER_BASE / "scripts" / "update_network_html.py")
                subprocess.run([sys.executable, _update_script], cwd=str(SCRAPER_BASE))
                st.success("生成完畢！請重新整理頁面。")

    network_path = SCRAPER_BASE / "cpo_chain" / "output" / "index.html"
    if network_path.exists():
        with open(network_path, "r", encoding="utf-8") as f:
            html_data = f.read()
        components.html(html_data, height=720, scrolling=False)
    else:
        st.warning("找不到 CPO Network 圖譜，請點擊「重新生成圖譜」。")
