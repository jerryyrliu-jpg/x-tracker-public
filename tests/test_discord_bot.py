import sys, re, os
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")

# Prevent bot.run(TOKEN) from blocking during import
with patch("discord.ext.commands.Bot.run"):
    import discord_bot


def test_ticker_re_accepts_valid():
    from discord_bot import TICKER_RE
    assert TICKER_RE.match("LITE")
    assert TICKER_RE.match("BRK.B")
    assert TICKER_RE.match("BTC-USD")

def test_ticker_re_rejects_invalid():
    from discord_bot import TICKER_RE
    assert not TICKER_RE.match("")
    assert not TICKER_RE.match("A" * 11)
    assert not TICKER_RE.match("../etc")

def test_parse_days_suffix_present():
    from discord_bot import parse_ticker_message
    ticker, days = parse_ticker_message("LITE days:7")
    assert ticker == "LITE"
    assert days == 7

def test_parse_days_suffix_absent():
    from discord_bot import parse_ticker_message
    ticker, days = parse_ticker_message("LITE")
    assert ticker == "LITE"
    assert days == 30  # default

def test_parse_days_suffix_clamps():
    from discord_bot import parse_ticker_message
    _, days = parse_ticker_message("LITE days:999")
    assert days <= 90  # max allowed

def test_parse_days_suffix_invalid_ignored():
    from discord_bot import parse_ticker_message
    ticker, days = parse_ticker_message("LITE days:abc")
    assert ticker == "LITE"
    assert days == 30


def test_parse_ticker_message_strips_invalid_days_suffix():
    from discord_bot import parse_ticker_message
    ticker, days = parse_ticker_message("NVDA days:invalid-value")
    assert ticker == "NVDA"
    assert days == 30


class TestLookupIndustryCache:
    def test_empty_dict_returns_none(self):
        from discord_bot import _lookup_industry_cache
        assert _lookup_industry_cache({}, "AI Server") is None

    def test_exact_key_match(self):
        from discord_bot import _lookup_industry_cache
        cache = {"AI Server": {"data": 1}}
        assert _lookup_industry_cache(cache, "AI Server") == {"data": 1}

    def test_uppercase_fallback(self):
        from discord_bot import _lookup_industry_cache
        cache = {"AI SERVER": {"data": 1}}
        result = _lookup_industry_cache(cache, "ai server")
        assert result is not None

    def test_case_insensitive_normalization(self):
        from discord_bot import _lookup_industry_cache
        cache = {"AI Server": {"data": 1}}
        result = _lookup_industry_cache(cache, "ai server")
        assert result is not None

    def test_underscore_to_space_normalization(self):
        from discord_bot import _lookup_industry_cache
        cache = {"AI Server": {"data": 1}}
        assert _lookup_industry_cache(cache, "AI_Server") is not None

    def test_slash_normalization(self):
        from discord_bot import _lookup_industry_cache
        cache = {"CPO / Silicon Photonics": {"data": 1}}
        assert _lookup_industry_cache(cache, "CPO/Silicon Photonics") is not None

    def test_missing_key_returns_none(self):
        from discord_bot import _lookup_industry_cache
        cache = {"AI Server": {"data": 1}}
        assert _lookup_industry_cache(cache, "Unknown Industry") is None

    def test_empty_dict_value_is_not_none(self):
        from discord_bot import _lookup_industry_cache
        cache = {"AI Server": {}}
        result = _lookup_industry_cache(cache, "AI Server")
        assert result is not None
        assert result == {}
