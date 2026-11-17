
from asea.tools.parser import PythonRepositoryParser


def test_parser_extracts_functions_and_classes(tmp_path):

    source = """
class Calculator:
    def divide(self, a, b):
        return a / b

def calculate_average(numbers):
    return sum(numbers) / len(numbers)

import math
"""

    file_path = tmp_path / "calculator.py"
    file_path.write_text(source)

    parser = PythonRepositoryParser()
    result = parser.parse_file(file_path)

    assert len(result["classes"]) == 1
    assert result["classes"][0]["name"] == "Calculator"

    assert len(result["functions"]) == 2
    assert result["functions"][0]["name"] == "divide"
    assert result["functions"][1]["name"] == "calculate_average"

    assert "import math" in result["imports"]



def test_parser_reports_python_syntax_errors(tmp_path):
    file_path = tmp_path / "broken.py"
    file_path.write_text("def broken(:\\n    pass\\n", encoding="utf-8")

    result = PythonRepositoryParser().parse_file(file_path)

    assert result["syntax_errors"] == [
        {
            "line": 1,
            "column": 13,
            "message": "invalid syntax",
        }
    ]


def test_repository_map_uses_safe_shared_python_file_discovery(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def run():\\n    return 1\\n", encoding="utf-8")
    (project / ".venv").mkdir()
    (project / ".venv" / "ignored.py").write_text("broken syntax (", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("def external():\\n    pass\\n", encoding="utf-8")
    (project / "linked.py").symlink_to(outside)

    output = tmp_path / "map.json"
    repo_map = PythonRepositoryParser().build_repository_map(project, output)

    assert [item["file"] for item in repo_map["files"]] == ["main.py"]
