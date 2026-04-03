import sys, re
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

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


def test_parse_days_from_args_present():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("days:3") == 3

def test_parse_days_from_args_absent():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("") == 7  # default for /summary is 7

def test_parse_days_from_args_clamps_upper():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("days:999") == 90

def test_parse_days_from_args_clamps_lower():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("days:0") == 1

def test_parse_days_from_args_invalid_ignored():
    from discord_bot import parse_days_from_args
    assert parse_days_from_args("days:abc") == 7
