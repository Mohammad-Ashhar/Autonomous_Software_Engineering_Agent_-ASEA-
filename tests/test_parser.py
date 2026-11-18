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
            "column": 12,
            "message": "invalid syntax",
        }
    ]


def test_repository_map_uses_safe_shared_python_file_discovery(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "def run():\\n    return 1\\n",
        encoding="utf-8",
    )
    (project / ".venv").mkdir()
    (project / ".venv" / "ignored.py").write_text(
        "broken syntax (",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.py"
    outside.write_text("def external():\\n    pass\\n", encoding="utf-8")
    (project / "linked.py").symlink_to(outside)

    output = tmp_path / "map.json"
    repo_map = PythonRepositoryParser().build_repository_map(project, output)

    assert [item["file"] for item in repo_map["files"]] == ["main.py"]


def test_parser_skips_oversized_python_files_with_clear_diagnostic(tmp_path):
    file_path = tmp_path / "large.py"
    file_path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))

    result = PythonRepositoryParser().parse_file(file_path)

    assert result["functions"] == []
    assert result["classes"] == []
    assert result["imports"] == []
    assert result["analysis_errors"] == [
        "File exceeds the 2 MiB analysis limit."
    ]
    assert result["syntax_errors"] == []


def test_parser_respects_declared_latin1_source_encoding(tmp_path):
    file_path = tmp_path / "legacy.py"
    file_path.write_bytes(
        b"# -*- coding: latin-1 -*-\n"
        b"def caf\xe9():\n"
        b"    return 'ol\xe9'\n"
    )

    result = PythonRepositoryParser().parse_file(file_path)

    assert result["analysis_errors"] == []
    assert result["syntax_errors"] == []
    assert result["functions"][0]["name"] == "café"


def test_parser_extracts_async_functions_and_nested_class_methods(tmp_path):
    file_path = tmp_path / "async_module.py"
    file_path.write_text(
        "class Outer:\n"
        "    class Inner:\n"
        "        async def load(self):\n"
        "            return True\n"
        "\n"
        "async def fetch():\n"
        "    return None\n",
        encoding="utf-8",
    )

    result = PythonRepositoryParser().parse_file(file_path)

    assert result["syntax_errors"] == []
    assert result["analysis_errors"] == []
    assert result["classes"] == [
        {
            "name": "Outer",
            "start_line": 1,
            "end_line": 4,
        },
        {
            "name": "Inner",
            "start_line": 2,
            "end_line": 4,
        },
    ]
    assert result["functions"] == [
        {
            "name": "load",
            "class": "Inner",
            "start_line": 3,
            "end_line": 4,
        },
        {
            "name": "fetch",
            "class": None,
            "start_line": 6,
            "end_line": 7,
        },
    ]


def test_parser_reports_unknown_source_encoding_without_crashing(tmp_path):
    file_path = tmp_path / "unknown_encoding.py"
    file_path.write_bytes(
        b"# coding: made-up-encoding\n"
        b"def example():\n"
        b"    return 1\n"
    )

    result = PythonRepositoryParser().parse_file(file_path)

    assert result["functions"] == []
    assert result["classes"] == []
    assert result["imports"] == []
    assert result["syntax_errors"] == []
    assert result["analysis_errors"] == [
        "File encoding could not be detected: SyntaxError."
    ]


def test_parser_does_not_classify_nested_local_functions_as_methods(tmp_path):
    file_path = tmp_path / "nested_functions.py"
    file_path.write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        def helper():\n"
        "            return True\n"
        "        return helper()\n"
        "\n"
        "def outer():\n"
        "    class LocalService:\n"
        "        def execute(self):\n"
        "            return 1\n"
        "    return LocalService()\n",
        encoding="utf-8",
    )

    result = PythonRepositoryParser().parse_file(file_path)

    assert result["syntax_errors"] == []
    assert result["analysis_errors"] == []
    assert result["functions"] == [
        {
            "name": "run",
            "class": "Service",
            "start_line": 2,
            "end_line": 5,
        },
        {
            "name": "helper",
            "class": None,
            "start_line": 3,
            "end_line": 4,
        },
        {
            "name": "outer",
            "class": None,
            "start_line": 7,
            "end_line": 10,
        },
        {
            "name": "execute",
            "class": "LocalService",
            "start_line": 9,
            "end_line": 10,
        },
    ]
