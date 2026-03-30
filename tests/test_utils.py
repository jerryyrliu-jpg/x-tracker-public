import os, sys, sqlite3, pytest, tempfile
from pathlib import Path
from unittest.mock import patch, mock_open

sys.path.insert(0, str(Path(__file__).parent.parent))

FAKE_YAML = """
accounts:
  testuser:
    display_name: Test User
    discord_webhook_env: DISCORD_WEBHOOK_TEST
"""

def test_load_account_config_found():
    with patch("builtins.open", mock_open(read_data=FAKE_YAML)):
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_TEST": "https://hook.example.com"}):
            from utils import load_account_config
            cfg = load_account_config("testuser", base_path=Path("."))
    assert cfg["username"] == "testuser"
    assert cfg["display_name"] == "Test User"
    assert cfg["discord_webhook"] == "https://hook.example.com"

def test_load_account_config_not_found():
    with patch("builtins.open", mock_open(read_data=FAKE_YAML)):
        from utils import load_account_config
        with pytest.raises(SystemExit):
            load_account_config("nonexistent", base_path=Path("."))

def test_get_db_conn_has_row_factory():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        from utils import get_db_conn
        conn = get_db_conn(db_path)
        assert conn.row_factory == sqlite3.Row
        conn.close()
    finally:
        import os; os.unlink(db_path)
