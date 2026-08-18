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
