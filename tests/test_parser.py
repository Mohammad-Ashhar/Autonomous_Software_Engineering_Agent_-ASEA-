
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


def test_parser_handles_javascript_and_typescript(tmp_path):
    from asea.tools.parser import RepositoryParser

    (tmp_path / "app.js").write_text(
        "import { sum } from './math.js';\n"
        "export function run(value) { return sum(value, 1); }\n"
        "class Worker {}\n"
    )
    (tmp_path / "app.ts").write_text(
        "export function greet(name: string): string { "
        "return 'Hello ' + name; }\n"
    )

    repo_map = RepositoryParser().build_repository_map(
        tmp_path, tmp_path / "repo_map.json"
    )

    by_file = {item["file"]: item for item in repo_map["files"]}
    assert by_file["app.js"]["language"] == "JavaScript"
    assert by_file["app.js"]["syntax_valid"] is True
    assert by_file["app.js"]["functions"][0]["name"] == "run"
    assert by_file["app.js"]["classes"][0]["name"] == "Worker"
    assert by_file["app.js"]["repair_supported"] is False
    assert by_file["app.ts"]["language"] == "TypeScript"
    assert by_file["app.ts"]["syntax_valid"] is True


def test_parser_reports_invalid_typescript(tmp_path):
    from asea.tools.parser import RepositoryParser

    source = tmp_path / "broken.ts"
    source.write_text("export function ( {")
    parsed = RepositoryParser().parse_file(source)

    assert parsed["syntax_valid"] is False
    assert parsed["syntax_error"]
