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
