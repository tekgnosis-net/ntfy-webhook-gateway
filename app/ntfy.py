import asyncio
from dataclasses import dataclass

import httpx

RETRY_DELAYS = (1.0, 5.0, 25.0)


@dataclass
class SendResult:
    ok: bool
    status_code: int | None
    error: str | None
    attempts: int


async def send(client: httpx.AsyncClient, server: str, topic: str, token: str | None,
               title: str, message: str, priority: str, tags: list[str],
               retry_delays=RETRY_DELAYS) -> SendResult:
    url = server.rstrip("/") + "/" + topic
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    attempts = 0
    status = None
    last_error = None
    for delay in (0.0, *retry_delays):
        if delay:
            await asyncio.sleep(delay)
        attempts += 1
        try:
            response = await client.post(url, content=message.encode("utf-8"), headers=headers)
            status = response.status_code
            if response.status_code < 300:
                return SendResult(True, status, None, attempts)
            last_error = f"ntfy returned {response.status_code}"
            if response.status_code < 500:
                break  # bad token/topic/request — retrying cannot help
        except httpx.HTTPError as exc:
            status = None
            last_error = str(exc) or exc.__class__.__name__
    return SendResult(False, status, last_error, attempts)
