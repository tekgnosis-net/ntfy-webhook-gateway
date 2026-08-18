import httpx
import pytest
from httpx import ASGITransport

from app import db as dbq
from app.state import AppState


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
        "secret": "",
    }


@pytest.fixture
async def state(db):
    async with httpx.AsyncClient() as client:
        yield AppState(db, client, retry_delays=(0,))


@pytest.fixture
async def hooks_client(state):
    from app.hooks import create_hooks_app
    transport = ASGITransport(app=create_hooks_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://hooks") as c:
        yield c


@pytest.fixture
async def admin_client(state):
    from app.api import create_admin_app
    transport = ASGITransport(app=create_admin_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://admin") as c:
        yield c
