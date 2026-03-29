import discord, os, subprocess, json, sys
from discord.ext import commands
from dotenv import load_dotenv
from pathlib import Path

SCRAPER_BASE = Path(os.getcwd())
load_dotenv(SCRAPER_BASE / '.env')
TOKEN = os.environ.get('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    print(f'🤖 Bot is ready! Logged in as {bot.user}')

@bot.command()
async def stats(ctx):
    import sqlite3
    conn = sqlite3.connect(SCRAPER_BASE / 'tweets.db')
    count = conn.execute('SELECT COUNT(*) FROM tweets').fetchone()[0]
    conn.close()
    await ctx.send(f'📊 目前資料庫共有 {count} 則推文。')

@bot.event
async def on_message(message):
    if message.author == bot.user: return
    
    # 優先處理 / 開頭的標準指令 (如 /stats)
    await bot.process_commands(message)
    
    # 若開頭是 $，則觸發股票分析 (如 $LITE)
    if message.content.startswith('$'):
        ticker = message.content[1:].upper().strip()
        if 1 <= len(ticker) <= 10:
            await message.channel.send(f'🔍 正在分析 {ticker}...')
            out_file = f'/tmp/bot_{ticker}.json'
            cmd = [sys.executable, 'query_topic.py', ticker, '--output', out_file]
            subprocess.run(cmd, capture_output=True)
            if os.path.exists(out_file):
                with open(out_file) as f: res = json.load(f)
                summary = res['summary']
                for i in range(0, len(summary), 1900):
                    await message.channel.send(summary[i:i+1900])
            else:
                await message.channel.send(f'❌ 找不到關於 {ticker} 的推文或分析失敗。')

bot.run(TOKEN)
