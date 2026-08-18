from pathlib import Path

from app import config


def test_defaults(monkeypatch):
    for var in ("DATA_DIR", "WEBHOOK_PORT", "ADMIN_PORT", "ADMIN_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    assert config.data_dir() == Path("/data")
    assert config.db_path() == Path("/data/gateway.db")
    assert config.webhook_port() == 5000
    assert config.admin_port() == 5001
    assert config.env_admin_password() is None


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "/tmp/x")
    monkeypatch.setenv("WEBHOOK_PORT", "9000")
    monkeypatch.setenv("ADMIN_PORT", "9001")
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter22")
    assert config.data_dir() == Path("/tmp/x")
    assert config.webhook_port() == 9000
    assert config.admin_port() == 9001
    assert config.env_admin_password() == "hunter22"


def test_legacy_env_requires_topic_and_token(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("NTFY_AUTH_TOKEN", raising=False)
    assert config.legacy_env() is None
    monkeypatch.setenv("NTFY_TOPIC", "omada")
    monkeypatch.setenv("NTFY_AUTH_TOKEN", "tk_x")
    monkeypatch.setenv("NTFY_HOST_URL", "https://ntfy.example.com")
    assert config.legacy_env() == {
        "server": "https://ntfy.example.com", "topic": "omada", "token": "tk_x",
    }
