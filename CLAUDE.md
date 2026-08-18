# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A generic webhook-to-ntfy gateway: two FastAPI ASGI apps sharing one SQLite
database and one httpx client, run together by a single asyncio process.
One app receives arbitrary webhooks and forwards them to ntfy as push
notifications; the other serves an admin API plus a small vanilla-JS SPA for
managing endpoints, settings, logs, and reports.

Module map (`app/`):

- `config.py` — env var accessors (`DATA_DIR`, ports, `ADMIN_PASSWORD`,
  legacy `NTFY_*` trio, `TZ`). No caching — every call re-reads the env.
- `db.py` — the whole schema (`endpoints`, `settings`, `sessions`,
  `deliveries`) and every SQLite query, via `aiosqlite`. `utcnow()` is the
  single timestamp source.
- `state.py` — `AppState`: holds the shared db connection, httpx client,
  retry delays, and the `spawn()`/`drain()` background-task bookkeeping.
- `auth.py` — password hashing (scrypt), session tokens, the auth-mode
  precedence chain, login/logout/change-password.
- `ntfy.py` — `send()`: POSTs one notification to an ntfy server with
  retries.
- `templating.py` — `{a.b.c}` / `{a|b}` / `{payload}` placeholder
  rendering over a JSON event.
- `presets.py` — built-in endpoint presets (`generic`, `omada`).
- `hooks.py` — `create_hooks_app()`: the public webhook FastAPI app
  (`/health`, `/hooks/{slug}`, `/omada-webhook`), plus
  `build_notification()`/`dispatch_event()` used by both the webhook app and
  the admin "Test" endpoint.
- `main.py` — process entrypoint: creates the db connection and httpx
  client, seeds the legacy `omada` endpoint on first run, runs both uvicorn
  servers concurrently, and runs the daily retention purge loop.
- `api/__init__.py` — `create_admin_app()`: mounts the routers below plus
  the static SPA (`app/static/`).
- `api/auth_routes.py`, `api/endpoints_routes.py`, `api/settings_routes.py`,
  `api/logs_routes.py` — the admin `/api/*` surface (login/session,
  endpoint CRUD + test, global settings, logs/reports/dashboard).
- `static/` — the admin SPA (`index.html`, `app.js`, `styles.css`), served
  by the admin app only.

`scripts/reset_password.py` is a standalone CLI for lockout recovery — it
opens the SQLite file directly (not through the running app) to set or
clear the stored password hash.

## Commands

```bash
pip install -r requirements-dev.txt        # installs requirements.txt + pytest/respx
pytest -q                                  # full suite
pytest tests/test_hooks_app.py::test_health -v   # single test
DATA_DIR=/tmp/gw python -m app.main        # run both listeners locally (webhook :5000, admin :5001)
docker compose up -d --build               # build image and run via compose
```

There's no separate lint/format command configured.

## Architecture essentials

**AppState sharing.** `main.run()` builds one `AppState(db, client)` and
passes the *same instance* to both `create_hooks_app(state)` and
`create_admin_app(state)`. Any new shared service (another HTTP client,
another cache, etc.) belongs on `AppState`, not duplicated per-app.

**Hot reload via DB reads — no config cache.** Every request path reads
endpoints and settings straight from SQLite (`dbq.get_endpoint_by_slug`,
`dbq.get_setting`, ...) rather than through any in-process cache. This is
what makes admin UI edits (templates, ntfy server, retention days, endpoint
enable/disable) take effect on the very next request with no restart and no
invalidation logic to get wrong. Do not add a config/endpoint cache — it
would reintroduce exactly the staleness bug class this design avoids, for a
cost (a few extra SQLite reads per request) that doesn't matter at this
scale.

**202 + background dispatch.** `hooks.receive()` parses the incoming body
into one or more events, calls `state.spawn(dispatch_event(...))` for each,
and returns `202 {"status": "accepted", "events": N}` immediately — before
any ntfy call has happened. Actual delivery (including up to three retries
with 1s/5s/25s backoff on failure) runs in a detached `asyncio.Task` tracked
by `AppState._tasks`. This keeps the webhook response fast and independent
of ntfy's latency or retry time, which matters because some webhook senders
(e.g. network controllers) have their own short timeouts and no retry logic
of their own. Tests that need to observe the outcome must `await
state.drain()` after posting, which awaits all currently-tracked tasks.

**Auth precedence chain.** `auth.auth_mode(db)` resolves, in order: (1) a
stored `admin_password_hash` in the `settings` table → `"db"` mode; (2) else
the `ADMIN_PASSWORD` env var → `"env"` mode; (3) else `"open"` mode (no
login required, `is_authenticated()` always `True`). Setting a password from
the UI always writes to the DB and permanently wins over the env var from
then on — there's no way back to `"env"` mode short of clearing the DB row
(`scripts/reset_password.py --clear`).

**Token-masking invariant.** `ntfy_token` is stored raw in SQLite, but
`endpoints_routes.public_endpoint()` must never put the raw value in an API
response — only `ntfy_token_set` (bool) and `ntfy_token_hint` (last 4 chars,
prefixed `…`). On update, an absent/`null` `ntfy_token` in the request means
"keep the existing secret" (the field is popped before the DB write); only a
non-null value overwrites it. Any new field or endpoint that surfaces
endpoint data must go through (or replicate) this masking — never add a
route that echoes the full token back.

**Two-listener isolation.** `create_hooks_app()` and `create_admin_app()`
are two independent `FastAPI()` instances with disjoint route sets; nothing
under `/api/*` (settings, endpoint CRUD, logs, auth) is mounted on the hooks
app, and nothing from `hooks.py` is mounted on the admin app. This is what
makes it safe to expose the webhook port (5000) publicly while keeping the
admin port (5001) LAN-only — a public webhook port can never reach settings,
logs, or tokens. `tests/test_main.py::test_apps_are_isolated` guards this;
keep it passing when adding routes.

**Timestamps via `db.utcnow()` only.** All stored timestamps go through
`db.utcnow()` (`datetime.now(timezone.utc).isoformat(timespec="seconds")`)
or an equivalent explicit `timezone.utc`-aware call — never naive local
time. Storage is always UTC, full stop.

**TZ display convention.** `config.display_timezone()` reads the `TZ` env
var and is exposed via `/api/auth/status`. The SPA's `fmtTime()` renders
stored UTC timestamps in that IANA zone if set, else the browser's local
zone. Report bucketing (`db.delivery_buckets`) takes an `offset_minutes`
parameter using the JS `Date.getTimezoneOffset()` convention so bucket
boundaries line up with the same displayed zone, not UTC. `TZ` also governs
container log timestamps (via `tzdata` in the Dockerfile) but never touches
what's written to the database.

## Testing conventions

- Fixtures live in `tests/conftest.py`: `db` (a fresh aiosqlite connection
  against a `tmp_path` file), `sample_endpoint_data`, `state` (an `AppState`
  built with `retry_delays=(0,)` so failure-path tests don't sleep), and
  `hooks_client` / `admin_client` (httpx `AsyncClient`s over `ASGITransport`
  for each app).
- `pytest-asyncio` runs in `asyncio_mode = "auto"` (`pyproject.toml`) — async
  test functions need no `@pytest.mark.asyncio` decorator.
- ntfy HTTP calls are mocked with `respx` (`@respx.mock` + `respx.post(url).mock(...)`)
  rather than hitting a real server.
- Because webhook POSTs return 202 before delivery finishes, tests that
  assert on delivery rows must `await state.drain()` first to let the
  spawned background task(s) complete.

## Further reading

- Spec: `docs/superpowers/specs/2026-08-18-webhook-gateway-design.md`
- Plan: `docs/superpowers/plans/2026-08-18-webhook-gateway.md`
