
from unittest.mock import patch

from asea.tools.static_analyzer import StaticAnalyzer


def test_static_analyzer_combines_findings(tmp_path):

    analyzer = StaticAnalyzer()

    ruff_result = {
        "tool": "ruff",
        "available": True,
        "findings": [
            {
                "tool": "ruff",
                "rule": "F401",
                "message": "Unused import",
                "file": "main.py",
                "line": 1,
                "severity": "warning"
            }
        ],
        "error": None
    }

    semgrep_result = {
        "tool": "semgrep",
        "available": True,
        "findings": [],
        "error": None
    }

    with patch.object(
        analyzer,
        "run_ruff",
        return_value=ruff_result
    ), patch.object(
        analyzer,
        "run_semgrep",
        return_value=semgrep_result
    ):

        result = analyzer.analyze(tmp_path)

    assert result["total_findings"] == 1
    assert result["findings"][0]["rule"] == "F401"
    assert result["ruff"]["available"] is True
    assert result["semgrep"]["available"] is True
