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

    from . import auth_routes, endpoints_routes, logs_routes, settings_routes
    app.include_router(auth_routes.router(state, require_auth))
    app.include_router(endpoints_routes.router(state, require_auth))
    app.include_router(settings_routes.router(state, require_auth))
    app.include_router(logs_routes.router(state, require_auth))

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
