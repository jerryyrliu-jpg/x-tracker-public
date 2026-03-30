---
title: X 爬蟲計劃 — @aleabitoreddit (v3.2 Production Ready)
date: 2026-03-25
updated: 2026-03-30
tags: [decision, connected-mode, playwright, automated]
status: stable-v3.2
---

# X 爬蟲計劃 (Serenity) v3.2 實作總結

## 1. 核心架構 (v3.2 Stable)
- **Connected Mode**: 透過 connect_over_cdp (9222 埠) 連接真實瀏覽器，徹底繞過 X 的自動化防禦與快取延遲問題。
- **Reliable Detection**: 透過 monitor_rss.py 自動觸發連線抓取，確保 100% 抓到最新推文。
- **Sync Workflow**: 新文自動存入 SQLite 並即時推送到 Discord。

## 2. 操作建議
- **保持瀏覽器開啟**: 確保 Chrome 帶著 --remote-debugging-port 在後台執行。
- **自動化**: 已設定為背景守護進程。
