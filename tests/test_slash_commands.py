"""Tests for slash command handlers: /stats, /summary, /analyze, /llm."""
import asyncio, json, sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")

with patch("discord.ext.commands.Bot.run"):
    import discord_bot


def _make_interaction():
    """Return a minimal discord.Interaction mock."""
    interaction = MagicMock()
    interaction.id = 12345
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_sends_message(self):
        interaction = _make_interaction()
        conn = MagicMock()
        conn.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=[42])),
            MagicMock(fetchall=MagicMock(return_value=[
                ("aleabitoreddit", 42, "2026-04-07T10:00:00")
            ])),
        ]
        with patch("discord_bot.get_db_conn", return_value=conn):
            run(discord_bot.stats.callback(interaction))
        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "42" in msg
        assert "@aleabitoreddit" in msg

    def test_stats_db_error_sends_followup_and_closes(self):
        interaction = _make_interaction()
        conn = MagicMock()
        conn.execute.side_effect = Exception("db error")
        with patch("discord_bot.get_db_conn", return_value=conn):
            run(discord_bot.stats.callback(interaction))
        conn.close.assert_called()
        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "無法讀取" in msg


# ---------------------------------------------------------------------------
# /chain
# ---------------------------------------------------------------------------

class TestChain:
    def test_chain_reads_cache_only_for_case_insensitive_context_match(self, tmp_path):
        interaction = _make_interaction()
        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        cache_payload = {
            "metadata": {"generated_at": "2026-07-14T00:00:00"},
            "industries": {
                "Aerospace": {
                    "aliases": ["Aerospace"],
                    "tiers": [
                        {"id": 1, "name": "Boeing", "ticker": "BA", "tier": 0},
                        {"id": 2, "name": "Supplier 2", "ticker": None, "tier": 1},
                    ],
                    "links": [],
                }
            },
        }
        (output_dir / "usci_tiers_cache.json").write_text(json.dumps(cache_payload), encoding="utf-8")

        with patch("discord_bot.get_db_conn", side_effect=AssertionError("DB should not be opened")), \
             patch("discord_bot._try_cooldown", new=AsyncMock(return_value=0)), \
             patch.object(discord_bot, "SCRAPER_BASE", tmp_path):
            run(discord_bot.chain_view.callback(interaction, industry="AEROSPACE"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "AEROSPACE Supply Chain" in msg
        assert "找不到" not in msg

    def test_chain_reads_cache_only_for_context_alias(self, tmp_path):
        interaction = _make_interaction()
        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        cache_payload = {
            "metadata": {"generated_at": "2026-07-14T00:00:00"},
            "industries": {
                "AI Server": {
                    "aliases": ["AI Server", "AI_Server"],
                    "tiers": [
                        {"id": 1, "name": "NVIDIA", "ticker": "NVDA", "tier": 0},
                        {"id": 2, "name": "Foxconn", "ticker": "2317", "tier": 1},
                    ],
                    "links": [],
                }
            },
        }
        (output_dir / "usci_tiers_cache.json").write_text(json.dumps(cache_payload), encoding="utf-8")

        with patch("discord_bot.get_db_conn", side_effect=AssertionError("DB should not be opened")), \
             patch("discord_bot._try_cooldown", new=AsyncMock(return_value=0)), \
             patch.object(discord_bot, "SCRAPER_BASE", tmp_path):
            run(discord_bot.chain_view.callback(interaction, industry="AI SERVER"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "AI SERVER Supply Chain" in msg
        assert "`$NVDA`" in msg

    def test_chain_reads_cache_only_for_slash_alias(self, tmp_path):
        interaction = _make_interaction()
        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        cache_payload = {
            "metadata": {"generated_at": "2026-07-14T00:00:00"},
            "industries": {
                "CPO / Silicon Photonics": {
                    "aliases": ["CPO / Silicon Photonics", "CPO/Silicon Photonics"],
                    "tiers": [
                        {"id": 1, "name": "NVIDIA", "ticker": "NVDA", "tier": 0},
                        {"id": 2, "name": "Ayar Labs", "ticker": None, "tier": 1},
                    ],
                    "links": [],
                }
            },
        }
        (output_dir / "usci_tiers_cache.json").write_text(json.dumps(cache_payload), encoding="utf-8")

        with patch("discord_bot.get_db_conn", side_effect=AssertionError("DB should not be opened")), \
             patch("discord_bot._try_cooldown", new=AsyncMock(return_value=0)), \
             patch.object(discord_bot, "SCRAPER_BASE", tmp_path):
            run(discord_bot.chain_view.callback(interaction, industry="CPO/SILICON PHOTONICS"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "CPO/SILICON PHOTONICS Supply Chain" in msg
        assert "`$NVDA`" in msg

    def test_chain_reads_canonical_cache_contract_without_opening_db(self, tmp_path):
        interaction = _make_interaction()
        output_dir = tmp_path / "cpo_chain" / "output"
        output_dir.mkdir(parents=True)
        cache_payload = {
            "metadata": {"generated_at": "2026-07-14T00:00:00"},
            "industries": {
                "AI Server": {
                    "aliases": ["AI Server", "AI_Server"],
                    "tiers": [
                        {"id": 1, "name": "NVIDIA", "ticker": "NVDA", "tier": 0},
                        {"id": 2, "name": "Foxconn", "ticker": "2317", "tier": 1},
                    ],
                    "links": [],
                }
            },
        }
        (output_dir / "usci_tiers_cache.json").write_text(json.dumps(cache_payload), encoding="utf-8")

        with patch("discord_bot.get_db_conn", side_effect=AssertionError("DB should not be opened")), \
             patch("discord_bot._try_cooldown", new=AsyncMock(return_value=0)), \
             patch.object(discord_bot, "SCRAPER_BASE", tmp_path):
            run(discord_bot.chain_view.callback(interaction, industry="AI SERVER"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "`$NVDA`" in msg
        assert "_資料來源: USCI Cache" in msg


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

class TestSummary:
    def _mock_proc(self, returncode=0, out_json=None):
        proc = AsyncMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    def test_summary_clamps_days_upper(self):
        """days > 90 should be clamped to 90 before subprocess call."""
        interaction = _make_interaction()
        proc = self._mock_proc(returncode=1)  # fail fast, we just check cmd
        captured = {}

        async def fake_exec(*args, **kwargs):
            captured["cmd"] = args
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=False):
            run(discord_bot.summary.callback(interaction, days=999))

        cmd = captured["cmd"]
        days_idx = list(cmd).index("--days") + 1
        assert int(cmd[days_idx]) == 90

    def test_summary_clamps_days_lower(self):
        interaction = _make_interaction()
        proc = self._mock_proc(returncode=1)
        captured = {}

        async def fake_exec(*args, **kwargs):
            captured["cmd"] = args
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=False):
            run(discord_bot.summary.callback(interaction, days=0))

        cmd = captured["cmd"]
        days_idx = list(cmd).index("--days") + 1
        assert int(cmd[days_idx]) == 1

    def test_summary_sends_result_via_followup(self):
        interaction = _make_interaction()
        proc = self._mock_proc()
        payload = json.dumps({"summary": "Bullish on NVDA"})

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"):
            run(discord_bot.summary.callback(interaction, days=7))

        interaction.followup.send.assert_awaited()
        sent = interaction.followup.send.call_args_list[0][0][0]
        assert "NVDA" in sent

    def test_summary_multi_chunk_uses_followup_only(self):
        """All chunks must go through followup.send, never interaction.channel."""
        interaction = _make_interaction()
        proc = self._mock_proc()
        long_text = "A" * 4000  # forces 3 chunks at 1900 chars
        payload = json.dumps({"summary": long_text})

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"):
            run(discord_bot.summary.callback(interaction, days=7))

        assert interaction.followup.send.await_count >= 2
        # channel.send must never be called
        assert not hasattr(interaction, "channel") or \
               not interaction.channel.send.called

    def test_summary_no_data_sends_error(self):
        interaction = _make_interaction()
        proc = self._mock_proc(returncode=1)

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=False):
            run(discord_bot.summary.callback(interaction, days=7))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "無推文資料" in msg


# ---------------------------------------------------------------------------
# /analyze
# ---------------------------------------------------------------------------

class TestAnalyze:
    def _mock_proc(self, returncode=0):
        proc = AsyncMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)
        return proc

    def test_analyze_invalid_symbol_rejected(self):
        interaction = _make_interaction()
        run(discord_bot.analyze.callback(interaction, symbol="../etc", days=30))
        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "無效" in msg

    def test_analyze_clamps_days_upper(self):
        interaction = _make_interaction()
        proc = self._mock_proc(returncode=1)
        captured = {}

        async def fake_exec(*args, **kwargs):
            captured["cmd"] = args
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=False):
            run(discord_bot.analyze.callback(interaction, symbol="NVDA", days=999))

        cmd = captured["cmd"]
        days_idx = list(cmd).index("--days") + 1
        assert int(cmd[days_idx]) == 90

    def test_analyze_sends_result_via_followup(self):
        interaction = _make_interaction()
        proc = self._mock_proc()
        payload = json.dumps({"summary": "Strong buy on TSLA"})

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"):
            run(discord_bot.analyze.callback(interaction, symbol="TSLA", days=30))

        interaction.followup.send.assert_awaited()
        sent = interaction.followup.send.call_args_list[0][0][0]
        assert "TSLA" in sent

    def test_analyze_multi_chunk_uses_followup_only(self):
        interaction = _make_interaction()
        proc = self._mock_proc()
        long_text = "B" * 4000
        payload = json.dumps({"summary": long_text})

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"):
            run(discord_bot.analyze.callback(interaction, symbol="NVDA", days=30))

        assert interaction.followup.send.await_count >= 2
        assert not hasattr(interaction, "channel") or \
               not interaction.channel.send.called

    def test_analyze_no_data_sends_error(self):
        interaction = _make_interaction()
        proc = self._mock_proc(returncode=1)

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=False):
            run(discord_bot.analyze.callback(interaction, symbol="NVDA", days=30))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "失敗" in msg

    def test_analyze_json_parse_error_sends_error(self):
        """JSON parse failure must not cause a silent non-response."""
        interaction = _make_interaction()
        proc = self._mock_proc()

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="not valid json")), \
             patch("os.unlink"):
            run(discord_bot.analyze.callback(interaction, symbol="NVDA", days=30))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "失敗" in msg

    def test_analyze_empty_summary_sends_error(self):
        """Empty summary string must not cause a silent non-response."""
        interaction = _make_interaction()
        proc = self._mock_proc()
        payload = json.dumps({"summary": ""})

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"):
            run(discord_bot.analyze.callback(interaction, symbol="NVDA", days=30))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "失敗" in msg


# ---------------------------------------------------------------------------
# /llm
# ---------------------------------------------------------------------------

class TestLlm:
    def _mock_proc(self, returncode=0):
        proc = AsyncMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)
        return proc

    def test_llm_rejects_invalid_scheme(self):
        interaction = _make_interaction()

        run(discord_bot.llm_summarize.callback(interaction, url="x.com/user/status/123"))

        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args[0][0]
        assert "http://" in msg or "https://" in msg

    def test_llm_sends_summary_via_followup(self):
        interaction = _make_interaction()
        proc = self._mock_proc()
        payload = json.dumps({"summary": "這是一篇文章摘要"})

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"):
            run(discord_bot.llm_summarize.callback(interaction, url="https://x.com/user/status/123"))

        interaction.followup.send.assert_awaited()
        sent = interaction.followup.send.call_args_list[0][0][0]
        assert "摘要" in sent

    def test_llm_timeout_sends_error(self):
        interaction = _make_interaction()
        proc = self._mock_proc()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            run(discord_bot.llm_summarize.callback(interaction, url="https://example.com/article"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "逾時" in msg

    def test_llm_failure_sends_error(self):
        interaction = _make_interaction()
        proc = self._mock_proc(returncode=1)

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=False):
            run(discord_bot.llm_summarize.callback(interaction, url="https://example.com/article"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "無法取得摘要" in msg

    def test_llm_failure_with_empty_output_file_does_not_fall_back_to_internal_error(self):
        interaction = _make_interaction()
        proc = self._mock_proc(returncode=1)

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="")), \
             patch("os.unlink"):
            run(discord_bot.llm_summarize.callback(interaction, url="https://example.com/article"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "無法取得摘要" in msg

    def test_llm_failure_surfaces_json_error_when_present(self):
        interaction = _make_interaction()
        proc = self._mock_proc(returncode=1)
        payload = json.dumps({"summary": "", "error": "無法擷取推文內容（CDP 未啟動或需要登入）"})

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"):
            run(discord_bot.llm_summarize.callback(interaction, url="https://x.com/user/status/123"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "無法擷取推文內容" in msg

    def test_llm_failure_surfaces_empty_backend_error_when_present(self):
        interaction = _make_interaction()
        proc = self._mock_proc(returncode=1)
        payload = json.dumps({"summary": "", "error": "LLM 無回應、逾時，或只回傳無效內容"})

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"):
            run(discord_bot.llm_summarize.callback(interaction, url="https://example.com/article"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "LLM 無回應、逾時，或只回傳無效內容" in msg

    def test_llm_uses_longer_outer_timeout_budget(self):
        interaction = _make_interaction()
        proc = self._mock_proc()
        payload = json.dumps({"summary": "摘要"})
        seen = {}

        async def fake_exec(*args, **kwargs):
            return proc

        async def fake_wait_for(awaitable, timeout):
            seen["timeout"] = timeout
            return await awaitable

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("asyncio.wait_for", side_effect=fake_wait_for), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"):
            run(discord_bot.llm_summarize.callback(interaction, url="https://x.com/user/status/123"))

        assert seen["timeout"] >= 300


# ---------------------------------------------------------------------------
# on_message $TICKER handler
# ---------------------------------------------------------------------------

def _make_message(content: str):
    msg = MagicMock()
    msg.content = content
    msg.id = 99999
    msg.author = MagicMock()
    msg.author.__eq__ = lambda self, other: False  # not bot.user
    msg.channel = AsyncMock()
    msg.channel.typing = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=None),
    ))
    return msg


class TestOnMessage:
    def _mock_proc(self, returncode=0):
        proc = AsyncMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    def test_ticker_sends_result(self):
        msg = _make_message("$NVDA")
        proc = self._mock_proc()
        payload = json.dumps({"summary": "Bullish on NVDA"})

        async def fake_exec(*args, **kwargs):
            return proc

        async def fake_process_commands(m):
            pass

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"), \
             patch.object(discord_bot.bot, "process_commands", side_effect=fake_process_commands):
            run(discord_bot.on_message(msg))

        msg.channel.send.assert_awaited()
        sent = msg.channel.send.call_args_list[0][0][0]
        assert "NVDA" in sent

    def test_ticker_empty_summary_sends_error(self):
        msg = _make_message("$NVDA")
        proc = self._mock_proc()
        payload = json.dumps({"summary": ""})

        async def fake_exec(*args, **kwargs):
            return proc

        async def fake_process_commands(m):
            pass

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=payload)), \
             patch("os.unlink"), \
             patch.object(discord_bot.bot, "process_commands", side_effect=fake_process_commands):
            run(discord_bot.on_message(msg))

        msg.channel.send.assert_awaited_once()
        sent = msg.channel.send.call_args[0][0]
        assert "失敗" in sent

    def test_ticker_subprocess_failure_sends_error(self):
        msg = _make_message("$TSLA")
        proc = self._mock_proc(returncode=1)

        async def fake_exec(*args, **kwargs):
            return proc

        async def fake_process_commands(m):
            pass

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=False), \
             patch.object(discord_bot.bot, "process_commands", side_effect=fake_process_commands):
            run(discord_bot.on_message(msg))

        msg.channel.send.assert_awaited_once()
        sent = msg.channel.send.call_args[0][0]
        assert "失敗" in sent

    def test_ticker_json_parse_error_sends_error(self):
        """JSON parse failure must not cause a silent no-reply."""
        msg = _make_message("$NVDA")
        proc = self._mock_proc()

        async def fake_exec(*args, **kwargs):
            return proc

        async def fake_process_commands(m):
            pass

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="not valid json")), \
             patch("os.unlink"), \
             patch.object(discord_bot.bot, "process_commands", side_effect=fake_process_commands):
            run(discord_bot.on_message(msg))

        msg.channel.send.assert_awaited_once()
        sent = msg.channel.send.call_args[0][0]
        assert "失敗" in sent

    def test_non_ticker_message_ignored(self):
        msg = _make_message("hello world")

        async def fake_process_commands(m):
            pass

        with patch.object(discord_bot.bot, "process_commands", side_effect=fake_process_commands):
            run(discord_bot.on_message(msg))

        msg.channel.send.assert_not_called()
