import ast
from pathlib import Path
from unittest.mock import patch

from asea.agents.bug_detector import BugDetectorAgent


def test_ast_detector_does_not_assume_function_names_prove_bugs():
    agent = BugDetectorAgent()
    source = """
def is_even(number: int) -> bool:
    return number % 2 == 1

def is_positive(number: int) -> bool:
    return number < 0
"""
    assert agent.detect_ast_bugs(ast.parse(source), Path("logic.py")) == []


def test_llm_failure_is_reported_as_partial_not_no_bugs(tmp_path):
    agent = BugDetectorAgent()
    source = tmp_path / "logic.py"
    source.write_text("def calculate(value):\n    return value * 2\n", encoding="utf-8")

    with patch.object(agent.llm, "chat", side_effect=RuntimeError("model unavailable")):
        result = agent.analyze_file_with_status(source)

    assert result["status"] == "partial"
    assert result["findings"] == []
    assert "LLM analysis failed" in result["error"]


def test_invalid_python_is_reported_as_failed(tmp_path):
    agent = BugDetectorAgent()
    source = tmp_path / "broken.py"
    source.write_text("def broken(:\n    pass\n", encoding="utf-8")

    result = agent.analyze_file_with_status(source)

    assert result["status"] == "failed"
    assert "syntax error" in result["error"].lower()


def test_successful_empty_llm_result_is_completed(tmp_path):
    agent = BugDetectorAgent()
    source = tmp_path / "clean.py"
    source.write_text("def double(value):\n    return value * 2\n", encoding="utf-8")

    with patch.object(agent.llm, "chat", return_value='{"bugs": []}'):
        result = agent.analyze_file_with_status(source)

    assert result["status"] == "completed"
    assert result["findings"] == []
    assert result["error"] is None
