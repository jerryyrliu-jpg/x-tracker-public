import asyncio, discord, json, os, re, sys
from discord.ext import commands
from dotenv import load_dotenv
from pathlib import Path

TICKER_RE = re.compile(r'^[A-Z0-9.\-]{1,10}$')

SCRAPER_BASE = Path(os.getcwd())
load_dotenv(SCRAPER_BASE / ".env")
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)


@bot.event
async def on_ready():
    print(f"Bot is ready! Logged in as {bot.user}")


@bot.command()
async def stats(ctx):
    import sqlite3
    conn = sqlite3.connect(SCRAPER_BASE / "tweets.db")
    count = conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    conn.close()
    await ctx.send(f"目前資料庫共有 {count} 則推文。")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)
    if message.content.startswith("$"):
        ticker = message.content[1:].upper().strip()
        if TICKER_RE.match(ticker):
            safe_ticker = re.sub(r'[^A-Z0-9]', '_', ticker)
            await message.channel.send(f"正在分析 {ticker}...")
            out_file = f"/tmp/bot_{safe_ticker}.json"
            cmd = [sys.executable, "query_topic.py", ticker, "--output", out_file]

            # Non-blocking: does not freeze the Discord event loop
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0 and os.path.exists(out_file):
                with open(out_file) as f:
                    res = json.load(f)
                summary = res["summary"]
                for i in range(0, len(summary), 1900):
                    await message.channel.send(summary[i : i + 1900])
            else:
                if stderr:
                    print(f"Error analyzing {ticker}: {stderr.decode()}")
                await message.channel.send(f"找不到關於 {ticker} 的推文或分析失敗。")


bot.run(TOKEN)
