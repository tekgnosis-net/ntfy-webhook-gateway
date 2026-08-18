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
