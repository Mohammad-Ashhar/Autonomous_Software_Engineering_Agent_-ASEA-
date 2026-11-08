from pathlib import Path

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


def test_blocks_mixed_runtime_repositories(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')\n")
    (tmp_path / "index.js").write_text("console.log('hello')\n")
    with pytest.raises(
        runner_factory.UnsupportedTestEnvironment,
        match="Mixed Python/JavaScript",
    ):
        runner_factory.get_repository_test_runner(tmp_path)


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
