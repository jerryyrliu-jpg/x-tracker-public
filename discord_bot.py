import asyncio, discord, json, os, re, sys
from datetime import datetime, time, timezone, timedelta
from discord.ext import commands, tasks
from dotenv import load_dotenv
from pathlib import Path
from utils import get_db_conn, load_account_config, send_discord
import yaml

TICKER_RE = re.compile(r'^[A-Z0-9.\-]{1,10}$')
DAYS_RE = re.compile(r'\bdays?:(\S+)\b', re.IGNORECASE)

SCRAPER_BASE = Path(__file__).resolve().parent
load_dotenv(SCRAPER_BASE / ".env")
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
TAIPEI = timezone(timedelta(hours=8))
DAILY_TIME_UTC = time(12, 0, tzinfo=timezone.utc)  # 20:00 Taipei

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)


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


def parse_days_from_args(args_str: str, default: int = 7) -> int:
    """Parse optional days:N from bot command argument string.

    Returns days (clamped 1–90), defaulting to `default` if absent or invalid.
    """
    m = DAYS_RE.search(args_str)
    if m:
        val = m.group(1)
        if val.isdigit():
            return max(1, min(int(val), 90))
    return default


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
        print(f"[auto-daily] query_topic failed: {stderr.decode()}")


async def _run_monthly_summary() -> None:
    """Call monthly_summary.py for every account in accounts.yaml."""
    with open(SCRAPER_BASE / "accounts.yaml") as f:
        accounts = list(yaml.safe_load(f).get("accounts", {}).keys())
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
            print(f"[auto-monthly] {account} failed: {stderr.decode()}")
        else:
            print(f"[auto-monthly] {account} done.")


@tasks.loop(time=DAILY_TIME_UTC)
async def scheduled_summary():
    """Fires daily at 20:00 Taipei. On the 1st also runs monthly per-account summary."""
    now_taipei = datetime.now(TAIPEI)
    print(f"[scheduler] running at {now_taipei.strftime('%Y-%m-%d %H:%M')} Taipei")

    # Daily summary (days:1) via webhook
    with open(SCRAPER_BASE / "accounts.yaml") as f:
        accounts_cfg = yaml.safe_load(f).get("accounts", {})
    for account_name, cfg in accounts_cfg.items():
        webhook_url = os.environ.get(cfg.get("discord_webhook_env", ""), "")
        if webhook_url:
            await _run_daily_summary(webhook_url)
            break  # one webhook is enough for the all-ticker summary

    # Monthly summary on the 1st of each month
    if now_taipei.day == 1:
        print("[scheduler] 1st of month — running monthly summary")
        await _run_monthly_summary()


@bot.event
async def on_ready():
    print(f"Bot is ready! Logged in as {bot.user}")
    if not scheduled_summary.is_running():
        scheduled_summary.start()


@bot.command()
async def stats(ctx):
    conn = get_db_conn(SCRAPER_BASE / "tweets.db")
    try:
        total = conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
        rows = conn.execute(
            "SELECT account, COUNT(*), MAX(scraped_at) FROM tweets GROUP BY account"
        ).fetchall()
    finally:
        conn.close()
    lines = [f"📊 **X-Tracker Stats** — 共 {total} 則推文"]
    for account, count, last_scraped in rows:
        ts = last_scraped[:16].replace("T", " ") if last_scraped else "—"
        lines.append(f"  • @{account}: {count} 則 · 最後抓取 {ts}")
    await ctx.send("\n".join(lines))


@bot.command()
async def summary(ctx, *, args: str = ""):
    days = parse_days_from_args(args)
    out_file = f"/tmp/bot_summary_{days}_{ctx.message.id}.json"
    cmd = [
        sys.executable,
        str(SCRAPER_BASE / "query_topic.py"),
        "--summary",
        "--days", str(days),
        "--output", out_file,
    ]

    async with ctx.typing():
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
                summary_text = res.get("summary", "")
                if summary_text:
                    for i in range(0, len(summary_text), 1900):
                        await ctx.send(summary_text[i : i + 1900])
                else:
                    await ctx.send("分析失敗，請稍後再試。")
            except Exception as e:
                print(f"Error reading /summary output: {e}")
                await ctx.send("分析失敗，請稍後再試。")
            finally:
                if os.path.exists(out_file):
                    os.unlink(out_file)
        else:
            if stderr:
                print(f"Error in /summary: {stderr.decode()}")
            await ctx.send(f"最近 {days} 天無推文資料。")


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
                        with open(out_file) as f:
                            res = json.load(f)
                        summary = res["summary"]
                        for i in range(0, len(summary), 1900):
                            await message.channel.send(summary[i : i + 1900])
                    finally:
                        os.unlink(out_file)
                else:
                    if stderr:
                        print(f"Error analyzing {ticker}: {stderr.decode()}")
                    await message.channel.send(f"找不到關於 {ticker} 的推文或分析失敗。")


bot.run(TOKEN)
