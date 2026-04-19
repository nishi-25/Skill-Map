import os
import json

CONFIG_FILE = "data/config.json"


def is_setup_complete() -> bool:
    if not os.path.exists(CONFIG_FILE):
        return False
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f).get("setup_complete", False)
    except Exception:
        return False


def save_config(data: dict):
    os.makedirs("data", exist_ok=True)
    try:
        with open(CONFIG_FILE) as f:
            existing = json.load(f)
    except Exception:
        existing = {}
    existing.update(data)
    with open(CONFIG_FILE, "w") as f:
        json.dump(existing, f)


def get_db_url() -> str:
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return db_url
    os.makedirs("data", exist_ok=True)
    return "sqlite:///data/skillmap.db"
