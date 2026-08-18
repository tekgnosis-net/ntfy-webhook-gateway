# Install guide

## Prerequisites

- Docker and the `docker compose` plugin on the host.
- An ntfy server you can reach (self-hosted or ntfy.sh) and an access token
  for the topic you'll publish to, if the topic requires auth.

## Install from the ghcr image

Create a directory for the deployment, fetch the compose file, and start it:

```bash
mkdir ntfy-webhook-gateway && cd ntfy-webhook-gateway
curl -LO https://raw.githubusercontent.com/tekgnosis-net/ntfy-webhook-helper/main/docker-compose.yml
docker compose up -d
```

This pulls `ghcr.io/tekgnosis-net/ntfy-webhook-helper:latest` and starts a
container named `ntfy-webhook-gateway` with a persistent `gateway-data`
volume. To update later:

```bash
docker compose pull && docker compose up -d
```

If you want to configure things via environment variables before first
start (bootstrap admin password, legacy endpoint seeding, ports, timezone),
create a `.env` file next to `docker-compose.yml` first — see the reference
below. It's entirely optional; everything it sets can also be changed later
from the admin UI, except the two listener ports.

## Build from source

```bash
git clone https://github.com/tekgnosis-net/ntfy-webhook-helper.git
cd ntfy-webhook-helper
docker compose up -d --build
```

This builds the image locally from the `Dockerfile` instead of pulling from
ghcr; everything else (volume, ports, `.env`) works the same way.

## `.env` reference

All variables are optional — copy `.env.example` to `.env` and uncomment
what you need, or set them directly in your process manager.

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_PASSWORD` | unset (open admin UI) | Bootstrap password for the admin UI. Once you set a password from within the UI, that database-stored password takes over and this variable is ignored from then on. |
| `NTFY_HOST_URL` | unset | Legacy single-endpoint config. Only read on the very first start against an empty database — see "Upgrading from the old omada-only proxy" below. |
| `NTFY_TOPIC` | unset | Same as above; paired with `NTFY_AUTH_TOKEN`. |
| `NTFY_AUTH_TOKEN` | unset | Same as above. |
| `WEBHOOK_PORT` | `5000` | Port the webhook receiver listens on inside the container. |
| `ADMIN_PORT` | `5001` | Port the admin UI listens on inside the container. |
| `TZ` | unset | IANA timezone name (e.g. `Australia/Sydney`). Sets the zone the admin UI renders timestamps in and the container's own log timestamps. All data is still stored in UTC regardless of this setting. If unset, the UI falls back to each viewer's browser-local timezone and container logs are in UTC. |

The legacy `NTFY_HOST_URL` / `NTFY_TOPIC` / `NTFY_AUTH_TOKEN` trio only ever
acts once: on the first start against an empty database, if `NTFY_TOPIC`
and `NTFY_AUTH_TOKEN` are both set, the gateway creates an `omada` endpoint
using them (and `NTFY_HOST_URL` as the ntfy server, if provided) and marks
seeding done. On every later start, these variables are read but ignored.

Changing `WEBHOOK_PORT` or `ADMIN_PORT` also requires updating the port
mapping (`ports:`) in `docker-compose.yml` to match — the env var only
changes what the process binds to *inside* the container.

## Data volume and backup

Everything the gateway needs to run — endpoints, settings, delivery logs —
lives in one SQLite file, `/data/gateway.db`, inside the `gateway-data`
Docker volume. There's nothing else to back up.

To take a backup:

```bash
docker run --rm -v gateway-data:/data alpine tar cz -C /data . > backup.tar.gz
```

To restore, stop the gateway, extract the archive back into a (new or
emptied) `gateway-data` volume, and start it again.

## Reverse-proxy exposure (CloudPanel)

Only ever proxy port **5000** (the webhook receiver). Never proxy port
**5001** — the admin UI must stay reachable only from your LAN, since it
holds ntfy tokens and lets anyone with access reconfigure or delete
endpoints.

In CloudPanel:

1. Create a new site as a reverse-proxy vhost for the domain you want
   webhooks to arrive at (e.g. `webhooks.example.com`).
2. Point the proxy's backend at `127.0.0.1:5000` (or the gateway host's LAN
   address and port 5000, if CloudPanel runs elsewhere).
3. Issue/attach a TLS certificate for the domain as usual — CloudPanel
   terminates TLS and forwards plain HTTP to the gateway.
4. Leave port 5001 out of the proxy configuration entirely. Reach the admin
   UI directly, e.g. `http://<gateway-host-lan-ip>:5001`, over the LAN or a
   VPN — not through the public domain.

Your webhook senders should then target
`https://webhooks.example.com/hooks/<slug>` (or `/omada-webhook` for the
legacy alias) instead of the raw host:port.

## Upgrading from the old omada-only proxy

If you're replacing a previous single-purpose Omada→ntfy proxy that used
`NTFY_HOST_URL`, `NTFY_TOPIC`, and `NTFY_AUTH_TOKEN`:

1. Keep your existing `.env` file as-is — those three variables are exactly
   what the gateway reads on first start.
2. Start the gateway against a fresh, empty `gateway-data` volume. It seeds
   an `omada` endpoint from your `.env` values automatically, using the
   built-in Omada preset (same level→priority mapping as before).
3. Your Omada controller's webhook URL doesn't need to change: `POST
   /omada-webhook` still works and is routed to the seeded `omada` endpoint.
4. Once it's running, you can manage that endpoint (rename it, edit its
   template, add more endpoints) from the admin UI like any other.
