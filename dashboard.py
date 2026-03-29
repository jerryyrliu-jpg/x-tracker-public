#!/usr/bin/env python3
"""
x-tracker dashboard — Streamlit UI for topic analysis + K-line chart with tweet annotations.

Usage:
  streamlit run ~/scraper/dashboard.py --server.port 8502
"""
import os
import json
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml
import yfinance as yf
from dotenv import load_dotenv

SCRAPER_BASE = Path(os.environ.get("SCRAPER_DIR", "~/scraper")).expanduser()
load_dotenv(SCRAPER_BASE / "config.env")
DB_PATH = SCRAPER_BASE / "tweets.db"


def get_accounts() -> dict:
    try:
        with open(SCRAPER_BASE / "accounts.yaml") as f:
            return yaml.safe_load(f).get("accounts", {})
    except Exception:
        return {}


st.set_page_config(layout="wide", page_title="X Tracker Dashboard")
st.title("📈 X Tracker — Tweet Analysis Dashboard")

accounts = get_accounts()
account_names = list(accounts.keys())

with st.sidebar:
    st.header("🔍 查詢設定")
    if account_names:
        account = st.selectbox(
            "追蹤帳號",
            account_names,
            format_func=lambda a: f"@{a} ({accounts[a].get('display_name', a)})"
        )
    else:
        st.error("No accounts configured in accounts.yaml")
        st.stop()

    topic = st.text_input("關鍵字 / 標的代號 (e.g. NVDA)", "NVDA")
    days = st.slider("查詢天數", 7, 365, 30)
    grounding = st.checkbox("Gemini Grounding（網路搜尋）", value=False)
    run_btn = st.button("🚀 開始分析")


@st.cache_data(ttl=3600)
def call_query_topic(account: str, topic: str, days: int, ground: bool):
    out_file = f"/tmp/topic_{account}_{topic}.json"
    cmd = [
        "python3", str(SCRAPER_BASE / "query_topic.py"),
        topic,
        "--account", account,
        "--days", str(days),
        "--output", out_file,
    ]
    if ground:
        cmd.append("--ground")
    subprocess.run(cmd, capture_output=True)
    if os.path.exists(out_file):
        with open(out_file) as f:
            return json.load(f)
    return None


if run_btn:
    with st.spinner("搜尋推文並分析中..."):
        result = call_query_topic(account, topic, days, grounding)

    if not result:
        st.error("查詢失敗或無相關推文。")
    else:
        col1, col2 = st.columns([3, 2])

        with col1:
            st.markdown(result["summary"])
            with st.expander(f"📚 原始推文（{result['tweet_count']} 則）"):
                for t in result["tweets"]:
                    st.write(f"**[{t['created_at'][:16]}]**")
                    st.write(t["text"])
                    st.link_button(
                        "查看原文",
                        f"https://x.com/{account}/status/{t['id']}"
                    )
                    st.divider()

        with col2:
            st.subheader(f"📊 {topic.upper()} K 線 + 推文標注")
            try:
                ticker = topic.upper()
                df = yf.download(
                    ticker,
                    start=(datetime.now() - timedelta(days=days + 30)).strftime("%Y-%m-%d"),
                    progress=False
                )
                if df.empty:
                    st.warning(f"無法取得 {ticker} 股價資料")
                else:
                    fig = go.Figure(data=[go.Candlestick(
                        x=df.index,
                        open=df["Open"], high=df["High"],
                        low=df["Low"], close=df["Close"],
                        name=ticker
                    )])

                    tweet_dates = sorted(set(
                        t["created_at"][:10] for t in result["tweets"]
                    ))
                    for d in tweet_dates:
                        dt = pd.Timestamp(d)
                        if dt in df.index:
                            price = float(df.loc[dt, "High"])
                            related = [t["text"][:120] for t in result["tweets"] if t["created_at"].startswith(d)]
                            hover = "<br>".join(related[:2])
                            fig.add_annotation(
                                x=dt, y=price * 1.01,
                                text="▲",
                                showarrow=False,
                                hovertext=hover,
                                font=dict(color="crimson", size=14)
                            )

                    fig.update_layout(
                        xaxis_rangeslider_visible=False,
                        height=550,
                        title=f"{ticker} — @{account} 推文標注"
                    )
                    st.plotly_chart(fig, use_container_width=True)
            except Exception as e:
                st.error(f"K 線圖錯誤: {e}")
else:
    st.info("請在左側選擇帳號、輸入關鍵字，然後點擊「開始分析」。")
