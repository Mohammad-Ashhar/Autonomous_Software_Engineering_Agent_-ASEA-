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


def test_local_paths_are_preserved_after_trimming():
    assert main.validate_api_repository("  /tmp/my-repository  ") == (
        "/tmp/my-repository"
    )


def test_plain_https_github_url_is_accepted():
    url = "https://github.com/owner/repository"

    assert main.validate_api_repository(url) == url


@pytest.mark.parametrize(
    "repository",
    [
        "   ",
        "http://github.com/owner/repository",
        "ssh://git@github.com/owner/repository",
        "git://github.com/owner/repository",
        "github.com/owner/repository",
        "www.github.com/owner/repository",
        "https://example.com/owner/repository",
        "https://github.com/owner",
    ],
)
def test_malformed_remote_repository_is_rejected(repository):
    with pytest.raises(ValueError):
        main.validate_api_repository(repository)


class ForbiddenJobStore:
    def create_job(self, *_args, **_kwargs):
        raise AssertionError("A rejected repository must not create a job.")


@pytest.mark.parametrize(
    "repository",
    [
        "http://github.com/owner/repository",
        "github.com/owner/repository",
        "https://example.com/owner/repository",
    ],
)
def test_repair_rejects_bad_repository_before_creating_a_job(
    api_client, monkeypatch, repository
):
    monkeypatch.setattr(main, "repair_jobs", ForbiddenJobStore())

    response = api_client.post("/repair", json={"repository": repository})

    assert response.status_code == 400
    assert response.json()["detail"]


class ForbiddenAnalyzer:
    def __init__(self):
        raise AssertionError("A rejected repository must not be analyzed.")


def test_analyze_rejects_unsupported_scheme_as_bad_request(api_client, monkeypatch):
    monkeypatch.setattr(main, "RepositoryAnalyzer", ForbiddenAnalyzer)

    response = api_client.post(
        "/analyze", json={"repository": "http://github.com/owner/repository"}
    )

    assert response.status_code == 400
    assert "HTTPS" in response.json()["detail"]


def test_analyze_rejects_scheme_less_github_url(api_client, monkeypatch):
    monkeypatch.setattr(main, "RepositoryAnalyzer", ForbiddenAnalyzer)

    response = api_client.post(
        "/analyze", json={"repository": "github.com/owner/repository"}
    )

    assert response.status_code == 400


class StubAnalyzer:
    def analyze_local_repository(self, repository):
        return {"repository": repository, "files": []}


def test_analyze_still_accepts_local_paths(api_client, monkeypatch):
    monkeypatch.setattr(main, "RepositoryAnalyzer", StubAnalyzer)

    response = api_client.post(
        "/analyze", json={"repository": "  /tmp/local-repository  "}
    )

    assert response.status_code == 200
    assert response.json()["analysis"]["repository"] == "/tmp/local-repository"
