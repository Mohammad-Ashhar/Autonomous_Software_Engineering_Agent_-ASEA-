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

def test_repository_without_python_files_is_unsupported(tmp_path):
    (tmp_path / "index.ts").write_text("export const value = 1;\n")
    agent = BugDetectorAgent()

    result = agent.run({"repo_path": str(tmp_path)})

    assert result["analysis_status"] == "unsupported"
    assert result["analysis_summary"]["files_discovered"] == 0
    assert result["detected_bugs"] == []


def test_repository_level_status_preserves_partial_file_failures(tmp_path):
    (tmp_path / "one.py").write_text("def one():\n    return 1\n")
    (tmp_path / "two.py").write_text("def two():\n    return 2\n")
    agent = BugDetectorAgent()

    def partial_result(path):
        return {
            "file": str(path),
            "findings": [],
            "status": "partial" if path.name == "one.py" else "completed",
            "error": "LLM unavailable" if path.name == "one.py" else None,
        }

    with patch.object(agent, "analyze_file_with_status", side_effect=partial_result):
        result = agent.run({"repo_path": str(tmp_path)})

    assert result["analysis_status"] == "partial"
    assert result["analysis_summary"]["files_partial"] == 1
    assert result["analysis_summary"]["files_completed"] == 1
    assert len(result["analysis_errors"]) == 1

def test_detector_analyzes_javascript_with_registered_adapter(tmp_path):
    agent = BugDetectorAgent()
    source = tmp_path / "logic.js"
    source.write_text(
        "export function divide(a, b) {\\n"
        "  return a / b;\\n"
        "}\\n",
        encoding="utf-8",
    )
    response = {
        "bugs": [{
            "title": "Division may receive zero",
            "line": 2,
            "severity": "medium",
            "classification": "potential_issue",
            "confidence": 0.7,
            "description": "The divisor is used without a guard.",
            "suggestion": "Validate the divisor where required.",
        }]
    }

    with patch.object(agent.llm, "chat", return_value=__import__("json").dumps(response)):
        result = agent.analyze_file_with_status(source)

    assert result["status"] == "completed"
    assert result["language"] == "JavaScript"
    assert result["findings"][0]["line"] == 2
