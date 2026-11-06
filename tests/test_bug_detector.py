
import ast
from pathlib import Path
from unittest.mock import patch

from asea.agents.bug_detector import BugDetectorAgent


def test_correct_is_even_is_not_detected():
    agent = BugDetectorAgent()

    source = """
def is_even(number: int) -> bool:
    return number % 2 == 0
"""

    tree = ast.parse(source)

    findings = agent.detect_ast_bugs(
        tree,
        Path("logic.py")
    )

    assert findings == []


def test_correct_is_positive_is_not_detected():
    agent = BugDetectorAgent()

    source = """
def is_positive(number: int) -> bool:
    return number > 0
"""

    tree = ast.parse(source)

    findings = agent.detect_ast_bugs(
        tree,
        Path("logic.py")
    )

    assert findings == []


def test_incorrect_is_even_is_detected():
    agent = BugDetectorAgent()

    source = """
def is_even(number: int) -> bool:
    return number % 2 == 1
"""

    tree = ast.parse(source)

    findings = agent.detect_ast_bugs(
        tree,
        Path("logic.py")
    )

    assert len(findings) == 1
    assert findings[0]["title"] == "Incorrect even-number check"
    assert findings[0]["classification"] == "confirmed_bug"


def test_incorrect_is_positive_is_detected():
    agent = BugDetectorAgent()

    source = """
def is_positive(number: int) -> bool:
    return number < 0
"""

    tree = ast.parse(source)

    findings = agent.detect_ast_bugs(
        tree,
        Path("logic.py")
    )

    assert len(findings) == 1
    assert findings[0]["title"] == "Incorrect positive-number check"
    assert findings[0]["classification"] == "confirmed_bug"


def test_zero_even_assertion_is_not_reported_as_ast_bug(tmp_path):
    agent = BugDetectorAgent()

    test_file = tmp_path / "test_logic.py"

    test_file.write_text(
        """
from logic import is_even

def test_zero_is_even():
    assert is_even(0) is True
""",
        encoding="utf-8"
    )

    with patch.object(
        agent.llm,
        "chat",
        return_value=(
            '{"bugs": [{"title": "Division by zero in is_even function", '
            '"line": 4, "severity": "high", '
            '"classification": "potential_issue", "confidence": 0.99, '
            '"description": "The function attempts to divide by zero.", '
            '"suggestion": "Handle division by zero."}]}'
        )
    ):
        findings = agent.analyze_file(test_file)

    assert findings == []
