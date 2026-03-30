"""Shared utilities for X-tracker modules."""
import asyncio, json, os, sqlite3, sys
from pathlib import Path
from typing import Optional

import httpx
import yaml
from dotenv import load_dotenv

_DEFAULT_BASE = Path(__file__).parent
load_dotenv(_DEFAULT_BASE / ".env")


def load_account_config(account_name: str, base_path: Path = _DEFAULT_BASE) -> dict:
    """Load account config from accounts.yaml. sys.exit(1) if account not found."""
    config_path = base_path / "accounts.yaml"
    with open(config_path) as f:
        data = yaml.safe_load(f)
    accounts = data.get("accounts", {})
    if account_name not in accounts:
        print(f"Error: account '{account_name}' not found in accounts.yaml")
        print(f"Available: {list(accounts.keys())}")
        sys.exit(1)
    cfg = accounts[account_name]
    cfg["username"] = account_name
    cfg["discord_webhook"] = os.environ.get(cfg.get("discord_webhook_env", ""), "")
    return cfg


def get_db_conn(db_path) -> sqlite3.Connection:
    """Open SQLite connection with WAL mode and row_factory=Row."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


async def send_discord(
    webhook: str,
    content: str,
    image_paths: Optional[list] = None,
) -> None:
    """Send message to Discord webhook. Chunks at 1900 chars. Supports file attachments."""
    if not webhook:
        print("  [skip] Discord webhook not configured")
        return

    async with httpx.AsyncClient() as client:
        if image_paths:
            opened = []
            try:
                files = {}
                for i, p in enumerate(image_paths[:4]):
                    f = open(p, "rb")
                    opened.append(f)
                    files[f"file{i}"] = (Path(p).name, f, "image/jpeg")
                files["payload_json"] = (None, json.dumps({"content": content}), "application/json")
                await client.post(webhook, files=files)
            finally:
                for f in opened:
                    f.close()
        else:
            chunks: list[str] = []
            remaining = content
            while len(remaining) > 1900:
                idx = remaining.rfind("\n", 0, 1900)
                if idx == -1:
                    idx = 1900
                chunks.append(remaining[:idx])
                remaining = remaining[idx:].strip()
            chunks.append(remaining)
            for chunk in chunks:
                if chunk:
                    await client.post(webhook, json={"content": chunk})
                    await asyncio.sleep(1)
