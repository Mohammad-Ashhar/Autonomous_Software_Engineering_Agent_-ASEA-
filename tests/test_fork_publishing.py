from types import SimpleNamespace

from github.GithubException import GithubException

from asea.integrations.github_pr import create_pull_request
from asea.integrations.repair_publisher import resolve_publish_target


def test_writable_upstream_keeps_direct_publish_target():
    upstream = SimpleNamespace(
        permissions=SimpleNamespace(push=True),
        owner=SimpleNamespace(login="owner"),
    )

    target, head_owner, remote = resolve_publish_target(
        github_client=object(),
        repository=upstream,
        base_branch="main",
    )

    assert target is upstream
    assert head_owner is None
    assert remote == "origin"


def test_read_only_upstream_creates_and_uses_authenticated_users_fork():
    upstream = SimpleNamespace(
        permissions=SimpleNamespace(push=False),
        name="sample-project",
        full_name="upstream/sample-project",
    )
    fork = SimpleNamespace(
        full_name="contributor/sample-project",
        parent=SimpleNamespace(full_name=upstream.full_name),
        get_branch=lambda branch: assert_branch(branch),
    )

    def assert_branch(branch):
        assert branch == "main"

    class User:
        login = "contributor"

        def get_repo(self, name):
            assert name == "sample-project"
            raise GithubException(404, {"message": "Not Found"})

    class GithubClient:
        def get_user(self):
            return User()

    upstream.create_fork = lambda: fork

    target, head_owner, remote = resolve_publish_target(
        GithubClient(),
        upstream,
        "main",
    )

    assert target is fork
    assert head_owner == "contributor"
    assert remote == "fork"


def test_fork_pull_request_uses_owner_branch_head():
    calls = []

    class Upstream:
        owner = SimpleNamespace(login="original-owner")

        def get_pulls(self, **kwargs):
            calls.append(("get_pulls", kwargs))
            return []

        def create_pull(self, **kwargs):
            calls.append(("create_pull", kwargs))
            return SimpleNamespace(
                number=7,
                title=kwargs["title"],
                html_url="https://github.com/original-owner/sample-project/pull/7",
            )

    result = create_pull_request(
        repository=Upstream(),
        head_branch="asea/repair-123",
        base_branch="main",
        title="ASEA repair",
        body="Validated repair.",
        head_owner="contributor",
    )

    assert result["status"] == "CREATED"
    assert calls[0][1]["head"] == "contributor:asea/repair-123"
    assert calls[1][1]["head"] == "contributor:asea/repair-123"
    assert calls[1][1]["base"] == "main"
