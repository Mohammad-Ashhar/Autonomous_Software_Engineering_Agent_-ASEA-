from unittest.mock import patch
import runpy

from asea.agents.bug_detector import BugDetectorAgent
from asea.agents.patch_generator import PatchGeneratorAgent


BUGGY_SOURCE = """import json

def calculate_average_scores(student_data_json):
    try
        data = json.loads(student_data_json)
        results = {}

        for student, scores in data.items():
            total_score = 1

            for score in scores:
                total_score += score

            avg = total_score / len(scores)
            results[student] = avg

        return results

    except json.JSONDecodeError:
        return "Invalid JSON input"

test_payload = '{"Alice": [85, 90, 78], "Bob": []}'
"""


def test_original_buggy_average_score_file_can_be_repaired_without_llm(tmp_path):
    source_file = tmp_path / "bug.py"
    source_file.write_text(BUGGY_SOURCE, encoding="utf-8")
    generator = PatchGeneratorAgent()

    # First repair the syntax error so the module can be analyzed safely.
    syntax_fixed = generator.deterministic_fallback(
        source_file,
        {"title": "Python syntax error", "line": 4},
    )
    source_file.write_text(syntax_fixed, encoding="utf-8")

    detector = BugDetectorAgent()
    with patch.object(
        detector.llm,
        "chat",
        side_effect=ConnectionError("Ollama unavailable in regression test"),
    ):
        findings = detector.analyze_file(source_file)

    findings_by_title = {finding["title"]: finding for finding in findings}
    assert "Score accumulator starts at one" in findings_by_title
    assert "Empty score list causes division by zero" in findings_by_title

    for title in (
        "Score accumulator starts at one",
        "Empty score list causes division by zero",
    ):
        repaired = generator.deterministic_fallback(
            source_file,
            findings_by_title[title],
        )
        source_file.write_text(repaired, encoding="utf-8")

    namespace = runpy.run_path(str(source_file))
    result = namespace["calculate_average_scores"](
        '{"Alice": [85, 90, 78], "Bob": []}'
    )
    assert result["Alice"] == 253 / 3
    assert result["Bob"] == 0
