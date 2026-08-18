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
