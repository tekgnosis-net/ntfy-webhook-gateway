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


async def test_migration_adds_secret_column(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(path))
    raw.execute("""
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
    """)
    raw.commit()
    raw.close()

    conn = await dbq.connect(path)
    try:
        cur = await conn.execute("PRAGMA table_info(endpoints)")
        columns = {row["name"] for row in await cur.fetchall()}
        assert "secret" in columns

        created = await dbq.create_endpoint(conn, {
            "slug": "migrated", "name": "Migrated", "enabled": True,
            "ntfy_topic": "alerts", "ntfy_token": "", "ntfy_server": None,
            "title_template": "", "message_template": "{payload}",
            "level_field": "", "rules": {}, "default_priority": "default",
            "tags": "", "secret": "abc",
        })
        assert created["secret"] == "abc"
    finally:
        await conn.close()


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
