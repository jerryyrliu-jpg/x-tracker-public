import sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))


def make_fake_tweets(n=3):
    return [
        (str(i), f"2026-04-0{i+1}T10:00:00", f"Tweet {i} about LITE stock")
        for i in range(1, n + 1)
    ]


def test_prompt_includes_today_date():
    from query_topic import build_prompt
    prompt = build_prompt("LITE", make_fake_tweets())
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in prompt, f"Prompt should contain today's date {today}"


def test_prompt_includes_week_grouping():
    from query_topic import build_prompt
    prompt = build_prompt("LITE", make_fake_tweets())
    assert "Week" in prompt or "週" in prompt, "Prompt should group tweets by week"


def test_prompt_includes_sentiment_separator():
    from query_topic import build_prompt
    prompt = build_prompt("LITE", make_fake_tweets())
    assert "---SENTIMENT_JSON---" in prompt


def test_prompt_includes_trend_instruction():
    from query_topic import build_prompt
    prompt = build_prompt("LITE", make_fake_tweets())
    assert "趨勢" in prompt or "trend" in prompt.lower() or "演變" in prompt
