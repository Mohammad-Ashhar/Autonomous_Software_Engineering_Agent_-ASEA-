
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



def test_mutable_keyword_only_default_is_detected():
    agent = BugDetectorAgent()
    tree = ast.parse(
        "def collect(*, items=[]):\n"
        "    return items\n"
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    finding = next(
        item for item in findings
        if item["title"] == "Mutable default argument"
    )
    assert finding["line"] == 1
    assert "items" in finding["description"]


def test_mutable_constructor_defaults_are_detected():
    agent = BugDetectorAgent()
    tree = ast.parse(
        "def collect(items=list(), *, options=dict(), tags=set()):\n"
        "    return items, options, tags\n"
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    mutable_findings = [
        item for item in findings
        if item["title"] == "Mutable default argument"
    ]
    assert len(mutable_findings) == 3
    assert all(
        item["classification"] == "confirmed_bug"
        for item in mutable_findings
    )


def test_immutable_and_none_defaults_are_not_reported():
    agent = BugDetectorAgent()
    tree = ast.parse(
        "def collect(items=None, count=0, names=()):\n"
        "    return items, count, names\n"
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    assert not any(
        item["title"] == "Mutable default argument"
        for item in findings
    )

def test_unreachable_statement_is_reported():
    agent = BugDetectorAgent()
    tree = ast.parse(
        "def calculate(value):\n"
        "    return value\n"
        "    print('unreachable')\n"
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    assert any(item["title"] == "Unreachable statement" for item in findings)


def test_statement_after_unconditional_break_is_reported():
    agent = BugDetectorAgent()
    tree = ast.parse(
        "def process(items):\\n"
        "    for item in items:\\n"
        "        break\\n"
        "        print(item)\\n"
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    unreachable = [
        item for item in findings
        if item["title"] == "Unreachable statement"
    ]
    assert len(unreachable) == 1
    assert unreachable[0]["line"] == 4


def test_statement_after_unconditional_continue_is_reported():
    agent = BugDetectorAgent()
    tree = ast.parse(
        "def process(items):\\n"
        "    for item in items:\\n"
        "        continue\\n"
        "        print(item)\\n"
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    unreachable = [
        item for item in findings
        if item["title"] == "Unreachable statement"
    ]
    assert len(unreachable) == 1
    assert unreachable[0]["line"] == 4


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



def test_llm_provider_failure_preserves_deterministic_ast_findings(tmp_path):
    agent = BugDetectorAgent()
    source = tmp_path / "logic.py"
    source.write_text(
        "def is_even(number):\n    return number % 2 == 1\n",
        encoding="utf-8",
    )

    def fail_request(**_kwargs):
        raise ConnectionError("LLM provider is unavailable")

    with patch.object(agent.llm, "chat", side_effect=fail_request):
        findings = agent.analyze_file(source)

    assert len(findings) == 1
    assert findings[0]["title"] == "Incorrect even-number check"
    assert findings[0]["source"] == "ast"



def test_syntax_error_is_reported_without_calling_llm(tmp_path):
    agent = BugDetectorAgent()
    source = tmp_path / "broken.py"
    source.write_text(
        "def calculate(value):\n"
        "    try\n"
        "        return value\n",
        encoding="utf-8",
    )

    with patch.object(agent.llm, "chat") as chat:
        findings = agent.analyze_file(source)

    chat.assert_not_called()
    assert len(findings) == 1
    assert findings[0]["title"] == "Python syntax error"
    assert findings[0]["line"] == 2
    assert findings[0]["classification"] == "confirmed_bug"
    assert findings[0]["source"] == "python_parser"

def test_score_calculation_defects_are_detected_from_source_evidence():
    agent = BugDetectorAgent()
    source = '''
import json

def calculate_average_scores(student_data_json):
    data = json.loads(student_data_json)
    results = {}
    for student, scores in data.items():
        total_score = 1
        for score in scores:
            total_score += score
        avg = total_score / len(scores)
        results[student] = avg
    return results

test_payload = '{"Alice": [85, 90, 78], "Bob": []}'
'''
    findings = agent.detect_ast_bugs(
        ast.parse(source),
        Path("bug.py"),
    )

    titles = {finding["title"] for finding in findings}
    assert "Score accumulator starts at one" in titles
    assert "Empty score list causes division by zero" in titles
    assert all(
        finding["classification"] == "confirmed_bug"
        for finding in findings
        if finding["title"] in titles
    )

def test_bug_detector_respects_declared_latin1_encoding(tmp_path):
    agent = BugDetectorAgent()
    source = tmp_path / "legacy.py"
    source.write_bytes(
        b"# coding: latin-1\n"
        b"def caf\xe9():\n"
        b"    return 'ol\xe9'\n"
    )

    with patch.object(agent.llm, "chat", return_value='{"bugs": []}') as chat:
        findings = agent.analyze_file(source)

    chat.assert_called_once()
    assert findings == []


def test_bug_detector_skips_unknown_source_encoding_without_false_syntax_bug(tmp_path):
    agent = BugDetectorAgent()
    source = tmp_path / "unknown.py"
    source.write_bytes(
        b"# coding: made-up-encoding\n"
        b"def example():\n"
        b"    return 1\n"
    )

    with patch.object(agent.llm, "chat") as chat:
        findings = agent.analyze_file(source)

    chat.assert_not_called()
    assert findings == []


def test_literal_zero_divisors_are_detected_without_llm():
    agent = BugDetectorAgent()
    tree = ast.parse(
        """
def calculate(value):
    first = value / 0
    second = value // -0
    third = value % False
    return first, second, third
"""
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))
    zero_divisors = [
        item for item in findings
        if item["title"] == "Literal zero divisor"
    ]

    assert len(zero_divisors) == 3
    assert all(item["classification"] == "potential_issue" for item in zero_divisors)
    assert [item["line"] for item in zero_divisors] == [3, 4, 5]


def test_nonzero_divisors_are_not_reported():
    agent = BugDetectorAgent()
    tree = ast.parse(
        """
def calculate(value):
    return value / 2 + value // 3 + value % 5
"""
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    assert not any(item["title"] == "Literal zero divisor" for item in findings)


def test_literal_zero_divisors_are_detected_at_module_and_class_scope():
    agent = BugDetectorAgent()
    tree = ast.parse(
        """
module_value = 10 / 0

class Calculator:
    class_value = 10 // 0
"""
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))
    zero_divisors = [
        item for item in findings
        if item["title"] == "Literal zero divisor"
    ]

    assert [item["line"] for item in zero_divisors] == [2, 5]
    assert all(item["classification"] == "potential_issue" for item in zero_divisors)


def test_literal_zero_divisor_is_reported_once_for_nested_functions():
    agent = BugDetectorAgent()
    tree = ast.parse(
        """
def outer():
    def inner():
        return 1 / 0
    return inner()
"""
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))
    zero_divisors = [
        item for item in findings
        if item["title"] == "Literal zero divisor"
    ]

    assert len(zero_divisors) == 1
    assert zero_divisors[0]["line"] == 4


def test_nested_mutable_values_inside_tuple_defaults_are_detected():
    agent = BugDetectorAgent()
    tree = ast.parse(
        """
def positional(options=([],)):
    return options

def keyword_only(*, settings=({"enabled": True},)):
    return settings

def constructor(options=tuple([set()])):
    return options
"""
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))
    mutable_defaults = [
        item for item in findings
        if item["title"] == "Mutable default argument"
    ]

    assert len(mutable_defaults) == 3
    assert all(item["classification"] == "confirmed_bug" for item in mutable_defaults)


def test_tuple_defaults_without_nested_mutables_are_not_reported():
    agent = BugDetectorAgent()
    tree = ast.parse(
        """
def calculate(options=(1, "safe", None)):
    return options
"""
    )

    findings = agent.detect_ast_bugs(tree, Path("logic.py"))

    assert not any(item["title"] == "Mutable default argument" for item in findings)
