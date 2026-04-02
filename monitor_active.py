import asyncio
import json
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from utils import setup_logger, PIDLock, Metrics, send_discord, load_account_config

# 配置
BASE_PATH = Path(__file__).resolve().parent
LOG_FILE = BASE_PATH / "monitor_active.log"
LOCK_FILE = BASE_PATH / ".x_tracker.lock"
METRICS_FILE = BASE_PATH / "metrics.json"
RESTART_SCRIPT = BASE_PATH / "scripts" / "restart_chrome.sh"

logger = setup_logger("Monitor", str(LOG_FILE))
metrics = Metrics(str(METRICS_FILE))
lock = PIDLock(str(LOCK_FILE))

# 從環境變數或 config 獲取 Discord Webhook
try:
    cfg = load_account_config("aleabitoreddit", BASE_PATH)
    DISCORD_WEBHOOK = cfg.get("discord_webhook")
except:
    DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_SERENITY")

async def run_scraper():
    """執行 Scraper 並處理結果"""
    start_time = time.time()
    try:
        # 使用 sys.executable 確保使用正確的 venv
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(BASE_PATH / "scraper_playwright.py"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        
        runtime = time.time() - start_time
        if proc.returncode == 0:
            try:
                res = json.loads(stdout.decode().strip())
                metrics.report(True, runtime)
                logger.info(f"✅ Success: New {res['new_count']} tweets ({runtime:.1f}s)")
                return True, res
            except:
                logger.error(f"❌ JSON Parse Error: {stdout.decode()}")
        elif proc.returncode == 2:
            logger.warning(f"⚠️ Potential structure change detected!")
            await send_discord(DISCORD_WEBHOOK, "🚨 **X-Tracker Alert**: Twitter structure change detected!")
        else:
            logger.error(f"❌ Scraper failed (Code {proc.returncode}): {stderr.decode()}")
            metrics.report(False, runtime)
            
    except asyncio.TimeoutError:
        logger.error(f"❌ Scraper Timeout (>600s)")
        metrics.report(False, 600)
    except Exception as e:
        logger.error(f"❌ Monitor Error: {e}")
    
    return False, None

async def main():
    logger.info("🚀 Starting X-Tracker v3.4 Active Monitor...")
    
    if not lock.acquire():
        logger.error("❌ Process already running (Lock active). Exiting.")
        sys.exit(1)

    fail_count = 0
    run_count = 0
    
    try:
        while True:
            run_count += 1
            success, res = await run_scraper()
            
            if not success:
                fail_count += 1
                if fail_count >= 3:
                    logger.warning("🚨 3 consecutive failures. Attempting Self-Healing...")
                    if RESTART_SCRIPT.exists():
                        try:
                            proc = await asyncio.create_subprocess_exec(
                                "bash", str(RESTART_SCRIPT),
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                            if proc.returncode == 0:
                                logger.info("♻️ Restart script executed successfully.")
                                await send_discord(DISCORD_WEBHOOK, "♻️ **Self-Healing**: Chrome restarted successfully.")
                                fail_count = 0
                            else:
                                logger.error(f"❌ Restart script failed: {stderr.decode()}")
                        except asyncio.TimeoutError:
                            logger.error("❌ Restart script timed out (>60s).")
                        except Exception as e:
                            logger.error(f"❌ Restart script error: {e}")
            else:
                fail_count = 0
            
            # 每 100 次或強制 Debug 時發送心跳報告
            if run_count % 100 == 0 or os.environ.get("DEBUG_HEARTBEAT"):
                stats = metrics.get_summary()
                total = stats['success'] + stats['fail']
                rate = stats['success'] / total if total > 0 else 0.0
                msg = (f"📈 **X-Tracker Heartbeat**\n"
                       f"Status: `Online`\n"
                       f"Runs: `{run_count}`\n"
                       f"Success Rate: `{rate:.1%}`\n"
                       f"Avg Runtime: `{stats['avg_runtime']:.1f}s`\n"
                       f"Last Run: `{datetime.now().strftime('%H:%M:%S')}`")
                await send_discord(DISCORD_WEBHOOK, msg)
                logger.info("💓 Heartbeat sent to Discord.")

            # 隨機抖動間隔 (15 ± 2 分鐘)
            jitter = random.randint(-120, 120)
            sleep_time = 900 + jitter
            logger.info(f"😴 Sleeping for {sleep_time}s (Jitter: {jitter}s)...")
            await asyncio.sleep(sleep_time)

    finally:
        lock.release()
        logger.info("🛑 Monitor stopped.")

if __name__ == "__main__":
    asyncio.run(main())
