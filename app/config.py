import os
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data"))


def db_path() -> Path:
    return data_dir() / "gateway.db"


def webhook_port() -> int:
    return int(os.environ.get("WEBHOOK_PORT", "5000"))


def admin_port() -> int:
    return int(os.environ.get("ADMIN_PORT", "5001"))


def env_admin_password() -> str | None:
    return os.environ.get("ADMIN_PASSWORD") or None


def legacy_env() -> dict | None:
    # Pre-gateway deployments configured one endpoint via these vars;
    # main.seed_legacy() uses them to create a compatible endpoint on first run.
    topic = os.environ.get("NTFY_TOPIC")
    token = os.environ.get("NTFY_AUTH_TOKEN")
    if not (topic and token):
        return None
    return {
        "server": os.environ.get("NTFY_HOST_URL", ""),
        "topic": topic,
        "token": token,
    }
