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
