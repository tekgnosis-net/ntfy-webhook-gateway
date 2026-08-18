# Generic Webhook → ntfy Gateway — Design

**Date:** 2026-08-18
**Status:** Approved design, pending implementation plan

## Purpose

Replace the single-purpose Omada→ntfy proxy with a generic, self-hosted webhook
gateway: arbitrary services POST webhooks to per-endpoint URLs, and the gateway
transforms and forwards them as notifications to a self-hosted ntfy server. A
built-in web UI manages endpoints, shows delivery logs, and reports on activity.
Settings changes take effect immediately and persist across restarts.

## Goals

- Multiple webhook endpoints, each mapped to an ntfy topic with its own token.
- Settings persisted in SQLite and hot-effective (no restart, no reload step).
- Per-webhook delivery logging with enough detail to debug payload mapping.
- SPA admin UI with four tabs: Dashboard, Settings, Reports, Logs.
- Two listening ports so only the webhook surface is exposed publicly
  (reverse-proxied via CloudPanel); the admin surface stays LAN-only.
- Organized multi-module project layout.
- GitHub Actions workflow publishing a multi-arch Docker image to ghcr.io.
- Existing Omada controller deployments keep working without reconfiguration.

## Non-goals (v1)

- Non-ntfy notification backends (Pushover, email, generic outbound webhooks).
- Per-endpoint arbitrary transform scripts (security liability).
- Multi-user accounts / roles. One admin password.
- Rate limiting on the webhook surface.

## Architecture

Single container, single Python process, **two FastAPI apps** served by two
programmatic uvicorn servers on one asyncio event loop. Both apps share the
same DB layer, dispatcher, and one `httpx.AsyncClient` (created in a shared
lifespan).

| Listener | Env var (default) | Routes | Exposure |
|---|---|---|---|
| Webhook | `WEBHOOK_PORT` (5000) | `POST /hooks/{slug}`, `POST /omada-webhook` (legacy alias), `GET /health` | Public via reverse proxy |
| Admin | `ADMIN_PORT` (5001) | SPA static files, `/api/*`, `GET /health` | LAN only (not proxied) |

The webhook app routes nothing sensitive: no admin API, no static files. Port
5000 is kept from the current deployment so existing port mappings and the
Omada controller's webhook URL survive the upgrade.

**Stack:** Python 3.12, FastAPI, uvicorn, httpx (async ntfy client), aiosqlite.
SQLite in WAL mode at `{DATA_DIR}/gateway.db` (`DATA_DIR` default `/data`,
volume-mounted).

**Hot reload by construction:** every request reads current config from SQLite;
there is no in-memory config cache to invalidate, so a UI save is atomic and
immediately effective.

### Project layout

```
app/
  main.py          # app factories, dual-server startup, lifespan
  db.py            # SQLite (WAL), schema migrations, query helpers
  auth.py          # scrypt password hashing, sessions, auth dependency
  hooks.py         # webhook receiver + dispatch pipeline
  ntfy.py          # ntfy client (httpx)
  templating.py    # {a.b.c} placeholder resolution
  presets.py       # built-in endpoint presets (Omada, Generic)
  api/             # admin API routers: auth, endpoints, settings, logs, reports
  static/          # SPA: index.html, app.js, styles.css (no build step)
tests/
scripts/reset_password.py
.github/workflows/docker-publish.yml
Dockerfile  docker-compose.yml  requirements.txt  .env.example  README.md  CLAUDE.md
docs/superpowers/specs/
```

## Data model (SQLite)

### `endpoints`

| Column | Notes |
|---|---|
| `id` | integer PK |
| `slug` | unique, URL-safe; forms `/hooks/{slug}` |
| `name` | display name |
| `enabled` | bool; disabled endpoints 404 |
| `ntfy_topic` | target topic |
| `ntfy_token` | secret; see "Secrets" below |
| `ntfy_server` | nullable; falls back to global setting |
| `title_template` | e.g. `Omada: {event.category} ({event.level})` |
| `message_template` | e.g. `[{event.category}] {event.target}: {event.text}` |
| `level_field` | JSON path to the severity value, e.g. `event.level` |
| `rules` | JSON: uppercased level → `{"priority": "...", "extra_tags": [...]}` |
| `default_priority` | used when no rule matches |
| `tags` | base tags (comma list), always sent |
| `secret` | optional shared secret; empty = no auth. See "Webhook pipeline" below |
| `created_at`, `updated_at` | timestamps |

### `settings`

Key/value rows: global ntfy server URL, `admin_password_hash`, log retention
days (default 30).

### `sessions`

Hashed session tokens with expiry, backing login cookies.

### `deliveries`

One row per received webhook event: endpoint id, received timestamp, source IP,
request body (stored truncated to 64 KB), rendered title/message, status,
ntfy HTTP status, error text, attempt count, duration ms. Statuses:
`delivered` (ntfy accepted), `failed` (all retries exhausted), `rejected`
(received for a known-but-disabled endpoint — logged for debugging, never
forwarded; the caller still gets a 404). This single table powers both the Logs tab (row detail)
and the Reports tab (aggregate queries). A purge job (startup + daily) enforces
the retention setting.

### Secrets

ntfy tokens are stored in SQLite on the data volume. The admin API never
returns them: responses mask to the last 4 characters, and an update payload
without a new token keeps the stored one (write-only semantics). Encryption at
rest is out of scope for v1 — the DB file lives on a local volume with
container-only access.

The per-endpoint webhook `secret` (see "Webhook pipeline" below) is handled
differently and deliberately: the admin API returns it in full to
authenticated admins, because the UI needs it to reconstruct the copyable
webhook URL (`?secret=...`). This is safe because the admin surface is
already LAN-only and session-authed — unlike `ntfy_token`, there's no
write-only masking for `secret`.

## Webhook pipeline

1. `POST /hooks/{slug}` → look up enabled endpoint; unknown or disabled → 404.
2. If the endpoint has a `secret` set, require it on the request (as
   `?secret=...`, an `X-Webhook-Secret` header, or a `shardSecret`/`secret`
   field in the JSON body — Omada's native Shard Secret) — compared with
   `secrets.compare_digest`; missing or wrong → same 404 as an unknown slug
   (no information leak), logged as a `rejected` delivery. An empty secret
   skips this check entirely, preserving legacy no-auth behavior.
3. Parse body: JSON object → one event; JSON array → one event per item;
   non-JSON → wrapped as `{"body": "<raw text>"}` so `{body}` resolves
   normally. The special placeholder `{payload}` renders the whole event
   pretty-printed (works for both JSON and raw-text events).
4. Respond **200 immediately**; dispatch continues in a background task.
   Rationale: webhook senders have short timeouts and aggressive retry loops —
   acking fast prevents duplicate storms and decouples the sender's timeout
   budget from ntfy's availability.
5. Per event: render title/message templates. Missing placeholder → empty
   string; a fully empty rendered message falls back to the pretty-printed
   payload (truncated).
6. Read `level_field`, uppercase, look up in `rules` → priority + extra tags;
   no match → `default_priority` and base tags only.
7. POST to `{server}/{topic}`: message text as body; Title / Priority / Tags /
   `Authorization: Bearer` as headers (ntfy's header-based metadata API).
8. Up to 3 attempts with backoff (1s / 5s / 25s). Final outcome recorded in
   `deliveries` — no silent failures.

### Presets

`presets.py` ships built-in endpoint templates selectable in the UI when
creating an endpoint:

- **Omada** — reproduces current behavior: templates over `event.*`,
  `level_field: event.level`, rules WARN→high/warning,
  ALERT|ERROR|CRITICAL→urgent/rotating_light+fire, base tags `omada,network`.
- **Generic** — title = endpoint name, message template = `{payload}`
  (pretty-printed event), no rules.

### Legacy compatibility

On first run with an empty DB, if legacy env vars (`NTFY_TOPIC`,
`NTFY_AUTH_TOKEN`, `NTFY_HOST_URL`) are present, seed an Omada-preset endpoint
and serve `POST /omada-webhook` as an alias for it. Existing controller
configurations keep working untouched.

## Admin API & auth

All admin routes live under `/api` on the admin listener, cookie-session
protected.

**Auth precedence:**

1. `admin_password_hash` in SQLite (set via the UI's change-password form) —
   authoritative when present.
2. Else `ADMIN_PASSWORD` env var — bootstrap mode.
3. Else open mode; the UI shows a persistent warning banner.

Password hashing: stdlib `hashlib.scrypt` with per-hash salt (no extra
dependency). Login creates a random token (`secrets.token_urlsafe`), stores its
hash in `sessions` with a 7-day expiry, sets an HttpOnly cookie. Lockout recovery:
`scripts/reset_password.py --clear | --set NEW` edits the settings row directly
(documented for `docker compose exec`).

**Routes:** login / logout / auth status; password change (requires current
password); endpoints CRUD; per-endpoint **send-test-notification**; global
settings get/put; logs list (filter: endpoint, status, text; paged) and per-row
detail; reports summary (range 24h / 7d / 30d: per-endpoint counts, success
rate, time-bucketed series); dashboard snapshot; presets list; health.

Validation via pydantic models; errors return structured JSON.

## SPA (no build step)

`index.html` + `app.js` + `styles.css`, hash-routed tabs, `fetch()` against
`/api`. No external assets — works offline.

Timestamps are stored UTC; ALL display is local time. When the `TZ` env var is
set (via .env/docker-compose) the UI renders every timestamp in that zone
(surfaced as `display_timezone` on `/api/auth/status`) and, with tzdata in the
image, container log timestamps follow it too; when unset, the UI uses each
viewer's browser-local zone. Report buckets are computed server-side with a
client-supplied `tz_offset` (minutes, `Date.getTimezoneOffset()` convention)
so hour/day groupings match the displayed zone. Python never converts
timezones — the browser does all rendering.

- **Dashboard** — stat cards (endpoint count, deliveries and failures last
  24h), recent activity feed, per-endpoint health at a glance.
- **Settings** — endpoint list; create/edit form with preset picker,
  copy-webhook-URL button, masked token field, template fields, level-rules
  editor, enabled toggle, test button; global settings (ntfy server, retention
  days); change password.
- **Reports** — range selector; per-endpoint success/failure table; timeline
  as CSS/inline-SVG bars (no chart library).
- **Logs** — filterable table (endpoint, status, text), auto-refresh toggle;
  row click opens a detail drawer: received payload, rendered notification,
  attempts, error text.

## Docker & CI

**Dockerfile:** `python:3.12-slim`, install `requirements.txt`, copy `app/`,
`VOLUME /data`, expose 5000 + 5001, healthcheck against the webhook listener's
`/health`, CMD runs the dual-server entrypoint.

**docker-compose.yml:** builds locally, maps `5000:5000` (webhooks — the port
to expose via CloudPanel) and `5001:5001` (admin — LAN only), mounts a named
volume at `/data`, `env_file: .env`.

**`.github/workflows/docker-publish.yml`:**

- PRs: run tests, build image (no push).
- Push to `main`: tests → publish `ghcr.io/tekgnosis-net/ntfy-webhook-helper`
  as `:latest` + `:sha-<short>`.
- Tags `v*`: publish semver tags (`:1.2.3`, `:1.2`, `:1`).
- Multi-arch `linux/amd64, linux/arm64` via buildx + QEMU; auth via the
  built-in `GITHUB_TOKEN` (`packages: write` permission).

## Testing

Pytest, FastAPI test clients for both apps, ntfy mocked with `respx`.

- Unit: template placeholder resolution (nested paths, lists, missing keys),
  level→priority rules, auth precedence order, token masking, slug validation.
- Integration: hook POST → dispatch → `deliveries` row (success, retry-then-
  fail, rejected); legacy alias seeding; settings change visible on next
  request without restart; admin routes absent from the webhook app.

## Documentation (final implementation step)

- `README.md` (project root) — what the gateway does, quick start, screenshot
  placeholder, links into `docs/`.
- `docs/install.md` — Docker Compose install (ghcr image and build-from-source),
  `.env` reference, volume/backup notes, CloudPanel exposure guidance
  (webhook port public, admin port LAN-only).
- `docs/operations.md` — creating endpoints and templates, presets, testing
  deliveries, reading Logs/Reports, password management incl. the reset
  script, log retention, upgrading.

## Error handling

- Unknown/disabled slug → 404 (no information leak about which slugs exist).
- Malformed JSON → treated as raw text event, never dropped silently.
- ntfy failures → retried, then recorded as `failed` with error text; visible
  in Logs and Reports.
- DB contention → WAL mode; writes serialized through the aiosqlite connection.
