import streamlit as st
import streamlit.components.v1 as components
import subprocess, sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objects as go
import yfinance as yf
import yaml
from dotenv import load_dotenv

SCRAPER_BASE = Path(__file__).resolve().parent
load_dotenv(SCRAPER_BASE / ".env")

sys.path.insert(0, str(SCRAPER_BASE))
from query_topic import analyze_topic

def get_accounts():
    try:
        with open(SCRAPER_BASE / "accounts.yaml") as f:
            return yaml.safe_load(f).get("accounts", {})
    except Exception as e:
        st.warning(f"Could not load accounts.yaml: {e}")
        return {}

st.set_page_config(layout="wide", page_title="X Tracker Dashboard v3.0")
st.title("📈 X Tracker — 專業交易儀表板 v3.0")

accounts = get_accounts()
account_names = list(accounts.keys())

# --- 側邊欄設定 ---
with st.sidebar:
    st.header("🔍 查詢設定")
    account = st.selectbox("追蹤帳號", ["all"] + account_names)
    topic = st.text_input("關鍵字 / 標的", "LITE")
    days = st.slider("查詢天數", 7, 180, 60)
    force_refresh = st.checkbox("🔁 強制重新分析（忽略快取）", value=False)
    run_btn = st.button("🚀 開始深度分析", type="primary")
    st.divider()
    auto_refresh = st.toggle("🔄 Auto-refresh (60s)", value=False)

if auto_refresh:
    st.html("<meta http-equiv='refresh' content='60'>")

# --- 分頁設計 ---
tab1, tab2 = st.tabs(["📈 個股深度分析", "🕸️ 股市知識圖譜"])

with tab1:
    if run_btn:
        with st.spinner(f"正在以最新推文為基準分析 {topic} 的觀點演變..."):
            result = analyze_topic(topic, account=account, days=days, force=force_refresh)

        if result is None:
            st.error(f"找不到「{topic}」的相關推文（最近 {days} 天）。")
        else:
            for w in result.get("warnings", []):
                st.warning(w)

            col1, col2 = st.columns([3, 2])
            with col1:
                if result.get("cached"):
                    st.info("📦 讀取自本地快取。勾選「強制重新分析」可取得最新結果。")

                st.markdown(result.get("summary", ""))
                with st.expander("📚 多空訊號詳情"):
                    _emoji = {
                        'StrongBullish': '🟢🟢', 'Bullish': '🟢',
                        'StrongBearish': '🔴🔴', 'Bearish': '🔴',
                    }
                    for t in result.get("tweets", []):
                        if t['sentiment'] == 'Neutral':
                            continue
                        emoji = _emoji.get(t['sentiment'], '⚪')
                        conf = max(0.0, min(1.0, t.get('confidence', 0.5)))
                        conf_str = f"`信心 {conf:.0%}`"
                        st.write(f"{emoji} **[{t['created_at'][:16]}]** {t['text']} {conf_str}")
                        st.divider()

            with col2:
                st.subheader(f"📊 {topic.upper()} 多空趨勢圖")
                ticker = topic.upper().strip('$')
                try:
                    df = yf.download(ticker, start=(datetime.now() - timedelta(days=days+30)).strftime("%Y-%m-%d"), progress=False)

                    if not df.empty:
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.droplevel(1)
                        fig = go.Figure(data=[go.Candlestick(
                            x=df.index, open=df['Open'], high=df['High'],
                            low=df['Low'], close=df['Close'], name=ticker
                        )])

                        for t in result.get('tweets', []):
                            if t['sentiment'] == 'Neutral':
                                continue
                            ts = pd.Timestamp(t['created_at'][:10])
                            if ts in df.index:
                                conf = max(0.0, min(1.0, t.get('confidence', 0.5)))
                                if t['sentiment'] in ('Bullish', 'StrongBullish'):
                                    base_size = 22 if t['sentiment'] == 'StrongBullish' else 16
                                    size = max(8, int(base_size * (0.7 + 0.6 * conf)))
                                    icon, color, y_pos, ay = "▲", "green", df.loc[ts, 'Low'] * 0.98, 40
                                else:
                                    base_size = 22 if t['sentiment'] == 'StrongBearish' else 16
                                    size = max(8, int(base_size * (0.7 + 0.6 * conf)))
                                    icon, color, y_pos, ay = "▼", "red", df.loc[ts, 'High'] * 1.02, -40
                                fig.add_annotation(
                                    x=ts, y=y_pos, text=icon, showarrow=True,
                                    arrowhead=1, arrowcolor=color,
                                    font=dict(color=color, size=size), ay=ay
                                )

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
        if "last_regen_time" not in st.session_state:
            st.session_state["last_regen_time"] = 0.0
        _REGEN_COOLDOWN = 60
        _now = datetime.now().timestamp()
        _cooldown_ok = (_now - st.session_state["last_regen_time"]) >= _REGEN_COOLDOWN
        if st.button("🔄 重新生成圖譜", disabled=not _cooldown_ok):
            st.session_state["last_regen_time"] = _now
            with st.spinner("正在從資料庫重新匯出供應鏈圖譜..."):
                try:
                    r1 = subprocess.run(
                        [sys.executable, "-m", "cpo_chain.export_universal"],
                        cwd=str(SCRAPER_BASE), capture_output=True, timeout=60,
                        text=True, encoding="utf-8",
                    )
                    r2 = subprocess.run(
                        [sys.executable, str(SCRAPER_BASE / "scripts" / "update_network_html.py")],
                        cwd=str(SCRAPER_BASE), capture_output=True, timeout=60,
                        text=True, encoding="utf-8",
                    )
                    if r1.returncode != 0 or r2.returncode != 0:
                        st.error("圖譜生成失敗，請查看 logs/monitor_active.err。")
                    else:
                        st.success("生成完畢！請重新整理頁面。")
                except subprocess.TimeoutExpired:
                    st.error("圖譜生成逾時。")

    network_path = SCRAPER_BASE / "cpo_chain" / "output" / "index.html"
    if network_path.exists():
        with open(network_path, "r", encoding="utf-8") as f:
            html_data = f.read()
        components.html(html_data, height=720, scrolling=False)
    else:
        st.warning("找不到 CPO Network 圖譜，請點擊「重新生成圖譜」。")
