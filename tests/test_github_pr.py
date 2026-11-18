from types import SimpleNamespace

import pytest

from asea.integrations.github_pr import create_pull_request


def _repository(existing_pulls=None):
    calls = {"get_pulls": [], "create_pull": []}

    class FakeRepository:
        owner = SimpleNamespace(login="example")

        def get_pulls(self, **kwargs):
            calls["get_pulls"].append(kwargs)
            return existing_pulls or []

        def create_pull(self, **kwargs):
            calls["create_pull"].append(kwargs)
            return SimpleNamespace(
                number=12,
                title=kwargs["title"],
                html_url="https://github.com/example/project/pull/12",
            )

    return FakeRepository(), calls


def test_create_pull_request_returns_existing_open_pull_request():
    existing = SimpleNamespace(
        number=7,
        title="Existing repair",
        html_url="https://github.com/example/project/pull/7",
    )
    repository, calls = _repository([existing])

    result = create_pull_request(
        repository=repository,
        head_branch="asea/repair-123",
        base_branch="main",
        title="ASEA repair",
        body="Validated repair",
    )

    assert result == {
        "status": "EXISTING",
        "number": 7,
        "title": "Existing repair",
        "url": "https://github.com/example/project/pull/7",
        "head_branch": "asea/repair-123",
        "base_branch": "main",
    }
    assert calls["get_pulls"] == [
        {
            "state": "open",
            "head": "example:asea/repair-123",
            "base": "main",
        }
    ]
    assert calls["create_pull"] == []


def test_create_pull_request_creates_when_no_open_match_exists():
    repository, calls = _repository()

    result = create_pull_request(
        repository=repository,
        head_branch="asea/repair-456",
        base_branch="main",
        title="ASEA repair",
        body="Validated repair",
    )

    assert result["status"] == "CREATED"
    assert result["number"] == 12
    assert result["head_branch"] == "asea/repair-456"
    assert result["base_branch"] == "main"
    assert len(calls["get_pulls"]) == 1
    assert calls["create_pull"] == [
        {
            "title": "ASEA repair",
            "body": "Validated repair",
            "head": "asea/repair-456",
            "base": "main",
        }
    ]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("head_branch", " ", "Source branch cannot be empty."),
        ("base_branch", "", "Target branch cannot be empty."),
        ("title", " ", "Pull Request title cannot be empty."),
    ],
)
def test_create_pull_request_rejects_missing_required_values(
    field, value, message
):
    repository, calls = _repository()
    arguments = {
        "repository": repository,
        "head_branch": "asea/repair-789",
        "base_branch": "main",
        "title": "ASEA repair",
        "body": "Validated repair",
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=message):
        create_pull_request(**arguments)

    assert calls["get_pulls"] == []
    assert calls["create_pull"] == []
