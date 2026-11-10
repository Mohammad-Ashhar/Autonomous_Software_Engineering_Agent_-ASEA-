import pytest
from fastapi.testclient import TestClient

from asea.api import main


def test_production_requires_api_key():
    with pytest.raises(RuntimeError, match="ASEA_API_KEY"):
        main.validate_api_security_configuration("production", None)


@pytest.mark.parametrize("api_key", ["", "   ", "short", " " * 40])
def test_production_rejects_missing_or_weak_api_key(api_key):
    with pytest.raises(RuntimeError, match="at least 32"):
        main.validate_api_security_configuration("production", api_key)


def test_production_accepts_api_key_with_at_least_32_characters():
    main.validate_api_security_configuration("production", "a" * 32)


def test_production_environment_name_is_case_and_whitespace_insensitive():
    with pytest.raises(RuntimeError, match="ASEA_API_KEY"):
        main.validate_api_security_configuration(" Production ", None)


@pytest.mark.parametrize("environment", ["development", "test", ""])
def test_nonproduction_does_not_require_api_key(environment):
    main.validate_api_security_configuration(environment, None)


def test_api_key_validator_accepts_matching_bearer_token():
    assert main.is_valid_api_key("Bearer secret-token", "secret-token")


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "secret-token",
        "Basic secret-token",
        "Bearer",
        "Bearer wrong-token",
    ],
)
def test_api_key_validator_rejects_invalid_authorization(authorization):
    assert not main.is_valid_api_key(authorization, "secret-token")


def test_api_requires_key_when_configured(monkeypatch):
    monkeypatch.setenv("ASEA_API_KEY", "secret-token")
    client = TestClient(main.app)

    response = client.get("/")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_api_accepts_valid_key_when_configured(monkeypatch):
    monkeypatch.setenv("ASEA_API_KEY", "secret-token")
    client = TestClient(main.app)

    response = client.get(
        "/",
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200


def test_health_check_remains_available_when_key_is_configured(monkeypatch):
    monkeypatch.setenv("ASEA_API_KEY", "secret-token")
    client = TestClient(main.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_production_startup_fails_without_api_key(monkeypatch):
    monkeypatch.setenv("ASEA_ENV", "production")
    monkeypatch.delenv("ASEA_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ASEA_API_KEY"):
        with TestClient(main.app):
            pass



def test_request_id_is_preserved_and_returned(monkeypatch):
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    client = TestClient(main.app)
    response = client.get("/", headers={"X-Request-ID": "trace-123"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "trace-123"


def test_invalid_request_id_is_replaced(monkeypatch):
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    client = TestClient(main.app)
    response = client.get("/", headers={"X-Request-ID": "bad id\r\n"})
    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    assert request_id
    assert " " not in request_id
    assert "\r" not in request_id


def test_readiness_requires_initialized_graph(monkeypatch):
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    if hasattr(main.app.state, "graph"):
        del main.app.state.graph
    client = TestClient(main.app)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "ASEA is not ready to process requests."


def test_readiness_checks_database(monkeypatch):
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    monkeypatch.setattr(main.repair_jobs, "check_database", lambda: None)
    main.app.state.graph = object()
    client = TestClient(main.app)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_returns_safe_error_when_database_fails(monkeypatch):
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    def fail_database():
        raise RuntimeError("private database path")
    monkeypatch.setattr(main.repair_jobs, "check_database", fail_database)
    main.app.state.graph = object()
    client = TestClient(main.app)
    response = client.get("/ready")
    assert response.status_code == 503
    assert "private database path" not in response.text


def test_request_id_is_added_to_health_response(monkeypatch):
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    client = TestClient(main.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("x-request-id")

def test_request_completion_is_logged_with_correlation_and_latency(monkeypatch, caplog):
    import logging

    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    client = TestClient(main.app)
    with caplog.at_level(logging.INFO):
        response = client.get(
            "/?sensitive=query-value",
            headers={"X-Request-ID": "observability-123"},
        )

    assert response.status_code == 200
    matching = [
        record.getMessage()
        for record in caplog.records
        if "API request completed." in record.getMessage()
    ]
    assert matching
    assert "method=GET" in matching[-1]
    assert "path=/" in matching[-1]
    assert "status_code=200" in matching[-1]
    assert "request_id=observability-123" in matching[-1]
    assert "duration_ms=" in matching[-1]
    assert "sensitive=query-value" not in matching[-1]


def test_server_error_request_is_logged_as_warning(monkeypatch, caplog):
    import logging

    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    if hasattr(main.app.state, "graph"):
        del main.app.state.graph
    client = TestClient(main.app)
    with caplog.at_level(logging.WARNING):
        response = client.get("/ready")

    assert response.status_code == 503
    assert any(
        record.levelno == logging.WARNING
        and "status_code=503" in record.getMessage()
        for record in caplog.records
    )

