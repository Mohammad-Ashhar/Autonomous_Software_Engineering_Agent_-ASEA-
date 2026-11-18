import hashlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from git import Repo

import asea.integrations.repair_publisher as publisher


def _git_repo(path: Path) -> Repo:
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "ASEA Test")
        config.set_value("user", "email", "asea-test@example.com")
    return repo


@pytest.mark.parametrize(
    ("pull_request_status", "expected_status"),
    [
        ("CREATED", "PULL_REQUEST_CREATED"),
        ("EXISTING", "PULL_REQUEST_EXISTS"),
    ],
)
def test_approved_validated_repair_pushes_branch_and_opens_pull_request(
    tmp_path, monkeypatch, pull_request_status, expected_status
):
    remote_path = tmp_path / "remote.git"
    seed_path = tmp_path / "seed"
    seed = _git_repo(seed_path)
    (seed_path / "demo_repo").mkdir()
    (seed_path / "demo_repo" / "logic.py").write_text(
        "def is_positive(value):\n    return value >= 0\n",
        encoding="utf-8",
    )
    seed.index.add(["demo_repo/logic.py"])
    seed.index.commit("Initial repository")
    bare_remote = Repo.clone_from(
        str(seed_path),
        remote_path,
        bare=True,
    )

    source_path = tmp_path / "source"
    source = Repo.clone_from(str(remote_path), source_path)
    source.remotes.origin.set_url(
        "https://github.com/example/python-demo.git"
    )
    original_file = source_path / "demo_repo" / "logic.py"

    working_path = tmp_path / "validated-workspace"
    working_path.mkdir()
    working_file = working_path / "demo_repo" / "logic.py"
    working_file.parent.mkdir()
    working_file.write_text(
        "def is_positive(value):\n    return value > 0\n",
        encoding="utf-8",
    )

    class FakeGithubRepository:
        full_name = "example/python-demo"
        default_branch = "master"
        clone_url = str(remote_path)

        def get_branch(self, branch_name):
            assert branch_name == self.default_branch

    fake_repository = FakeGithubRepository()

    class FakeGithub:
        def __init__(self, auth):
            assert auth == "fake-token"

        def get_repo(self, repository_name):
            assert repository_name == "example/python-demo"
            return fake_repository

    fake_github_module = ModuleType("github")
    fake_github_module.Auth = SimpleNamespace(Token=lambda token: token)
    fake_github_module.Github = FakeGithub
    monkeypatch.setitem(sys.modules, "github", fake_github_module)
    monkeypatch.setattr(
        publisher,
        "get_settings",
        lambda: SimpleNamespace(github_token="fake-token"),
    )

    pull_request_calls = []

    def fake_create_pull_request(**kwargs):
        pull_request_calls.append(kwargs)
        return {
            "status": pull_request_status,
            "number": 42,
            "url": "https://github.com/example/python-demo/pull/42",
        }

    monkeypatch.setattr(
        publisher,
        "create_pull_request",
        fake_create_pull_request,
    )

    state = {
        "status": "COMPLETED",
        "repo_path": str(source_path),
        "working_repo_path": str(working_path),
        "working_test_results": {
            "success": True,
            "status": "PASSED",
            "passed": 6,
            "failed": 0,
            "total": 6,
        },
        "repaired_bugs": [
            {
                "status": "REPAIRED",
                "bug_key": "demo_repo/logic.py::2::Incorrect positive boundary",
                "attempts": 1,
                "title": "Incorrect positive boundary",
                "file": str(original_file),
            }
        ],
        "review_history": [
            {
                "bug_key": "demo_repo/logic.py::2::Incorrect positive boundary",
                "decision": "approved",
                "attempt": 1,
            }
        ],
    }

    result = publisher.publish_validated_repairs(state, job_id="stable-job-123")

    assert result["status"] == expected_status
    assert result["repository"] == "example/python-demo"
    assert result["branch"].startswith("asea/repair-")
    expected_suffix = hashlib.sha256(b"stable-job-123").hexdigest()[:16]
    assert result["branch"] == f"asea/repair-{expected_suffix}"
    assert result["files"] == ["demo_repo/logic.py"]
    assert result["pull_request"]["number"] == 42
    assert len(pull_request_calls) == 1
    assert pull_request_calls[0]["head_branch"] == result["branch"]

    pushed_branch = bare_remote.branches[result["branch"]]
    assert pushed_branch.commit.tree / "demo_repo" / "logic.py"
    assert (
        (pushed_branch.commit.tree / "demo_repo" / "logic.py").data_stream.read()
        .decode("utf-8")
        == "def is_positive(value):\n    return value > 0\n"
    )

    # Re-running the same job must reuse its branch and avoid another commit.
    retry_result = publisher.publish_validated_repairs(
        state,
        job_id="stable-job-123",
    )
    assert retry_result["branch"] == result["branch"]
    assert retry_result["commit"] == result["commit"]
    assert len(pull_request_calls) == 2


def test_publishing_is_blocked_when_final_validation_fails():
    state = {
        "status": "COMPLETED",
        "working_test_results": {"success": False, "status": "FAILED"},
    }

    with pytest.raises(RuntimeError, match="final tests did not pass"):
        publisher.publish_validated_repairs(state)


def test_publishing_is_blocked_without_human_approval(tmp_path, monkeypatch):
    original_path = tmp_path / "source"
    original = _git_repo(original_path)
    original.create_remote(
        "origin",
        "https://github.com/example/python-demo.git",
    )
    source_file = original_path / "logic.py"
    source_file.write_text("value = 1\\n", encoding="utf-8")

    working_path = tmp_path / "validated-workspace"
    working_path.mkdir()
    (working_path / "logic.py").write_text(
        "value = 2\\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        publisher,
        "get_settings",
        lambda: SimpleNamespace(github_token="fake-token"),
    )

    state = {
        "status": "COMPLETED",
        "repo_path": str(original_path),
        "working_repo_path": str(working_path),
        "working_test_results": {"success": True, "status": "PASSED"},
        "repaired_bugs": [
            {
                "status": "REPAIRED",
                "bug_key": "logic.py::1::Changed value",
                "attempts": 1,
                "file": str(source_file),
            }
        ],
        "review_history": [],
    }

    with pytest.raises(RuntimeError, match="No approved and validated"):
        publisher.publish_validated_repairs(state)
