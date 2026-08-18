# Generic Webhook → ntfy Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-purpose Omada→ntfy proxy with a generic multi-endpoint webhook gateway: SQLite-persisted endpoints with template-based payload mapping, a no-build SPA admin UI (Dashboard/Settings/Reports/Logs) on a separate LAN-only port, and a ghcr.io publish workflow.

**Architecture:** One Python process runs two FastAPI apps via two programmatic uvicorn servers on one event loop — a webhook listener (port 5000, public via reverse proxy) and an admin listener (port 5001, LAN only). Both share an `AppState` (aiosqlite connection + httpx client). All config reads hit SQLite per request, so UI saves are hot-effective with no reload mechanism.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, aiosqlite; pytest + pytest-asyncio + respx for tests; vanilla JS/HTML/CSS SPA (no build step); Docker + GitHub Actions → ghcr.io.

**Spec:** `docs/superpowers/specs/2026-08-18-webhook-gateway-design.md`

## Global Constraints

- Runtime deps ONLY: `fastapi`, `uvicorn[standard]`, `httpx`, `aiosqlite`. Dev deps ONLY: `pytest`, `pytest-asyncio`, `respx`.
- SPA has no build step and no external/CDN assets.
- Ports: webhook `5000` (`WEBHOOK_PORT`), admin `5001` (`ADMIN_PORT`). DB at `${DATA_DIR:-/data}/gateway.db`, WAL mode.
- All timestamps are UTC ISO-8601 strings from `app.db.utcnow()` (`timespec="seconds"`), so lexicographic comparison works in SQL.
- Timestamps are STORED in UTC; ALL user-facing display converts in the browser. If the server has a `TZ` env var (set via .env/docker-compose), the SPA renders in that zone — surfaced as `display_timezone` on `GET /api/auth/status` — otherwise it uses the viewer's browser-local zone. Report buckets are computed with a client-supplied `tz_offset` (minutes, JS `Date.getTimezoneOffset()` convention) so hour/day groupings match the displayed zone. The Python side never converts timezones (no tzdata dependency).
- The admin API NEVER returns a raw ntfy token — only `ntfy_token_set` (bool) and `ntfy_token_hint` (last 4 chars).
- Request bodies stored in `deliveries` are truncated to 64 KB.
- Retry behavior: initial send + up to 3 retries with delays 1s/5s/25s; 4xx responses are permanent (no retry). Tests inject tiny delays via `AppState.retry_delays`.
- Commit messages: plain conventional style, NO attribution trailers of any kind (no Co-Authored-By, no "Generated with", no session links).
- Image name: `ghcr.io/tekgnosis-net/ntfy-webhook-helper` (workflow uses `ghcr.io/${{ github.repository }}`).

---

### Task 1: Scaffolding and config module

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `app/__init__.py`, `app/config.py`, `tests/conftest.py`, `tests/test_config.py`
- Delete: `app.py` (replaced by the `app/` package)
- Modify: none

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `app.config` functions used everywhere: `data_dir() -> Path`, `db_path() -> Path`, `webhook_port() -> int`, `admin_port() -> int`, `env_admin_password() -> str | None`, `legacy_env() -> dict | None` (keys `server`, `topic`, `token`; returns None unless both `NTFY_TOPIC` and `NTFY_AUTH_TOKEN` are set).

- [ ] **Step 1: Commit the pending working-tree changes (bug fix + CLAUDE.md) so the plan starts from a clean tree**

```bash
git add app.py CLAUDE.md
git commit -m "fix: correct NTFY_HOST reference; add CLAUDE.md"
```

- [ ] **Step 2: Create dependency and tool files**

`requirements.txt`:
```
fastapi>=0.111
uvicorn[standard]>=0.30
httpx>=0.27
aiosqlite>=0.20
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8
pytest-asyncio>=0.23
respx>=0.21
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
```
(`pythonpath = ["."]` makes `import app` / `import scripts` work in tests without installing the package.)

- [ ] **Step 3: Remove the old single-file app and create the package**

```bash
git rm app.py
mkdir -p app tests
touch app/__init__.py tests/conftest.py
pip install -r requirements-dev.txt
```

- [ ] **Step 4: Write the failing test**

`tests/test_config.py`:
```python
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
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'` (or AttributeError)

- [ ] **Step 6: Write minimal implementation**

`app/config.py`:
```python
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
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: project scaffolding, config module, replace single-file app"
```

---

### Task 2: Templating engine

**Files:**
- Create: `app/templating.py`
- Test: `tests/test_templating.py`

**Interfaces:**
- Consumes: nothing
- Produces: `resolve_path(data, path: str) -> object | None` (dotted path, numeric list indices); `resolve_first(data, spec: str) -> object | None` (spec = paths separated by `|`, first non-None wins — this is how the Omada preset supports both wrapped `{"event": {...}}` and flat payloads); `payload_text(event: dict) -> str` (raw body for `{"body": ...}`-only events, else pretty JSON); `render(template: str, event: dict) -> str` (`{a.b|c}` substitution, `{payload}` special, missing → empty string, result stripped).

- [ ] **Step 1: Write the failing test**

`tests/test_templating.py`:
```python
from app.templating import payload_text, render, resolve_first, resolve_path


def test_resolve_nested_path():
    assert resolve_path({"event": {"level": "WARN"}}, "event.level") == "WARN"


def test_resolve_list_index():
    assert resolve_path({"items": [{"x": 1}]}, "items.0.x") == 1


def test_resolve_missing_returns_none():
    assert resolve_path({"a": 1}, "a.b.c") is None


def test_resolve_first_takes_first_hit():
    assert resolve_first({"text": "hi"}, "event.text|text") == "hi"
    assert resolve_first({"event": {"text": "in"}}, "event.text|text") == "in"


def test_render_substitutes_and_blanks_missing():
    out = render("[{cat}] {who}: {msg}", {"cat": "System", "msg": "up"})
    assert out == "[System] : up"


def test_render_payload_pretty_prints_json():
    assert '"a": 1' in render("{payload}", {"a": 1})


def test_render_payload_raw_body():
    assert render("{payload}", {"body": "plain text"}) == "plain text"


def test_render_empty_template():
    assert render("", {"a": 1}) == ""
    assert render(None, {"a": 1}) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_templating.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.templating'`

- [ ] **Step 3: Write minimal implementation**

`app/templating.py`:
```python
import json
import re

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.|\-]+)\}")


def resolve_path(data, path: str):
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def resolve_first(data, spec: str):
    for path in spec.split("|"):
        value = resolve_path(data, path.strip())
        if value is not None:
            return value
    return None


def payload_text(event: dict) -> str:
    if set(event.keys()) == {"body"}:
        return str(event["body"])
    return json.dumps(event, indent=2, ensure_ascii=False, default=str)


def render(template: str | None, event: dict) -> str:
    def substitute(match: re.Match) -> str:
        spec = match.group(1)
        if spec == "payload":
            return payload_text(event)
        value = resolve_first(event, spec)
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(substitute, template or "").strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_templating.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add app/templating.py tests/test_templating.py
git commit -m "feat: template engine with path alternatives and {payload} placeholder"
```

---

### Task 3: Database core — schema, settings, endpoints, sessions

**Files:**
- Create: `app/db.py`
- Modify: `tests/conftest.py` (add `db` and `sample_endpoint_data` fixtures)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing
- Produces (all `async`, first arg `db: aiosqlite.Connection`):
  - `utcnow() -> str` (sync; UTC ISO, seconds precision)
  - `connect(path) -> aiosqlite.Connection` (WAL, foreign keys, row factory, creates schema)
  - `get_setting(db, key, default=None) -> str | None`, `set_setting(db, key, value)` (value `None` deletes the row)
  - `create_endpoint(db, data: dict) -> dict` (data must contain every name in `ENDPOINT_FIELDS`; raises `aiosqlite.IntegrityError` on duplicate slug), `list_endpoints(db) -> list[dict]` (ordered by name), `get_endpoint(db, endpoint_id) -> dict | None`, `get_endpoint_by_slug(db, slug) -> dict | None`, `update_endpoint(db, endpoint_id, data: dict) -> dict | None` (partial: only provided keys change), `delete_endpoint(db, endpoint_id) -> bool`
  - Endpoint dicts have `enabled` as bool and `rules` as a parsed dict.
  - `create_session(db, token_hash, expires_at)`, `session_valid(db, token_hash) -> bool`, `delete_session(db, token_hash)`, `purge_sessions(db)`
  - `ENDPOINT_FIELDS = ("slug", "name", "enabled", "ntfy_topic", "ntfy_token", "ntfy_server", "title_template", "message_template", "level_field", "rules", "default_priority", "tags")`

- [ ] **Step 1: Add shared fixtures**

Append to `tests/conftest.py`:
```python
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
```

- [ ] **Step 2: Write the failing test**

`tests/test_db.py`:
```python
import aiosqlite
import pytest

from app import db as dbq


async def test_settings_roundtrip(db):
    assert await dbq.get_setting(db, "missing") is None
    assert await dbq.get_setting(db, "missing", "fallback") == "fallback"
    await dbq.set_setting(db, "ntfy_server", "https://n.example")
    assert await dbq.get_setting(db, "ntfy_server") == "https://n.example"
    await dbq.set_setting(db, "ntfy_server", None)
    assert await dbq.get_setting(db, "ntfy_server") is None


async def test_endpoint_crud_roundtrip(db, sample_endpoint_data):
    created = await dbq.create_endpoint(db, sample_endpoint_data)
    assert created["id"] > 0
    assert created["enabled"] is True
    assert created["rules"] == sample_endpoint_data["rules"]

    assert (await dbq.get_endpoint_by_slug(db, "test-hook"))["id"] == created["id"]
    assert len(await dbq.list_endpoints(db)) == 1

    updated = await dbq.update_endpoint(db, created["id"], {"name": "Renamed", "enabled": False})
    assert updated["name"] == "Renamed"
    assert updated["enabled"] is False
    assert updated["ntfy_token"] == "tk_secret1234"  # untouched by partial update

    assert await dbq.delete_endpoint(db, created["id"]) is True
    assert await dbq.get_endpoint(db, created["id"]) is None
    assert await dbq.delete_endpoint(db, created["id"]) is False


async def test_duplicate_slug_raises(db, sample_endpoint_data):
    await dbq.create_endpoint(db, sample_endpoint_data)
    with pytest.raises(aiosqlite.IntegrityError):
        await dbq.create_endpoint(db, sample_endpoint_data)


async def test_sessions(db):
    future = "2999-01-01T00:00:00+00:00"
    past = "2000-01-01T00:00:00+00:00"
    await dbq.create_session(db, "hash-a", future)
    await dbq.create_session(db, "hash-b", past)
    assert await dbq.session_valid(db, "hash-a") is True
    assert await dbq.session_valid(db, "hash-b") is False
    assert await dbq.session_valid(db, "hash-c") is False
    await dbq.purge_sessions(db)
    assert await dbq.session_valid(db, "hash-a") is True
    await dbq.delete_session(db, "hash-a")
    assert await dbq.session_valid(db, "hash-a") is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 4: Write minimal implementation**

`app/db.py`:
```python
import json
from datetime import datetime, timezone

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  ntfy_topic TEXT NOT NULL,
  ntfy_token TEXT NOT NULL DEFAULT '',
  ntfy_server TEXT,
  title_template TEXT NOT NULL DEFAULT '',
  message_template TEXT NOT NULL DEFAULT '{payload}',
  level_field TEXT NOT NULL DEFAULT '',
  rules TEXT NOT NULL DEFAULT '{}',
  default_priority TEXT NOT NULL DEFAULT 'default',
  tags TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY,
  endpoint_id INTEGER NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
  received_at TEXT NOT NULL,
  source_ip TEXT NOT NULL DEFAULT '',
  request_body TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  ntfy_status INTEGER,
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_deliveries_received ON deliveries(received_at);
CREATE INDEX IF NOT EXISTS ix_deliveries_endpoint ON deliveries(endpoint_id, received_at);
"""

ENDPOINT_FIELDS = (
    "slug", "name", "enabled", "ntfy_topic", "ntfy_token", "ntfy_server",
    "title_template", "message_template", "level_field", "rules",
    "default_priority", "tags",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def connect(path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(SCHEMA)
    await db.commit()
    return db


async def get_setting(db, key, default=None):
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(db, key, value):
    if value is None:
        await db.execute("DELETE FROM settings WHERE key=?", (key,))
    else:
        await db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    await db.commit()


def _endpoint_row(row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    d["rules"] = json.loads(d["rules"] or "{}")
    return d


def _encode(fields: dict) -> dict:
    out = dict(fields)
    if "rules" in out:
        out["rules"] = json.dumps(out["rules"] or {})
    if "enabled" in out:
        out["enabled"] = int(out["enabled"])
    return out


async def create_endpoint(db, data: dict) -> dict:
    row = _encode({k: data[k] for k in ENDPOINT_FIELDS})
    now = utcnow()
    columns = ", ".join(row) + ", created_at, updated_at"
    marks = ", ".join("?" for _ in row) + ", ?, ?"
    cur = await db.execute(
        f"INSERT INTO endpoints ({columns}) VALUES ({marks})",
        [*row.values(), now, now],
    )
    await db.commit()
    return await get_endpoint(db, cur.lastrowid)


async def get_endpoint(db, endpoint_id) -> dict | None:
    cur = await db.execute("SELECT * FROM endpoints WHERE id=?", (endpoint_id,))
    row = await cur.fetchone()
    return _endpoint_row(row) if row else None


async def get_endpoint_by_slug(db, slug) -> dict | None:
    cur = await db.execute("SELECT * FROM endpoints WHERE slug=?", (slug,))
    row = await cur.fetchone()
    return _endpoint_row(row) if row else None


async def list_endpoints(db) -> list[dict]:
    cur = await db.execute("SELECT * FROM endpoints ORDER BY name")
    return [_endpoint_row(r) for r in await cur.fetchall()]


async def update_endpoint(db, endpoint_id, data: dict) -> dict | None:
    fields = _encode({k: v for k, v in data.items() if k in ENDPOINT_FIELDS})
    if fields:
        assignments = ", ".join(f"{k}=?" for k in fields)
        await db.execute(
            f"UPDATE endpoints SET {assignments}, updated_at=? WHERE id=?",
            [*fields.values(), utcnow(), endpoint_id],
        )
        await db.commit()
    return await get_endpoint(db, endpoint_id)


async def delete_endpoint(db, endpoint_id) -> bool:
    cur = await db.execute("DELETE FROM endpoints WHERE id=?", (endpoint_id,))
    await db.commit()
    return cur.rowcount > 0


async def create_session(db, token_hash, expires_at):
    await db.execute(
        "INSERT OR REPLACE INTO sessions(token_hash, expires_at) VALUES(?, ?)",
        (token_hash, expires_at),
    )
    await db.commit()


async def session_valid(db, token_hash) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?",
        (token_hash, utcnow()),
    )
    return await cur.fetchone() is not None


async def delete_session(db, token_hash):
    await db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
    await db.commit()


async def purge_sessions(db):
    await db.execute("DELETE FROM sessions WHERE expires_at<=?", (utcnow(),))
    await db.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add app/db.py tests/test_db.py tests/conftest.py
git commit -m "feat: sqlite layer with settings, endpoints, sessions"
```

---

### Task 4: Delivery log queries

**Files:**
- Modify: `app/db.py` (append delivery helpers)
- Test: `tests/test_deliveries.py`

**Interfaces:**
- Consumes: Task 3 (`connect`, `create_endpoint`, `utcnow`)
- Produces:
  - `record_delivery(db, *, endpoint_id, status, source_ip="", request_body="", title="", message="", ntfy_status=None, error=None, attempts=0, duration_ms=0) -> int` (returns delivery id; truncates `request_body` to 65536 chars)
  - `list_deliveries(db, endpoint_id=None, status=None, q=None, before_id=None, limit=50) -> list[dict]` (newest first; `q` substring-matches body/title/message/error; `before_id` pages backwards)
  - `get_delivery(db, delivery_id) -> dict | None`
  - `delivery_stats(db, since_iso) -> list[dict]` (rows `{endpoint_id, status, n}`)
  - `delivery_buckets(db, since_iso, fmt, offset_minutes=0) -> list[dict]` (rows `{bucket, status, n}`; `fmt` is an SQLite strftime format; `offset_minutes` follows JS `Date.getTimezoneOffset()` — minutes UTC is ahead of the display zone — and is applied as an SQLite datetime modifier so bucket labels come out in the display zone)
  - `last_delivery_times(db) -> dict[int, str]` (endpoint_id → max received_at)
  - `purge_deliveries(db, older_than_iso) -> int` (rows deleted)

- [ ] **Step 1: Write the failing test**

`tests/test_deliveries.py`:
```python
from app import db as dbq


async def _seed(db, sample_endpoint_data):
    ep = await dbq.create_endpoint(db, sample_endpoint_data)
    ids = []
    for status, error in (("delivered", None), ("failed", "boom"), ("rejected", "endpoint disabled")):
        ids.append(await dbq.record_delivery(
            db, endpoint_id=ep["id"], status=status, request_body='{"n":1}',
            title=f"t-{status}", message="m", error=error,
        ))
    return ep, ids


async def test_record_and_get(db, sample_endpoint_data):
    ep, ids = await _seed(db, sample_endpoint_data)
    row = await dbq.get_delivery(db, ids[0])
    assert row["status"] == "delivered"
    assert row["endpoint_id"] == ep["id"]
    assert row["received_at"]


async def test_body_truncated_to_64k(db, sample_endpoint_data):
    ep = await dbq.create_endpoint(db, sample_endpoint_data)
    did = await dbq.record_delivery(db, endpoint_id=ep["id"], status="delivered",
                                    request_body="x" * 100_000)
    assert len((await dbq.get_delivery(db, did))["request_body"]) == 65536


async def test_list_filters_and_paging(db, sample_endpoint_data):
    ep, ids = await _seed(db, sample_endpoint_data)
    assert len(await dbq.list_deliveries(db)) == 3
    assert [d["id"] for d in await dbq.list_deliveries(db)] == sorted(ids, reverse=True)
    assert len(await dbq.list_deliveries(db, status="failed")) == 1
    assert len(await dbq.list_deliveries(db, q="t-rejected")) == 1
    assert len(await dbq.list_deliveries(db, endpoint_id=ep["id"] + 1)) == 0
    page = await dbq.list_deliveries(db, before_id=ids[2], limit=1)
    assert page[0]["id"] == ids[1]


async def test_stats_buckets_last_times(db, sample_endpoint_data):
    ep, _ = await _seed(db, sample_endpoint_data)
    stats = await dbq.delivery_stats(db, "2000-01-01T00:00:00+00:00")
    assert {(s["endpoint_id"], s["status"], s["n"]) for s in stats} == {
        (ep["id"], "delivered", 1), (ep["id"], "failed", 1), (ep["id"], "rejected", 1),
    }
    buckets = await dbq.delivery_buckets(db, "2000-01-01T00:00:00+00:00", "%Y-%m-%d")
    assert sum(b["n"] for b in buckets) == 3
    # display-zone bucketing: 23:00 UTC on Jan 1 is Jan 2 in UTC+2 (offset -120)
    await db.execute("UPDATE deliveries SET received_at='2026-01-01T23:00:00+00:00'")
    await db.commit()
    local = await dbq.delivery_buckets(db, "2000-01-01T00:00:00+00:00", "%Y-%m-%d",
                                       offset_minutes=-120)
    assert {b["bucket"] for b in local} == {"2026-01-02"}
    assert ep["id"] in await dbq.last_delivery_times(db)


async def test_purge(db, sample_endpoint_data):
    await _seed(db, sample_endpoint_data)
    assert await dbq.purge_deliveries(db, "2000-01-01T00:00:00+00:00") == 0
    assert await dbq.purge_deliveries(db, "2999-01-01T00:00:00+00:00") == 3
    assert await dbq.list_deliveries(db) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_deliveries.py -v`
Expected: FAIL with `AttributeError: module 'app.db' has no attribute 'record_delivery'`

- [ ] **Step 3: Write minimal implementation**

Append to `app/db.py`:
```python
MAX_BODY_CHARS = 65536


async def record_delivery(db, *, endpoint_id, status, source_ip="", request_body="",
                          title="", message="", ntfy_status=None, error=None,
                          attempts=0, duration_ms=0) -> int:
    cur = await db.execute(
        "INSERT INTO deliveries (endpoint_id, received_at, source_ip, request_body,"
        " title, message, status, ntfy_status, error, attempts, duration_ms)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (endpoint_id, utcnow(), source_ip, request_body[:MAX_BODY_CHARS],
         title, message, status, ntfy_status, error, attempts, duration_ms),
    )
    await db.commit()
    return cur.lastrowid


async def list_deliveries(db, endpoint_id=None, status=None, q=None,
                          before_id=None, limit=50) -> list[dict]:
    where, params = ["1=1"], []
    if endpoint_id is not None:
        where.append("endpoint_id=?")
        params.append(endpoint_id)
    if status:
        where.append("status=?")
        params.append(status)
    if q:
        where.append("(request_body LIKE ? OR title LIKE ? OR message LIKE ? OR error LIKE ?)")
        params += [f"%{q}%"] * 4
    if before_id is not None:
        where.append("id<?")
        params.append(before_id)
    cur = await db.execute(
        f"SELECT * FROM deliveries WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
        [*params, limit],
    )
    return [dict(r) for r in await cur.fetchall()]


async def get_delivery(db, delivery_id) -> dict | None:
    cur = await db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def delivery_stats(db, since_iso) -> list[dict]:
    cur = await db.execute(
        "SELECT endpoint_id, status, COUNT(*) AS n FROM deliveries"
        " WHERE received_at>=? GROUP BY endpoint_id, status",
        (since_iso,),
    )
    return [dict(r) for r in await cur.fetchall()]


async def delivery_buckets(db, since_iso, fmt, offset_minutes=0) -> list[dict]:
    # offset_minutes follows JS Date.getTimezoneOffset(): minutes UTC is ahead
    # of the display zone, so display time = UTC - offset.
    modifier = f"{-offset_minutes} minutes"
    cur = await db.execute(
        "SELECT strftime(?, received_at, ?) AS bucket, status, COUNT(*) AS n"
        " FROM deliveries WHERE received_at>=? GROUP BY bucket, status ORDER BY bucket",
        (fmt, modifier, since_iso),
    )
    return [dict(r) for r in await cur.fetchall()]


async def last_delivery_times(db) -> dict[int, str]:
    cur = await db.execute(
        "SELECT endpoint_id, MAX(received_at) AS last_at FROM deliveries GROUP BY endpoint_id"
    )
    return {r["endpoint_id"]: r["last_at"] for r in await cur.fetchall()}


async def purge_deliveries(db, older_than_iso) -> int:
    cur = await db.execute("DELETE FROM deliveries WHERE received_at<?", (older_than_iso,))
    await db.commit()
    return cur.rowcount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_deliveries.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_deliveries.py
git commit -m "feat: delivery log storage, filtering, aggregation, purge"
```

---

### Task 5: Built-in presets

**Files:**
- Create: `app/presets.py`
- Test: `tests/test_presets.py`

**Interfaces:**
- Consumes: nothing
- Produces: `PRESETS: list[dict]` (each with keys `key`, `label`, `description`, `title_template`, `message_template`, `level_field`, `rules`, `default_priority`, `tags`); `get_preset(key: str) -> dict | None` (returns a copy).

- [ ] **Step 1: Write the failing test**

`tests/test_presets.py`:
```python
from app.presets import PRESETS, get_preset
from app.templating import render, resolve_first


def test_preset_keys_complete():
    required = {"key", "label", "description", "title_template", "message_template",
                "level_field", "rules", "default_priority", "tags"}
    for preset in PRESETS:
        assert required <= set(preset), preset.get("key")


def test_get_preset_returns_copy():
    omada = get_preset("omada")
    omada["tags"] = "mutated"
    assert get_preset("omada")["tags"] == "omada,network"
    assert get_preset("nope") is None


def test_omada_preset_handles_both_payload_shapes():
    omada = get_preset("omada")
    wrapped = {"event": {"category": "Device", "target": "AP1", "text": "went down", "level": "WARN"}}
    flat = {"category": "Device", "target": "AP1", "text": "went down", "level": "WARN"}
    for payload in (wrapped, flat):
        assert render(omada["message_template"], payload) == "[Device] AP1: went down"
        assert resolve_first(payload, omada["level_field"]) == "WARN"
    assert "WARN" in omada["rules"]
    assert omada["rules"]["ERROR"]["priority"] == "urgent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_presets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.presets'`

- [ ] **Step 3: Write minimal implementation**

`app/presets.py`:
```python
import copy

_URGENT = {"priority": "urgent", "extra_tags": ["rotating_light", "fire"]}
_HIGH = {"priority": "high", "extra_tags": ["warning"]}
_INFO = {"priority": "default", "extra_tags": ["information_source"]}

PRESETS = [
    {
        "key": "generic",
        "label": "Generic JSON",
        "description": "Sends the whole payload pretty-printed; the title falls back to the endpoint name.",
        "title_template": "",
        "message_template": "{payload}",
        "level_field": "",
        "rules": {},
        "default_priority": "default",
        "tags": "webhook",
    },
    {
        "key": "omada",
        "label": "TP-Link Omada",
        "description": "Reproduces the original Omada controller mapping (wrapped or flat payloads).",
        "title_template": "Omada: {event.category|category} ({event.level|level})",
        "message_template": "[{event.category|category}] {event.target|target}: {event.text|text}",
        "level_field": "event.level|level",
        "rules": {
            "WARN": _HIGH, "WARNING": _HIGH,
            "ALERT": _URGENT, "ERROR": _URGENT, "CRITICAL": _URGENT,
            "NOTICE": _INFO, "INFO": _INFO,
        },
        "default_priority": "default",
        "tags": "omada,network",
    },
]


def get_preset(key: str) -> dict | None:
    preset = next((p for p in PRESETS if p["key"] == key), None)
    return copy.deepcopy(preset) if preset else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_presets.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/presets.py tests/test_presets.py
git commit -m "feat: built-in generic and omada endpoint presets"
```

---

### Task 6: ntfy client

**Files:**
- Create: `app/ntfy.py`
- Test: `tests/test_ntfy.py`

**Interfaces:**
- Consumes: nothing
- Produces: `SendResult` dataclass (`ok: bool`, `status_code: int | None`, `error: str | None`, `attempts: int`); `RETRY_DELAYS = (1.0, 5.0, 25.0)`; `async send(client: httpx.AsyncClient, server: str, topic: str, token: str | None, title: str, message: str, priority: str, tags: list[str], retry_delays=RETRY_DELAYS) -> SendResult`. Semantics: POST `{server}/{topic}` with message as UTF-8 body and Title/Priority/Tags/`Authorization: Bearer` headers; initial attempt + one retry per entry in `retry_delays` (sleeping that many seconds first); `<300` → success, 4xx → permanent failure (stop), 5xx/network error → retry.

- [ ] **Step 1: Write the failing test**

`tests/test_ntfy.py`:
```python
import httpx
import pytest
import respx

from app.ntfy import send


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


@respx.mock
async def test_success_sends_ntfy_headers(client):
    route = respx.post("https://n.example/alerts").mock(return_value=httpx.Response(200))
    result = await send(client, "https://n.example", "alerts", "tk_abc",
                        "Title", "hello", "high", ["a", "b"], retry_delays=())
    assert result.ok and result.status_code == 200 and result.attempts == 1
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer tk_abc"
    assert req.headers["Title"] == "Title"
    assert req.headers["Priority"] == "high"
    assert req.headers["Tags"] == "a,b"
    assert req.content == b"hello"


@respx.mock
async def test_no_token_no_auth_header(client):
    route = respx.post("https://n.example/alerts").mock(return_value=httpx.Response(200))
    await send(client, "https://n.example/", "alerts", None, "T", "m", "default", [], retry_delays=())
    assert "authorization" not in route.calls[0].request.headers


@respx.mock
async def test_server_error_retries_then_succeeds(client):
    route = respx.post("https://n.example/alerts")
    route.side_effect = [httpx.Response(500), httpx.Response(200)]
    result = await send(client, "https://n.example", "alerts", None, "T", "m",
                        "default", [], retry_delays=(0, 0, 0))
    assert result.ok and result.attempts == 2


@respx.mock
async def test_client_error_is_permanent(client):
    respx.post("https://n.example/alerts").mock(return_value=httpx.Response(403))
    result = await send(client, "https://n.example", "alerts", None, "T", "m",
                        "default", [], retry_delays=(0, 0, 0))
    assert not result.ok and result.attempts == 1 and "403" in result.error


@respx.mock
async def test_network_error_exhausts_retries(client):
    respx.post("https://n.example/alerts").mock(side_effect=httpx.ConnectError("refused"))
    result = await send(client, "https://n.example", "alerts", None, "T", "m",
                        "default", [], retry_delays=(0, 0))
    assert not result.ok and result.attempts == 3 and result.status_code is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ntfy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ntfy'`

- [ ] **Step 3: Write minimal implementation**

`app/ntfy.py`:
```python
import asyncio
from dataclasses import dataclass

import httpx

RETRY_DELAYS = (1.0, 5.0, 25.0)


@dataclass
class SendResult:
    ok: bool
    status_code: int | None
    error: str | None
    attempts: int


async def send(client: httpx.AsyncClient, server: str, topic: str, token: str | None,
               title: str, message: str, priority: str, tags: list[str],
               retry_delays=RETRY_DELAYS) -> SendResult:
    url = server.rstrip("/") + "/" + topic
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    attempts = 0
    status = None
    last_error = None
    for delay in (0.0, *retry_delays):
        if delay:
            await asyncio.sleep(delay)
        attempts += 1
        try:
            response = await client.post(url, content=message.encode("utf-8"), headers=headers)
            status = response.status_code
            if response.status_code < 300:
                return SendResult(True, status, None, attempts)
            last_error = f"ntfy returned {response.status_code}"
            if response.status_code < 500:
                break  # bad token/topic/request — retrying cannot help
        except httpx.HTTPError as exc:
            status = None
            last_error = str(exc) or exc.__class__.__name__
    return SendResult(False, status, last_error, attempts)
```

Note: ntfy titles travel as HTTP headers, which are latin-1 in httpx; non-ASCII titles would raise. That is acceptable for v1 (ntfy's own docs recommend ASCII titles); the message body is UTF-8 and unaffected.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ntfy.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ntfy.py tests/test_ntfy.py
git commit -m "feat: async ntfy client with bounded retries"
```

---

### Task 7: AppState and dispatch pipeline

**Files:**
- Create: `app/state.py`, `app/hooks.py` (pipeline functions only; the FastAPI app comes in Task 8)
- Modify: `tests/conftest.py` (add `state` fixture)
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: Tasks 2–6 (`templating.render/resolve_first/payload_text`, `db.record_delivery/get_setting`, `ntfy.send/SendResult`)
- Produces:
  - `app.state.AppState(db, client, retry_delays=(1.0, 5.0, 25.0))` with `.db`, `.client`, `.retry_delays`, `.spawn(coro) -> asyncio.Task` (tracked so tasks aren't GC'd), `async .drain()` (await all tracked tasks — test helper; never call it after spawning an infinite loop).
  - `app.hooks.parse_events(raw: bytes) -> list[dict]` — JSON object → `[obj]`; JSON array → one event per item (non-dict items become `{"body": str(item)}`); non-JSON / scalar → `[{"body": text}]`.
  - `app.hooks.build_notification(endpoint: dict, event: dict) -> dict` with keys `title`, `message`, `priority`, `tags` (list). Empty rendered title → endpoint name; empty rendered message → `payload_text(event)`; level looked up via `resolve_first(event, endpoint["level_field"])`, uppercased, matched against `endpoint["rules"]`; rule may override priority and append `extra_tags` (deduplicated).
  - `async app.hooks.dispatch_event(state, endpoint, event, source_ip, request_body) -> dict` — sends via ntfy (server = endpoint override or `ntfy_server` setting; no server configured → immediate failure, 0 attempts), records a `deliveries` row, returns `{"status", "ntfy_status", "error", "attempts", "delivery_id"}`.
  - `app.hooks.MAX_BODY = 65536`

- [ ] **Step 1: Add the state fixture**

Append to `tests/conftest.py`:
```python
import httpx

from app.state import AppState


@pytest.fixture
async def state(db):
    async with httpx.AsyncClient() as client:
        yield AppState(db, client, retry_delays=(0,))
```

- [ ] **Step 2: Write the failing test**

`tests/test_dispatch.py`:
```python
import httpx
import respx

from app import db as dbq
from app.hooks import build_notification, dispatch_event, parse_events


def test_parse_events_shapes():
    assert parse_events(b'{"a": 1}') == [{"a": 1}]
    assert parse_events(b'[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]
    assert parse_events(b"[1, 2]") == [{"body": "1"}, {"body": "2"}]
    assert parse_events(b"plain text") == [{"body": "plain text"}]
    assert parse_events(b'"scalar"') == [{"body": "scalar"}]


def test_build_notification_rules_and_fallbacks(sample_endpoint_data):
    endpoint = {**sample_endpoint_data, "id": 1}
    notif = build_notification(endpoint, {"level": "warn", "msg": "disk"})
    assert notif == {"title": "T: warn", "message": "disk",
                     "priority": "high", "tags": ["webhook", "warning"]}

    # no rule match -> default priority, base tags only
    notif = build_notification(endpoint, {"level": "INFO", "msg": "ok"})
    assert notif["priority"] == "default" and notif["tags"] == ["webhook"]

    # empty renders fall back to endpoint name / payload text
    empty = {**endpoint, "title_template": "", "message_template": "{missing}"}
    notif = build_notification(empty, {"a": 1})
    assert notif["title"] == "Test Hook" and '"a": 1' in notif["message"]


@respx.mock
async def test_dispatch_records_success(state, sample_endpoint_data):
    endpoint = await dbq.create_endpoint(state.db, sample_endpoint_data)
    respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    outcome = await dispatch_event(state, endpoint, {"level": "WARN", "msg": "hi"}, "1.2.3.4", "{}")
    assert outcome["status"] == "delivered" and outcome["ntfy_status"] == 200
    row = await dbq.get_delivery(state.db, outcome["delivery_id"])
    assert row["status"] == "delivered" and row["source_ip"] == "1.2.3.4"
    assert row["title"] == "T: WARN" and row["message"] == "hi"


@respx.mock
async def test_dispatch_uses_global_server_fallback(state, sample_endpoint_data):
    data = {**sample_endpoint_data, "ntfy_server": None}
    endpoint = await dbq.create_endpoint(state.db, data)
    await dbq.set_setting(state.db, "ntfy_server", "https://global.example")
    route = respx.post("https://global.example/alerts").mock(return_value=httpx.Response(200))
    outcome = await dispatch_event(state, endpoint, {"msg": "x"}, "", "")
    assert outcome["status"] == "delivered" and route.called


async def test_dispatch_without_server_fails(state, sample_endpoint_data):
    data = {**sample_endpoint_data, "ntfy_server": None}
    endpoint = await dbq.create_endpoint(state.db, data)
    outcome = await dispatch_event(state, endpoint, {"msg": "x"}, "", "")
    assert outcome["status"] == "failed" and outcome["attempts"] == 0
    assert "no ntfy server" in outcome["error"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_dispatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.state'`

- [ ] **Step 4: Write minimal implementation**

`app/state.py`:
```python
import asyncio


class AppState:
    """Services shared by the webhook and admin apps."""

    def __init__(self, db, client, retry_delays=(1.0, 5.0, 25.0)):
        self.db = db
        self.client = client
        self.retry_delays = retry_delays
        self._tasks = set()

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self):
        # Test helper: wait for spawned dispatches to finish.
        while self._tasks:
            await asyncio.gather(*list(self._tasks))
```

`app/hooks.py`:
```python
import json
import time

from . import db as dbq
from . import ntfy
from .templating import payload_text, render, resolve_first

MAX_BODY = 65536


def parse_events(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [{"body": text}]
    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"body": str(item)} for item in data]
    if isinstance(data, dict):
        return [data]
    return [{"body": str(data)}]


def build_notification(endpoint: dict, event: dict) -> dict:
    title = render(endpoint["title_template"], event) or endpoint["name"]
    message = render(endpoint["message_template"], event) or payload_text(event)
    priority = endpoint["default_priority"] or "default"
    tags = [t.strip() for t in (endpoint["tags"] or "").split(",") if t.strip()]
    level = resolve_first(event, endpoint["level_field"]) if endpoint["level_field"] else None
    if level is not None:
        rule = (endpoint["rules"] or {}).get(str(level).upper())
        if rule:
            priority = rule.get("priority", priority)
            tags += [t for t in rule.get("extra_tags", []) if t not in tags]
    return {"title": title, "message": message, "priority": priority, "tags": tags}


async def dispatch_event(state, endpoint, event, source_ip, request_body) -> dict:
    notification = build_notification(endpoint, event)
    server = endpoint["ntfy_server"] or await dbq.get_setting(state.db, "ntfy_server", "")
    started = time.monotonic()
    if not server:
        result = ntfy.SendResult(False, None, "no ntfy server configured", 0)
    else:
        result = await ntfy.send(
            state.client, server, endpoint["ntfy_topic"], endpoint["ntfy_token"],
            notification["title"], notification["message"], notification["priority"],
            notification["tags"], retry_delays=state.retry_delays,
        )
    status = "delivered" if result.ok else "failed"
    delivery_id = await dbq.record_delivery(
        state.db, endpoint_id=endpoint["id"], status=status, source_ip=source_ip,
        request_body=request_body, title=notification["title"],
        message=notification["message"], ntfy_status=result.status_code,
        error=result.error, attempts=result.attempts,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return {"status": status, "ntfy_status": result.status_code, "error": result.error,
            "attempts": result.attempts, "delivery_id": delivery_id}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dispatch.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add app/state.py app/hooks.py tests/test_dispatch.py tests/conftest.py
git commit -m "feat: shared app state and webhook dispatch pipeline"
```

---

### Task 8: Webhook receiver app

**Files:**
- Modify: `app/hooks.py` (append `create_hooks_app`), `tests/conftest.py` (add `hooks_client` fixture)
- Test: `tests/test_hooks_app.py`

**Interfaces:**
- Consumes: Task 7 pipeline, Task 3/4 db helpers
- Produces: `app.hooks.create_hooks_app(state) -> FastAPI` exposing exactly: `GET /health` → `{"status": "ok"}`; `POST /hooks/{slug}`; `POST /omada-webhook` (alias for slug `omada`). Receiver behavior: unknown slug → 404 `{"error": "not found"}` with no delivery row; disabled endpoint → 404 AND a `rejected` delivery row with error `"endpoint disabled"`; enabled → `202 {"status": "accepted", "events": N}` and one spawned `dispatch_event` per parsed event. OpenAPI/docs routes are disabled.

- [ ] **Step 1: Add the hooks_client fixture**

Append to `tests/conftest.py`:
```python
from httpx import ASGITransport


@pytest.fixture
async def hooks_client(state):
    from app.hooks import create_hooks_app
    transport = ASGITransport(app=create_hooks_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://hooks") as c:
        yield c
```

- [ ] **Step 2: Write the failing test**

`tests/test_hooks_app.py`:
```python
import httpx
import respx

from app import db as dbq


async def test_health(hooks_client):
    response = await hooks_client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_unknown_slug_404_no_log(hooks_client, state):
    response = await hooks_client.post("/hooks/nope", json={"a": 1})
    assert response.status_code == 404
    assert await dbq.list_deliveries(state.db) == []


@respx.mock
async def test_accepts_and_dispatches(hooks_client, state, sample_endpoint_data):
    await dbq.create_endpoint(state.db, sample_endpoint_data)
    respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    response = await hooks_client.post("/hooks/test-hook",
                                       json=[{"level": "WARN", "msg": "a"}, {"msg": "b"}])
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "events": 2}
    await state.drain()
    rows = await dbq.list_deliveries(state.db)
    assert len(rows) == 2 and {r["status"] for r in rows} == {"delivered"}


@respx.mock
async def test_non_json_body_accepted(hooks_client, state, sample_endpoint_data):
    await dbq.create_endpoint(state.db, sample_endpoint_data)
    respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    response = await hooks_client.post("/hooks/test-hook", content=b"plain alert")
    assert response.status_code == 202
    await state.drain()
    row = (await dbq.list_deliveries(state.db))[0]
    assert row["message"] == "plain alert"


async def test_disabled_endpoint_rejected(hooks_client, state, sample_endpoint_data):
    await dbq.create_endpoint(state.db, {**sample_endpoint_data, "enabled": False})
    response = await hooks_client.post("/hooks/test-hook", json={"a": 1})
    assert response.status_code == 404
    row = (await dbq.list_deliveries(state.db))[0]
    assert row["status"] == "rejected" and row["error"] == "endpoint disabled"


@respx.mock
async def test_legacy_alias_routes_to_omada_slug(hooks_client, state, sample_endpoint_data):
    await dbq.create_endpoint(state.db, {**sample_endpoint_data, "slug": "omada"})
    respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    response = await hooks_client.post("/omada-webhook", json={"level": "WARN", "msg": "x"})
    assert response.status_code == 202
    await state.drain()
    assert len(await dbq.list_deliveries(state.db)) == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_hooks_app.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_hooks_app'`

- [ ] **Step 4: Write minimal implementation**

Append to `app/hooks.py` (add `from fastapi import FastAPI, Request` and `from fastapi.responses import JSONResponse` to the imports):
```python
def create_hooks_app(state) -> "FastAPI":
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    async def receive(slug: str, request: Request) -> JSONResponse:
        endpoint = await dbq.get_endpoint_by_slug(state.db, slug)
        if endpoint is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        raw = await request.body()
        body_text = raw.decode("utf-8", errors="replace")[:MAX_BODY]
        source_ip = request.client.host if request.client else ""
        if not endpoint["enabled"]:
            await dbq.record_delivery(state.db, endpoint_id=endpoint["id"],
                                      status="rejected", source_ip=source_ip,
                                      request_body=body_text, error="endpoint disabled")
            return JSONResponse({"error": "not found"}, status_code=404)
        events = parse_events(raw)
        for event in events:
            state.spawn(dispatch_event(state, endpoint, event, source_ip, body_text))
        return JSONResponse({"status": "accepted", "events": len(events)}, status_code=202)

    @app.post("/hooks/{slug}")
    async def hook(slug: str, request: Request):
        return await receive(slug, request)

    @app.post("/omada-webhook")
    async def legacy(request: Request):
        # Pre-gateway deployments point the Omada controller here.
        return await receive("omada", request)

    return app
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_hooks_app.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add app/hooks.py tests/test_hooks_app.py tests/conftest.py
git commit -m "feat: webhook receiver app with legacy omada alias"
```

---

### Task 9: Auth core, admin app factory, auth routes

**Files:**
- Create: `app/auth.py`, `app/api/__init__.py`, `app/api/auth_routes.py`
- Modify: `tests/conftest.py` (add `admin_client` fixture), `app/config.py` (add `display_timezone()`)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: Task 1 `config.env_admin_password`, Task 3 session/settings helpers
- Produces:
  - `app.auth`: `SESSION_COOKIE = "gateway_session"`, `SESSION_DAYS = 7`, `hash_password(str) -> str` (format `scrypt$<salt_hex>$<hash_hex>`), `verify_password(password, stored) -> bool`, `hash_token(str) -> str` (sha256 hex), `async auth_mode(db) -> "db" | "env" | "open"`, `async check_password(db, password) -> bool`, `async login(db, password) -> str | None` (session token), `async logout(db, token)`, `async is_authenticated(db, request) -> bool` (open mode → always True), `async change_password(db, current, new) -> bool`.
  - `app.api.create_admin_app(state) -> FastAPI`: builds `require_auth` dependency (401 with `{"detail": "authentication required"}`), includes routers, exposes `GET /api/health`, and mounts `app/static/` at `/` (html=True) when the directory exists. Later tasks add routers here.
  - `app.api.auth_routes.router(state, require_auth) -> APIRouter` with `POST /api/login` (`{"password": ...}`, sets cookie, 401 on wrong password), `POST /api/logout`, `GET /api/auth/status` → `{"mode", "authenticated", "display_timezone"}`, `POST /api/password` (`{"current", "new"}`, auth-protected, 422 if new < 8 chars, 403 if current wrong).
  - `app.config.display_timezone() -> str | None`: the `TZ` env var (IANA name, set via .env/docker-compose) or None. Surfaced on `/api/auth/status` so the SPA can render all timestamps in that zone; None means browser-local display.

- [ ] **Step 1: Add the admin_client fixture**

Append to `tests/conftest.py`:
```python
@pytest.fixture
async def admin_client(state):
    from app.api import create_admin_app
    transport = ASGITransport(app=create_admin_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://admin") as c:
        yield c
```

- [ ] **Step 2: Write the failing test**

`tests/test_auth.py`:
```python
from app import auth
from app import db as dbq


def test_password_hash_roundtrip():
    stored = auth.hash_password("s3cret-pass")
    assert stored.startswith("scrypt$")
    assert auth.verify_password("s3cret-pass", stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password("s3cret-pass", "garbage")


async def test_auth_mode_precedence(db, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    assert await auth.auth_mode(db) == "open"
    monkeypatch.setenv("ADMIN_PASSWORD", "envpass")
    assert await auth.auth_mode(db) == "env"
    assert await auth.check_password(db, "envpass")
    await dbq.set_setting(db, "admin_password_hash", auth.hash_password("dbpass"))
    assert await auth.auth_mode(db) == "db"
    assert await auth.check_password(db, "dbpass")
    assert not await auth.check_password(db, "envpass")  # db hash wins over env


async def test_open_mode_allows_api(admin_client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    status = (await admin_client.get("/api/auth/status")).json()
    assert status == {"mode": "open", "authenticated": True, "display_timezone": None}


async def test_display_timezone_from_env(admin_client, monkeypatch):
    monkeypatch.setenv("TZ", "Australia/Sydney")
    status = (await admin_client.get("/api/auth/status")).json()
    assert status["display_timezone"] == "Australia/Sydney"


async def test_login_flow(admin_client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "envpass")
    monkeypatch.delenv("TZ", raising=False)
    assert (await admin_client.post("/api/login", json={"password": "nope"})).status_code == 401
    response = await admin_client.post("/api/login", json={"password": "envpass"})
    assert response.status_code == 200
    assert auth.SESSION_COOKIE in response.cookies
    status = (await admin_client.get("/api/auth/status")).json()
    assert status == {"mode": "env", "authenticated": True, "display_timezone": None}
    await admin_client.post("/api/logout")
    status = (await admin_client.get("/api/auth/status")).json()
    assert status["authenticated"] is False


async def test_change_password_switches_to_db_mode(admin_client, state, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "envpass")
    await admin_client.post("/api/login", json={"password": "envpass"})
    response = await admin_client.post("/api/password",
                                       json={"current": "envpass", "new": "brand-new-pass"})
    assert response.status_code == 200
    assert await auth.auth_mode(state.db) == "db"
    short = await admin_client.post("/api/password", json={"current": "brand-new-pass", "new": "x"})
    assert short.status_code == 422
    wrong = await admin_client.post("/api/password", json={"current": "bad", "new": "whatever-else"})
    assert wrong.status_code == 403


async def test_protected_route_requires_session(admin_client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "envpass")
    response = await admin_client.post("/api/password", json={"current": "", "new": "long-enough"})
    assert response.status_code == 401
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 4: Write minimal implementation**

Append to `app/config.py`:
```python
def display_timezone() -> str | None:
    # IANA zone from the TZ env var (.env/docker-compose). None -> the SPA
    # falls back to each viewer's browser-local zone.
    return os.environ.get("TZ") or None
```

`app/auth.py`:
```python
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request

from . import db as dbq
from .config import env_admin_password

SESSION_COOKIE = "gateway_session"
SESSION_DAYS = 7
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def auth_mode(db) -> str:
    if await dbq.get_setting(db, "admin_password_hash"):
        return "db"
    if env_admin_password():
        return "env"
    return "open"


async def check_password(db, password: str) -> bool:
    mode = await auth_mode(db)
    if mode == "db":
        return verify_password(password, await dbq.get_setting(db, "admin_password_hash"))
    if mode == "env":
        return secrets.compare_digest(password, env_admin_password())
    return False


async def login(db, password: str) -> str | None:
    if not await check_password(db, password):
        return None
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    await dbq.create_session(db, hash_token(token), expires)
    return token


async def logout(db, token: str):
    await dbq.delete_session(db, hash_token(token))


async def is_authenticated(db, request: Request) -> bool:
    if await auth_mode(db) == "open":
        return True
    token = request.cookies.get(SESSION_COOKIE)
    return bool(token) and await dbq.session_valid(db, hash_token(token))


async def change_password(db, current: str, new: str) -> bool:
    if await auth_mode(db) != "open" and not await check_password(db, current):
        return False
    await dbq.set_setting(db, "admin_password_hash", hash_password(new))
    return True
```

`app/api/__init__.py`:
```python
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from .. import auth


def create_admin_app(state) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    async def require_auth(request: Request):
        if not await auth.is_authenticated(state.db, request):
            raise HTTPException(status_code=401, detail="authentication required")

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    from . import auth_routes
    app.include_router(auth_routes.router(state, require_auth))

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
```

`app/api/auth_routes.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from ..config import display_timezone


class LoginIn(BaseModel):
    password: str


class PasswordChangeIn(BaseModel):
    current: str = ""
    new: str = Field(min_length=8)


def router(state, require_auth) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.post("/login")
    async def login(body: LoginIn, response: Response):
        token = await auth.login(state.db, body.password)
        if token is None:
            raise HTTPException(status_code=401, detail="invalid password")
        response.set_cookie(auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
                            max_age=auth.SESSION_DAYS * 86400, path="/")
        return {"ok": True}

    @r.post("/logout")
    async def logout(request: Request, response: Response):
        token = request.cookies.get(auth.SESSION_COOKIE)
        if token:
            await auth.logout(state.db, token)
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"ok": True}

    @r.get("/auth/status")
    async def status(request: Request):
        return {"mode": await auth.auth_mode(state.db),
                "authenticated": await auth.is_authenticated(state.db, request),
                "display_timezone": display_timezone()}

    @r.post("/password", dependencies=[Depends(require_auth)])
    async def change_password(body: PasswordChangeIn):
        if not await auth.change_password(state.db, body.current, body.new):
            raise HTTPException(status_code=403, detail="current password is wrong")
        return {"ok": True}

    return r
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_auth.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add app/auth.py app/api/ tests/test_auth.py tests/conftest.py
git commit -m "feat: session auth with db-over-env precedence and admin app factory"
```

---

### Task 10: Endpoints, presets, and settings admin API

**Files:**
- Create: `app/api/endpoints_routes.py`, `app/api/settings_routes.py`
- Modify: `app/api/__init__.py` (include the new routers)
- Test: `tests/test_api_endpoints.py`

**Interfaces:**
- Consumes: Tasks 3–7 (`db` CRUD, `presets.PRESETS`, `hooks.dispatch_event`), Task 9 factory
- Produces (all auth-protected under `/api`):
  - `GET /api/presets` → `PRESETS` as-is.
  - `GET /api/endpoints`, `POST /api/endpoints` (201; 409 duplicate slug), `GET/PUT/DELETE /api/endpoints/{id}` (404 unknown; PUT 409 if slug collides with another endpoint; DELETE → 204).
  - Endpoint JSON out = `public_endpoint(row)`: all fields except `ntfy_token`, plus `ntfy_token_set: bool` and `ntfy_token_hint: "…last4"`.
  - Token write semantics: `ntfy_token: null`/absent on PUT → keep stored token; `""` → clear; non-empty → replace. POST treats `null` as `""`.
  - Rules keys are uppercased server-side. Slug pattern `^[a-z0-9][a-z0-9_-]{0,63}$` (pydantic 422 otherwise).
  - `POST /api/endpoints/{id}/test` → runs `dispatch_event` inline with event `{"body": "Test notification from ntfy webhook gateway"}` and returns its outcome dict (so the test also appears in Logs).
  - `GET /api/settings` → `{"ntfy_server", "retention_days", "auth_mode", "webhook_port"}`; `PUT /api/settings` accepts `{"ntfy_server"?, "retention_days"?}` (retention 1–365), returns the GET shape.

- [ ] **Step 1: Write the failing test**

`tests/test_api_endpoints.py`:
```python
import httpx
import respx

from app import db as dbq

EP = {
    "name": "Omada", "slug": "omada", "ntfy_topic": "net",
    "ntfy_token": "tk_secret9999", "ntfy_server": "https://n.example",
    "title_template": "{t}", "message_template": "{m}",
    "level_field": "lvl", "rules": {"warn": {"priority": "high", "extra_tags": ["w"]}},
    "default_priority": "default", "tags": "net", "enabled": True,
}


async def test_crud_and_masking(admin_client):
    created = (await admin_client.post("/api/endpoints", json=EP)).json()
    assert created["ntfy_token_set"] is True
    assert created["ntfy_token_hint"] == "…9999"
    assert "ntfy_token" not in created
    assert "WARN" in created["rules"]  # keys uppercased

    assert (await admin_client.post("/api/endpoints", json=EP)).status_code == 409
    bad = {**EP, "slug": "Bad Slug!"}
    assert (await admin_client.post("/api/endpoints", json=bad)).status_code == 422

    listed = (await admin_client.get("/api/endpoints")).json()
    assert len(listed) == 1

    # PUT without token keeps it; empty string clears it
    update = {**EP, "name": "Renamed", "ntfy_token": None}
    row = (await admin_client.put(f"/api/endpoints/{created['id']}", json=update)).json()
    assert row["name"] == "Renamed" and row["ntfy_token_set"] is True
    update["ntfy_token"] = ""
    row = (await admin_client.put(f"/api/endpoints/{created['id']}", json=update)).json()
    assert row["ntfy_token_set"] is False

    assert (await admin_client.delete(f"/api/endpoints/{created['id']}")).status_code == 204
    assert (await admin_client.get(f"/api/endpoints/{created['id']}")).status_code == 404


async def test_presets_listed(admin_client):
    keys = {p["key"] for p in (await admin_client.get("/api/presets")).json()}
    assert {"generic", "omada"} <= keys


@respx.mock
async def test_send_test_notification(admin_client, state):
    created = (await admin_client.post("/api/endpoints", json=EP)).json()
    respx.post("https://n.example/net").mock(return_value=httpx.Response(200))
    outcome = (await admin_client.post(f"/api/endpoints/{created['id']}/test")).json()
    assert outcome["status"] == "delivered"
    assert len(await dbq.list_deliveries(state.db)) == 1


async def test_settings_roundtrip(admin_client):
    settings = (await admin_client.get("/api/settings")).json()
    assert settings["retention_days"] == 30 and settings["webhook_port"] == 5000
    updated = (await admin_client.put(
        "/api/settings", json={"ntfy_server": "https://n.example/", "retention_days": 7},
    )).json()
    assert updated["ntfy_server"] == "https://n.example"  # trailing slash stripped
    assert updated["retention_days"] == 7
    assert (await admin_client.put("/api/settings", json={"retention_days": 0})).status_code == 422


async def test_endpoints_require_auth(admin_client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "envpass")
    assert (await admin_client.get("/api/endpoints")).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_endpoints.py -v`
Expected: FAIL with 404s (routes not registered)

- [ ] **Step 3: Write minimal implementation**

`app/api/endpoints_routes.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db as dbq
from ..hooks import dispatch_event
from ..presets import PRESETS


class Rule(BaseModel):
    priority: str = "default"
    extra_tags: list[str] = []


class EndpointIn(BaseModel):
    name: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    ntfy_topic: str = Field(min_length=1)
    ntfy_token: str | None = None
    ntfy_server: str | None = None
    title_template: str = ""
    message_template: str = "{payload}"
    level_field: str = ""
    rules: dict[str, Rule] = {}
    default_priority: str = "default"
    tags: str = ""
    enabled: bool = True


PUBLIC_FIELDS = ("id", "slug", "name", "enabled", "ntfy_topic", "ntfy_server",
                 "title_template", "message_template", "level_field", "rules",
                 "default_priority", "tags", "created_at", "updated_at")


def public_endpoint(row: dict) -> dict:
    out = {k: row[k] for k in PUBLIC_FIELDS}
    token = row["ntfy_token"] or ""
    out["ntfy_token_set"] = bool(token)
    out["ntfy_token_hint"] = ("…" + token[-4:]) if token else ""
    return out


def _payload(body: EndpointIn) -> dict:
    data = body.model_dump()
    data["rules"] = {k.upper(): v for k, v in data["rules"].items()}
    return data


def router(state, require_auth) -> APIRouter:
    r = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    @r.get("/presets")
    async def presets():
        return PRESETS

    @r.get("/endpoints")
    async def list_all():
        return [public_endpoint(e) for e in await dbq.list_endpoints(state.db)]

    @r.post("/endpoints", status_code=201)
    async def create(body: EndpointIn):
        if await dbq.get_endpoint_by_slug(state.db, body.slug):
            raise HTTPException(status_code=409, detail="slug already exists")
        data = _payload(body)
        data["ntfy_token"] = data["ntfy_token"] or ""
        return public_endpoint(await dbq.create_endpoint(state.db, data))

    @r.get("/endpoints/{endpoint_id}")
    async def get_one(endpoint_id: int):
        row = await dbq.get_endpoint(state.db, endpoint_id)
        if row is None:
            raise HTTPException(status_code=404, detail="endpoint not found")
        return public_endpoint(row)

    @r.put("/endpoints/{endpoint_id}")
    async def update(endpoint_id: int, body: EndpointIn):
        if await dbq.get_endpoint(state.db, endpoint_id) is None:
            raise HTTPException(status_code=404, detail="endpoint not found")
        other = await dbq.get_endpoint_by_slug(state.db, body.slug)
        if other and other["id"] != endpoint_id:
            raise HTTPException(status_code=409, detail="slug already exists")
        data = _payload(body)
        if body.ntfy_token is None:
            data.pop("ntfy_token")  # absent/null token = keep the stored secret
        return public_endpoint(await dbq.update_endpoint(state.db, endpoint_id, data))

    @r.delete("/endpoints/{endpoint_id}", status_code=204)
    async def delete(endpoint_id: int):
        if not await dbq.delete_endpoint(state.db, endpoint_id):
            raise HTTPException(status_code=404, detail="endpoint not found")

    @r.post("/endpoints/{endpoint_id}/test")
    async def send_test(endpoint_id: int, request: Request):
        row = await dbq.get_endpoint(state.db, endpoint_id)
        if row is None:
            raise HTTPException(status_code=404, detail="endpoint not found")
        event = {"body": "Test notification from ntfy webhook gateway"}
        source_ip = request.client.host if request.client else ""
        return await dispatch_event(state, row, event, source_ip, "(test)")

    return r
```

`app/api/settings_routes.py`:
```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import auth
from .. import db as dbq
from ..config import webhook_port


class SettingsIn(BaseModel):
    ntfy_server: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=365)


def router(state, require_auth) -> APIRouter:
    r = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    async def current() -> dict:
        return {
            "ntfy_server": await dbq.get_setting(state.db, "ntfy_server", ""),
            "retention_days": int(await dbq.get_setting(state.db, "retention_days", "30")),
            "auth_mode": await auth.auth_mode(state.db),
            "webhook_port": webhook_port(),
        }

    @r.get("/settings")
    async def get_settings():
        return await current()

    @r.put("/settings")
    async def put_settings(body: SettingsIn):
        if body.ntfy_server is not None:
            await dbq.set_setting(state.db, "ntfy_server", body.ntfy_server.rstrip("/"))
        if body.retention_days is not None:
            await dbq.set_setting(state.db, "retention_days", str(body.retention_days))
        return await current()

    return r
```

In `app/api/__init__.py`, replace the `from . import auth_routes` block with:
```python
    from . import auth_routes, endpoints_routes, settings_routes
    app.include_router(auth_routes.router(state, require_auth))
    app.include_router(endpoints_routes.router(state, require_auth))
    app.include_router(settings_routes.router(state, require_auth))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_endpoints.py -v`
Expected: PASS (6 tests). Also run `pytest -q` — the whole suite must stay green.

- [ ] **Step 5: Commit**

```bash
git add app/api/ tests/test_api_endpoints.py
git commit -m "feat: endpoints, presets, and settings admin API with token masking"
```

---

### Task 11: Logs, reports, and dashboard admin API

**Files:**
- Create: `app/api/logs_routes.py`
- Modify: `app/api/__init__.py` (include router)
- Test: `tests/test_api_logs_reports.py`

**Interfaces:**
- Consumes: Task 4 delivery queries, Task 9/10 factory pattern
- Produces (auth-protected under `/api`):
  - `GET /api/logs?endpoint_id&status&q&before_id&limit` (limit ≤ 200, default 50) → `{"items": [{id, received_at, endpoint_id, endpoint_name, status, title, ntfy_status, attempts, duration_ms}]}`
  - `GET /api/logs/{id}` → full delivery row (incl. `request_body`, `message`, `source_ip`, `error`); 404 unknown.
  - `GET /api/reports/summary?range=24h|7d|30d&tz_offset=<minutes>` (range default 7d, 422 otherwise; `tz_offset` default 0, JS `Date.getTimezoneOffset()` convention, passed through to `delivery_buckets` so bucket labels come out in the display zone) → `{"range", "since", "endpoints": [{id, name, delivered, failed, rejected, total, success_rate}], "buckets": [{bucket, delivered, failed, rejected}]}`. Bucket format: hourly `%Y-%m-%dT%H:00` for 24h, daily `%Y-%m-%d` otherwise. `success_rate` = round(100*delivered/total, 1) or null when total 0.
  - `GET /api/dashboard` → `{"endpoint_count", "enabled_count", "deliveries_24h", "failures_24h", "endpoints": [{id, name, enabled, delivered, failed, rejected, last_at}], "recent": [same shape as logs items, 10 rows]}` (per-endpoint counts are 24h; `failures_24h` counts failed + rejected).

- [ ] **Step 1: Write the failing test**

`tests/test_api_logs_reports.py`:
```python
from app import db as dbq


async def _seed(state, sample_endpoint_data):
    ep = await dbq.create_endpoint(state.db, sample_endpoint_data)
    for status in ("delivered", "delivered", "failed", "rejected"):
        await dbq.record_delivery(state.db, endpoint_id=ep["id"], status=status,
                                  title=f"t-{status}", request_body='{"k":"v"}',
                                  message="m", error=None if status == "delivered" else "e")
    return ep


async def test_logs_list_and_detail(admin_client, state, sample_endpoint_data):
    ep = await _seed(state, sample_endpoint_data)
    items = (await admin_client.get("/api/logs")).json()["items"]
    assert len(items) == 4
    assert items[0]["endpoint_name"] == ep["name"]
    assert "request_body" not in items[0]

    failed = (await admin_client.get("/api/logs", params={"status": "failed"})).json()["items"]
    assert len(failed) == 1
    detail = (await admin_client.get(f"/api/logs/{failed[0]['id']}")).json()
    assert detail["request_body"] == '{"k":"v"}' and detail["error"] == "e"
    assert (await admin_client.get("/api/logs/999999")).status_code == 404


async def test_reports_summary(admin_client, state, sample_endpoint_data):
    ep = await _seed(state, sample_endpoint_data)
    summary = (await admin_client.get("/api/reports/summary", params={"range": "24h"})).json()
    row = next(e for e in summary["endpoints"] if e["id"] == ep["id"])
    assert (row["delivered"], row["failed"], row["rejected"], row["total"]) == (2, 1, 1, 4)
    assert row["success_rate"] == 50.0
    assert sum(b["delivered"] + b["failed"] + b["rejected"] for b in summary["buckets"]) == 4
    assert (await admin_client.get("/api/reports/summary", params={"range": "bad"})).status_code == 422


async def test_dashboard(admin_client, state, sample_endpoint_data):
    ep = await _seed(state, sample_endpoint_data)
    data = (await admin_client.get("/api/dashboard")).json()
    assert data["endpoint_count"] == 1 and data["enabled_count"] == 1
    assert data["deliveries_24h"] == 4 and data["failures_24h"] == 2
    assert data["endpoints"][0]["last_at"]
    assert len(data["recent"]) == 4 and data["recent"][0]["endpoint_name"] == ep["name"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_logs_reports.py -v`
Expected: FAIL with 404s

- [ ] **Step 3: Write minimal implementation**

`app/api/logs_routes.py`:
```python
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db as dbq

LIST_FIELDS = ("id", "received_at", "endpoint_id", "status", "title",
               "ntfy_status", "attempts", "duration_ms")
RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


def _since(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat(timespec="seconds")


def _list_item(row: dict, names: dict) -> dict:
    return {**{k: row[k] for k in LIST_FIELDS},
            "endpoint_name": names.get(row["endpoint_id"], "?")}


def router(state, require_auth) -> APIRouter:
    r = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    async def endpoint_names() -> dict:
        return {e["id"]: e["name"] for e in await dbq.list_endpoints(state.db)}

    @r.get("/logs")
    async def logs(endpoint_id: int | None = None, status: str | None = None,
                   q: str | None = None, before_id: int | None = None,
                   limit: int = Query(default=50, le=200)):
        rows = await dbq.list_deliveries(state.db, endpoint_id=endpoint_id,
                                         status=status, q=q, before_id=before_id,
                                         limit=limit)
        names = await endpoint_names()
        return {"items": [_list_item(row, names) for row in rows]}

    @r.get("/logs/{delivery_id}")
    async def log_detail(delivery_id: int):
        row = await dbq.get_delivery(state.db, delivery_id)
        if row is None:
            raise HTTPException(status_code=404, detail="delivery not found")
        return row

    @r.get("/reports/summary")
    async def summary(range: str = "7d", tz_offset: int = 0):
        delta = RANGES.get(range)
        if delta is None:
            raise HTTPException(status_code=422, detail="range must be one of 24h, 7d, 30d")
        since = _since(delta)
        per_endpoint = {}
        for row in await dbq.delivery_stats(state.db, since):
            agg = per_endpoint.setdefault(row["endpoint_id"],
                                          {"delivered": 0, "failed": 0, "rejected": 0})
            agg[row["status"]] = row["n"]
        endpoints = []
        for e in await dbq.list_endpoints(state.db):
            agg = per_endpoint.get(e["id"], {"delivered": 0, "failed": 0, "rejected": 0})
            total = sum(agg.values())
            endpoints.append({"id": e["id"], "name": e["name"], **agg, "total": total,
                              "success_rate": round(100 * agg["delivered"] / total, 1) if total else None})
        fmt = "%Y-%m-%dT%H:00" if range == "24h" else "%Y-%m-%d"
        buckets = {}
        for row in await dbq.delivery_buckets(state.db, since, fmt,
                                              offset_minutes=tz_offset):
            b = buckets.setdefault(row["bucket"], {"bucket": row["bucket"],
                                                   "delivered": 0, "failed": 0, "rejected": 0})
            b[row["status"]] = row["n"]
        return {"range": range, "since": since, "endpoints": endpoints,
                "buckets": list(buckets.values())}

    @r.get("/dashboard")
    async def dashboard():
        since = _since(timedelta(hours=24))
        per_endpoint = {}
        for row in await dbq.delivery_stats(state.db, since):
            agg = per_endpoint.setdefault(row["endpoint_id"],
                                          {"delivered": 0, "failed": 0, "rejected": 0})
            agg[row["status"]] = row["n"]
        last_times = await dbq.last_delivery_times(state.db)
        all_endpoints = await dbq.list_endpoints(state.db)
        endpoints = [{"id": e["id"], "name": e["name"], "enabled": e["enabled"],
                      **per_endpoint.get(e["id"], {"delivered": 0, "failed": 0, "rejected": 0}),
                      "last_at": last_times.get(e["id"])} for e in all_endpoints]
        names = {e["id"]: e["name"] for e in all_endpoints}
        recent = [_list_item(row, names)
                  for row in await dbq.list_deliveries(state.db, limit=10)]
        return {
            "endpoint_count": len(all_endpoints),
            "enabled_count": sum(1 for e in all_endpoints if e["enabled"]),
            "deliveries_24h": sum(sum(a.values()) for a in per_endpoint.values()),
            "failures_24h": sum(a["failed"] + a["rejected"] for a in per_endpoint.values()),
            "endpoints": endpoints,
            "recent": recent,
        }

    return r
```

In `app/api/__init__.py`, extend the router imports:
```python
    from . import auth_routes, endpoints_routes, logs_routes, settings_routes
    app.include_router(auth_routes.router(state, require_auth))
    app.include_router(endpoints_routes.router(state, require_auth))
    app.include_router(settings_routes.router(state, require_auth))
    app.include_router(logs_routes.router(state, require_auth))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_logs_reports.py -v` then `pytest -q`
Expected: PASS; full suite green

- [ ] **Step 5: Commit**

```bash
git add app/api/ tests/test_api_logs_reports.py
git commit -m "feat: logs, reports, and dashboard admin API"
```

---

### Task 12: Entrypoint — seeding, retention, dual servers

**Files:**
- Create: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `async seed_legacy(db)` — idempotent: if `config.legacy_env()` is set, the `legacy_seeded` setting is absent, and no `omada` slug exists, create an endpoint from the omada preset (slug `omada`, name `Omada Controller`, topic/token from env, `ntfy_server: None`) and, if the global `ntfy_server` setting is empty, set it from `NTFY_HOST_URL`; finally set `legacy_seeded=1` whenever legacy env was present.
  - `async run_retention_once(state)` — purge deliveries older than `retention_days` (default 30) and expired sessions.
  - `async retention_loop(state)` — `run_retention_once` then sleep 24h, forever.
  - `async run()` — mkdir DATA_DIR, connect db, seed, open httpx client (timeout 15s), build `AppState`, start `retention_loop` as a plain `asyncio.create_task` (NOT `state.spawn` — `drain()` would hang), serve `create_hooks_app(state)` on `0.0.0.0:WEBHOOK_PORT` and `create_admin_app(state)` on `0.0.0.0:ADMIN_PORT` via `asyncio.gather` of two `uvicorn.Server.serve()` calls.
  - `python -m app.main` runs `asyncio.run(run())`.

- [ ] **Step 1: Write the failing test**

`tests/test_main.py`:
```python
from datetime import datetime, timedelta, timezone

from app import db as dbq
from app.main import run_retention_once, seed_legacy


async def test_seed_legacy_creates_endpoint_once(db, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "omada-topic")
    monkeypatch.setenv("NTFY_AUTH_TOKEN", "tk_legacy")
    monkeypatch.setenv("NTFY_HOST_URL", "https://ntfy.example.com")
    await seed_legacy(db)
    ep = await dbq.get_endpoint_by_slug(db, "omada")
    assert ep is not None
    assert ep["ntfy_topic"] == "omada-topic" and ep["ntfy_token"] == "tk_legacy"
    assert ep["level_field"] == "event.level|level"
    assert await dbq.get_setting(db, "ntfy_server") == "https://ntfy.example.com"
    # Idempotent: even after the endpoint is deleted, seeding never re-runs.
    await dbq.delete_endpoint(db, ep["id"])
    await seed_legacy(db)
    assert await dbq.get_endpoint_by_slug(db, "omada") is None


async def test_seed_legacy_noop_without_env(db, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("NTFY_AUTH_TOKEN", raising=False)
    await seed_legacy(db)
    assert await dbq.list_endpoints(db) == []


async def test_retention_purges_old_rows(state, sample_endpoint_data):
    ep = await dbq.create_endpoint(state.db, sample_endpoint_data)
    old_id = await dbq.record_delivery(state.db, endpoint_id=ep["id"], status="delivered")
    stale = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
    await state.db.execute("UPDATE deliveries SET received_at=? WHERE id=?", (stale, old_id))
    await state.db.commit()
    fresh_id = await dbq.record_delivery(state.db, endpoint_id=ep["id"], status="delivered")
    await run_retention_once(state)
    assert await dbq.get_delivery(state.db, old_id) is None
    assert await dbq.get_delivery(state.db, fresh_id) is not None


async def test_apps_are_isolated(state):
    from httpx import ASGITransport, AsyncClient
    from app.api import create_admin_app
    from app.hooks import create_hooks_app
    hooks_transport = ASGITransport(app=create_hooks_app(state))
    admin_transport = ASGITransport(app=create_admin_app(state))
    async with AsyncClient(transport=hooks_transport, base_url="http://x") as hooks_client:
        assert (await hooks_client.get("/api/endpoints")).status_code == 404
        assert (await hooks_client.get("/api/health")).status_code == 404
    async with AsyncClient(transport=admin_transport, base_url="http://x") as admin_client:
        # 404 before the SPA exists; 405 once StaticFiles is mounted at "/"
        # (static files reject POST). Either way: no webhook handling here.
        assert (await admin_client.post("/hooks/anything", json={})).status_code in (404, 405)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write minimal implementation**

`app/main.py`:
```python
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import uvicorn

from . import config
from . import db as dbq
from .api import create_admin_app
from .hooks import create_hooks_app
from .presets import get_preset
from .state import AppState


async def seed_legacy(db):
    legacy = config.legacy_env()
    if not legacy or await dbq.get_setting(db, "legacy_seeded"):
        return
    if not await dbq.get_endpoint_by_slug(db, "omada"):
        preset = get_preset("omada")
        await dbq.create_endpoint(db, {
            "slug": "omada", "name": "Omada Controller", "enabled": True,
            "ntfy_topic": legacy["topic"], "ntfy_token": legacy["token"],
            "ntfy_server": None,
            "title_template": preset["title_template"],
            "message_template": preset["message_template"],
            "level_field": preset["level_field"], "rules": preset["rules"],
            "default_priority": preset["default_priority"], "tags": preset["tags"],
        })
        if legacy["server"] and not await dbq.get_setting(db, "ntfy_server"):
            await dbq.set_setting(db, "ntfy_server", legacy["server"].rstrip("/"))
    await dbq.set_setting(db, "legacy_seeded", "1")


async def run_retention_once(state):
    days = int(await dbq.get_setting(state.db, "retention_days", "30"))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    await dbq.purge_deliveries(state.db, cutoff)
    await dbq.purge_sessions(state.db)


async def retention_loop(state):
    while True:
        await run_retention_once(state)
        await asyncio.sleep(24 * 3600)


async def run():
    config.data_dir().mkdir(parents=True, exist_ok=True)
    db = await dbq.connect(config.db_path())
    await seed_legacy(db)
    async with httpx.AsyncClient(timeout=15) as client:
        state = AppState(db, client)
        retention = asyncio.create_task(retention_loop(state))
        hooks_server = uvicorn.Server(uvicorn.Config(
            create_hooks_app(state), host="0.0.0.0", port=config.webhook_port(),
            log_level="info"))
        admin_server = uvicorn.Server(uvicorn.Config(
            create_admin_app(state), host="0.0.0.0", port=config.admin_port(),
            log_level="info"))
        try:
            await asyncio.gather(hooks_server.serve(), admin_server.serve())
        finally:
            retention.cancel()
    await db.close()


if __name__ == "__main__":
    asyncio.run(run())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -v` then `pytest -q`
Expected: PASS; full suite green

- [ ] **Step 5: Smoke-run both listeners**

```bash
DATA_DIR=/tmp/gw-smoke WEBHOOK_PORT=15000 ADMIN_PORT=15001 python -m app.main &
sleep 2
curl -s http://127.0.0.1:15000/health   # {"status":"ok"}
curl -s http://127.0.0.1:15001/api/health   # {"status":"ok"}
curl -s http://127.0.0.1:15000/api/health -o /dev/null -w '%{http_code}\n'   # 404
kill %1
```

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: dual-listener entrypoint with legacy seeding and retention"
```

---

### Task 13: SPA shell, login, and Dashboard tab

**Files:**
- Create: `app/static/index.html`, `app/static/styles.css`, `app/static/app.js`

**Interfaces:**
- Consumes: `/api/auth/status`, `/api/login`, `/api/logout`, `/api/dashboard` (Task 11 shapes)
- Produces: global JS used by Tasks 14–15: `$(sel, root?)`, `esc(value)`, `fmtTime(iso)`, `toast(message, isError?)`, `async api(path, options?)` (JSON in/out, 401 → shows login overlay and throws), `views` registry (`views.<tab> = async (root) => {}`), `route()`, and `logsTimer` (a shared interval handle the router clears on every tab switch). Tabs are hash-routed: `#/dashboard`, `#/settings`, `#/reports`, `#/logs`.

There are no JS unit tests (no build step / no JS toolchain by design); verification is the running app plus the already-tested API contract.

- [ ] **Step 1: Create `app/static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ntfy Webhook Gateway</title>
<link rel="stylesheet" href="/styles.css">
</head>
<body>
<div id="login" class="login hidden">
  <form id="login-form" class="card">
    <h1>ntfy Webhook Gateway</h1>
    <input type="password" id="login-password" placeholder="Admin password"
           autocomplete="current-password" required>
    <button type="submit">Sign in</button>
    <p id="login-error" class="error hidden">Wrong password</p>
  </form>
</div>
<header>
  <h1>ntfy Webhook Gateway</h1>
  <nav>
    <a href="#/dashboard" data-tab="dashboard">Dashboard</a>
    <a href="#/settings" data-tab="settings">Settings</a>
    <a href="#/reports" data-tab="reports">Reports</a>
    <a href="#/logs" data-tab="logs">Logs</a>
  </nav>
  <button id="logout" class="ghost hidden">Log out</button>
</header>
<div id="banner" class="banner hidden">
  No admin password is set — anyone who can reach this port can read tokens and
  change settings. Set a password in Settings.
</div>
<main id="view"></main>
<div id="toast" class="toast hidden"></div>
<script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `app/static/styles.css`**

```css
:root {
  --bg: #f5f6f8; --card: #ffffff; --text: #1c2330; --muted: #68738a;
  --line: #dfe3ea; --accent: #2563eb; --ok: #16a34a; --bad: #dc2626;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12151c; --card: #1b202b; --text: #e6e9f0; --muted: #8b94a7;
    --line: #2a3140; --accent: #5b8def; --ok: #34c073; --bad: #ef5350;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
header {
  display: flex; align-items: center; gap: 1.5rem;
  padding: .7rem 1.2rem; background: var(--card); border-bottom: 1px solid var(--line);
}
header h1 { font-size: 1.05rem; margin: 0; }
nav { display: flex; gap: .25rem; flex: 1; }
nav a {
  padding: .35rem .8rem; border-radius: 6px; text-decoration: none; color: var(--muted);
}
nav a.active { background: var(--accent); color: #fff; }
main { max-width: 1100px; margin: 1.2rem auto; padding: 0 1rem; }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 1rem 1.2rem; margin-bottom: 1.2rem;
}
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; }
.cards .card { margin: 0; }
.stat b { display: block; font-size: 1.8rem; }
.stat.bad b { color: var(--bad); }
.stat span { color: var(--muted); }
h2 { font-size: 1rem; margin: .2rem 0 .8rem; }
table { width: 100%; border-collapse: collapse; font-size: .92rem; }
th, td { text-align: left; padding: .45rem .5rem; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 500; }
tr.clickable { cursor: pointer; }
tr.clickable:hover td { background: color-mix(in srgb, var(--accent) 8%, transparent); }
.badge { padding: .1rem .5rem; border-radius: 99px; font-size: .8rem; color: #fff; }
.badge.delivered { background: var(--ok); }
.badge.failed { background: var(--bad); }
.badge.rejected { background: var(--muted); }
button {
  background: var(--accent); color: #fff; border: 0; border-radius: 6px;
  padding: .45rem .9rem; cursor: pointer; font: inherit;
}
button.ghost {
  background: transparent; color: var(--accent); border: 1px solid var(--line);
}
button.ghost.active { background: var(--accent); color: #fff; }
button.ghost.danger { color: var(--bad); }
button:disabled { opacity: .5; cursor: default; }
input, select, textarea {
  width: 100%; padding: .45rem .6rem; border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg); color: var(--text); font: inherit;
}
textarea { min-height: 4.5rem; }
label { display: block; font-size: .88rem; color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: .8rem; align-items: end; }
.grid fieldset, .grid .row { grid-column: 1 / -1; }
fieldset { border: 1px solid var(--line); border-radius: 8px; }
.check { display: flex; align-items: center; gap: .4rem; }
.check input { width: auto; }
.row { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.row-between { display: flex; justify-content: space-between; align-items: center; }
.actions { white-space: nowrap; }
.muted { color: var(--muted); }
.error { color: var(--bad); }
.hidden { display: none !important; }
.banner {
  background: #b45309; color: #fff; text-align: center; padding: .45rem 1rem; font-size: .9rem;
}
.login {
  position: fixed; inset: 0; z-index: 10; background: var(--bg);
  display: flex; align-items: center; justify-content: center;
}
.login .card { width: 320px; display: grid; gap: .8rem; }
.toast {
  position: fixed; bottom: 1.2rem; left: 50%; transform: translateX(-50%);
  background: var(--text); color: var(--bg); padding: .55rem 1.1rem;
  border-radius: 8px; font-size: .9rem; z-index: 20;
}
.toast.error { background: var(--bad); color: #fff; }
.chart { display: flex; align-items: flex-end; gap: 3px; height: 140px; margin-bottom: 1rem; }
.chart .col {
  flex: 1; display: flex; flex-direction: column; justify-content: flex-end; height: 100%;
}
.chart .seg.delivered { background: var(--ok); border-radius: 2px 2px 0 0; }
.chart .seg.failed { background: var(--bad); }
pre {
  background: var(--bg); border: 1px solid var(--line); border-radius: 6px;
  padding: .6rem; overflow-x: auto; font-size: .85rem; white-space: pre-wrap;
}
code { word-break: break-all; }
```

- [ ] **Step 3: Create `app/static/app.js` (core + Dashboard)**

```js
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);

const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let displayTz = null;  // IANA zone from the server's TZ env; null = browser-local

const fmtTime = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, displayTz ? { timeZone: displayTz } : {});
  } catch (e) {
    return new Date(iso).toLocaleString();  // unknown zone name: browser-local fallback
  }
};

function tzOffsetMinutes() {
  // Same convention as Date.getTimezoneOffset(): minutes UTC is ahead of display time.
  if (!displayTz) return new Date().getTimezoneOffset();
  const now = new Date();
  try {
    const inZone = new Date(now.toLocaleString("en-US", { timeZone: displayTz }));
    return Math.round((now - inZone) / 60000);
  } catch (e) {
    return new Date().getTimezoneOffset();
  }
}

function toast(message, isError = false) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.toggle("error", isError);
  box.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => box.classList.add("hidden"), 3500);
}

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.body !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers = { "Content-Type": "application/json", ...opts.headers };
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  if (response.status === 401) {
    showLogin(true);
    throw new Error("authentication required");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (e) { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.status === 204 ? null : response.json();
}

function showLogin(show) {
  $("#login").classList.toggle("hidden", !show);
  if (show) $("#login-password").focus();
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/login", { body: { password: $("#login-password").value } });
    $("#login-password").value = "";
    $("#login-error").classList.add("hidden");
    showLogin(false);
    route();
  } catch (err) {
    $("#login-error").classList.remove("hidden");
  }
});

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { body: {} });
  showLogin(true);
});

async function refreshAuthUi() {
  const status = await fetch("/api/auth/status", { credentials: "same-origin" })
    .then((r) => r.json());
  displayTz = status.display_timezone || null;
  $("#banner").classList.toggle("hidden", status.mode !== "open");
  $("#logout").classList.toggle("hidden", status.mode === "open");
  if (status.mode !== "open" && !status.authenticated) {
    showLogin(true);
    return false;
  }
  return true;
}

const views = {};
let logsTimer = null;

async function route() {
  clearInterval(logsTimer);
  const tab = (location.hash || "#/dashboard").slice(2) || "dashboard";
  document.querySelectorAll("nav a").forEach(
    (a) => a.classList.toggle("active", a.dataset.tab === tab));
  const root = $("#view");
  root.innerHTML = "<p class='muted'>Loading…</p>";
  if (!(await refreshAuthUi())) return;
  try {
    await (views[tab] || views.dashboard)(root);
  } catch (err) {
    root.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

window.addEventListener("hashchange", route);

views.dashboard = async (root) => {
  const data = await api("/api/dashboard");
  root.innerHTML = `
    <section class="cards">
      <div class="card stat"><b>${data.endpoint_count}</b>
        <span>endpoints (${data.enabled_count} enabled)</span></div>
      <div class="card stat"><b>${data.deliveries_24h}</b><span>deliveries, 24 h</span></div>
      <div class="card stat ${data.failures_24h ? "bad" : ""}"><b>${data.failures_24h}</b>
        <span>failures, 24 h</span></div>
    </section>
    <section class="card">
      <h2>Endpoints</h2>
      <table><thead><tr><th>Name</th><th>Status</th><th>Delivered 24 h</th>
        <th>Failed</th><th>Rejected</th><th>Last event</th></tr></thead>
      <tbody>${data.endpoints.map((e) => `
        <tr><td>${esc(e.name)}</td>
        <td>${e.enabled ? "enabled" : "<span class='muted'>disabled</span>"}</td>
        <td>${e.delivered}</td><td class="${e.failed ? "error" : ""}">${e.failed}</td>
        <td>${e.rejected}</td><td>${fmtTime(e.last_at)}</td></tr>`).join("")
        || "<tr><td colspan='6' class='muted'>No endpoints yet — create one in Settings.</td></tr>"}
      </tbody></table>
    </section>
    <section class="card">
      <h2>Recent activity</h2>
      <table><thead><tr><th>Time</th><th>Endpoint</th><th>Status</th><th>Title</th></tr></thead>
      <tbody>${data.recent.map((d) => `
        <tr><td>${fmtTime(d.received_at)}</td><td>${esc(d.endpoint_name)}</td>
        <td><span class="badge ${esc(d.status)}">${esc(d.status)}</span></td>
        <td>${esc(d.title)}</td></tr>`).join("")
        || "<tr><td colspan='4' class='muted'>Nothing yet.</td></tr>"}
      </tbody></table>
    </section>`;
};

route();
```

- [ ] **Step 4: Verify by running the server**

```bash
DATA_DIR=/tmp/gw-ui WEBHOOK_PORT=15000 ADMIN_PORT=15001 python -m app.main &
sleep 2
curl -s http://127.0.0.1:15001/ | grep -q "ntfy Webhook Gateway" && echo SPA-OK
curl -s http://127.0.0.1:15001/app.js | head -1
kill %1
```
Expected: `SPA-OK` and the first line of app.js. If a browser (or the Playwright MCP tools) is available, open `http://127.0.0.1:15001/#/dashboard` and confirm the stat cards render and the open-mode warning banner shows.

- [ ] **Step 5: Run the full test suite (static mount must not break the admin app)**

Run: `pytest -q`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add app/static/
git commit -m "feat: SPA shell with login, tab routing, and dashboard"
```

---

### Task 14: SPA Settings tab

**Files:**
- Modify: `app/static/app.js` (append the settings view before the final `route();` line — move `route();` to stay last)

**Interfaces:**
- Consumes: `/api/endpoints` CRUD + `/test`, `/api/presets`, `/api/settings`, `/api/password` (Tasks 9–10 shapes); core helpers from Task 13.
- Produces: `views.settings`.

- [ ] **Step 1: Append the settings view to `app/static/app.js`** (insert before the trailing `route();`)

```js
views.settings = async (root) => {
  const [endpoints, presets, settings] = await Promise.all([
    api("/api/endpoints"), api("/api/presets"), api("/api/settings"),
  ]);
  const webhookBase = `${location.protocol}//${location.hostname}:${settings.webhook_port}/hooks/`;
  const PRIORITIES = ["min", "low", "default", "high", "urgent"];

  root.innerHTML = `<div id="settings-page">
    <section class="card">
      <div class="row-between"><h2>Webhook endpoints</h2>
        <button id="ep-new">New endpoint</button></div>
      <table><thead><tr><th>Name</th><th>Webhook URL</th><th>Topic</th>
        <th>Enabled</th><th></th></tr></thead>
      <tbody>${endpoints.map((e) => `
        <tr><td>${esc(e.name)}</td>
        <td><code>${esc(webhookBase + e.slug)}</code>
          <button class="ghost" data-copy="${esc(webhookBase + e.slug)}">Copy</button></td>
        <td>${esc(e.ntfy_topic)}</td>
        <td>${e.enabled ? "yes" : "<span class='muted'>no</span>"}</td>
        <td class="actions">
          <button data-test="${e.id}" class="ghost">Test</button>
          <button data-edit="${e.id}" class="ghost">Edit</button>
          <button data-del="${e.id}" class="ghost danger">Delete</button>
        </td></tr>`).join("")
        || "<tr><td colspan='5' class='muted'>No endpoints yet.</td></tr>"}
      </tbody></table>
    </section>
    <section class="card hidden" id="ep-editor"></section>
    <section class="card">
      <h2>Global settings</h2>
      <form id="global-form" class="grid">
        <label>ntfy server URL
          <input name="ntfy_server" value="${esc(settings.ntfy_server)}"
                 placeholder="https://ntfy.example.com"></label>
        <label>Log retention (days)
          <input name="retention_days" type="number" min="1" max="365"
                 value="${settings.retention_days}"></label>
        <button>Save</button>
      </form>
    </section>
    <section class="card">
      <h2>Admin password</h2>
      <p class="muted">${settings.auth_mode === "open"
        ? "No password set — set one below to protect this UI."
        : settings.auth_mode === "env"
          ? "Using the ADMIN_PASSWORD environment variable; setting a password here overrides it."
          : "Password is set. Forgot it? Run scripts/reset_password.py inside the container."}</p>
      <form id="pw-form" class="grid">
        <label>Current password
          <input name="current" type="password" autocomplete="current-password"></label>
        <label>New password (min 8 chars)
          <input name="new" type="password" minlength="8" required
                 autocomplete="new-password"></label>
        <button>Change password</button>
      </form>
    </section></div>`;

  const page = $("#settings-page");

  const ruleRow = (level = "", rule = {}) => `<tr>
    <td><input class="rule-level" value="${esc(level)}" placeholder="WARN"></td>
    <td><select class="rule-priority">${PRIORITIES.map((p) =>
      `<option ${p === (rule.priority || "default") ? "selected" : ""}>${p}</option>`).join("")}
    </select></td>
    <td><input class="rule-tags" value="${esc((rule.extra_tags || []).join(","))}"
        placeholder="warning,fire"></td>
    <td><button type="button" class="ghost rule-del">✕</button></td></tr>`;

  function openEditor(endpoint) {
    const box = $("#ep-editor", page);
    box.classList.remove("hidden");
    const e = endpoint || {
      name: "", slug: "", ntfy_topic: "", ntfy_server: "", title_template: "",
      message_template: "{payload}", level_field: "", rules: {},
      default_priority: "default", tags: "", enabled: true,
    };
    box.innerHTML = `
      <h2>${endpoint ? "Edit" : "New"} endpoint</h2>
      ${endpoint ? "" : `<label>Start from preset
        <select id="ep-preset"><option value="">—</option>
        ${presets.map((p) => `<option value="${p.key}">${esc(p.label)}</option>`).join("")}
        </select></label>`}
      <form id="ep-form" class="grid">
        <label>Name <input name="name" required value="${esc(e.name)}"></label>
        <label>Slug (URL path) <input name="slug" required
          pattern="[a-z0-9][a-z0-9_-]{0,63}" value="${esc(e.slug)}"></label>
        <label>ntfy topic <input name="ntfy_topic" required value="${esc(e.ntfy_topic)}"></label>
        <label>ntfy token${endpoint && e.ntfy_token_set
          ? ` <span class="muted">(saved ${esc(e.ntfy_token_hint)} — blank keeps it)</span>` : ""}
          <input name="ntfy_token" type="password" autocomplete="off"
                 placeholder="${endpoint && e.ntfy_token_set ? "unchanged" : "tk_…"}"></label>
        <label>ntfy server override
          <input name="ntfy_server" value="${esc(e.ntfy_server || "")}"
                 placeholder="uses global setting"></label>
        <label>Title template <input name="title_template" value="${esc(e.title_template)}"></label>
        <label>Message template
          <textarea name="message_template">${esc(e.message_template)}</textarea></label>
        <label>Level field <input name="level_field" value="${esc(e.level_field)}"
          placeholder="event.level|level"></label>
        <label>Base tags <input name="tags" value="${esc(e.tags)}" placeholder="webhook,alerts"></label>
        <label>Default priority <select name="default_priority">${PRIORITIES.map((p) =>
          `<option ${p === e.default_priority ? "selected" : ""}>${p}</option>`).join("")}
        </select></label>
        <label class="check"><input type="checkbox" name="enabled"
          ${e.enabled ? "checked" : ""}> Enabled</label>
        <fieldset><legend>Level rules</legend>
          <table><thead><tr><th>Level</th><th>Priority</th><th>Extra tags</th><th></th></tr></thead>
          <tbody id="rule-rows">${Object.entries(e.rules)
            .map(([lvl, rule]) => ruleRow(lvl, rule)).join("")}</tbody></table>
          <button type="button" id="rule-add" class="ghost">Add rule</button>
        </fieldset>
        <div class="row"><button>Save</button>
          <button type="button" id="ep-cancel" class="ghost">Cancel</button></div>
      </form>`;

    const presetSelect = $("#ep-preset", box);
    if (presetSelect) presetSelect.addEventListener("change", () => {
      const preset = presets.find((p) => p.key === presetSelect.value);
      if (!preset) return;
      const form = $("#ep-form", box);
      for (const field of ["title_template", "message_template", "level_field",
                           "default_priority", "tags"]) {
        form.elements[field].value = preset[field];
      }
      $("#rule-rows", box).innerHTML = Object.entries(preset.rules)
        .map(([lvl, rule]) => ruleRow(lvl, rule)).join("");
    });
    $("#rule-add", box).addEventListener("click", () =>
      $("#rule-rows", box).insertAdjacentHTML("beforeend", ruleRow()));
    $("#ep-cancel", box).addEventListener("click", () => box.classList.add("hidden"));

    $("#ep-form", box).addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target;
      const rules = {};
      for (const row of $("#rule-rows", box).querySelectorAll("tr")) {
        const level = $(".rule-level", row).value.trim();
        if (!level) continue;
        rules[level] = {
          priority: $(".rule-priority", row).value,
          extra_tags: $(".rule-tags", row).value.split(",")
            .map((t) => t.trim()).filter(Boolean),
        };
      }
      const tokenInput = form.elements.ntfy_token.value;
      const body = {
        name: form.elements.name.value, slug: form.elements.slug.value,
        ntfy_topic: form.elements.ntfy_topic.value,
        ntfy_token: endpoint ? (tokenInput === "" ? null : tokenInput) : tokenInput,
        ntfy_server: form.elements.ntfy_server.value || null,
        title_template: form.elements.title_template.value,
        message_template: form.elements.message_template.value,
        level_field: form.elements.level_field.value,
        rules,
        default_priority: form.elements.default_priority.value,
        tags: form.elements.tags.value,
        enabled: form.elements.enabled.checked,
      };
      try {
        if (endpoint) await api(`/api/endpoints/${endpoint.id}`, { method: "PUT", body });
        else await api("/api/endpoints", { body });
        toast("Endpoint saved");
        route();
      } catch (err) { toast(err.message, true); }
    });
  }

  page.addEventListener("click", async (event) => {
    const btn = event.target.closest("button");
    if (!btn) return;
    if (btn.dataset.copy) {
      await navigator.clipboard.writeText(btn.dataset.copy);
      toast("Webhook URL copied");
    } else if (btn.dataset.test) {
      btn.disabled = true;
      try {
        const outcome = await api(`/api/endpoints/${btn.dataset.test}/test`, { body: {} });
        toast(outcome.status === "delivered" ? "Test notification delivered"
          : `Test failed: ${outcome.error}`, outcome.status !== "delivered");
      } catch (err) { toast(err.message, true); }
      btn.disabled = false;
    } else if (btn.dataset.edit) {
      openEditor(endpoints.find((e) => e.id === Number(btn.dataset.edit)));
    } else if (btn.dataset.del) {
      const target = endpoints.find((e) => e.id === Number(btn.dataset.del));
      if (confirm(`Delete endpoint "${target.name}" and its logs?`)) {
        await api(`/api/endpoints/${target.id}`, { method: "DELETE" });
        toast("Endpoint deleted");
        route();
      }
    } else if (btn.id === "ep-new") {
      openEditor(null);
    }
  });

  $("#global-form", page).addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api("/api/settings", { method: "PUT", body: {
        ntfy_server: form.elements.ntfy_server.value,
        retention_days: Number(form.elements.retention_days.value),
      } });
      toast("Settings saved");
    } catch (err) { toast(err.message, true); }
  });

  $("#pw-form", page).addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api("/api/password", { body: {
        current: form.elements.current.value, new: form.elements.new.value,
      } });
      toast("Password changed");
      form.reset();
      route();
    } catch (err) { toast(err.message, true); }
  });
};
```

- [ ] **Step 2: Verify in the running app**

Same smoke-run as Task 13 Step 4. In a browser (or Playwright): create an endpoint from the "TP-Link Omada" preset, confirm the template fields and rules populate, save it, copy its URL, POST to it with curl, hit "Test", and confirm the token hint shows `…` + 4 chars after reload.

```bash
curl -s -X POST http://127.0.0.1:15000/hooks/<slug> \
  -H 'Content-Type: application/json' -d '{"level":"WARN","text":"hello"}'
```
Expected: `{"status":"accepted","events":1}`

- [ ] **Step 3: Commit**

```bash
git add app/static/app.js
git commit -m "feat: settings tab with endpoint editor, presets, and password change"
```

---

### Task 15: SPA Reports and Logs tabs

**Files:**
- Modify: `app/static/app.js` (append both views before the trailing `route();`)

**Interfaces:**
- Consumes: `/api/logs`, `/api/logs/{id}`, `/api/reports/summary` (Task 11 shapes); core helpers and `logsTimer` from Task 13.
- Produces: `views.reports`, `views.logs`.

- [ ] **Step 1: Append both views to `app/static/app.js`**

```js
views.reports = async (root) => {
  let range = "7d";
  root.innerHTML = `<div id="reports-page">
    <section class="card">
      <div class="row-between"><h2>Delivery report</h2>
        <div class="row" id="range-buttons">
          ${["24h", "7d", "30d"].map((r) =>
            `<button class="ghost ${r === range ? "active" : ""}" data-range="${r}">${r}</button>`
          ).join("")}
        </div></div>
      <div id="report-body"></div>
    </section></div>`;
  const page = $("#reports-page");

  async function load() {
    const summary = await api(`/api/reports/summary?range=${range}&tz_offset=${tzOffsetMinutes()}`);
    const max = Math.max(...summary.buckets.map((b) => b.delivered + b.failed + b.rejected), 1);
    $("#report-body", page).innerHTML = `
      ${summary.buckets.length ? `<div class="chart">${summary.buckets.map((b) => {
        const bad = b.failed + b.rejected;
        return `<div class="col" title="${esc(b.bucket)}: ${b.delivered} ok, ${bad} failed/rejected">
          <div class="seg failed" style="height:${(bad / max) * 100}%"></div>
          <div class="seg delivered" style="height:${(b.delivered / max) * 100}%"></div>
        </div>`;
      }).join("")}</div>` : "<p class='muted'>No deliveries in this range.</p>"}
      <table><thead><tr><th>Endpoint</th><th>Delivered</th><th>Failed</th>
        <th>Rejected</th><th>Total</th><th>Success</th></tr></thead>
      <tbody>${summary.endpoints.map((e) => `
        <tr><td>${esc(e.name)}</td><td>${e.delivered}</td>
        <td class="${e.failed ? "error" : ""}">${e.failed}</td>
        <td>${e.rejected}</td><td>${e.total}</td>
        <td>${e.success_rate == null ? "—" : e.success_rate + "%"}</td></tr>`).join("")}
      </tbody></table>`;
  }

  $("#range-buttons", page).addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-range]");
    if (!btn) return;
    range = btn.dataset.range;
    page.querySelectorAll("[data-range]").forEach(
      (b) => b.classList.toggle("active", b === btn));
    load();
  });
  await load();
};

views.logs = async (root) => {
  const endpoints = await api("/api/endpoints");
  root.innerHTML = `<div id="logs-page">
    <section class="card">
      <form id="log-filters" class="row">
        <select name="endpoint_id" style="width:auto"><option value="">All endpoints</option>
          ${endpoints.map((e) => `<option value="${e.id}">${esc(e.name)}</option>`).join("")}
        </select>
        <select name="status" style="width:auto"><option value="">Any status</option>
          ${["delivered", "failed", "rejected"].map((s) => `<option>${s}</option>`).join("")}
        </select>
        <input name="q" placeholder="Search text" style="width:12rem">
        <button>Apply</button>
        <label class="check"><input type="checkbox" id="log-auto"> Auto-refresh</label>
      </form>
      <table><thead><tr><th>Time</th><th>Endpoint</th><th>Status</th><th>Title</th>
        <th>HTTP</th><th>Attempts</th><th>ms</th></tr></thead>
        <tbody id="log-rows"></tbody></table>
      <button id="log-more" class="ghost hidden">Load more</button>
    </section>
    <section class="card hidden" id="log-detail"></section>
  </div>`;
  const page = $("#logs-page");
  let items = [];

  async function load(append = false) {
    const form = $("#log-filters", page);
    const params = new URLSearchParams();
    for (const name of ["endpoint_id", "status", "q"]) {
      if (form.elements[name].value) params.set(name, form.elements[name].value);
    }
    if (append && items.length) params.set("before_id", items[items.length - 1].id);
    const batch = (await api(`/api/logs?${params}`)).items;
    items = append ? items.concat(batch) : batch;
    $("#log-more", page).classList.toggle("hidden", batch.length < 50);
    $("#log-rows", page).innerHTML = items.map((d) => `
      <tr data-id="${d.id}" class="clickable">
        <td>${fmtTime(d.received_at)}</td><td>${esc(d.endpoint_name)}</td>
        <td><span class="badge ${esc(d.status)}">${esc(d.status)}</span></td>
        <td>${esc(d.title)}</td><td>${d.ntfy_status ?? "—"}</td>
        <td>${d.attempts}</td><td>${d.duration_ms}</td></tr>`).join("")
      || "<tr><td colspan='7' class='muted'>No deliveries match.</td></tr>";
  }

  $("#log-filters", page).addEventListener("submit", (event) => {
    event.preventDefault();
    load();
  });
  $("#log-more", page).addEventListener("click", () => load(true));
  $("#log-auto", page).addEventListener("change", (event) => {
    clearInterval(logsTimer);
    if (event.target.checked) logsTimer = setInterval(() => load(), 5000);
  });
  page.addEventListener("click", async (event) => {
    const row = event.target.closest("tr[data-id]");
    if (!row) return;
    const detail = await api(`/api/logs/${row.dataset.id}`);
    const box = $("#log-detail", page);
    box.classList.remove("hidden");
    box.innerHTML = `
      <h2>Delivery #${detail.id}</h2>
      <p><span class="badge ${esc(detail.status)}">${esc(detail.status)}</span>
        ${fmtTime(detail.received_at)} · from ${esc(detail.source_ip) || "unknown"}
        · ${detail.attempts} attempt(s) · ${detail.duration_ms} ms
        ${detail.ntfy_status ? `· ntfy HTTP ${detail.ntfy_status}` : ""}</p>
      ${detail.error ? `<p class="error">${esc(detail.error)}</p>` : ""}
      <h3>Notification sent</h3>
      <pre>${esc(detail.title)}\n${esc(detail.message)}</pre>
      <h3>Received payload</h3>
      <pre>${esc(detail.request_body) || "(empty)"}</pre>`;
    box.scrollIntoView({ behavior: "smooth" });
  });
  await load();
};
```

- [ ] **Step 2: Verify in the running app**

Smoke-run again; POST a few webhooks (one to a disabled endpoint, one with a bad token so it fails), then check: Logs tab filters by status, row click opens the detail with the payload, auto-refresh ticks; Reports tab switches ranges and the bar chart/success rates match; Dashboard failure card turns red.

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: green

- [ ] **Step 4: Commit**

```bash
git add app/static/app.js
git commit -m "feat: reports and logs tabs with filters, detail drawer, css chart"
```

---

### Task 16: Password reset script

**Files:**
- Create: `scripts/__init__.py` (empty, makes the test import work), `scripts/reset_password.py`
- Test: `tests/test_reset_script.py`

**Interfaces:**
- Consumes: `app.auth.hash_password`, `app.config.db_path`
- Produces: `python scripts/reset_password.py --set NEWPASS | --clear` operating on `${DATA_DIR}/gateway.db`; `main(argv: list | None)` for tests.

- [ ] **Step 1: Write the failing test**

`tests/test_reset_script.py`:
```python
import sqlite3

from app import auth
from app import db as dbq
from scripts.reset_password import main


async def test_set_then_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    conn = await dbq.connect(tmp_path / "gateway.db")
    await conn.close()

    main(["--set", "brand-new-pass"])
    con = sqlite3.connect(tmp_path / "gateway.db")
    stored = con.execute(
        "SELECT value FROM settings WHERE key='admin_password_hash'").fetchone()[0]
    assert auth.verify_password("brand-new-pass", stored)

    main(["--clear"])
    assert con.execute(
        "SELECT value FROM settings WHERE key='admin_password_hash'").fetchone() is None
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reset_script.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.reset_password'`

- [ ] **Step 3: Write minimal implementation**

`scripts/reset_password.py`:
```python
#!/usr/bin/env python3
"""Reset or clear the admin password directly in the gateway database.

For lockout recovery. Inside the container:
    docker compose exec ntfy-webhook-gateway python scripts/reset_password.py --set NEWPASS
    docker compose exec ntfy-webhook-gateway python scripts/reset_password.py --clear
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password  # noqa: E402
from app.config import db_path  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reset the gateway admin password.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--clear", action="store_true",
                       help="remove the stored password (falls back to ADMIN_PASSWORD env or open mode)")
    group.add_argument("--set", dest="new_password", help="set a new password")
    args = parser.parse_args(argv)

    con = sqlite3.connect(db_path())
    if args.clear:
        con.execute("DELETE FROM settings WHERE key='admin_password_hash'")
        print("Password cleared.")
    else:
        con.execute(
            "INSERT INTO settings(key, value) VALUES('admin_password_hash', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (hash_password(args.new_password),),
        )
        print("Password updated.")
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reset_script.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/ tests/test_reset_script.py
git commit -m "feat: password reset script for lockout recovery"
```

---

### Task 17: Docker packaging

**Files:**
- Create: `.dockerignore`
- Modify: `Dockerfile`, `docker-compose.yml`, `.env.example` (all three exist; replace their contents entirely)

**Interfaces:**
- Consumes: `python -m app.main` entrypoint (Task 12), `/health` on the webhook port
- Produces: an image any later task/CI can build; compose stack with named volume `gateway-data` at `/data`.

- [ ] **Step 1: Replace `Dockerfile`**

```dockerfile
FROM python:3.12-slim

# tzdata lets the TZ env var (from .env) govern container log timestamps
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY scripts/ scripts/

ENV DATA_DIR=/data
VOLUME /data
EXPOSE 5000 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD \
  python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WEBHOOK_PORT','5000')+'/health')"

CMD ["python", "-m", "app.main"]
```

- [ ] **Step 2: Create `.dockerignore`**

```
.git
.env
docs
tests
**/__pycache__
*.pyc
```

- [ ] **Step 3: Replace `docker-compose.yml`**

```yaml
services:
  ntfy-webhook-gateway:
    build: .
    image: ghcr.io/tekgnosis-net/ntfy-webhook-helper:latest
    container_name: ntfy-webhook-gateway
    restart: unless-stopped
    ports:
      - "5000:5000"   # webhook receiver — the ONLY port to expose via your reverse proxy
      - "5001:5001"   # admin UI — keep LAN-only, do not proxy publicly
    volumes:
      - gateway-data:/data
    env_file:
      - path: .env
        required: false

volumes:
  gateway-data:
```

- [ ] **Step 4: Replace `.env.example`**

```
# All settings are optional; the admin UI manages endpoints and the ntfy server.

# Bootstrap admin password. Once you change the password in the UI, the UI
# password (stored in the database) takes precedence over this.
#ADMIN_PASSWORD=change-me

# Legacy single-endpoint seeding: if these are set on FIRST start (empty
# database), an "omada" endpoint is created automatically and the old
# /omada-webhook URL keeps working.
#NTFY_HOST_URL=https://ntfy.example.com
#NTFY_TOPIC=omada
#NTFY_AUTH_TOKEN=tk_your_token

# Listener ports (defaults shown)
#WEBHOOK_PORT=5000
#ADMIN_PORT=5001

# Display timezone (IANA name). Sets the zone the admin UI renders timestamps
# in and the container's log timestamps. Unset = each viewer's browser-local
# time in the UI, UTC in container logs. Storage is always UTC.
#TZ=Australia/Sydney
```

- [ ] **Step 5: Build and smoke-test (skip gracefully if docker is unavailable)**

```bash
docker build -t gateway-smoke .
docker run -d --rm --name gateway-smoke -p 25000:5000 -p 25001:5001 gateway-smoke
sleep 3
curl -s http://127.0.0.1:25000/health          # {"status":"ok"}
curl -s http://127.0.0.1:25001/api/health      # {"status":"ok"}
curl -s http://127.0.0.1:25001/ | grep -q "ntfy Webhook Gateway" && echo UI-OK
docker rm -f gateway-smoke
```

- [ ] **Step 6: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml .env.example
git commit -m "feat: docker packaging with data volume and dual-port compose"
```

---

### Task 18: GitHub Actions — publish to ghcr.io

**Files:**
- Create: `.github/workflows/docker-publish.yml`

**Interfaces:**
- Consumes: `requirements-dev.txt`, `pytest`, `Dockerfile`
- Produces: on PR → test + build only; on push to `main` → `ghcr.io/tekgnosis-net/ntfy-webhook-helper:latest` + `:sha-<short>`; on tag `v1.2.3` → `:1.2.3`, `:1.2`, `:1`; platforms `linux/amd64, linux/arm64`.

- [ ] **Step 1: Create `.github/workflows/docker-publish.yml`**

```yaml
name: docker-publish

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements-dev.txt
      - run: pytest -q

  publish:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - name: Log in to ghcr.io
        if: github.event_name != 'pull_request'
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=sha,prefix=sha-
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=semver,pattern={{major}}
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Validate the YAML parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/docker-publish.yml')); print('yaml ok')"` (pyyaml ships with many systems; if missing, `pip install pyyaml` first — it is a check tool, not a project dependency)
Expected: `yaml ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/docker-publish.yml
git commit -m "ci: build, test, and publish multi-arch image to ghcr"
```

Note: ghcr packages default to private on first publish; after the first push to `main`, make the package public in GitHub → Packages → package settings if anonymous pulls are wanted.

---

### Task 19: Documentation — README, install, operations, CLAUDE.md

**Files:**
- Create: `README.md`, `docs/install.md`, `docs/operations.md`
- Modify: `CLAUDE.md` (full rewrite — the old content describes the deleted single-file app)

Write these against the implemented reality — verify every command and path against the code before writing it down. Content outline (structure is binding; keep wording natural):

- [ ] **Step 1: Write `README.md`**

Cover, in order: one-paragraph description (generic webhook → ntfy gateway with web UI); feature bullets (multiple endpoints, template mapping with presets, hot-reloaded SQLite settings, per-delivery logs + reports, dual ports for safe exposure, multi-arch image on ghcr); quick start:

````markdown
## Quick start

```bash
mkdir ntfy-webhook-gateway && cd ntfy-webhook-gateway
curl -LO https://raw.githubusercontent.com/tekgnosis-net/ntfy-webhook-helper/main/docker-compose.yml
docker compose up -d
```

Open `http://<host>:5001`, set an admin password, set your ntfy server URL in
Settings, create an endpoint, and point your service's webhook at
`http://<host>:5000/hooks/<slug>`.
````

Then: a "Ports" table (5000 webhooks/public, 5001 admin/LAN-only with one sentence of reasoning); links to `docs/install.md` and `docs/operations.md`; a "Development" section (`pip install -r requirements-dev.txt`, `pytest`, `DATA_DIR=/tmp/gw python -m app.main`).

- [ ] **Step 2: Write `docs/install.md`**

Cover: prerequisites (Docker + compose, an ntfy server with an access token); install from ghcr image (compose file above, `docker compose pull && up -d`); build from source (`git clone`, `docker compose up -d --build`); `.env` reference — a table of ADMIN_PASSWORD / NTFY_HOST_URL / NTFY_TOPIC / NTFY_AUTH_TOKEN / WEBHOOK_PORT / ADMIN_PORT / TZ with the semantics from `.env.example`, including that legacy vars only act on first start with an empty database and that TZ sets the UI display zone + container log zone while storage stays UTC (unset TZ = browser-local UI display); data volume & backup (`gateway-data` volume, everything lives in `/data/gateway.db`, back up by `docker run --rm -v gateway-data:/data alpine tar cz -C /data . > backup.tar.gz`); reverse-proxy exposure — explicit CloudPanel guidance: create a site/proxy that forwards ONLY to port 5000, never proxy 5001; the admin UI is reached directly on the LAN; upgrading from the old omada-only proxy (keep your old `.env`, first start seeds the `omada` endpoint, controller URL `/omada-webhook` unchanged).

- [ ] **Step 3: Write `docs/operations.md`**

Cover: creating an endpoint (slug→URL relationship, presets, copy-URL button); timestamp display (stored UTC; UI renders in the `TZ` env zone when set, else browser-local; report buckets follow the same zone); template syntax — placeholders `{a.b.c}`, alternatives `{event.text|text}`, `{payload}`, missing→empty, empty title→endpoint name, empty message→pretty payload; level rules (level_field, uppercase matching, priority + extra_tags, default_priority); testing (Test button and a curl example); Logs tab (filters, detail drawer contents, auto-refresh, `rejected` meaning disabled-endpoint hits); Reports tab (ranges, success rate definition); retention setting; password management — precedence chain (DB > env > open + warning banner), change in Settings, lockout recovery:

````markdown
```bash
docker compose exec ntfy-webhook-gateway python scripts/reset_password.py --set NEWPASS
# or remove it entirely (falls back to ADMIN_PASSWORD env or open mode):
docker compose exec ntfy-webhook-gateway python scripts/reset_password.py --clear
```
````

Then: upgrading (`docker compose pull && docker compose up -d`; schema is created idempotently at startup); troubleshooting table (webhook 404 → slug/disabled; notification missing → check Logs detail error, token, server URL; UI unreachable → is 5001 exposed on the LAN?).

- [ ] **Step 4: Rewrite `CLAUDE.md`**

Keep the standard header, then: project overview (two-app FastAPI gateway, module map of `app/` one line each); commands (`pip install -r requirements-dev.txt`, `pytest -q`, single test `pytest tests/test_hooks_app.py::test_health -v`, run locally `DATA_DIR=/tmp/gw python -m app.main`, docker compose); architecture essentials that span files: AppState sharing, hot-reload-via-DB-reads (no config cache — do not add one), the 202+background dispatch pattern and why, auth precedence chain, token masking invariant (raw token must never appear in an API response), two-listener isolation (nothing sensitive routes on the webhook app), timestamps via `db.utcnow()` only; testing conventions (fixtures in conftest, respx for ntfy, `state.drain()` for background dispatches); pointer to the spec and this plan.

- [ ] **Step 5: Verify docs against reality**

Every command in the docs must have been run (or its exact shape verified against code — e.g. container name matches compose, script flags match argparse). Check README renders: `python -c "print(open('README.md').read()[:500])"` is not enough — read the files once fully after writing.

- [ ] **Step 6: Run the full suite one final time**

Run: `pytest -q`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add README.md docs/install.md docs/operations.md CLAUDE.md
git commit -m "docs: readme, install and operations guides, refreshed CLAUDE.md"
```

---

## Completion

After Task 19: run `pytest -q` once more, then use the superpowers:finishing-a-development-branch skill to decide integration (this plan was written for direct commits on `main`; if executed on a feature branch, merge per that skill). Pushing `main` to GitHub triggers the first ghcr publish.

