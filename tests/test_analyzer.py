
import json

from asea.tools.analyzer import RepositoryAnalyzer


def test_repository_analyzer(tmp_path):

    # Create a sample repository
    project = tmp_path / "sample_project"
    project.mkdir()

    source = """
class Calculator:
    def add(self, a, b):
        return a + b

def multiply(a, b):
    return a * b

import math
"""

    (project / "calculator.py").write_text(source)

    # Initialize analyzer
    analyzer = RepositoryAnalyzer(
        workspace=tmp_path / "workspace"
    )

    output = tmp_path / "artifacts" / "repo_map.json"

    # Analyze repository
    result = analyzer.analyze_local_repository(
        project,
        output
    )

    # Verify statistics
    assert result["total_files"] == 1
    assert result["total_functions"] == 2
    assert result["total_classes"] == 1
    assert result["total_imports"] == 1

    # Verify JSON output
    assert output.exists()

    data = json.loads(output.read_text())

    assert data["repository"] == "sample_project"
    assert len(data["files"]) == 1


def test_repository_inventory_reports_languages_without_claiming_parser_support(
    tmp_path
):
    project = tmp_path / "mixed_project"
    project.mkdir()
    (project / "main.py").write_text("def run():\n    return True\n")
    (project / "app.ts").write_text("export const run = () => true;\n")
    (project / "Main.java").write_text("class Main {}\n")
    (project / "package.json").write_text('{"name": "mixed-project"}')

    ignored = project / "node_modules"
    ignored.mkdir()
    (ignored / "dependency.js").write_text("ignored")

    analyzer = RepositoryAnalyzer(workspace=tmp_path / "workspace")
    result = analyzer.analyze_local_repository(
        project,
        tmp_path / "artifacts" / "repo_map.json",
    )

    assert result["languages"] == {
        "Java": 1,
        "Python": 1,
        "TypeScript": 1,
    }
    assert result["language_count"] == 3
    assert result["parsed_languages"] == ["Python", "TypeScript"]
    assert result["repair_supported_languages"] == ["Python"]
    assert result["inventory_only_languages"] == ["Java"]
    assert result["manifests"] == ["package.json"]
    assert result["total_files"] == 2
    assert result["repository_file_count"] == 4
