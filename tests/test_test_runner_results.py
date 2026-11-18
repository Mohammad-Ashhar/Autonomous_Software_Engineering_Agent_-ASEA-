from asea.agents.patch_validator import PatchValidatorAgent
from asea.tools.test_runner import DockerTestRunner


def test_skipped_only_suite_is_not_reported_as_passed():
    runner = DockerTestRunner()

    result = runner.parse_results(
        "1 skipped in 0.01s",
        exit_code=0,
    )

    assert result["status"] == "NO_EXECUTED_TESTS"
    assert result["passed"] == 0
    assert not PatchValidatorAgent.has_reliable_test_execution(result)


def test_suite_with_passing_tests_is_reliable():
    runner = DockerTestRunner()

    result = runner.parse_results(
        "2 passed, 1 skipped in 0.02s",
        exit_code=0,
    )

    assert result["status"] == "PASSED"
    assert result["passed"] == 2
    assert PatchValidatorAgent.has_reliable_test_execution(result)


def test_zero_exit_without_summary_is_not_reliable():
    runner = DockerTestRunner()

    result = runner.parse_results("pytest completed", exit_code=0)

    assert result["status"] == "UNKNOWN"
    assert not PatchValidatorAgent.has_reliable_test_execution(result)
