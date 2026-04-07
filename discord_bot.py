import asyncio, discord, json, os, re, sys
from datetime import datetime, time, timezone, timedelta
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from pathlib import Path
from utils import get_db_conn, load_account_config, send_discord
import yaml

TICKER_RE = re.compile(r'^[A-Z0-9.\-]{1,10}$')
DAYS_RE = re.compile(r'\bdays?:(\S+)\b', re.IGNORECASE)

SCRAPER_BASE = Path(__file__).resolve().parent
load_dotenv(SCRAPER_BASE / ".env")
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is not set in environment")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")  # set for dev (instant guild sync); unset = no auto-sync
TAIPEI = timezone(timedelta(hours=8))
DAILY_TIME_UTC = time(12, 0, tzinfo=timezone.utc)  # 20:00 Taipei

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="$", intents=intents)
tree = bot.tree


def parse_ticker_message(raw: str) -> tuple[str, int]:
    """Extract ticker and optional days:N from a message string.

    Returns (ticker_upper, days) where days defaults to 30 and caps at 90.
    days:N is stripped before ticker validation.
    """
    days = 30
    m = DAYS_RE.search(raw)
    if m:
        val = m.group(1)
        if val.isdigit():
            days = min(int(val), 90)
        raw = DAYS_RE.sub("", raw)
    return raw.strip().upper(), days



async def _run_daily_summary(webhook_url: str) -> None:
    """Call query_topic.py --summary --days 1 and send result via webhook."""
    out_file = f"/tmp/auto_daily_{datetime.now(TAIPEI).strftime('%Y%m%d')}.json"
    cmd = [
        sys.executable,
        str(SCRAPER_BASE / "query_topic.py"),
        "--summary", "--days", "1",
        "--output", out_file,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(SCRAPER_BASE),
    )
    _, stderr = await proc.communicate()
    if proc.returncode == 0 and os.path.exists(out_file):
        try:
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
            text = res.get("summary", "")
            if text:
                today = datetime.now(TAIPEI).strftime("%Y-%m-%d")
                header = f"📅 **每日摘要 ({today})**\n"
                for i in range(0, len(text), 1900):
                    await send_discord(webhook_url, (header if i == 0 else "") + text[i:i + 1900])
            else:
                await send_discord(webhook_url, "⚠️ 每日摘要：今日無推文資料。")
        except Exception as e:
            print(f"[auto-daily] error reading output: {e}")
        finally:
            if os.path.exists(out_file):
                os.unlink(out_file)
    else:
        msg = f"⚠️ 每日摘要失敗，請查看伺服器日誌。\n`{stderr.decode()[:300]}`"
        print(f"[auto-daily] query_topic failed: {stderr.decode()}")
        await send_discord(webhook_url, msg)


async def _run_monthly_summary(webhook_url: str) -> None:
    """Call monthly_summary.py for every account in accounts.yaml."""
    try:
        with open(SCRAPER_BASE / "accounts.yaml") as f:
            accounts = list(yaml.safe_load(f).get("accounts", {}).keys())
    except Exception as e:
        print(f"[auto-monthly] failed to load accounts.yaml: {e}")
        await send_discord(webhook_url, f"⚠️ 月度摘要失敗：無法讀取 accounts.yaml。\n`{e}`")
        return
    for account in accounts:
        cmd = [
            sys.executable,
            str(SCRAPER_BASE / "monthly_summary.py"),
            "--account", account,
            "--days", "30",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(SCRAPER_BASE),
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode()[:300]
            print(f"[auto-monthly] {account} failed: {err}")
            await send_discord(webhook_url, f"⚠️ 月度摘要失敗 (@{account})，請查看伺服器日誌。\n`{err}`")
        else:
            print(f"[auto-monthly] {account} done.")


@tasks.loop(time=DAILY_TIME_UTC)
async def scheduled_summary():
    """Fires daily at 20:00 Taipei. On the 1st also runs monthly per-account summary."""
    now_taipei = datetime.now(TAIPEI)
    print(f"[scheduler] running at {now_taipei.strftime('%Y-%m-%d %H:%M')} Taipei")

    try:
        with open(SCRAPER_BASE / "accounts.yaml") as f:
            accounts_cfg = yaml.safe_load(f).get("accounts", {})
    except Exception as e:
        print(f"[scheduler] failed to load accounts.yaml: {e}")
        return

    webhook_url = ""
    for cfg in accounts_cfg.values():
        webhook_url = os.environ.get(cfg.get("discord_webhook_env", ""), "")
        if webhook_url:
            break

    if not webhook_url:
        print("[scheduler] no webhook URL found, skipping.")
        return

    await _run_daily_summary(webhook_url)

    if now_taipei.day == 1:
        print("[scheduler] 1st of month — running monthly summary")
        await _run_monthly_summary(webhook_url)


@bot.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        await tree.sync(guild=guild)
        print(f"Slash commands synced to guild {GUILD_ID} (dev mode).")
    print(f"Bot is ready! Logged in as {bot.user}")
    if not scheduled_summary.is_running():
        scheduled_summary.start()


@tree.command(name="stats", description="顯示各帳號推文數量及最後抓取時間")
async def stats(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    conn = get_db_conn(SCRAPER_BASE / "tweets.db")
    try:
        total = conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
        rows = conn.execute(
            "SELECT account, COUNT(*), MAX(scraped_at) FROM tweets GROUP BY account"
        ).fetchall()
    except Exception as e:
        print(f"Error in /stats: {e}")
        await interaction.followup.send("⚠️ 無法讀取統計資料，請稍後再試。")
        return
    finally:
        conn.close()
    lines = [f"📊 **X-Tracker Stats** — 共 {total} 則推文"]
    for account, count, last_scraped in rows:
        ts = last_scraped[:16].replace("T", " ") if last_scraped else "—"
        lines.append(f"  • @{account}: {count} 則 · 最後抓取 {ts}")
    await interaction.followup.send("\n".join(lines))


@tree.command(name="summary", description="生成全標的情緒摘要報告")
@app_commands.describe(days="要追蹤的天數 (預設 7, 上限 90)")
async def summary(interaction: discord.Interaction, days: int = 7):
    days = max(1, min(days, 90))
    await interaction.response.defer(thinking=True)
    out_file = f"/tmp/bot_summary_{days}_{interaction.id}.json"
    cmd = [
        sys.executable,
        str(SCRAPER_BASE / "query_topic.py"),
        "--summary",
        "--days", str(days),
        "--output", out_file,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(SCRAPER_BASE),
    )
    _, stderr = await proc.communicate()

    if proc.returncode == 0 and os.path.exists(out_file):
        try:
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
            summary_text = res.get("summary", "")
            if summary_text:
                for i in range(0, len(summary_text), 1900):
                    await interaction.followup.send(summary_text[i : i + 1900])
            else:
                await interaction.followup.send("分析失敗，請稍後再試。")
        except Exception as e:
            print(f"Error reading /summary output: {e}")
            await interaction.followup.send("分析失敗，請稍後再試。")
        finally:
            if os.path.exists(out_file):
                os.unlink(out_file)
    else:
        if stderr:
            print(f"Error in /summary: {stderr.decode()}")
        await interaction.followup.send(f"最近 {days} 天無推文資料。")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)
    if message.content.startswith("$"):
        raw = message.content[1:].strip()
        ticker, days = parse_ticker_message(raw)
        if TICKER_RE.match(ticker):
            safe_ticker = re.sub(r'[^A-Z0-9]', '_', ticker)
            out_file = f"/tmp/bot_{safe_ticker}_{message.id}.json"
            cmd = [
                sys.executable,
                str(SCRAPER_BASE / "query_topic.py"),
                ticker,
                "--days", str(days),
                "--output", out_file,
            ]

            async with message.channel.typing():
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(SCRAPER_BASE),
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode == 0 and os.path.exists(out_file):
                    try:
                        with open(out_file, encoding="utf-8") as f:
                            res = json.load(f)
                        result_text = res.get("summary", "")
                        if result_text:
                            for i in range(0, len(result_text), 1900):
                                await message.channel.send(result_text[i : i + 1900])
                        else:
                            await message.channel.send(f"找不到關於 {ticker} 的推文或分析失敗。")
                    except Exception as e:
                        print(f"Error reading {ticker} output: {e}")
                        await message.channel.send(f"找不到關於 {ticker} 的推文或分析失敗。")
                    finally:
                        if os.path.exists(out_file):
                            os.unlink(out_file)
                else:
                    if stderr:
                        print(f"Error analyzing {ticker}: {stderr.decode()}")
                    await message.channel.send(f"找不到關於 {ticker} 的推文或分析失敗。")



@tree.command(name="analyze", description="分析特定標的的觀點趨勢")
@app_commands.describe(symbol="標的名稱 (如 TSLA, BTC)", days="追蹤天數 (預設 30, 上限 90)")
async def analyze(interaction: discord.Interaction, symbol: str, days: int = 30):
    days = max(1, min(days, 90))
    await interaction.response.defer(thinking=True)
    ticker = symbol.strip().upper()
    if not TICKER_RE.match(ticker):
        await interaction.followup.send("⚠️ 無效的標的名稱格式。")
        return

    safe_ticker = re.sub(r"[^A-Z0-9]", "_", ticker)
    out_file = f"/tmp/bot_{safe_ticker}_{interaction.id}.json"
    cmd = [
        sys.executable,
        str(SCRAPER_BASE / "query_topic.py"),
        ticker,
        "--days", str(days),
        "--output", out_file,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(SCRAPER_BASE),
    )
    _, stderr = await proc.communicate()

    if proc.returncode == 0 and os.path.exists(out_file):
        try:
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
            result_text = res.get("summary", "")
            if result_text:
                for i in range(0, len(result_text), 1900):
                    await interaction.followup.send(result_text[i : i + 1900])
            else:
                await interaction.followup.send(f"找不到關於 {ticker} 的推文或分析失敗。")
        except Exception as e:
            print(f"Error reading /analyze output: {e}")
            await interaction.followup.send(f"找不到關於 {ticker} 的推文或分析失敗。")
        finally:
            if os.path.exists(out_file):
                os.unlink(out_file)
    else:
        if stderr:
            print(f"Error analyzing {ticker}: {stderr.decode()}")
        await interaction.followup.send(f"找不到關於 {ticker} 的推文或分析失敗。")


bot.run(TOKEN)
