import pytest

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
            "failed_tests": [
                "tests/test_math.py::test_add",
                "tests/test_math.py::test_subtract",
            ],
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

def test_patch_generator_uses_safe_even_check_fallback_on_llm_timeout(tmp_path):
    source = "def is_even(number):\n    return number % 2 == 1\n"
    file_path = tmp_path / "logic.py"
    file_path.write_text(source, encoding="utf-8")
    agent = PatchGeneratorAgent()

    def timeout(**_kwargs):
        raise TimeoutError("local model did not respond in time")

    agent.llm.chat = timeout
    result = agent.generate_patch(
        file_path,
        {"title": "Incorrect even-number check"},
    )

    assert result["generator"] == "deterministic_fallback"
    assert "-    return number % 2 == 1" in result["patch"]
    assert "+    return number % 2 == 0" in result["patch"]


def test_patch_generator_uses_safe_positive_boundary_fallback_on_llm_timeout(tmp_path):
    source = "def is_positive(number):\n    return number >= 0\n"
    file_path = tmp_path / "logic.py"
    file_path.write_text(source, encoding="utf-8")
    agent = PatchGeneratorAgent()

    def timeout(**_kwargs):
        raise TimeoutError("local model did not respond in time")

    agent.llm.chat = timeout
    result = agent.generate_patch(
        file_path,
        {"title": "Incorrect positive boundary"},
    )

    assert result["generator"] == "deterministic_fallback"
    assert "-    return number >= 0" in result["patch"]
    assert "+    return number > 0" in result["patch"]


def test_patch_generator_repairs_missing_try_colon_without_llm(tmp_path):
    source = "\n".join([
        "import json",
        "def calculate_average_scores(payload):",
        "    try",
        "        return json.loads(payload)",
        "    except json.JSONDecodeError:",
        "        return {}",
        "",
    ])
    file_path = tmp_path / "bug.py"
    file_path.write_text(source, encoding="utf-8")
    agent = PatchGeneratorAgent()

    def invalid_model_output(**_kwargs):
        return "def still_broken(:"

    agent.llm.chat = invalid_model_output
    result = agent.generate_patch(
        file_path,
        {"title": "Python syntax error", "line": 3},
    )

    assert result["generator"] == "deterministic_fallback"
    assert "+    try:" in result["patch"]
    assert "-    try" in result["patch"]
    assert result["repair_scope"] == "syntax_only"



def test_patch_generator_records_invalid_llm_output_and_fallback_failure(tmp_path):
    file_path = tmp_path / "unknown.py"
    file_path.write_text("def calculate(value):\\n    return value + 1\\n", encoding="utf-8")
    agent = PatchGeneratorAgent()

    def invalid_model_output(**_kwargs):
        return "not valid python"

    agent.llm.chat = invalid_model_output

    with pytest.raises(RuntimeError, match="Deterministic fallback"):
        agent.generate_patch(
            file_path,
            {"title": "Unsupported calculation defect"},
        )

    assert agent.last_attempt_trace[0]["stage"] == "llm_generation"
    assert agent.last_attempt_trace[0]["status"] == "failed"
    assert "not valid python" in agent.last_attempt_trace[0]["response_excerpt"]
    assert agent.last_attempt_trace[1]["stage"] == "deterministic_fallback"
    assert agent.last_attempt_trace[1]["status"] == "failed"


def test_patch_generator_records_successful_llm_generation(tmp_path):
    file_path = tmp_path / "logic.py"
    file_path.write_text("def add(a, b):\\n    return a - b\\n", encoding="utf-8")
    agent = PatchGeneratorAgent()
    agent.llm.chat = lambda **_kwargs: "def add(a, b):\\n    return a + b\\n"

    result = agent.generate_patch(
        file_path,
        {"title": "Incorrect addition"},
    )

    assert result["generator"] == "ollama"
    assert result["generation_trace"][0]["stage"] == "llm_generation"
    assert result["generation_trace"][0]["status"] == "succeeded"
    assert "return a + b" in result["generation_trace"][0]["response_excerpt"]
