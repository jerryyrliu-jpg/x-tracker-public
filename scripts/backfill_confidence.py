
import sqlite3
import os
import sys
import shutil
import time
import argparse
from pathlib import Path

# Setup paths
SCRAPER_BASE = Path(__file__).resolve().parent.parent
sys.path.append(str(SCRAPER_BASE))

from cpo_chain.edgar_fetcher import EdgarFetcher
from cpo_chain.news_fetcher import CompositeNewsFetcher
from cpo_chain.company_ticker_mapper import CompanyTickerMapper
from cpo_chain.confidence_updater import ConfidenceUpdater

def main():
    parser = argparse.ArgumentParser(description="Backfill confidence scores using EDGAR and News RSS.")
    parser.add_argument("--dry-run", action="store_true", help="Don't commit changes to DB")
    parser.add_argument("--limit", type=int, default=500, help="Max number of relations to process")
    parser.add_argument("--offset", type=int, default=0, help="Resume from relation id > N")
    args = parser.parse_args()

    db_path = SCRAPER_BASE / "tweets.db"
    if not args.dry_run:
        backup_path = SCRAPER_BASE / f"tweets.db.bak.{int(time.time())}"
        print(f"Creating backup at {backup_path}...")
        shutil.copy(db_path, backup_path)

    print(f"Starting backfill (dry_run={args.dry_run}, limit={args.limit}, offset={args.offset})...")

    mapper = CompanyTickerMapper()
    edgar = EdgarFetcher()
    news = CompositeNewsFetcher(mapper=mapper)
    updater = ConfidenceUpdater(str(db_path), edgar, news, mapper)

    start_time = time.time()
    result = updater.run(limit=args.limit, dry_run=args.dry_run, offset=args.offset)
    duration = time.time() - start_time

    print("\nBackfill Result:")
    print(f"  Updated:  {result['updated']}")
    print(f"  Skipped:  {result['skipped']}")
    print(f"  Errors:   {result['errors']}")
    print(f"  Last id:  {result['last_id']}  (use --offset {result['last_id']} to resume)")
    print(f"  Time:     {duration:.2f}s")
    
    if not args.dry_run and result['updated'] > 0:
        print("\nUpdating USCI cache...")
        from cpo_chain.export_universal import export_all
        export_all()

if __name__ == "__main__":
    main()
