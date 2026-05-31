import asyncio, discord, json, os, re, sys, logging, tempfile
from contextlib import asynccontextmanager
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

_accounts_yaml_lock = asyncio.Lock()

TICKER_RE = re.compile(r'^[A-Z\$][A-Z0-9.\-]{0,9}$')
DAYS_RE = re.compile(r'\bdays?:([^\s]+)\b', re.IGNORECASE)
_URL_SCHEME_RE = re.compile(r'^https?://', re.IGNORECASE)

_COOLDOWN_SECS = 60
_CHAIN_COOLDOWN_SECS = 10
_STATS_COOLDOWN_SECS = 5
_PAUSE_COOLDOWN_SECS = 30
_LLM_COOLDOWN_SECS = 30
_user_cooldowns: dict[int, float] = {}   # heavy ops (Gemini)
_chain_cooldowns: dict[int, float] = {}  # /chain, /supply
_stats_cooldowns: dict[int, float] = {}  # /stats
_pause_cooldowns: dict[int, float] = {}  # /pausex, /resumex
_llm_cooldowns: dict[int, float] = {}   # /llm
_gemini_sem = asyncio.Semaphore(3)       # max 3 concurrent Gemini subprocesses
_MAX_GEMINI_QUEUE = 8
_GEMINI_MAX_PENDING = 3 + _MAX_GEMINI_QUEUE
_gemini_pending = 0
_gemini_pending_lock = asyncio.Lock()


def _try_cooldown(user_id: int, cooldown_dict: dict = None, secs: int = None) -> float:
    """Atomically check and mark cooldown. Returns 0.0 if allowed, else remaining seconds.
    Also prunes entries older than 10× the cooldown window."""
    import time
    if cooldown_dict is None:
        cooldown_dict = _user_cooldowns
    if secs is None:
        secs = _COOLDOWN_SECS
    now = time.time()
    stale = [uid for uid, ts in cooldown_dict.items() if now - ts > secs * 10]
    for uid in stale:
        del cooldown_dict[uid]
    remaining = secs - (now - cooldown_dict.get(user_id, 0.0))
    if remaining > 0:
        return remaining
    cooldown_dict[user_id] = now
    return 0.0


def _parse_id_set(raw: str | None) -> set[int]:
    """Parse comma-separated Discord snowflake IDs from env."""
    if not raw:
        return set()
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.isdigit():
            out.add(int(chunk))
    return out


async def _is_owner_user(user: discord.abc.User) -> bool:
    """Owner check with optional static owner ID allowlist."""
    uid = getattr(user, "id", None)
    uid_int = None
    try:
        uid_int = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        uid_int = None
    if uid_int is not None and OWNER_USER_IDS:
        return uid_int in OWNER_USER_IDS
    try:
        return await bot.is_owner(user)
    except Exception as e:
        logging.warning("Owner check failed: %s", type(e).__name__)
        return False

SCRAPER_BASE = Path(__file__).resolve().parent
load_dotenv(SCRAPER_BASE / ".env")
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is not set in environment")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")  # set for dev (instant guild sync); unset = no auto-sync
OWNER_USER_IDS = _parse_id_set(os.environ.get("DISCORD_OWNER_IDS"))
ALLOWED_GUILD_IDS = _parse_id_set(os.environ.get("ALLOWED_GUILD_IDS"))
ALLOWED_CHANNEL_IDS = _parse_id_set(os.environ.get("ALLOWED_CHANNEL_IDS"))
TAIPEI = timezone(timedelta(hours=8))
DAILY_TIME_UTC = time(12, 0, tzinfo=timezone.utc)  # 20:00 Taipei
CONFIDENCE_TIME_UTC = time(10, 0, tzinfo=timezone.utc) # 18:00 Taipei
NEWS_FETCH_TIME_UTC = time(8, 0, tzinfo=timezone.utc)    # 16:00 Taipei
NEWS_EXTRACT_TIME_UTC = time(8, 30, tzinfo=timezone.utc)  # 16:30 Taipei

intents = discord.Intents.default()
intents.message_content = True
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # prevent webhook URL leaking into logs
bot = commands.Bot(command_prefix="$", intents=intents, allowed_mentions=discord.AllowedMentions.none())
tree = bot.tree


def _is_allowed_message_context(message: discord.Message) -> bool:
    """Restrict $ticker message entrypoint to approved guild/channel context."""
    if message.guild is None:
        return False
    # Default deny for message-triggered heavy analysis unless allowlist is configured.
    if not ALLOWED_GUILD_IDS and not ALLOWED_CHANNEL_IDS:
        return False
    if ALLOWED_GUILD_IDS and message.guild.id not in ALLOWED_GUILD_IDS:
        return False
    if ALLOWED_CHANNEL_IDS and message.channel.id not in ALLOWED_CHANNEL_IDS:
        return False
    return True


async def _gemini_queue_saturated() -> bool:
    """Return True when concurrent + queued heavy Gemini jobs exceed threshold."""
    async with _gemini_pending_lock:
        return _gemini_pending >= _GEMINI_MAX_PENDING


@asynccontextmanager
async def _gemini_job_slot():
    """Track pending Gemini jobs without relying on asyncio private internals."""
    global _gemini_pending
    async with _gemini_pending_lock:
        _gemini_pending += 1
    try:
        async with _gemini_sem:
            yield
    finally:
        async with _gemini_pending_lock:
            _gemini_pending = max(0, _gemini_pending - 1)


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
            days = max(1, min(int(val), 90))
        raw = DAYS_RE.sub("", raw)
    return raw.strip().upper(), days



async def _run_daily_summary_for_account(account: str, display_name: str, webhook_url: str) -> None:
    """Run daily summary for a single account and send to webhook."""
    today = datetime.now(TAIPEI).strftime("%Y-%m-%d")
    _fd, out_file = tempfile.mkstemp(suffix=".json", prefix="xtracker_daily_")
    os.close(_fd)
    try:
        cmd = [
            sys.executable,
            str(SCRAPER_BASE / "query_topic.py"),
            "--summary", "--days", "1",
            "--account", account,
            "--output", out_file,
        ]
        async with _gemini_job_slot():
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(SCRAPER_BASE),
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=420)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                await send_discord(webhook_url, f"⚠️ @{account} 每日摘要逾時 (>7m)，已跳過。")
                return
        if proc.returncode == 0 and os.path.exists(out_file):
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
            text = res.get("summary", "")[:20000]
            if text:
                header = f"📅 **每日摘要 — @{account} ({display_name}) · {today}**\n"
                for i in range(0, len(text), 1900):
                    await send_discord(webhook_url, (header if i == 0 else "") + text[i:i + 1900])
            else:
                await send_discord(webhook_url, f"⚠️ @{account} 每日摘要：今日無推文資料。")
        else:
            logging.warning("[auto-daily] %s query_topic failed: %s", account, stderr.decode(errors='replace')[:500])
            await send_discord(webhook_url, f"⚠️ @{account} 每日摘要失敗，請查看伺服器日誌。")
    except Exception as e:
        logging.warning("[auto-daily] %s error: %s", account, e)
    finally:
        if os.path.exists(out_file):
            os.unlink(out_file)


async def _run_daily_summary(accounts_cfg: dict, default_webhook: str) -> None:
    """Run daily summary for every enabled account, sending each as a separate message."""
    for account, cfg in accounts_cfg.items():
        if not cfg.get("enabled", True):
            continue
        webhook = os.environ.get(cfg.get("discord_webhook_env", ""), "") or default_webhook
        if not webhook:
            print(f"[auto-daily] no webhook for {account}, skipping")
            continue
        display = cfg.get("display_name", account)
        await _run_daily_summary_for_account(account, display, webhook)



async def _run_cpo_update() -> None:
    """Run Universal Supply Chain extraction and export as subprocess."""
    extract_script = str(SCRAPER_BASE / "cpo_chain" / "extract_universal.py")
    export_script = str(SCRAPER_BASE / "cpo_chain" / "export_universal.py")
    
    # 1. Extract with vector search and larger limit
    proc1 = await asyncio.create_subprocess_exec(
        sys.executable, extract_script, "--limit", "200", "--vector",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(SCRAPER_BASE)
    )
    try:
        st1, er1 = await asyncio.wait_for(proc1.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc1.kill(); await proc1.wait()
        logging.warning("[usci-update] extract timed out (>600s)")
        return
    if proc1.returncode != 0:
        logging.warning("[usci-update] %s failed: %s", extract_script, er1.decode(errors='replace')[:500])
        # Fallback to keyword search if vector fails — must await to avoid race with export
        fallback = await asyncio.create_subprocess_exec(
            sys.executable, extract_script, "--limit", "100",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(SCRAPER_BASE)
        )
        try:
            _, fb_er = await asyncio.wait_for(fallback.communicate(), timeout=600)
        except asyncio.TimeoutError:
            fallback.kill(); await fallback.wait()
            logging.warning("[usci-update] fallback extract timed out (>600s)")
            return
        if fallback.returncode != 0:
            logging.warning("[usci-update] Fallback extract failed: %s", fb_er.decode(errors='replace')[:500])
            return

    # 2. Export
    proc2 = await asyncio.create_subprocess_exec(
        sys.executable, export_script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(SCRAPER_BASE)
    )
    try:
        st2, er2 = await asyncio.wait_for(proc2.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc2.kill(); await proc2.wait()
        logging.warning("[usci-update] export timed out (>120s)")
        return
    if proc2.returncode != 0:
        logging.warning("[usci-update] %s failed: %s", export_script, er2.decode(errors='replace')[:500])
        return

    logging.info("[usci-update] Universal supply chain update successful.")

async def _run_monthly_summary(webhook_url: str) -> None:
    """Call monthly_summary.py for every account in accounts.yaml."""
    try:
        async with _accounts_yaml_lock:
            with open(SCRAPER_BASE / "accounts.yaml") as f:
                accounts = list(yaml.safe_load(f).get("accounts", {}).keys())
    except Exception as e:
        print(f"[auto-monthly] failed to load accounts.yaml: {e}")
        await send_discord(webhook_url, "⚠️ 月度摘要失敗：無法讀取 accounts.yaml，請查看伺服器日誌。")
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
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill(); await proc.wait()
            print(f"[auto-monthly] {account} timed out (>600s)")
            await send_discord(webhook_url, f"⚠️ 月度摘要逾時 (@{account})，已跳過。")
            continue
        if proc.returncode != 0:
            err = stderr.decode(errors='replace')[:300]
            print(f"[auto-monthly] {account} failed: {err}")
            await send_discord(webhook_url, f"⚠️ 月度摘要失敗 (@{account})，請查看伺服器日誌。")
        else:
            print(f"[auto-monthly] {account} done.")

async def _run_confidence_boost() -> None:
    """Run News Confidence Booster (EDGAR + News RSS)."""
    db_path = str(SCRAPER_BASE / "tweets.db")
    mapper = CompanyTickerMapper()
    edgar = EdgarFetcher()
    news = CompositeNewsFetcher(mapper=mapper)
    updater = ConfidenceUpdater(db_path, edgar, news, mapper)
    
    loop = asyncio.get_running_loop()
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
        async with _accounts_yaml_lock:
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

    await _run_daily_summary(accounts_cfg, webhook_url)

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
    await bot.change_presence(activity=discord.Game(name="v4.7.0"))
    if not scheduled_summary.is_running():
        scheduled_summary.start()
    if not scheduled_confidence_boost.is_running():
        scheduled_confidence_boost.start()
    if not scheduled_news_fetch.is_running():
        scheduled_news_fetch.start()
    if not scheduled_news_extract.is_running():
        scheduled_news_extract.start()


@bot.command(name="summary_test")
@commands.is_owner()
async def summary_prefix(ctx, days: int = 1):
    days = max(1, min(days, 90))
    remaining = _try_cooldown(ctx.author.id)
    if remaining > 0:
        await ctx.send(f"⏳ 請等 {remaining:.0f} 秒後再試。")
        return
    print(f"[bot] $summary_test called with days={days}")
    await ctx.send(f"正在為您準備最近 {days} 天的摘要分析... (這可能需要一點時間)")

    _fd, out_file = tempfile.mkstemp(suffix=".json", prefix="xtracker_")
    os.close(_fd)
    cmd = [
        sys.executable,
        str(SCRAPER_BASE / "query_topic.py"),
        "--summary",
        "--days", str(days),
        "--output", out_file,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(SCRAPER_BASE),
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=420)
        except asyncio.TimeoutError:
            proc.kill(); await proc.wait()
            await ctx.send("⚠️ 分析逾時 (>7m)，已中止。")
            return
        if proc.returncode == 0 and os.path.exists(out_file):
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
            text = res.get("summary", "")
            if text:
                for i in range(0, len(text), 1900):
                    await ctx.send(text[i : i + 1900])
            else:
                await ctx.send("分析失敗，今日無資料。")
        else:
            print(f"[summary_test] failed: {stderr.decode(errors='replace')[:200]}")
            await ctx.send("分析執行失敗，請查看伺服器日誌。")
    except Exception as e:
        print(f"Error reading summary_test output: {e}")
        await ctx.send("讀取分析結果失敗。")
    finally:
        if os.path.exists(out_file):
            os.unlink(out_file)

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
    remaining = _try_cooldown(interaction.user.id, _chain_cooldowns, _CHAIN_COOLDOWN_SECS)
    if remaining > 0:
        await interaction.response.send_message(f"⏳ 請等 {remaining:.0f} 秒後再試。", ephemeral=True)
        return
    industry = industry.strip()[:30]
    if not re.match(r'^[A-Za-z0-9_\- ]{1,30}$', industry):
        await interaction.response.send_message("⚠️ 無效的產業語境格式。", ephemeral=True)
        return
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
        gen_at = datetime.fromisoformat(gen_at_str) if gen_at_str else datetime.now(timezone.utc)
        now_for_stale = datetime.now(gen_at.tzinfo) if gen_at.tzinfo else datetime.now()
        is_stale = (now_for_stale - gen_at).days >= 8
        
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
            line = f"T{t_val}: {country_tag}**{discord.utils.escape_markdown(t_name)}**"
            
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
                    cust_parts.append(f"→ {discord.utils.escape_markdown(target_name)}: {discord.utils.escape_markdown(role_str)} {conf_str}")
                
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
        print(f"Error in /supply: {type(e).__name__}", file=sys.stderr)
        await interaction.followup.send("❌ 讀取 USCI 快取失敗。")

@tree.command(name="chain", description="列出 CPO 供應鏈上中下游公司全景")
@app_commands.describe(industry="產業語境 (預設 CPO)")
async def chain_view(interaction: discord.Interaction, industry: str = "CPO"):
    remaining = _try_cooldown(interaction.user.id, _chain_cooldowns, _CHAIN_COOLDOWN_SECS)
    if remaining > 0:
        await interaction.response.send_message(f"⏳ 請等 {remaining:.0f} 秒後再試。", ephemeral=True)
        return
    industry = industry.strip()[:30]
    if not re.match(r'^[A-Za-z0-9_\- ]{1,30}$', industry):
        await interaction.response.send_message("⚠️ 無效的產業語境格式。", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    db_path = SCRAPER_BASE / "tweets.db"
    conn = None
    try:
        conn = get_db_conn(db_path)
        ctx = industry.upper()

        # Layers defined by role_category on outgoing relations
        LAYERS = [
            ("material",    "🪨 原材料層"),
            ("upstream",    "⚙️ 製造/元器件"),
            ("midstream",   "🔄 中游/整合"),
            ("equipment",   "🔧 設備/EDA"),
            ("downstream",  "📦 模組/封裝"),
        ]

        # Get all companies with relations in this context
        rows = conn.execute("""
            SELECT DISTINCT e.id, e.name, e.ticker, e.industry_tags,
                   r.role_category,
                   MAX(r.confidence) as max_conf
            FROM industry_entities e
            JOIN industry_relations r ON r.from_company_id = e.id
            WHERE r.status='active' AND r.industry_context=?
            GROUP BY e.id, r.role_category
            ORDER BY r.role_category, max_conf DESC
        """, (ctx,)).fetchall()

        # Hyperscaler tier 0 companies (customers / root nodes)
        root_rows = conn.execute("""
            SELECT DISTINCT e.id, e.name, e.ticker
            FROM industry_entities e
            JOIN industry_relations r ON r.to_company_id = e.id
            WHERE r.status='active' AND r.industry_context=?
              AND e.id NOT IN (
                  SELECT from_company_id FROM industry_relations
                  WHERE status='active' AND industry_context=?
              )
            ORDER BY e.name
        """, (ctx, ctx)).fetchall()

        # Fall back to JSON cache when SQL DB is sparse (< 20 active relations for this context)
        USE_CACHE = len(rows) + len(root_rows) < 20
        cache_path = SCRAPER_BASE / "cpo_chain" / "output" / "usci_tiers_cache.json"
        if USE_CACHE and cache_path.exists():
            with open(cache_path, encoding="utf-8") as _f:
                _cache = json.load(_f)
            _inds = _cache.get("industries", {})
            industry_data = _inds.get(ctx) or _inds.get(industry)
            if industry_data:
                TIER_LABELS = {0: "🏢 終端客戶", 1: "⚙️ 直接供應商", 2: "🔄 二階供應商", 3: "🪨 原材料"}
                tiers_list = industry_data.get("tiers", [])
                from collections import defaultdict as _dd
                by_tier: dict = _dd(list)
                for item in tiers_list:
                    by_tier[item.get("tier", 99)].append(item)
                lines = [f"## 📊 {ctx} Supply Chain — 上中下游全景\n"]
                for t_num in sorted(by_tier):
                    label = TIER_LABELS.get(t_num, f"Tier {t_num}")
                    companies = by_tier[t_num]
                    parts = []
                    for c in companies[:15]:
                        ticker = c.get("ticker") or ""
                        name = discord.utils.escape_markdown(c.get("name", ""))
                        tag = f"`${ticker}`" if ticker and TICKER_RE.match(ticker) else f"_{name}_"
                        parts.append(tag)
                    overflow = len(companies) - 15
                    line = f"**{label}** ({len(companies)})\n" + "  ".join(parts)
                    if overflow > 0:
                        line += f" _(+{overflow} more)_"
                    lines.append(line)
                gen = _cache.get("metadata", {}).get("generated_at", "")[:10]
                lines.append(f"\n_資料來源: USCI Cache ({gen}) · 使用 `/supply company:NVDA` 查詢詳細_")
                msg = "\n\n".join(lines)
                if len(msg) > 1950:
                    msg = msg[:1947] + "…"
                await interaction.followup.send(msg)
                return

        if not rows and not root_rows:
            await interaction.followup.send(f"❌ 找不到 `{ctx}` 的供應鏈資料。")
            return

        # Group by role_category
        from collections import defaultdict
        groups = defaultdict(list)
        seen = set()
        for r in rows:
            key = (r["id"], r["role_category"])
            if key not in seen:
                seen.add(key)
                groups[r["role_category"]].append(r)

        lines = [f"## 📊 {ctx} Supply Chain — 上中下游全景\n"]

        for cat, label in LAYERS:
            companies = groups.get(cat, [])
            if not companies:
                continue
            parts = []
            for c in companies[:12]:
                ticker = c["ticker"]
                name = discord.utils.escape_markdown(c["name"])
                conf = c["max_conf"]
                badge = "✅" if conf >= 0.8 else ("📄" if conf >= 0.6 else "⚠️")
                tag = f"`${ticker}`" if ticker and TICKER_RE.match(ticker) else f"_{name}_"
                parts.append(f"{badge} {tag}")
            overflow = len(companies) - 12
            line = f"**{label}**\n" + "  ".join(parts)
            if overflow > 0:
                line += f" _(+{overflow} more)_"
            lines.append(line)

        # Add hyperscalers
        if root_rows:
            h_parts = [f"`${r['ticker']}`" if r["ticker"] and TICKER_RE.match(r["ticker"]) else f"_{discord.utils.escape_markdown(r['name'])}_" for r in root_rows[:8]]
            lines.append(f"**🏢 終端客戶 (Hyperscaler)**\n" + "  ".join(h_parts))

        lines.append(f"\n_資料來源: USCI DB · 使用 `/supply company:NVDA` 查詢詳細供應關係_")
        msg = "\n\n".join(lines)

        # Discord 2000 char limit
        if len(msg) > 1950:
            msg = msg[:1947] + "…"
        await interaction.followup.send(msg)

    except Exception as e:
        print(f"Error in /chain: {type(e).__name__}", file=sys.stderr)
        await interaction.followup.send("❌ 查詢失敗。")
    finally:
        if conn:
            conn.close()


@tree.command(name="account", description="啟用或停用監控帳號 (僅限 Bot 擁有者)")
@app_commands.describe(
    action="enable 或 disable",
    name="帳號名稱 (不含 @)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="enable", value="enable"),
    app_commands.Choice(name="disable", value="disable"),
    app_commands.Choice(name="list", value="list"),
])
async def account_toggle(interaction: discord.Interaction, action: str, name: str = ""):
    await interaction.response.defer(thinking=True, ephemeral=True)
    # Authorization: bot owner only (handles both single-owner and team-owned apps)
    if not await bot.is_owner(interaction.user):
        await interaction.followup.send("❌ 僅限 Bot 擁有者使用。", ephemeral=True)
        return

    yaml_path = SCRAPER_BASE / "accounts.yaml"
    try:
        async with _accounts_yaml_lock:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            accounts_cfg = data.get("accounts", {})

            if action == "list":
                if not accounts_cfg:
                    await interaction.followup.send("⚠️ 尚無帳號設定。", ephemeral=True)
                    return
                lines = ["**📋 監控帳號狀態**\n"]
                for acct, cfg in accounts_cfg.items():
                    status = "✅ 啟用" if cfg.get("enabled", True) else "⏸ 停用"
                    lines.append(f"{status} `@{acct}` — {cfg.get('display_name', acct)}")
                await interaction.followup.send("\n".join(lines), ephemeral=True)
                return

            if not name:
                await interaction.followup.send("❌ 請指定帳號名稱，例如：`/account action:disable name:gbstocks`", ephemeral=True)
                return

            if name not in accounts_cfg:
                available = ", ".join(f"`{k}`" for k in accounts_cfg)
                await interaction.followup.send(f"❌ 找不到帳號 `{name}`。可用帳號：{available}", ephemeral=True)
                return

            accounts_cfg[name]["enabled"] = (action == "enable")

            # Atomic write: write to temp then replace
            fd, tmp_path = tempfile.mkstemp(dir=str(yaml_path.parent), prefix=".accounts.", suffix=".yaml")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, yaml_path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

        verb = "✅ 已啟用" if action == "enable" else "⏸ 已停用"
        display = accounts_cfg[name].get("display_name", name)
        await interaction.followup.send(
            f"{verb} `@{name}` ({display})。將於下一輪 monitor 輪詢自動生效（最多 ~2 小時），或執行 `/pausex` 然後 `/resumex` 立即生效。",
            ephemeral=True
        )

    except Exception as e:
        print(f"Error in /account: {type(e).__name__}")
        await interaction.followup.send("❌ 操作失敗，請查看伺服器日誌。", ephemeral=True)


@tree.command(name="stats", description="顯示各帳號推文數量及最後抓取時間")
async def stats(interaction: discord.Interaction):
    remaining = _try_cooldown(interaction.user.id, _stats_cooldowns, _STATS_COOLDOWN_SECS)
    if remaining > 0:
        await interaction.response.send_message(f"⏳ 請等 {remaining:.0f} 秒後再試。", ephemeral=True)
        return
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
    if not await _is_owner_user(interaction.user):
        await interaction.response.send_message("❌ 僅限 Bot 擁有者使用。", ephemeral=True)
        return
    print(f"[bot] /summary called with days={days}")
    if await _gemini_queue_saturated():
        await interaction.response.send_message("⚠️ 系統忙碌中，請稍後再試。", ephemeral=True)
        return
    remaining = _try_cooldown(interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏳ 請等 {remaining:.0f} 秒後再試。", ephemeral=True)
        return
    days = max(1, min(days, 90))
    await interaction.response.defer(thinking=True)
    _fd, out_file = tempfile.mkstemp(suffix=".json", prefix="xtracker_")
    os.close(_fd)
    cmd = [
        sys.executable,
        str(SCRAPER_BASE / "query_topic.py"),
        "--summary",
        "--days", str(days),
        "--output", out_file,
    ]

    try:
        async with _gemini_job_slot():
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(SCRAPER_BASE),
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=420)
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()
                await interaction.followup.send("⚠️ 分析逾時 (>7m)，已中止。")
                return
        if proc.returncode == 0 and os.path.exists(out_file):
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
            summary_text = res.get("summary", "")[:20000]
            if summary_text:
                for i in range(0, len(summary_text), 1900):
                    await interaction.followup.send(summary_text[i : i + 1900])
            else:
                await interaction.followup.send("分析失敗，請稍後再試。")
        else:
            if stderr:
                print(f"Error in /summary: {stderr.decode(errors='replace')[:500]}")
            await interaction.followup.send(f"最近 {days} 天無推文資料。")
    except Exception as e:
        print(f"Error reading /summary output: {e}")
        await interaction.followup.send("分析失敗，請稍後再試。")
    finally:
        if os.path.exists(out_file):
            os.unlink(out_file)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)
    if message.content.startswith("$"):
        if not _is_allowed_message_context(message):
            return
        print(f"[bot] Message received in guild: {message.guild.name} ({message.guild.id})" if message.guild else "[bot] Message in DM")
        raw = message.content[1:].strip()
        ticker, days = parse_ticker_message(raw)
        if TICKER_RE.match(ticker):
            if await _gemini_queue_saturated():
                await message.channel.send("⚠️ 系統忙碌中，請稍後再試。")
                return
            remaining = _try_cooldown(message.author.id)
            if remaining > 0:
                await message.channel.send(f"⏳ 請等 {remaining:.0f} 秒後再試。")
                return
            safe_ticker = re.sub(r'[^A-Z0-9]', '_', ticker)
            _fd, out_file = tempfile.mkstemp(suffix=".json", prefix="xtracker_")
            os.close(_fd)
            try:
                cmd = [
                    sys.executable,
                    str(SCRAPER_BASE / "query_topic.py"),
                    ticker,
                    "--account", "all",
                    "--days", str(days),
                    "--output", out_file,
                ]

                async with message.channel.typing():
                    async with _gemini_job_slot():
                        proc = await asyncio.create_subprocess_exec(
                            *cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            cwd=str(SCRAPER_BASE),
                        )
                        try:
                            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=420)
                        except asyncio.TimeoutError:
                            proc.kill(); await proc.wait()
                            await message.channel.send(f"⚠️ {ticker} 分析逾時 (>7m)，已中止。")
                            return

                    if proc.returncode == 0 and os.path.exists(out_file):
                        with open(out_file, encoding="utf-8") as f:
                            res = json.load(f)
                        result_text = res.get("summary", "")[:20000]
                        if result_text:
                            for i in range(0, len(result_text), 1900):
                                await message.channel.send(result_text[i : i + 1900])
                        else:
                            await message.channel.send(f"找不到關於 {ticker} 的推文或分析失敗。")
                    else:
                        if stderr:
                            logging.warning("Error analyzing %s: %s", ticker, stderr.decode(errors='replace')[:500])
                        await message.channel.send(f"找不到關於 {ticker} 的推文或分析失敗。")
            except Exception as e:
                logging.warning("Error reading %s output: %s", ticker, e)
                await message.channel.send(f"找不到關於 {ticker} 的推文或分析失敗。")
            finally:
                if os.path.exists(out_file):
                    os.unlink(out_file)



@tree.command(name="analyze", description="分析特定標的的觀點趨勢")
@app_commands.describe(symbol="標的名稱 (如 TSLA, BTC)", days="追蹤天數 (預設 30, 上限 90)")
async def analyze(interaction: discord.Interaction, symbol: str, days: int = 30):
    if not await _is_owner_user(interaction.user):
        await interaction.response.send_message("❌ 僅限 Bot 擁有者使用。", ephemeral=True)
        return
    if await _gemini_queue_saturated():
        await interaction.response.send_message("⚠️ 系統忙碌中，請稍後再試。", ephemeral=True)
        return
    remaining = _try_cooldown(interaction.user.id)
    if remaining > 0:
        await interaction.response.send_message(f"⏳ 請等 {remaining:.0f} 秒後再試。", ephemeral=True)
        return
    days = max(1, min(days, 90))
    await interaction.response.defer(thinking=True)
    ticker = symbol.strip().upper()
    if not TICKER_RE.match(ticker):
        await interaction.followup.send("⚠️ 無效的標的名稱格式。")
        return

    safe_ticker = re.sub(r"[^A-Z0-9]", "_", ticker)
    _fd, out_file = tempfile.mkstemp(suffix=".json", prefix="xtracker_")
    os.close(_fd)
    cmd = [
        sys.executable,
        str(SCRAPER_BASE / "query_topic.py"),
        ticker,
        "--account", "all",
        "--days", str(days),
        "--output", out_file,
    ]

    try:
        async with _gemini_job_slot():
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(SCRAPER_BASE),
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=420)
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()
                await interaction.followup.send("⚠️ 分析逾時 (>7m)，已中止。")
                return
        if proc.returncode == 0 and os.path.exists(out_file):
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
            result_text = res.get("summary", "")[:20000]
            if result_text:
                for i in range(0, len(result_text), 1900):
                    await interaction.followup.send(result_text[i : i + 1900])
            else:
                await interaction.followup.send(f"找不到關於 {ticker} 的推文或分析失敗。")
        else:
            if stderr:
                print(f"Error analyzing {ticker}: {stderr.decode(errors='replace')[:500]}")
            await interaction.followup.send(f"找不到關於 {ticker} 的推文或分析失敗。")
    except Exception as e:
        print(f"Error reading /analyze output: {e}")
        await interaction.followup.send(f"找不到關於 {ticker} 的推文或分析失敗。")
    finally:
        if os.path.exists(out_file):
            os.unlink(out_file)


@tree.command(name="llm", description="摘要任意 URL 的內文，支援 X/Twitter 推文與一般網頁")
@app_commands.describe(url="要摘要的網址（例如：https://x.com/user/status/123 或任意文章 URL）")
async def llm_summarize(interaction: discord.Interaction, url: str):
    if not await _is_owner_user(interaction.user):
        await interaction.response.send_message("❌ 僅限 Bot 擁有者使用。", ephemeral=True)
        return
    if await _gemini_queue_saturated():
        await interaction.response.send_message("⚠️ 系統忙碌中，請稍後再試。", ephemeral=True)
        return
    remaining = _try_cooldown(interaction.user.id, _llm_cooldowns, _LLM_COOLDOWN_SECS)
    if remaining > 0:
        await interaction.response.send_message(f"⏳ 請等 {remaining:.0f} 秒後再試。", ephemeral=True)
        return
    url = url.strip()
    if not _URL_SCHEME_RE.match(url):
        await interaction.response.send_message(
            "⚠️ 請提供完整網址（以 http:// 或 https:// 開頭）。", ephemeral=True
        )
        return
    if len(url) > 2048:
        await interaction.response.send_message("⚠️ URL 過長（超過 2048 字元）。", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    _fd, out_file = tempfile.mkstemp(suffix=".json", prefix="xtracker_llm_")
    os.close(_fd)
    try:
        cmd = [sys.executable, str(SCRAPER_BASE / "llm_url.py"), "--url", url, "--output", out_file]
        async with _gemini_job_slot():
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(SCRAPER_BASE),
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                await interaction.followup.send("⚠️ 摘要逾時 (>3m)，已中止。")
                return
        if os.path.exists(out_file):
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
            summary = res.get("summary", "")[:8000]
            if summary:
                safe_display_url = discord.utils.escape_markdown(url[:80])
                header = f"🔗 **摘要 — {safe_display_url}{'…' if len(url) > 80 else ''}**\n"
                for i in range(0, len(summary), 1900):
                    await interaction.followup.send((header if i == 0 else "") + summary[i : i + 1900])
            else:
                err_msg = res.get("error", "摘要失敗")
                if stderr:
                    logging.warning("[llm] %s", stderr.decode(errors="replace")[:300])
                await interaction.followup.send(f"⚠️ {err_msg}")
        else:
            if stderr:
                logging.warning("[llm] %s", stderr.decode(errors="replace")[:300])
            await interaction.followup.send("⚠️ 無法取得摘要，請確認網址是否可存取。")
    except Exception as e:
        logging.warning("[llm] read output error: %s", e)
        await interaction.followup.send("⚠️ 內部錯誤，請查看日誌。")
    finally:
        if os.path.exists(out_file):
            os.unlink(out_file)


@tree.command(name="pausex", description="暫停 X-Tracker 輪詢並釋放 Chrome 資源")
async def pausex(interaction: discord.Interaction):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("❌ 僅限 Bot 擁有者使用。", ephemeral=True)
        return
    remaining = _try_cooldown(interaction.user.id, _pause_cooldowns, _PAUSE_COOLDOWN_SECS)
    if remaining > 0:
        await interaction.response.send_message(f"⏳ 請等 {remaining:.0f} 秒後再試。", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    # 1. 停止所有監控進程
    for script in ("monitor_active.py", "monitor_rss.py"):
        p = await asyncio.create_subprocess_exec("pkill", "-f", str(SCRAPER_BASE / script))
        try:
            await asyncio.wait_for(p.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
    # 2. 強制關閉 Chrome (帶有特定 profile)
    p3 = await asyncio.create_subprocess_exec("pkill", "-f", "Google Chrome.*x_scraper")
    try:
        await asyncio.wait_for(p3.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass

    await interaction.followup.send("🛑 **X-Tracker 已暫停**。Chrome 資源已釋放，您可以手動使用 Chrome。")


@tree.command(name="resumex", description="恢復 X-Tracker 輪詢並重啟 Chrome")
async def resumex(interaction: discord.Interaction):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("❌ 僅限 Bot 擁有者使用。", ephemeral=True)
        return
    remaining = _try_cooldown(interaction.user.id, _pause_cooldowns, _PAUSE_COOLDOWN_SECS)
    if remaining > 0:
        await interaction.response.send_message(f"⏳ 請等 {remaining:.0f} 秒後再試。", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    # 0. 先清理舊的監控進程，確保冪等性
    for script in ("monitor_active.py", "monitor_rss.py"):
        p = await asyncio.create_subprocess_exec("pkill", "-f", str(SCRAPER_BASE / script))
        try:
            await asyncio.wait_for(p.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass

    # 1. 重啟 Chrome (透過腳本)
    restart_script = SCRAPER_BASE / "scripts" / "restart_chrome.sh"
    if restart_script.exists():
        p_restart = await asyncio.create_subprocess_exec("bash", str(restart_script))
        try:
            await asyncio.wait_for(p_restart.wait(), timeout=60)
        except asyncio.TimeoutError:
            p_restart.kill()

    # 2. 啟動監控進程 (使用 venv python)，以 start_new_session 脫離當前進程組
    venv_python = sys.executable
    active_script = SCRAPER_BASE / "monitor_active.py"
    rss_script = SCRAPER_BASE / "monitor_rss.py"

    for script in (active_script, rss_script):
        await asyncio.create_subprocess_exec(
            str(venv_python), str(script),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )

    await interaction.followup.send("🚀 **X-Tracker 已恢復**。Chrome 已重啟並恢復監控輪詢。")


if __name__ == "__main__":
    bot.run(TOKEN)
