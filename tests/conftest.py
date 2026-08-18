import pytest

from app import db as dbq


@pytest.fixture
async def db(tmp_path):
    conn = await dbq.connect(tmp_path / "test.db")
    yield conn
    await conn.close()


@pytest.fixture
def sample_endpoint_data():
    return {
        "slug": "test-hook",
        "name": "Test Hook",
        "enabled": True,
        "ntfy_topic": "alerts",
        "ntfy_token": "tk_secret1234",
        "ntfy_server": "https://ntfy.example.com",
        "title_template": "T: {level}",
        "message_template": "{msg}",
        "level_field": "level",
        "rules": {"WARN": {"priority": "high", "extra_tags": ["warning"]}},
        "default_priority": "default",
        "tags": "webhook",
    }
