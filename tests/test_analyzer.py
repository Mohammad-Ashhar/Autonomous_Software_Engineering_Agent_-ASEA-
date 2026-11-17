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
    assert result["preflight"]["python_file_count"] == 1
    assert result["preflight"]["has_existing_tests"] is False

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


def test_preflight_reports_tests_manifests_and_configuration(tmp_path):
    project = tmp_path / "sample_project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_service.py").write_text(
        "import pytest\n\ndef test_answer():\n    assert 42 == 42\n",
        encoding="utf-8",
    )
    (project / "requirements-dev.txt").write_text(
        "pytest>=8\n",
        encoding="utf-8",
    )
    (project / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\n",
        encoding="utf-8",
    )
    (project / "app.py").write_text(
        "def answer():\n    return 42\n",
        encoding="utf-8",
    )

    result = RepositoryAnalyzer._repository_preflight(project)

    assert result["python_file_count"] == 2
    assert result["test_file_count"] == 1
    assert result["test_files"] == ["tests/test_service.py"]
    assert result["has_existing_tests"] is True
    assert result["test_frameworks"] == ["pytest"]
    assert result["test_configuration"] == ["pytest.ini"]
    assert result["dependency_manifests"] == ["requirements-dev.txt"]


def test_preflight_does_not_count_non_test_python_files_as_tests(tmp_path):
    project = tmp_path / "sample_project"
    project.mkdir()
    (project / "contest.py").write_text(
        "def score():\n    return 1\n",
        encoding="utf-8",
    )

    result = RepositoryAnalyzer._repository_preflight(project)

    assert result["test_file_count"] == 0
    assert result["has_existing_tests"] is False


def test_default_cloned_analysis_artifacts_are_unique_per_run(tmp_path, monkeypatch):
    from git import Repo

    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    project = workspace / "owner__sample"
    project.mkdir(parents=True)
    (project / "module.py").write_text(
        "def answer():\\n    return 42\\n",
        encoding="utf-8",
    )
    Repo.init(project)
    analyzer = RepositoryAnalyzer(workspace=workspace)

    first = analyzer.analyze_cloned_repository(project)
    second = analyzer.analyze_cloned_repository(project)

    assert first["output_file"] != second["output_file"]
    assert Path(first["output_file"]).is_file()
    assert Path(second["output_file"]).is_file()
