import pytest

from asea.agents.patch_validator import PatchValidatorAgent


@pytest.mark.parametrize(
    "results",
    [
        {"status": "NO_TESTS", "total": 0, "success": False},
        {"status": "TIMEOUT", "total": 4, "timed_out": True},
        {"status": "PASSED", "total": 0, "success": True},
        {"status": "ERROR", "total": 3, "success": False},
        {},
    ],
)
def test_unreliable_test_runs_are_not_accepted(results):
    assert not PatchValidatorAgent.has_reliable_test_execution(results)


@pytest.mark.parametrize(
    "results",
    [
        {"status": "PASSED", "total": 12, "success": True},
        {
            "status": "FAILED",
            "total": 12,
            "failed": 1,
            "success": False,
        },
    ],
)
def test_executed_test_suites_are_reliable(results):
    assert PatchValidatorAgent.has_reliable_test_execution(results)


def test_existing_failures_can_be_preserved_without_new_regressions():
    baseline = {
        "failed": 2,
        "errors": 0,
        "failed_tests": ["test_a", "test_b"],
    }
    candidate = {
        "failed": 1,
        "errors": 0,
        "failed_tests": ["test_b"],
    }

    assert PatchValidatorAgent.has_no_new_failures(baseline, candidate)


def test_new_test_failure_is_a_regression():
    baseline = {
        "failed": 1,
        "errors": 0,
        "failed_tests": ["test_a"],
    }
    candidate = {
        "failed": 2,
        "errors": 0,
        "failed_tests": ["test_a", "test_new"],
    }

    assert not PatchValidatorAgent.has_no_new_failures(
        baseline,
        candidate
    )
