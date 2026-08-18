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
