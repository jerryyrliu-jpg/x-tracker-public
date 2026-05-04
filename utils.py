"""Shared utilities for X-tracker modules."""
import asyncio, json, os, sqlite3, sys, time, logging
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler

import httpx
import yaml
from dotenv import load_dotenv

_DEFAULT_BASE = Path(__file__).parent
load_dotenv(_DEFAULT_BASE / ".env")

def setup_logger(name: str, log_file: str, level=logging.INFO):
    """Setup a rotating logger."""
    handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        logger.addHandler(handler)
        # Also log to console
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    return logger

class PIDLock:
    """Atomic PID Lock with stale process cleanup."""
    def __init__(self, lock_file: str, timeout_mins: int = 20):
        self.lock_path = Path(lock_file)
        self.timeout_mins = timeout_mins

    def acquire(self) -> bool:
        if self.lock_path.exists():
            try:
                pid = int(self.lock_path.read_text().strip())
                os.kill(pid, 0)
                mtime = self.lock_path.stat().st_mtime
                if (time.time() - mtime) > (self.timeout_mins * 60):
                    print(f"⚠️ Stale lock found (PID {pid}, >{self.timeout_mins}m). Killing stale process...")
                    try:
                        os.kill(pid, 9)
                    except OSError:
                        pass
                    self.lock_path.unlink()
                else:
                    return False
            except (ProcessLookupError, ValueError):
                self.lock_path.unlink(missing_ok=True)
            except Exception as e:
                print(f"❌ Lock Error: {e}")
                return False

        # Atomic create: fails if another process won the race
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def release(self):
        if self.lock_path.exists():
            self.lock_path.unlink()

class Metrics:
    """Simple metrics tracker."""
    def __init__(self, metrics_file: str):
        self.path = Path(metrics_file)
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        return {"success": 0, "fail": 0, "total_runtime": 0, "avg_runtime": 0, "last_reset": time.time()}

    def report(self, success: bool, runtime: float):
        self.data["success" if success else "fail"] += 1
        total_runs = self.data["success"] + self.data["fail"]
        self.data["total_runtime"] += runtime
        self.data["avg_runtime"] = self.data["total_runtime"] / total_runs
        self.path.write_text(json.dumps(self.data))

    def get_summary(self):
        return self.data

def load_account_config(account_name: str, base_path: Path = _DEFAULT_BASE) -> dict:
    """Load account config from accounts.yaml. sys.exit(1) if account not found."""
    config_path = base_path / "accounts.yaml"
    with open(config_path) as f:
        data = yaml.safe_load(f)
    accounts = data.get("accounts", {})
    if account_name not in accounts:
        print(f"Error: account '{account_name}' not found in accounts.yaml")
        sys.exit(1)
    cfg = accounts[account_name]
    cfg["username"] = account_name
    cfg["discord_webhook"] = os.environ.get(cfg.get("discord_webhook_env", ""), "")
    return cfg

def get_db_conn(db_path) -> sqlite3.Connection:
    """Get a database connection with WAL mode and standard settings."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

async def send_discord(webhook: str, content: str = None, embeds: list[dict] = None) -> None:
    if not webhook:
        return
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    if not payload:
        return
        
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook, json=payload)
    except Exception as e:
        print(f"send_discord failed: {e}", file=sys.stderr)
