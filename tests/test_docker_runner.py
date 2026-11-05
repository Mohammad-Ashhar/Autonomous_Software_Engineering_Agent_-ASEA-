
from asea.tools.test_runner import DockerTestRunner


def test_docker_sandbox_execution(tmp_path):

    project = tmp_path / "demo"
    project.mkdir()

    (project / "calculator.py").write_text(
        "def add(a, b):\n    return a + b\n"
    )

    (project / "test_calculator.py").write_text(
        "from calculator import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )

    runner = DockerTestRunner(timeout=30)

    result = runner.run_tests(project)

    assert result["success"] is True
    assert result["status"] == "PASSED"
    assert result["total"] == 1
    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["timed_out"] is False
    assert result["duration"] > 0
