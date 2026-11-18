import asyncio

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from asea.api.protection import RequestProtectionMiddleware


def make_client(**limits):
    app = FastAPI()
    app.add_middleware(RequestProtectionMiddleware, **limits)

    @app.get("/health")
    def health():
        return {"status": "healthy"}

    @app.get("/")
    def root():
        return {"status": "ok"}

    @app.post("/echo")
    async def echo(request: Request):
        return {"size": len(await request.body())}

    return TestClient(app)


def test_rate_limit_allows_requests_within_configured_window():
    client = make_client(requests_per_window=2, window_seconds=60)
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200


def test_rate_limit_returns_429_and_retry_after():
    client = make_client(requests_per_window=1, window_seconds=60)
    assert client.get("/").status_code == 200
    response = client.get("/")
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1


def test_rate_limit_window_resets(monkeypatch):
    import asea.api.protection as protection

    clock = iter([100.0, 161.0])
    monkeypatch.setattr(protection, "_monotonic", lambda: next(clock))
    client = make_client(requests_per_window=1, window_seconds=60)
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200


def test_rate_limit_isolated_by_client_address():
    middleware = RequestProtectionMiddleware(
        lambda *_: None, requests_per_window=1, window_seconds=60
    )
    assert middleware._allow_request("192.0.2.1", 1.0)[0]
    assert middleware._allow_request("192.0.2.2", 1.0)[0]
    assert not middleware._allow_request("192.0.2.1", 2.0)[0]


def test_health_endpoint_is_exempt_from_rate_limiting():
    client = make_client(requests_per_window=1, window_seconds=60)
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200


def test_options_preflight_is_exempt_from_rate_limiting():
    client = make_client(requests_per_window=1, window_seconds=60)
    assert client.get("/").status_code == 200
    assert client.options("/").status_code != 429


def test_declared_oversized_body_is_rejected_before_endpoint():
    client = make_client(max_body_bytes=4)
    response = client.post("/echo", content=b"12345")
    assert response.status_code == 413


def test_malformed_content_length_is_rejected():
    middleware = RequestProtectionMiddleware(lambda *_: None, max_body_bytes=4)
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"content-length", b"not-a-number")],
        "client": ("127.0.0.1", 1234),
    }
    asyncio.run(middleware(scope, receive, send))
    assert messages[0]["status"] == 400


def test_response_includes_security_headers():
    client = make_client()
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"


def test_rejected_requests_also_include_security_headers():
    client = make_client(requests_per_window=1, window_seconds=60)
    client.get("/")
    response = client.get("/")
    assert response.status_code == 429
    assert response.headers["x-content-type-options"] == "nosniff"


def test_streamed_body_cannot_exceed_configured_limit():
    middleware = RequestProtectionMiddleware(
        lambda scope, receive, send: None, max_body_bytes=4
    )
    messages = []

    async def send(message):
        messages.append(message)

    chunks = iter([
        {"type": "http.request", "body": b"123", "more_body": True},
        {"type": "http.request", "body": b"456", "more_body": False},
    ])

    async def receive():
        return next(chunks)

    async def endpoint(scope, receive, send):
        await receive()
        await receive()

    middleware.app = endpoint
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/upload",
        "headers": [],
        "client": ("127.0.0.1", 1234),
    }
    asyncio.run(middleware(scope, receive, send))
    assert messages[0]["status"] == 413


def test_rate_limit_client_registry_is_bounded():
    middleware = RequestProtectionMiddleware(
        lambda *_: None,
        requests_per_window=1,
        window_seconds=60,
        max_clients=2,
    )
    assert middleware._allow_request("one", 1.0)[0]
    assert middleware._allow_request("two", 1.0)[0]
    assert middleware._allow_request("three", 1.0)[0]
    assert len(middleware._requests) == 2
