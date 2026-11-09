
import json
from pathlib import Path

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


def test_default_analysis_artifacts_are_unique_per_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "sample_project"
    project.mkdir()
    (project / "module.py").write_text(
        "def answer():\n    return 42\n",
        encoding="utf-8",
    )
    analyzer = RepositoryAnalyzer(workspace=tmp_path / "workspace")

    first = analyzer.analyze_local_repository(project)
    second = analyzer.analyze_local_repository(project)

    assert first["output_file"] != second["output_file"]
    assert Path(first["output_file"]).is_file()
    assert Path(second["output_file"]).is_file()
