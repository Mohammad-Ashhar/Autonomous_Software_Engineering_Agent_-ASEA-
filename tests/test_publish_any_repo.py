"""Publishing must target whichever GitHub repo was analysed."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from git import Actor, Repo

from asea.integrations import repair_publisher as publisher

ACTOR = Actor("t", "t@example.com")


def make_upstream(tmp_path: Path, name: str, branch: str):
    """A bare 'GitHub' remote holding one file on ``branch``."""

    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    repo = Repo.init(seed, initial_branch=branch)
    (seed / "app.py").write_text("def f():\n    return 1\n")
    repo.index.add(["app.py"])
    repo.index.commit("init", author=ACTOR, committer=ACTOR)

    bare = tmp_path / f"{name}.git"
    Repo.clone_from(str(seed), str(bare), bare=True)
    return bare


def fake_github(monkeypatch, upstream, fork=None, can_push=True):
    calls = {}

    class FakeRepo:
        def __init__(self, full_name, path, owner):
            self.full_name = full_name
            self.clone_url = str(path)
            self.default_branch = "trunk"
            self.permissions = SimpleNamespace(push=can_push)
            self.owner = SimpleNamespace(login=owner)

        def get_branch(self, name):
            return name

    up = FakeRepo("octo/widgets", upstream, "octo")

    class FakeUser:
        login = "me"

        def create_fork(self, repo):
            calls["forked"] = True
            return FakeRepo("me/widgets", fork, "me")

    class FakeGithub:
        def __init__(self, auth=None):
            pass

        def get_repo(self, name):
            calls["repo"] = name
            return up

        def get_user(self):
            return FakeUser()

    import github

    monkeypatch.setattr(github, "Github", FakeGithub)

    def fake_pr(**kwargs):
        calls["pr"] = kwargs
        return {"url": "https://github.com/octo/widgets/pull/9"}

    monkeypatch.setattr(publisher, "create_pull_request", fake_pr)
    monkeypatch.setattr(
        publisher,
        "get_settings",
        lambda: SimpleNamespace(
            github_token="t0ken",
            # Deliberately a DIFFERENT repo/branch than the one analysed:
            github_repository="Mohammad-Ashhar/ASEA",
            github_base_branch="MohammadAshhar/deepseek-langraph",
        ),
    )
    return calls


def make_state(tmp_path):
    original = tmp_path / "octo__widgets"
    original.mkdir()
    repo = Repo.init(original)
    repo.create_remote("origin", "https://github.com/octo/widgets.git")
    (original / "app.py").write_text("def f():\n    return 1\n")

    working = tmp_path / "work"
    working.mkdir()
    (working / "app.py").write_text("def f():\n    return 2\n")

    key = "bug1"
    return {
        "status": "COMPLETED",
        "repo_path": str(original),
        "working_repo_path": str(working),
        "working_test_results": {"success": True},
        "repaired_bugs": [
            {
                "bug_key": key,
                "status": "REPAIRED",
                "attempts": 1,
                "file": str(original / "app.py"),
            }
        ],
        "review_history": [
            {"bug_key": key, "decision": "approved", "attempt": 1}
        ],
    }


def test_direct_push_uses_origin_repo_and_default_branch(
    tmp_path, monkeypatch
):
    upstream = make_upstream(tmp_path, "up", "trunk")
    calls = fake_github(monkeypatch, upstream)

    result = publisher.publish_validated_repairs(make_state(tmp_path))

    assert calls["repo"] == "octo/widgets"  # not the .env repository
    assert result["base_branch"] == "trunk"  # not the .env branch
    assert result["pushed_to_fork"] is False
    assert calls["pr"]["repository_name"] == "octo/widgets"
    assert calls["pr"]["head_owner"] is None
    assert result["branch"] in Repo(str(upstream)).heads


def test_fork_used_when_no_push_access(tmp_path, monkeypatch):
    upstream = make_upstream(tmp_path, "up", "trunk")
    fork = make_upstream(tmp_path, "fork", "trunk")
    calls = fake_github(monkeypatch, upstream, fork, can_push=False)

    result = publisher.publish_validated_repairs(make_state(tmp_path))

    assert calls["forked"]
    assert result["pushed_to_fork"] is True
    assert calls["pr"]["head_owner"] == "me"
    assert result["branch"] in Repo(str(fork)).heads
    assert result["branch"] not in Repo(str(upstream)).heads


def test_preexisting_failures_do_not_block_publishing(tmp_path, monkeypatch):
    upstream = make_upstream(tmp_path, "up", "trunk")
    fake_github(monkeypatch, upstream)

    state = make_state(tmp_path)
    state["status"] = "COMPLETED_WITH_REMAINING_FAILURES"
    failing = {"success": False, "failed": 1, "failed_tests": ["t::a"]}
    state["test_results"] = failing
    state["working_test_results"] = failing

    assert (
        publisher.publish_validated_repairs(state)["status"]
        == "PULL_REQUEST_CREATED"
    )


def test_new_failures_still_block_publishing(tmp_path):
    state = make_state(tmp_path)
    state["status"] = "COMPLETED_WITH_REMAINING_FAILURES"
    state["test_results"] = {"success": False, "failed": 1,
                             "failed_tests": ["t::a"]}
    state["working_test_results"] = {"success": False, "failed": 2,
                                     "failed_tests": ["t::a", "t::b"]}

    with pytest.raises(RuntimeError, match="did not complete"):
        publisher.publish_validated_repairs(state)
