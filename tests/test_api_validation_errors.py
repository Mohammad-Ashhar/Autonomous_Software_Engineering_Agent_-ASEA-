import pytest
from fastapi.testclient import TestClient

from asea.api import main


@pytest.fixture
def api_client(monkeypatch):
    """Build a client without ambient authentication configuration."""

    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    return TestClient(main.app)


def assert_shared_error_envelope(response):
    """Validation failures must use the same public shape as other errors."""

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"detail", "request_id"}
    assert isinstance(body["detail"], str)
    assert body["detail"]
    assert "input" not in body
    assert "loc" not in body
    assert response.headers["x-request-id"] == body["request_id"]
    assert response.headers["cache-control"] == "no-store"


def test_missing_required_field_uses_shared_error_envelope(api_client):
    response = api_client.post("/repair", json={})

    assert_shared_error_envelope(response)
    assert "repository" in response.json()["detail"]


def test_blank_repository_uses_shared_error_envelope(api_client):
    response = api_client.post("/repair", json={"repository": "   "})

    assert_shared_error_envelope(response)
    assert "repository" in response.json()["detail"]


def test_invalid_review_decision_uses_shared_error_envelope(api_client):
    response = api_client.post(
        "/repair/review-job/review",
        json={"decision": "maybe", "feedback": ""},
    )

    assert_shared_error_envelope(response)
    assert "decision" in response.json()["detail"]


def test_invalid_query_parameter_uses_shared_error_envelope(api_client):
    response = api_client.get("/repairs?limit=not-a-number")

    assert_shared_error_envelope(response)
    assert "limit" in response.json()["detail"]
