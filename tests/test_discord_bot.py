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


def test_can_use_vector_recall_false_when_sqlite_vec_missing():
    from discord_bot import _can_use_vector_recall
    with patch("importlib.util.find_spec", return_value=None):
        assert _can_use_vector_recall() is False


class TestFormatQcAlert:
    def test_returns_none_when_nothing_new_or_resolved(self):
        from discord_bot import _format_qc_alert
        assert _format_qc_alert({"new": {}, "resolved": []}) is None

    def test_formats_new_warnings_by_context(self):
        from discord_bot import _format_qc_alert
        diff = {"new": {"CPO": ["[runtime-qc][CPO] orphan nodes: A"]}, "resolved": []}
        message = _format_qc_alert(diff)
        assert message is not None
        assert "CPO" in message
        assert "orphan nodes: A" in message

    def test_truncates_long_warning_lists_per_context(self):
        from discord_bot import _format_qc_alert
        warnings = [f"[runtime-qc][CPO] orphan nodes: N{i}" for i in range(5)]
        diff = {"new": {"CPO": warnings}, "resolved": []}
        message = _format_qc_alert(diff)
        assert "N0" in message and "N1" in message and "N2" in message
        assert "N4" not in message
        assert "還有" in message

    def test_formats_resolved_contexts(self):
        from discord_bot import _format_qc_alert
        diff = {"new": {}, "resolved": ["CPO", "AI Server"]}
        message = _format_qc_alert(diff)
        assert message is not None
        assert "CPO" in message and "AI Server" in message


class TestCheckRuntimeQcAndAlert:
    def test_no_qc_file_sends_nothing(self, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock
        from discord_bot import _check_runtime_qc_and_alert

        with patch.object(discord_bot, "SCRAPER_BASE", tmp_path), \
             patch("discord_bot.send_discord", new=AsyncMock()) as mock_send:
            asyncio.run(_check_runtime_qc_and_alert("https://example.invalid/webhook"))

        mock_send.assert_not_awaited()

    def test_no_webhook_url_sends_nothing(self, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock
        from discord_bot import _check_runtime_qc_and_alert

        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        (output_dir / "usci_runtime_qc.json").write_text(
            '{"runs": [{"contexts": {"CPO": ["warn"]}}]}', encoding="utf-8"
        )

        with patch.object(discord_bot, "SCRAPER_BASE", tmp_path), \
             patch("discord_bot.send_discord", new=AsyncMock()) as mock_send:
            asyncio.run(_check_runtime_qc_and_alert(""))

        mock_send.assert_not_awaited()

    def test_new_warning_since_previous_run_sends_alert(self, tmp_path):
        import asyncio, json
        from unittest.mock import AsyncMock
        from discord_bot import _check_runtime_qc_and_alert

        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        payload = {
            "runs": [
                {"contexts": {}},
                {"contexts": {"CPO": ["[runtime-qc][CPO] orphan nodes: A"]}},
            ]
        }
        (output_dir / "usci_runtime_qc.json").write_text(json.dumps(payload), encoding="utf-8")

        with patch.object(discord_bot, "SCRAPER_BASE", tmp_path), \
             patch("discord_bot.send_discord", new=AsyncMock()) as mock_send:
            asyncio.run(_check_runtime_qc_and_alert("https://example.invalid/webhook"))

        mock_send.assert_awaited_once()
        args, _ = mock_send.call_args
        assert args[0] == "https://example.invalid/webhook"
        assert "orphan nodes: A" in args[1]

    def test_unchanged_warning_since_previous_run_sends_nothing(self, tmp_path):
        import asyncio, json
        from unittest.mock import AsyncMock
        from discord_bot import _check_runtime_qc_and_alert

        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        same_warnings = {"CPO": ["[runtime-qc][CPO] orphan nodes: A"]}
        payload = {"runs": [{"contexts": same_warnings}, {"contexts": same_warnings}]}
        (output_dir / "usci_runtime_qc.json").write_text(json.dumps(payload), encoding="utf-8")

        with patch.object(discord_bot, "SCRAPER_BASE", tmp_path), \
             patch("discord_bot.send_discord", new=AsyncMock()) as mock_send:
            asyncio.run(_check_runtime_qc_and_alert("https://example.invalid/webhook"))

        mock_send.assert_not_awaited()

    def test_corrupt_qc_file_sends_nothing_and_does_not_raise(self, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock
        from discord_bot import _check_runtime_qc_and_alert

        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        (output_dir / "usci_runtime_qc.json").write_text("{ not valid json", encoding="utf-8")

        with patch.object(discord_bot, "SCRAPER_BASE", tmp_path), \
             patch("discord_bot.send_discord", new=AsyncMock()) as mock_send:
            asyncio.run(_check_runtime_qc_and_alert("https://example.invalid/webhook"))

        mock_send.assert_not_awaited()

    def test_top_level_json_array_sends_nothing_and_does_not_raise(self, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock
        from discord_bot import _check_runtime_qc_and_alert

        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        (output_dir / "usci_runtime_qc.json").write_text("[]", encoding="utf-8")

        with patch.object(discord_bot, "SCRAPER_BASE", tmp_path), \
             patch("discord_bot.send_discord", new=AsyncMock()) as mock_send:
            asyncio.run(_check_runtime_qc_and_alert("https://example.invalid/webhook"))

        mock_send.assert_not_awaited()

    def test_non_dict_run_entries_send_nothing_and_do_not_raise(self, tmp_path):
        import asyncio, json
        from unittest.mock import AsyncMock
        from discord_bot import _check_runtime_qc_and_alert

        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        (output_dir / "usci_runtime_qc.json").write_text(
            json.dumps({"runs": ["not-a-dict", "also-not-a-dict"]}), encoding="utf-8"
        )

        with patch.object(discord_bot, "SCRAPER_BASE", tmp_path), \
             patch("discord_bot.send_discord", new=AsyncMock()) as mock_send:
            asyncio.run(_check_runtime_qc_and_alert("https://example.invalid/webhook"))

        mock_send.assert_not_awaited()
