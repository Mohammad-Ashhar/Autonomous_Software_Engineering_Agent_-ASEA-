from asea.agents.patch_generator import PatchGeneratorAgent


def test_retry_feedback_includes_validation_and_test_failure_details():
    feedback = {
        "status": "PATCH_REJECTED",
        "reason": "The candidate introduced a regression.",
        "test_results": {
            "passed": 4,
            "failed": 2,
            "errors": 1,
            "total": 7,
            "failed_tests": ["tests/test_math.py::test_add", "tests/test_math.py::test_subtract"],
            "output": "AssertionError: expected 4, got 5",
        },
    }

    formatted = PatchGeneratorAgent.format_retry_feedback(feedback)

    assert "PATCH_REJECTED" in formatted
    assert "introduced a regression" in formatted
    assert "passed=4, failed=2, errors=1, total=7" in formatted
    assert "tests/test_math.py::test_add" in formatted
    assert "AssertionError: expected 4, got 5" in formatted


def test_retry_feedback_is_bounded_to_recent_test_output():
    feedback = {
        "status": "PATCH_REJECTED",
        "test_results": {"output": "x" * 20000},
    }

    formatted = PatchGeneratorAgent.format_retry_feedback(
        feedback,
        max_output_chars=100
    )

    assert len(formatted) < 300
    assert "x" * 100 in formatted
    assert "x" * 101 not in formatted


def test_retry_feedback_handles_missing_previous_attempt():
    assert (
        PatchGeneratorAgent.format_retry_feedback(None)
        == "No previous attempt or validation feedback."
    )


def test_retry_feedback_includes_human_review_feedback():
    formatted = PatchGeneratorAgent.format_retry_feedback({
        "status": "PATCH_REJECTED",
        "reason": "Reviewer rejected the patch.",
        "review_feedback": "Keep the public API unchanged.",
    })

    assert "Keep the public API unchanged." in formatted
