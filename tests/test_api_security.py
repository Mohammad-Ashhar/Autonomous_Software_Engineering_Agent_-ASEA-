import pytest
from fastapi.testclient import TestClient

from asea.api import main


def test_production_requires_supabase_configuration(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        main.validate_api_security_configuration("production", None)


@pytest.mark.parametrize(
    ("url", "anon_key"),
    [
        ("", "public-anon-key"),
        ("https://example.supabase.co", ""),
        ("http://example.supabase.co", "public-anon-key"),
    ],
)
def test_production_rejects_missing_or_invalid_supabase_configuration(
    monkeypatch, url, anon_key
):
    monkeypatch.setenv("SUPABASE_URL", url)
    monkeypatch.setenv("SUPABASE_ANON_KEY", anon_key)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        main.validate_api_security_configuration("production", "legacy-api-key")


def test_production_accepts_valid_supabase_configuration(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    main.validate_api_security_configuration("production", None)


def test_production_environment_name_is_case_and_whitespace_insensitive(
    monkeypatch,
):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        main.validate_api_security_configuration(" Production ", None)


@pytest.mark.parametrize("environment", ["development", "test", ""])
def test_nonproduction_does_not_require_supabase_configuration(environment):
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


def test_production_startup_fails_without_supabase_configuration(monkeypatch):
    monkeypatch.setenv("ASEA_ENV", "production")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
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

@pytest.mark.parametrize(
    "origins",
    [
        "*",
        "https://trusted.example/path",
        "https://trusted.example?debug=true",
        "https://user:password@trusted.example",
        "https://trusted.example,",
        "ftp://trusted.example",
        "https://trusted.example:invalid",
    ],
)
def test_cors_configuration_rejects_non_origin_values(monkeypatch, origins):
    monkeypatch.setenv("ASEA_CORS_ORIGINS", origins)

    with pytest.raises(RuntimeError, match="ASEA_CORS_ORIGINS"):
        main.configured_cors_origins()


@pytest.mark.parametrize(
    ("origins", "expected"),
    [
        ("https://app.example", ["https://app.example"]),
        (
            "https://app.example/, http://localhost:3000",
            ["https://app.example", "http://localhost:3000"],
        ),
    ],
)
def test_cors_configuration_accepts_exact_origins(
    monkeypatch, origins, expected
):
    monkeypatch.setenv("ASEA_CORS_ORIGINS", origins)

    assert main.configured_cors_origins() == expected


def test_api_uses_verified_supabase_identity_when_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    monkeypatch.setattr(
        main, "verify_supabase_access_token", lambda token: "verified-user-id"
    )
    client = TestClient(main.app)

    response = client.get(
        "/",
        headers={"Authorization": "Bearer verified-access-token"},
    )

    assert response.status_code == 200


def test_api_rejects_missing_supabase_access_token(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    client = TestClient(main.app)

    response = client.get("/")

    assert response.status_code == 401


def test_api_rejects_invalid_supabase_access_token(monkeypatch):
    from asea.api.supabase_auth import SupabaseAuthError

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    monkeypatch.setattr(
        main,
        "verify_supabase_access_token",
        lambda _token: (_ for _ in ()).throw(
            SupabaseAuthError("The Supabase access token is invalid or expired.")
        ),
    )
    client = TestClient(main.app)

    response = client.get(
        "/", headers={"Authorization": "Bearer expired-token"}
    )

    assert response.status_code == 401


@pytest.mark.parametrize("pepper", [None, "", "too-short"])
def test_cloud_storage_requires_worker_credential_pepper(monkeypatch, pepper):
    from asea.api.main import validate_cloud_worker_security_configuration

    validate = validate_cloud_worker_security_configuration
    with pytest.raises(RuntimeError, match="ASEA_WORKER_CREDENTIAL_PEPPER"):
        validate("postgresql://db.example/asea?sslmode=require", pepper)


def test_cloud_storage_accepts_sufficient_worker_credential_pepper():
    from asea.api.main import validate_cloud_worker_security_configuration

    validate_cloud_worker_security_configuration(
        "postgresql://db.example/asea?sslmode=require",
        "a-private-random-pepper-value-with-32-plus-characters",
    )


def test_local_sqlite_mode_does_not_require_worker_credential_pepper():
    from asea.api.main import validate_cloud_worker_security_configuration

    validate_cloud_worker_security_configuration(None, None)
