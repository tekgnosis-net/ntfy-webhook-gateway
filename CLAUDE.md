# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A single-file Flask service that receives webhook POSTs from a TP-Link Omada controller at `POST /omada-webhook` and forwards each event as a push notification to a self-hosted ntfy server. All logic lives in `app.py`. There is no requirements file — dependencies (`flask`, `requests`) are installed directly in the Dockerfile. There are no tests.

## Commands

Configuration is required first: copy `.env.example` to `.env` and set `NTFY_HOST_URL`, `NTFY_AUTH_TOKEN`, and `NTFY_TOPIC`.

- Run (intended deployment): `docker compose up --build -d`
- Logs: `docker compose logs -f omada-ntfy-proxy`
- Run locally: `pip install flask requests && python app.py` — note Flask does not read `.env` itself (docker compose does, via `env_file`), so export the variables first, e.g. `set -a; . ./.env; set +a; python app.py`
- Manual smoke test: `curl -X POST localhost:5000/omada-webhook -H 'Content-Type: application/json' -d '{"text":"test message","level":"WARN"}'`

## Architecture

The entire pipeline is `handle_omada_webhook` in `app.py`:

1. **Payload normalization** — accepts either a single JSON event object or a list of them; each item may either be the event itself or wrap it under an `"event"` key.
2. **Level mapping** — Omada's `level` field is translated to ntfy semantics: `WARN`/`WARNING` → priority `high` + warning tag; `ALERT`/`ERROR`/`CRITICAL` → priority `urgent` + alarm tags; anything else → priority `default`. Tags always include `omada,network`.
3. **Forwarding** — each event is POSTed to `{NTFY_HOST_URL}/{NTFY_TOPIC}`. Per ntfy's API, notification metadata (Title, Priority, Tags) goes in HTTP headers along with the `Authorization: Bearer` token; the message text is the raw request body.

Port 5000 is hardcoded in three places that must stay in sync: `app.py` (`app.run`), `Dockerfile` (`EXPOSE`), and both sides of the port mapping in `docker-compose.yml`.
