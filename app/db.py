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
