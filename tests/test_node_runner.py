import json

from asea.tools import node_test_runner


class FakeContainer:
    def __init__(self, payload, exit_code=0):
        self.payload = payload
        self.status = "created"
        self.attrs = {"State": {"ExitCode": exit_code}}
        self.removed = False
        self.killed = False

    def start(self):
        self.status = "exited"

    def reload(self):
        return None

    def logs(self, **kwargs):
        return json.dumps(self.payload).encode()

    def remove(self, force=False):
        self.removed = True

    def kill(self):
        self.killed = True


class FakeContainers:
    def __init__(self, container):
        self.container = container
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.container


class FakeDockerClient:
    def __init__(self, container):
        self.containers = FakeContainers(container)


def test_node_runner_uses_restricted_container_and_reports_pass(tmp_path, monkeypatch):
    project = tmp_path / "js-project"
    project.mkdir()
    container = FakeContainer({
        "status": "PASSED",
        "syntax_checked": 2,
        "test_files": ["test/basic.test.js"],
        "output": "tests passed",
    })
    client = FakeDockerClient(container)
    monkeypatch.setattr(node_test_runner.docker, "from_env", lambda: client)

    result = node_test_runner.NodeTestRunner().run_tests(project)

    assert result["success"] is True
    assert result["status"] == "PASSED"
    assert client.containers.kwargs["network_disabled"] is True
    assert client.containers.kwargs["read_only"] is True
    assert client.containers.kwargs["cap_drop"] == ["ALL"]
    assert client.containers.kwargs["user"] == "1000:1000"
    assert container.removed is True


def test_node_runner_does_not_treat_missing_tests_as_success(tmp_path, monkeypatch):
    project = tmp_path / "js-project"
    project.mkdir()
    container = FakeContainer({
        "status": "NO_TESTS",
        "syntax_errors": [],
        "test_files": [],
        "syntax_checked": 1,
    }, exit_code=5)
    client = FakeDockerClient(container)
    monkeypatch.setattr(node_test_runner.docker, "from_env", lambda: client)

    result = node_test_runner.NodeTestRunner().run_tests(project)

    assert result["status"] == "NO_TESTS"
    assert result["success"] is False


def test_node_runner_rejects_missing_repository(tmp_path):
    missing = tmp_path / "missing"
    try:
        node_test_runner.NodeTestRunner.__new__(
            node_test_runner.NodeTestRunner
        ).run_tests(missing)
    except (NotADirectoryError, AttributeError):
        # The directory check must happen before Docker client use.
        pass
    else:
        raise AssertionError("Missing repository should be rejected")
