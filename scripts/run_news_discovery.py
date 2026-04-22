#!/usr/bin/env python3
"""
Manual trigger for News Discovery Pipeline.
Usage:
  python scripts/run_news_discovery.py --fetch
  python scripts/run_news_discovery.py --extract --limit 20
  python scripts/run_news_discovery.py --fetch --extract --limit 100
"""
import argparse, sys, yaml
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cpo_chain.db import get_conn, init_usci_tables

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="News Discovery Pipeline runner")
    parser.add_argument("--fetch", action="store_true", help="Fetch new articles from Google News + SEC 8-K")
    parser.add_argument("--extract", action="store_true", help="Extract supply chain relations from unprocessed articles")
    parser.add_argument("--limit", type=int, default=50, help="Max articles to extract per run (default: 50)")
    args = parser.parse_args()

    if not args.fetch and not args.extract:
        parser.error("Must specify at least one of --fetch or --extract")
    if args.limit < 1:
        parser.error("--limit must be >= 1")

    db_path = str(PROJECT_ROOT / "tweets.db")
    keywords_path = PROJECT_ROOT / "cpo_chain" / "keywords.yaml"

    conn = get_conn(db_path)
    init_usci_tables(conn)
    has_errors = False

    try:
        if args.fetch:
            print("[news-fetch] Starting article fetch...")
            from cpo_chain.news_article_fetcher import NewsArticleFetcher
            try:
                with open(keywords_path) as f:
                    cfg = yaml.safe_load(f)
            except (FileNotFoundError, yaml.YAMLError) as exc:
                print(f"[news-fetch] ERROR: Cannot read keywords.yaml: {exc}", file=sys.stderr)
                sys.exit(1)
            root_companies = cfg.get("root_tickers") or []
            if not root_companies:
                print("[news-fetch] WARNING: root_tickers is empty in keywords.yaml", file=sys.stderr)
            else:
                fetcher = NewsArticleFetcher()
                result = fetcher.run(conn, root_companies)
                print(f"[news-fetch] Done: {result}")
                # Note: errors are logged internally by fetcher; no error count in return dict

        if args.extract:
            print(f"[news-extract] Starting extraction (limit={args.limit})...")
            from cpo_chain.news_extractor import NewsExtractor
            extractor = NewsExtractor(db_path, str(keywords_path))
            result = extractor.run(conn, args.limit)
            print(f"[news-extract] Done: {result}")
            if result.get("errors", 0) > 0:
                has_errors = True

    finally:
        conn.close()

    print("[news-discovery] Complete.")
    if has_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
