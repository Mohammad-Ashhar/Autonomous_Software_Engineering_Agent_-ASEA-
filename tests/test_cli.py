import importlib

from typer.testing import CliRunner

cli = importlib.import_module("asea.cli")

runner = CliRunner()


def test_analyze_local_repository_prints_summary(monkeypatch, tmp_path):
    expected = {
        "repository": "sample",
        "path": str(tmp_path),
        "total_files": 2,
        "total_functions": 3,
        "total_classes": 1,
        "total_imports": 4,
        "output_file": str(tmp_path / "repo_map.json"),
    }
    monkeypatch.setattr(cli, "resolve_repository", lambda _: tmp_path)

    class FakeAnalyzer:
        def analyze_local_repository(self, path):
            assert path == tmp_path
            return expected

    monkeypatch.setattr(cli, "RepositoryAnalyzer", FakeAnalyzer)
    result = runner.invoke(cli.app, ["analyze", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Analysis completed successfully" in result.output
    assert "Python files" in result.output
    assert "Elapsed time" in result.output


def test_analyze_reports_errors_with_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "resolve_repository", lambda _: tmp_path)

    class BrokenAnalyzer:
        def analyze_local_repository(self, path):
            raise RuntimeError("parser unavailable")

    monkeypatch.setattr(cli, "RepositoryAnalyzer", BrokenAnalyzer)
    result = runner.invoke(cli.app, ["analyze", str(tmp_path)])

    assert result.exit_code == 1
    assert "parser unavailable" in result.output


def test_repair_reports_validated_result_without_publishing(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "resolve_repository", lambda _: tmp_path)
    monkeypatch.setattr(
        cli,
        "run_asea",
        lambda _: {
            "status": "COMPLETED",
            "detected_bugs": [{"title": "example"}],
            "repaired_bugs": [{"title": "example"}],
            "escalated_bugs": [],
            "patches": [{"patch": "diff"}],
            "working_test_results": {"passed": 5, "failed": 0},
        },
    )
    result = runner.invoke(cli.app, ["repair", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "All final validation checks passed" in result.output
    assert "5 passed, 0 failed" in result.output
    assert "Validated changes remain in the ASEA workspace." in result.output
    assert "requested." in result.output


def test_repair_failure_blocks_publish(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "resolve_repository", lambda _: tmp_path)
    monkeypatch.setattr(
        cli,
        "run_asea",
        lambda _: {
            "status": "COMPLETED_WITH_REMAINING_FAILURES",
            "detected_bugs": [],
            "repaired_bugs": [],
            "escalated_bugs": [],
            "patches": [],
            "working_test_results": {"passed": 2, "failed": 1},
        },
    )

    def unexpected_publish(_):
        raise AssertionError("publisher must not be called")

    monkeypatch.setattr(cli, "publish_validated_repairs", unexpected_publish)
    result = runner.invoke(
        cli.app,
        ["repair", str(tmp_path), "--publish"],
    )

    assert result.exit_code == 2
    assert "Publishing is disabled" in result.output


def test_status_displays_configuration(monkeypatch):
    monkeypatch.setattr(
        "asea.core.config.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "ollama_model": "test-model",
                "ollama_base_url": "http://localhost:11434/v1",
                "github_repository": "",
                "github_token": "",
            },
        )(),
    )
    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0, result.output
    assert "test-model" in result.output
    assert "Docker" in result.output

def test_repair_publishes_only_after_validated_success(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "resolve_repository", lambda _: tmp_path)
    monkeypatch.setattr(
        cli,
        "run_asea",
        lambda _: {
            "status": "COMPLETED",
            "detected_bugs": [{"title": "example"}],
            "repaired_bugs": [{"title": "example"}],
            "escalated_bugs": [],
            "patches": [{"patch": "diff"}],
            "working_test_results": {"passed": 5, "failed": 0},
        },
    )
    published = {}

    def fake_publish(state):
        published["state"] = state
        return {
            "branch": "asea/repair-example",
            "commit": "abc123",
            "pull_request": {"url": "https://github.com/example/repo/pull/1"},
        }

    monkeypatch.setattr(cli, "publish_validated_repairs", fake_publish)
    result = runner.invoke(cli.app, ["repair", str(tmp_path), "--publish"])

    assert result.exit_code == 0, result.output
    assert published["state"]["status"] == "COMPLETED"
    assert "asea/repair-example" in result.output
    assert "abc123" in result.output
    assert "https://github.com/example/repo/pull/1" in result.output

