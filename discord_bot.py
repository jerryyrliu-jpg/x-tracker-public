import asyncio, discord, json, os, re, sys, logging
from datetime import datetime, time, timezone, timedelta
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from pathlib import Path
from utils import get_db_conn, load_account_config, send_discord
from cpo_chain.edgar_fetcher import EdgarFetcher
from cpo_chain.news_fetcher import CompositeNewsFetcher
from cpo_chain.company_ticker_mapper import CompanyTickerMapper
from cpo_chain.confidence_updater import ConfidenceUpdater
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
CONFIDENCE_TIME_UTC = time(10, 0, tzinfo=timezone.utc) # 18:00 Taipei
NEWS_FETCH_TIME_UTC = time(8, 0, tzinfo=timezone.utc)    # 16:00 Taipei
NEWS_EXTRACT_TIME_UTC = time(8, 30, tzinfo=timezone.utc)  # 16:30 Taipei

intents = discord.Intents.default()
intents.message_content = True
logging.basicConfig(level=logging.INFO)
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



async def _run_cpo_update() -> None:
    """Run Universal Supply Chain extraction and export as subprocess."""
    extract_script = str(SCRAPER_BASE / "cpo_chain" / "extract_universal.py")
    export_script = str(SCRAPER_BASE / "cpo_chain" / "export_universal.py")
    
    # 1. Extract with vector search and larger limit
    proc1 = await asyncio.create_subprocess_exec(
        sys.executable, extract_script, "--limit", "200", "--vector",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(SCRAPER_BASE)
    )
    st1, er1 = await proc1.communicate()
    if proc1.returncode != 0:
        print(f"[usci-update] {extract_script} failed: {er1.decode()}")
        # Fallback to keyword search if vector fails
        await asyncio.create_subprocess_exec(sys.executable, extract_script, "--limit", "100")

    # 2. Export
    proc2 = await asyncio.create_subprocess_exec(
        sys.executable, export_script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(SCRAPER_BASE)
    )
    st2, er2 = await proc2.communicate()
    if proc2.returncode != 0:
        print(f"[usci-update] {export_script} failed: {er2.decode()}")
        return
        
    print("[usci-update] Universal supply chain update successful.")

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

async def _run_confidence_boost() -> None:
    """Run News Confidence Booster (EDGAR + News RSS)."""
    db_path = str(SCRAPER_BASE / "tweets.db")
    mapper = CompanyTickerMapper()
    edgar = EdgarFetcher()
    news = CompositeNewsFetcher(mapper=mapper)
    updater = ConfidenceUpdater(db_path, edgar, news, mapper)
    
    loop = asyncio.get_event_loop()
    try:
        # Run 100 relations per day
        result = await loop.run_in_executor(None, updater.run, 100)
        print(f"[confidence-boost] Result: {result}")
    except Exception as e:
        print(f"[confidence-boost] Error: {e}")

async def _run_news_fetch() -> None:
    """Run NewsArticleFetcher for all root companies from keywords.yaml."""
    from cpo_chain.news_article_fetcher import NewsArticleFetcher
    from cpo_chain.db import get_conn
    db_path = str(SCRAPER_BASE / "tweets.db")
    keywords_path = SCRAPER_BASE / "cpo_chain" / "keywords.yaml"

    def _fetch_in_thread():
        try:
            with open(keywords_path) as f:
                cfg = yaml.safe_load(f)
            root_companies = cfg.get("root_tickers") or []
            if not root_companies:
                print("[news-fetch] WARNING: root_tickers is empty in keywords.yaml — skipping")
                return {"google_news": 0, "sec_8k": 0}
            fetcher = NewsArticleFetcher()
            conn = get_conn(db_path)
            try:
                from cpo_chain.db import init_usci_tables
                init_usci_tables(conn)
                return fetcher.run(conn, root_companies)
            finally:
                conn.close()
        except Exception as e:
            print(f"[news-fetch] Error in thread: {e}")
            return None

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _fetch_in_thread)
        print(f"[news-fetch] Result: {result}")
    except Exception as e:
        print(f"[news-fetch] Error: {e}")

async def _run_news_extract() -> None:
    """Run NewsExtractor to process unprocessed articles."""
    from cpo_chain.news_extractor import NewsExtractor
    from cpo_chain.db import get_conn
    db_path = str(SCRAPER_BASE / "tweets.db")
    keywords_path = str(SCRAPER_BASE / "cpo_chain" / "keywords.yaml")

    def _extract_in_thread():
        extractor = NewsExtractor(db_path, keywords_path)  # Gemini init in thread
        conn = get_conn(db_path)
        try:
            from cpo_chain.db import init_usci_tables
            init_usci_tables(conn)
            return extractor.run(conn, 50)
        except Exception as e:
            print(f"[news-extract] Error in thread: {e}")
            return None
        finally:
            conn.close()

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _extract_in_thread)
        print(f"[news-extract] Result: {result}")
    except Exception as e:
        print(f"[news-extract] Error: {e}")

@tasks.loop(time=CONFIDENCE_TIME_UTC)
async def scheduled_confidence_boost():
    print(f"[scheduler] Running confidence boost at {datetime.now(TAIPEI).strftime('%Y-%m-%d %H:%M')} Taipei")
    await _run_confidence_boost()

@tasks.loop(time=NEWS_FETCH_TIME_UTC)
async def scheduled_news_fetch():
    print(f"[scheduler] Running news fetch at {datetime.now(TAIPEI).strftime('%Y-%m-%d %H:%M')} Taipei")
    await _run_news_fetch()

@tasks.loop(time=NEWS_EXTRACT_TIME_UTC)
async def scheduled_news_extract():
    print(f"[scheduler] Running news extract at {datetime.now(TAIPEI).strftime('%Y-%m-%d %H:%M')} Taipei")
    await _run_news_extract()

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
        
    if now_taipei.weekday() == 0: # Monday
        print("[scheduler] Monday — running CPO chain update")
        await _run_cpo_update()


@bot.event
async def on_ready():
    print(f"[bot] on_ready triggered for {bot.user}")
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        print(f"[bot] Syncing to guild {GUILD_ID}...")
        try:
            tree.copy_global_to(guild=guild)
            print(f"[bot] Copy global to guild done.")
            synced = await tree.sync(guild=guild)
            print(f"[bot] Guild synced {len(synced)} commands.")
        except Exception as e:
            print(f"[bot] Guild sync error: {e}")
    


    print(f"[bot] Bot is ready!")
    await bot.change_presence(activity=discord.Game(name="V3.7_LOCAL_ACTIVE"))
    if not scheduled_summary.is_running():
        scheduled_summary.start()
    if not scheduled_confidence_boost.is_running():
        scheduled_confidence_boost.start()
    if not scheduled_news_fetch.is_running():
        scheduled_news_fetch.start()
    if not scheduled_news_extract.is_running():
        scheduled_news_extract.start()


@bot.command(name="summary_test")
async def summary_prefix(ctx, days: int = 1):
    print(f"[bot]  called with days={days}")
    await ctx.send(f"正在為您準備最近 {days} 天的摘要分析... (這可能需要一點時間)")
    
    out_file = f"/tmp/prefix_summary_{days}_{ctx.message.id}.json"
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
            text = res.get("summary", "")
            if text:
                for i in range(0, len(text), 1900):
                    await ctx.send(text[i : i + 1900])
            else:
                await ctx.send("分析失敗，今日無資料。")
        except:
            await ctx.send("讀取分析結果失敗。")
    else:
        await ctx.send(f"分析執行失敗: {stderr.decode()[:200]}")

@bot.command(name="sync")
@commands.is_owner()
async def sync_commands(ctx):
    """One-time global slash command sync (bot owner only). Takes up to 1 hour to propagate."""
    await ctx.send("⏳ 正在 global sync slash commands...")
    synced = await tree.sync()
    await ctx.send(f"✅ 已同步 {len(synced)} 個指令。最多等 1 小時後 `/` 才會出現建議。")
    print(f"[sync] global sync done: {[s.name for s in synced]}")


@tree.command(name="ping", description="測試 Bot 是否有反應")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong! 我還活著。")


def format_confidence(conf: float, edgar: float, news: float) -> str:
    """Format confidence score with source badges."""
    sources = []
    if edgar > 0:
        # Each 0.15 is roughly 1 filing in our scoring model (simplified)
        count = max(1, int(edgar / 0.15) if edgar < 0.3 else 3)
        sources.append(f"SEC×{count}")
    if news > 0:
        sources.append("📰 News×1")
    
    badge = "✅ High" if conf >= 0.8 else ("📄 Mid" if conf >= 0.6 else "⚠️ Low")
    detail = f" ({', '.join(sources)})" if sources else " (Twitter only)"
    return f"[{badge}{detail}]"


@tree.command(name="supply", description="查詢通用供應鏈關係圖譜 (USCI)")
@app_commands.describe(
    industry="指定產業語境 (如 CPO, HBM, Liquid Cooling, 預設為 CPO)",
    tier="篩選特定 Tier (1-5)",
    country="篩選特定國家 (如 TW, US)",
    company="查詢特定公司"
)
async def supply_query(interaction: discord.Interaction, industry: str = "CPO", tier: int = None, country: str = None, company: str = None):
    await interaction.response.defer(thinking=True)
    cache_path = SCRAPER_BASE / "cpo_chain" / "output" / "usci_tiers_cache.json"
    
    if not cache_path.exists():
        await interaction.followup.send("⚠️ 尚未生成 USCI 快取，請等待下次排程或手動執行。")
        return

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            full_data = json.load(f)
        
        metadata = full_data.get("metadata", {})
        gen_at_str = metadata.get("generated_at", "")
        gen_at = datetime.fromisoformat(gen_at_str) if gen_at_str else datetime.now()
        is_stale = (datetime.now() - gen_at).days >= 8
        
        # Filter by industry context
        industry_data = full_data.get("industries", {}).get(industry.upper())
        if not industry_data:
            industry_data = full_data.get("industries", {}).get(industry)
            
        if not industry_data:
            available = ", ".join(full_data.get("industries", {}).keys())
            await interaction.followup.send(f"🔍 找不到產業語境 '{industry}'。目前可用: {available}")
            return

        tiers_list = industry_data.get("tiers", [])
        links = industry_data.get("links", [])
        
        results = []
        node_map = {r['id']: r for r in tiers_list}
        
        for item in tiers_list:
            t_name = item.get("name", "Unknown")
            t_val = item.get("tier", 99)
            t_country = item.get("country", "")
            t_id = item.get("id")
            
            if tier is not None and t_val != tier: continue
            # Corrected Multi-match Logic
            t_ticker = str(item.get("ticker", "") or "").upper()
            if company:
                s = company.strip().upper()
                if s not in t_name.upper() and s != t_ticker:
                    continue
            if country and country.upper() != (t_country or "").upper(): continue
            
            country_tag = f"[{t_country}] " if t_country else ""
            line = f"T{t_val}: {country_tag}**{t_name}**"
            
            # Find customers (links where this company is source)
            customers = [l for l in links if l['source'] == t_id]
            if customers:
                # Deduplicate and group by target company
                target_groups = {}
                for l in customers:
                    target_id = l['target']
                    if target_id not in target_groups:
                        target_groups[target_id] = {'roles': [], 'best_conf': l}
                    target_groups[target_id]['roles'].append(l.get('role', 'Partner'))
                    if l.get('confidence', 0) > target_groups[target_id]['best_conf'].get('confidence', 0):
                        target_groups[target_id]['best_conf'] = l
                
                cust_parts = []
                for target_id, data in target_groups.items():
                    target_node = node_map.get(target_id, {})
                    target_name = target_node.get('name', 'Unknown')
                    # Distinct and summarized roles
                    unique_roles = sorted(list(set(data['roles'])))
                    role_str = ", ".join(unique_roles)[:60]
                    l = data['best_conf']
                    conf_str = format_confidence(l.get('confidence', 0.5), l.get('edgar_score', 0), l.get('news_score', 0))
                    cust_parts.append(f"→ {target_name}: {role_str} {conf_str}")
                
                line += "\n  " + "\n  ".join(cust_parts)
                
            results.append(line)
        
        if not results:
            await interaction.followup.send(f"🔍 在 '{industry}' 中找不到符合條件的公司。")
            return

        header = f"🔗 **USCI 供應鏈: {industry}** (更新於 {gen_at.strftime('%Y-%m-%d')})\n"
        footer = "\n⚠️ 資料可能過期，請參考最新推文。" if is_stale else ""
        
        # Build text with limit
        output_text = header
        count = 0
        for res in results:
            if len(output_text) + len(res) + len(footer) > 1900:
                output_text += f"\n...以及其他 {len(results)-count} 項"
                break
            output_text += "\n" + res
            count += 1
        
        output_text += footer
        await interaction.followup.send(output_text[:2000])

    except Exception as e:
        print(f"Error in /supply: {e}")
        import traceback
        traceback.print_exc()
        await interaction.followup.send("❌ 讀取 USCI 快取失敗。")

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
    print(f"[bot] /summary called with days={days}")
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
        print(f"[bot] Message received in guild: {message.guild.name} ({message.guild.id})" if message.guild else "[bot] Message in DM")
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



@tree.command(name="pausex", description="暫停 X-Tracker 輪詢並釋放 Chrome 資源")
async def pausex(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    # 1. 停止所有監控進程
    p1 = await asyncio.create_subprocess_shell("pkill -f monitor_active.py")
    await p1.wait()
    p2 = await asyncio.create_subprocess_shell("pkill -f monitor_rss.py")
    await p2.wait()
    # 2. 強制關閉 Chrome (帶有特定 profile)
    p3 = await asyncio.create_subprocess_shell("pkill -f \"Google Chrome.*x_scraper\"")
    await p3.wait()
    
    await interaction.followup.send("🛑 **X-Tracker 已暫停**。Chrome 資源已釋放，您可以手動使用 Chrome。")


@tree.command(name="resumex", description="恢復 X-Tracker 輪詢並重啟 Chrome")
async def resumex(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    # 0. 先清理舊的監控進程，確保冪等性
    p0a = await asyncio.create_subprocess_shell("pkill -f monitor_active.py")
    await p0a.wait()
    p0b = await asyncio.create_subprocess_shell("pkill -f monitor_rss.py")
    await p0b.wait()

    # 1. 重啟 Chrome (透過腳本)
    restart_script = SCRAPER_BASE / "scripts" / "restart_chrome.sh"
    if restart_script.exists():
        p_restart = await asyncio.create_subprocess_exec("bash", str(restart_script))
        await p_restart.wait()
    
    # 2. 啟動監控進程 (使用 venv python)
    venv_python = SCRAPER_BASE / "venv" / "bin" / "python"
    active_script = SCRAPER_BASE / "monitor_active.py"
    rss_script = SCRAPER_BASE / "monitor_rss.py"
    
    cmd_active = f"nohup \"{venv_python}\" \"{active_script}\" > \"{SCRAPER_BASE}/monitor_active.log\" 2>&1 &"
    cmd_rss = f"nohup \"{venv_python}\" \"{rss_script}\" > \"{SCRAPER_BASE}/monitor_rss.log\" 2>&1 &"
    
    await asyncio.create_subprocess_shell(cmd_active)
    await asyncio.create_subprocess_shell(cmd_rss)
    
    await interaction.followup.send("🚀 **X-Tracker 已恢復**。Chrome 已重啟並恢復監控輪詢。")


bot.run(TOKEN)

