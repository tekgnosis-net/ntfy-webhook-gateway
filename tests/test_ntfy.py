import httpx
import pytest
import respx

from app.ntfy import send


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


@respx.mock
async def test_success_sends_ntfy_headers(client):
    route = respx.post("https://n.example/alerts").mock(return_value=httpx.Response(200))
    result = await send(client, "https://n.example", "alerts", "tk_abc",
                        "Title", "hello", "high", ["a", "b"], retry_delays=())
    assert result.ok and result.status_code == 200 and result.attempts == 1
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer tk_abc"
    assert req.headers["Title"] == "Title"
    assert req.headers["Priority"] == "high"
    assert req.headers["Tags"] == "a,b"
    assert req.content == b"hello"


@respx.mock
async def test_no_token_no_auth_header(client):
    route = respx.post("https://n.example/alerts").mock(return_value=httpx.Response(200))
    await send(client, "https://n.example/", "alerts", None, "T", "m", "default", [], retry_delays=())
    assert "authorization" not in route.calls[0].request.headers


@respx.mock
async def test_server_error_retries_then_succeeds(client):
    route = respx.post("https://n.example/alerts")
    route.side_effect = [httpx.Response(500), httpx.Response(200)]
    result = await send(client, "https://n.example", "alerts", None, "T", "m",
                        "default", [], retry_delays=(0, 0, 0))
    assert result.ok and result.attempts == 2


@respx.mock
async def test_client_error_is_permanent(client):
    respx.post("https://n.example/alerts").mock(return_value=httpx.Response(403))
    result = await send(client, "https://n.example", "alerts", None, "T", "m",
                        "default", [], retry_delays=(0, 0, 0))
    assert not result.ok and result.attempts == 1 and "403" in result.error


@respx.mock
async def test_network_error_exhausts_retries(client):
    respx.post("https://n.example/alerts").mock(side_effect=httpx.ConnectError("refused"))
    result = await send(client, "https://n.example", "alerts", None, "T", "m",
                        "default", [], retry_delays=(0, 0))
    assert not result.ok and result.attempts == 3 and result.status_code is None
