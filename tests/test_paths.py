import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_query_topic_scraper_base_is_file_relative():
    """SCRAPER_BASE must be relative to __file__, not os.getcwd()."""
    import query_topic
    expected = Path(__file__).resolve().parent.parent
    assert query_topic.SCRAPER_BASE == expected, (
        f"SCRAPER_BASE is {query_topic.SCRAPER_BASE}, expected {expected}. "
        "Fix: use Path(__file__).resolve().parent"
    )

def test_monthly_summary_scraper_base_is_file_relative():
    """SCRAPER_BASE must be relative to __file__, not os.getcwd()."""
    import monthly_summary
    expected = Path(__file__).resolve().parent.parent
    assert monthly_summary.SCRAPER_BASE == expected, (
        f"SCRAPER_BASE is {monthly_summary.SCRAPER_BASE}, expected {expected}. "
        "Fix: use Path(__file__).resolve().parent"
    )
