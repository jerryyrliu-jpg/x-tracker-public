"""Tests for slash command handlers: /stats, /summary, /analyze."""
import asyncio, json, sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

sys.path.insert(0, str(Path(__file__).parent.parent))

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
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args[0][0]
        assert "42" in msg
        assert "@aleabitoreddit" in msg

    def test_stats_closes_db_on_error(self):
        interaction = _make_interaction()
        conn = MagicMock()
        conn.execute.side_effect = Exception("db error")
        with patch("discord_bot.get_db_conn", return_value=conn):
            try:
                run(discord_bot.stats.callback(interaction))
            except Exception:
                pass
        conn.close.assert_called()


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
