# Operations guide

## Creating an endpoint

Go to **Settings → Webhook endpoints → New endpoint**. Every endpoint has a
**slug** — a URL-safe identifier (lowercase letters, digits, `_`, `-`) that
becomes part of its webhook URL: `http://<host>:5000/hooks/<slug>`. That's
the URL you give to the service sending the webhook.

You can start from a **preset** (Generic JSON, TP-Link Omada) to pre-fill
the title/message templates, level field, and level rules, then adjust them,
or start from a blank template. Set the ntfy topic (and, optionally, a
per-endpoint ntfy server override and token) and save.

Each endpoint row in the table shows its full webhook URL with a **Copy**
button next to it, so you can paste it straight into the sending service's
webhook configuration.

## Webhook secret

An endpoint can optionally require a shared secret on incoming webhook
requests. Leave it empty (the default) and the endpoint accepts any request
for its slug, exactly like before this feature existed. Set one and every
POST must present it, either as a `?secret=...` query parameter or an
`X-Webhook-Secret` header — pick whichever the sending service supports; a
request with a missing or wrong secret gets the same `404` as an unknown
slug, and is logged as `rejected`. The editor's **Generate** button fills the
field with 32 random URL-safe characters if you'd rather not make one up.
Once a secret is set, the endpoint's webhook URL (and its **Copy** button)
automatically includes `?secret=...` so you can paste the whole thing
straight into the sending service.

The legacy `omada` endpoint seeded from environment variables has no secret
until you set one in its editor — remember to update the Omada controller's
configured webhook URL to include `?secret=...` afterward, or its requests
will start getting rejected.

## Timestamp display

All delivery and endpoint timestamps are stored in UTC. The admin UI
renders them in the zone set by the `TZ` environment variable, if one is
configured; otherwise it falls back to each viewer's browser-local
timezone. This applies everywhere timestamps appear — the dashboard, the
Logs tab, and delivery detail — and the Reports tab's day/hour buckets
follow the same zone, so a day boundary in the report lines up with
midnight in that zone rather than UTC midnight.

## Template syntax

Title and message templates are plain text with `{...}` placeholders:

- `{a.b.c}` — a dotted path into the received JSON payload (walks nested
  objects and, for numeric segments, list indices). Resolves to empty text
  if any part of the path is missing.
- `{a.b|c}` — alternatives, tried left to right; the first path that
  resolves to a non-null value is used. Useful when a webhook sends either
  `{"event": {"text": "..."}}` or `{"text": "..."}` depending on version.
- `{payload}` — the whole received event, pretty-printed JSON (or the raw
  request body if it wasn't valid JSON).

Any placeholder whose path doesn't resolve renders as empty text — it
never causes an error. If the rendered **title** ends up empty, the
notification falls back to the endpoint's name. If the rendered
**message** ends up empty, it falls back to the same pretty-printed
payload text as `{payload}`.

## Level rules

An endpoint can optionally set a **level field** — a template path or
`|`-alternatives spec (same syntax as above, without the surrounding
braces, e.g. `event.level|level`) pointing at a field in the payload that
carries a severity. Its resolved value is uppercased and looked up in the
endpoint's **rules** table; a match overrides the priority and adds extra
tags on top of the endpoint's base tags. If the field is empty, missing, or
doesn't match any rule, the notification uses the endpoint's **default
priority** and base tags unchanged.

## Testing

Each endpoint row has a **Test** button that sends a synthetic "Test
notification from ntfy webhook gateway" event through the same dispatch
path as a real webhook (rendered through the endpoint's templates, logged
like any other delivery) so you can confirm the ntfy server and token are
correct without waiting for a real event. Equivalently, from the command
line:

```bash
curl -X POST http://<host>:5000/hooks/<slug> \
  -H 'Content-Type: application/json' \
  -d '{"level": "WARN", "text": "test message"}'
```

The webhook responds `202 Accepted` immediately with the number of events
it queued; check the Logs tab a moment later to see how it was actually
delivered.

## Logs tab

The Logs tab lists delivery attempts, newest first, with filters for
endpoint, status (`delivered` / `failed` / `rejected`), and a free-text
search over the request body, title, message, and error. Clicking a row
opens a detail drawer with the full received payload, the rendered
notification title/message, the ntfy HTTP status, attempt count, duration,
source IP, and error text (if any). An **Auto-refresh** checkbox polls for
new rows every 5 seconds. A `rejected` status means the webhook hit a
disabled endpoint, or (if the endpoint has a secret set) presented a missing
or wrong secret — either way, the request was logged but nothing was sent to
ntfy.

## Reports tab

The Reports tab summarizes delivery volume and outcomes over a selectable
range — 24h, 7d, or 30d — as a bar chart of deliveries per bucket plus a
per-endpoint table of delivered/failed/rejected/total counts. **Success
rate** is `delivered / total` for that endpoint over the selected range,
shown as a percentage (or `—` if the endpoint had no deliveries at all in
that range).

## Retention

Settings → Global settings has a **Log retention (days)** field (default
30). Once a day, the gateway purges delivery log rows older than that many
days. This only affects the Logs/Reports history — endpoints and settings
are never purged.

## Password management

The admin UI's auth mode follows a fixed precedence, checked in this order:

1. **Database** — if a password has ever been set from the UI, it's stored
   (hashed) in SQLite and always wins from then on.
2. **Environment** — otherwise, if `ADMIN_PASSWORD` is set, that's used.
3. **Open** — otherwise, the UI requires no login at all, and shows a
   warning banner ("No admin password is set...") on every page, since
   anyone who can reach port 5001 can read ntfy tokens and change settings.

To change the password, use Settings → Admin password (requires the
current password unless you're currently in open mode).

If you're locked out, run the reset script inside the running container:

```bash
docker compose exec ntfy-webhook-gateway python scripts/reset_password.py --set NEWPASS
# or remove it entirely (falls back to ADMIN_PASSWORD env or open mode):
docker compose exec ntfy-webhook-gateway python scripts/reset_password.py --clear
```

## Upgrading

```bash
docker compose pull && docker compose up -d
```

The database schema is created idempotently at startup (`CREATE TABLE IF
NOT EXISTS ...`), and new columns added to existing tables (e.g. the
`secret` column) are migrated in automatically on connect, so upgrades never
require a manual migration step — just pull the new image and restart.

## Troubleshooting

| Symptom | Check |
|---|---|
| Webhook POST returns 404 | Confirm the slug in the URL matches an existing endpoint exactly, and that the endpoint is enabled — a disabled endpoint also returns 404 (logged as `rejected`). |
| Webhook accepted (202) but no notification arrives | Open the delivery's detail in the Logs tab for the error message; confirm the ntfy token is correct and not expired, and that the ntfy server URL (global or per-endpoint override) is reachable from the container. |
| Admin UI unreachable | Confirm port 5001 is actually exposed/reachable from where you're connecting — remember it's meant to be LAN-only and is deliberately not proxied publicly. |
