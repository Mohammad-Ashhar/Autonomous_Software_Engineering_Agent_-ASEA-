import pytest

from asea.tools import runner_factory


def test_selects_python_runner_for_python_repository(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("print('hello')\n")
    monkeypatch.setattr(
        runner_factory,
        "DockerTestRunner",
        lambda: "python-runner",
    )
    assert runner_factory.get_repository_test_runner(tmp_path) == "python-runner"


def test_selects_node_runner_for_plain_javascript_repository(tmp_path, monkeypatch):
    (tmp_path / "index.js").write_text("console.log('hello')\n")
    monkeypatch.setattr(
        runner_factory,
        "NodeTestRunner",
        lambda: "node-runner",
    )
    assert runner_factory.get_repository_test_runner(tmp_path) == "node-runner"


def test_selects_composite_runner_for_mixed_runtime_repositories(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')\n")
    (tmp_path / "index.js").write_text("console.log('hello')\n")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        runner_factory,
        "DockerTestRunner",
        lambda: "python-runner",
    )
    monkeypatch.setattr(
        runner_factory,
        "NodeTestRunner",
        lambda: "node-runner",
    )
    runner = runner_factory.get_repository_test_runner(tmp_path)
    assert isinstance(runner, runner_factory.CompositeTestRunner)
    assert runner.python_runner == "python-runner"
    assert runner.node_runner == "node-runner"
    monkeypatch.undo()


def test_composite_runner_requires_every_runtime_to_pass():
    class StubRunner:
        def __init__(self, result):
            self.result = result

        def run_tests(self, repository_path):
            return self.result

    runner = runner_factory.CompositeTestRunner(
        StubRunner({"success": True, "status": "PASSED"}),
        StubRunner({"success": False, "status": "NO_TESTS"}),
    )
    result = runner.run_tests("unused")
    assert result["success"] is False
    assert result["status"] == "FAILED"
    assert result["runtimes"]["python"]["success"] is True
    assert result["runtimes"]["javascript"]["success"] is False


def test_composite_runner_passes_only_when_every_runtime_passes():
    class StubRunner:
        def run_tests(self, repository_path):
            return {"success": True, "status": "PASSED", "failed": 0, "errors": 0}

    result = runner_factory.CompositeTestRunner(
        StubRunner(), StubRunner()
    ).run_tests("unused")
    assert result["success"] is True
    assert result["status"] == "PASSED"


def test_ignores_generated_and_dependency_directories(tmp_path, monkeypatch):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dependency.py").write_text("pass\n")
    (tmp_path / "index.js").write_text("console.log('hello')\n")
    monkeypatch.setattr(
        runner_factory,
        "NodeTestRunner",
        lambda: "node-runner",
    )
    assert runner_factory.get_repository_test_runner(tmp_path) == "node-runner"
