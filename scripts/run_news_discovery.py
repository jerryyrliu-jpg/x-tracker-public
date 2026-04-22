#!/usr/bin/env python3
"""
Manual trigger for News Discovery Pipeline.
Usage:
  python scripts/run_news_discovery.py --fetch
  python scripts/run_news_discovery.py --extract --limit 20
  python scripts/run_news_discovery.py --fetch --extract --limit 100
"""
import argparse
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cpo_chain.db import get_conn, init_usci_tables
from cpo_chain.news_article_fetcher import NewsArticleFetcher
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="News Discovery Pipeline runner")
    parser.add_argument("--fetch", action="store_true", help="Fetch new articles from Google News + SEC 8-K")
    parser.add_argument("--extract", action="store_true", help="Extract supply chain relations from unprocessed articles")
    parser.add_argument("--limit", type=int, default=50, help="Max articles to extract per run (default: 50)")
    args = parser.parse_args()

    if not args.fetch and not args.extract:
        parser.error("Must specify at least one of --fetch or --extract")

    db_path = str(PROJECT_ROOT / "tweets.db")
    conn = get_conn(db_path)
    init_usci_tables(conn)

    if args.fetch:
        print("[news-fetch] Starting article fetch...")
        keywords_path = PROJECT_ROOT / "cpo_chain" / "keywords.yaml"
        with open(keywords_path) as f:
            cfg = yaml.safe_load(f)
        root_companies = cfg.get("root_tickers") or []
        if not root_companies:
            print("[news-fetch] WARNING: root_tickers is empty in keywords.yaml")
        else:
            fetcher = NewsArticleFetcher()
            result = fetcher.run(conn, root_companies)
            print(f"[news-fetch] Done: {result}")

    if args.extract:
        print(f"[news-extract] Starting extraction (limit={args.limit})...")
        from cpo_chain.news_extractor import NewsExtractor
        keywords_path = str(PROJECT_ROOT / "cpo_chain" / "keywords.yaml")
        extractor = NewsExtractor(db_path, keywords_path)
        result = extractor.run(conn, args.limit)
        print(f"[news-extract] Done: {result}")

    conn.close()
    print("[news-discovery] Complete.")


if __name__ == "__main__":
    main()
