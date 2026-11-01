
from asea.tools.test_runner import DockerTestRunner


def test_docker_detects_failing_test(tmp_path):

    project = tmp_path / "buggy_project"
    project.mkdir()

    (project / "calculator.py").write_text(
        "def add(a, b):\n    return a - b\n"
    )

    (project / "test_calculator.py").write_text(
        "from calculator import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )

    runner = DockerTestRunner(timeout=30)

    result = runner.run_tests(project)

    assert result["success"] is False
    assert result["status"] == "FAILED"
    assert result["failed"] == 1
    assert result["total"] == 1
    assert len(result["failed_tests"]) == 1
