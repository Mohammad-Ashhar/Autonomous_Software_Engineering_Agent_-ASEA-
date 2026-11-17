import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from git import Repo

import asea.integrations.repair_publisher as publisher


def _git_repo(path: Path) -> Repo:
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "ASEA Test")
        config.set_value("user", "email", "asea-test@example.com")
    return repo


def test_approved_validated_repair_pushes_branch_and_opens_pull_request(
    tmp_path, monkeypatch
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
        return {"number": 42, "url": "https://github.com/example/python-demo/pull/42"}

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
                "bug_key": f"{original_file}::2::Incorrect positive boundary",
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

    result = publisher.publish_validated_repairs(state)

    assert result["status"] == "PULL_REQUEST_CREATED"
    assert result["repository"] == "example/python-demo"
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
