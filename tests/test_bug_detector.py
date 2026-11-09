
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


def test_mutable_default_argument_is_detected():
    agent = BugDetectorAgent()
    tree = ast.parse(
        "def collect(items=[]):\n    items.append(1)\n    return items\n"
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    assert any(item["title"] == "Mutable default argument" for item in findings)
    assert next(
        item for item in findings
        if item["title"] == "Mutable default argument"
    )["classification"] == "confirmed_bug"


def test_unreachable_statement_is_reported():
    agent = BugDetectorAgent()
    tree = ast.parse(
        "def calculate(value):\n"
        "    return value\n"
        "    print('unreachable')\n"
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    assert any(item["title"] == "Unreachable statement" for item in findings)


def test_assert_false_is_reported_as_potential_issue():
    agent = BugDetectorAgent()
    tree = ast.parse(
        "def process():\n"
        "    assert False, 'not implemented'\n"
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    finding = next(
        item for item in findings
        if item["title"] == "Unconditional assertion failure"
    )
    assert finding["classification"] == "potential_issue"


def test_bug_detector_integrates_cross_file_findings(tmp_path):
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "api.py").write_text(
        "def send(user):\n    return user\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "from .api import send\n"
        "def run():\n"
        "    return send('alice', 'email')\n",
        encoding="utf-8",
    )

    agent = BugDetectorAgent()
    with patch.object(agent, "analyze_file", return_value=[]):
        result = agent.run({"repo_path": str(tmp_path)})

    assert result["dependency_analysis"]["module_count"] == 3
    assert result["dependency_analysis"]["dependency_count"] == 1
    assert result["dependency_analysis"]["cross_file_issue_count"] == 1
    assert result["confirmed_bug_count"] == 1
    assert result["detected_bugs"][0]["source"] == "dependency_ast"
