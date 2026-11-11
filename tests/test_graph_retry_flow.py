import asea.core.graph as graph


def _state(tmp_path, validation=None, retries=1, max_retries=3):
    repository = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    repository.mkdir()
    workspace.mkdir()
    source = repository / "module.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    working_source = workspace / "module.py"
    working_source.write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )
    bug = {
        "file": str(source),
        "line": 2,
        "title": "Incorrect return value",
    }
    key = graph.get_bug_key(bug)
    return {
        "repo_path": str(repository),
        "working_repo_path": str(workspace),
        "current_bug": bug,
        "current_validation": validation or {},
        "retry_counts": {key: retries},
        "max_retries": max_retries,
        "patches": [],
        "errors": [],
    }, working_source, key


def test_retry_generation_uses_latest_workspace_and_validation_feedback(
    tmp_path, monkeypatch
):
    feedback = {
        "status": "PATCH_REJECTED",
        "reason": "The previous patch caused a regression.",
        "test_results": {"failed": 1, "total": 4},
    }
    state, working_source, key = _state(tmp_path, feedback)
    captured = {}

    class FakeGenerator:
        def generate_patch(self, **kwargs):
            captured.update(kwargs)
            return {
                "file": str(kwargs["file_path"]),
                "patch": "diff --git",
                "status": "PATCH_GENERATED",
            }

    monkeypatch.setattr(graph, "PatchGeneratorAgent", FakeGenerator)

    result = graph.patch_generation_node(state)

    assert captured["file_path"] == working_source
    assert captured["file_path"].read_text(encoding="utf-8").endswith(
        "return 2\n"
    )
    assert captured["previous_feedback"] == feedback
    assert captured["attempt"] == 2
    assert result["retry_counts"][key] == 2
    assert result["current_validation"] == {}


def test_retry_generation_preserves_reviewer_feedback(tmp_path, monkeypatch):
    feedback = {
        "status": "PATCH_REJECTED",
        "reason": "Reviewer rejected the patch.",
        "review_feedback": "Keep the public API unchanged.",
    }
    state, _, _ = _state(tmp_path, feedback)

    class FakeGenerator:
        def generate_patch(self, **kwargs):
            assert kwargs["previous_feedback"] == feedback
            return {"file": str(kwargs["file_path"]), "patch": "diff"}

    monkeypatch.setattr(graph, "PatchGeneratorAgent", FakeGenerator)

    result = graph.patch_generation_node(state)

    assert result["status"] == "patch_generation_completed"


def test_retry_routes_to_escalation_at_maximum_attempts(tmp_path):
    state, _, key = _state(tmp_path, retries=3, max_retries=3)
    state["status"] = "patch_generation_failed"

    assert graph.route_after_generation(state) == "escalate"
    assert graph.route_after_validation({
        "current_bug": state["current_bug"],
        "retry_counts": {key: 3},
        "max_retries": 3,
        "current_validation": {"status": "PATCH_REJECTED"},
    }) == "escalate"


def test_retry_routes_back_while_attempts_remain(tmp_path):
    state, _, key = _state(tmp_path, retries=2, max_retries=3)
    state["status"] = "patch_generation_failed"

    assert graph.route_after_generation(state) == "retry"
    assert graph.route_after_review({
        "patch_review_decision": "rejected",
        "current_bug": state["current_bug"],
        "retry_counts": {key: 2},
        "max_retries": 3,
    }) == "retry"



def test_baseline_runner_exception_allows_static_analysis(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()

    class FailingRunner:
        def run_tests(self, _repository):
            raise RuntimeError("Docker daemon unavailable")

    monkeypatch.setattr(graph, "DockerTestRunner", FailingRunner)
    state = {"repo_path": str(repository), "errors": []}

    result = graph.test_execution_node(state)

    assert result["status"] == "baseline_test_execution_failed"
    assert result["test_results"]["status"] == "UNKNOWN"
    assert result["test_results"]["success"] is False
    assert "Docker daemon unavailable" in result["errors"][-1]
    assert graph.route_after_test_execution(result) == "continue"


def test_unreliable_baseline_result_allows_static_analysis(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()

    class UnknownRunner:
        def run_tests(self, _repository):
            return {
                "success": False,
                "status": "UNKNOWN",
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "total": 0,
            }

    monkeypatch.setattr(graph, "DockerTestRunner", UnknownRunner)
    result = graph.test_execution_node({
        "repo_path": str(repository),
        "errors": [],
    })

    assert result["status"] == "baseline_test_execution_unavailable"
    assert graph.route_after_test_execution(result) == "continue"
    assert result["errors"]


def test_final_validation_runner_exception_returns_structured_failure(
    tmp_path, monkeypatch
):
    working_repository = tmp_path / "working"
    working_repository.mkdir()

    class FailingRunner:
        def run_tests(self, _repository):
            raise RuntimeError("Docker timed out")

    monkeypatch.setattr(graph, "DockerTestRunner", FailingRunner)
    result = graph.final_validation_node({
        "working_repo_path": str(working_repository),
        "errors": [],
    })

    assert result["status"] == "COMPLETED_WITH_REMAINING_FAILURES"
    assert result["working_test_results"]["status"] == "UNKNOWN"
    assert result["working_test_results"]["success"] is False
    assert "Docker timed out" in result["errors"][-1]


def test_syntax_only_validation_reanalyzes_file_for_hidden_bugs(
    tmp_path, monkeypatch
):
    state, working_source, key = _state(tmp_path)
    state["patches"] = [{
        "bug_key": key,
        "repair_scope": "syntax_only",
        "patch": "diff",
        "attempt": 1,
    }]
    state["detected_bugs"] = [state["current_bug"]]
    state["prioritized_bugs"] = [state["current_bug"]]
    state["processed_bug_keys"] = []
    state["repaired_bugs"] = []
    state["validation_results"] = []

    class ValidatingAgent:
        def validate_patch(self, **_kwargs):
            return {
                "status": "PATCH_VALIDATED",
                "test_results": {"status": "PASSED", "total": 1},
            }

    class FollowUpDetector:
        def analyze_file(self, file_path):
            assert file_path == working_source
            return [{
                "file": str(file_path),
                "line": 3,
                "title": "Possible division by zero",
                "classification": "confirmed_bug",
                "severity": "high",
            }]

    monkeypatch.setattr(graph, "PatchValidatorAgent", ValidatingAgent)
    monkeypatch.setattr(graph, "BugDetectorAgent", FollowUpDetector)

    result = graph.patch_validation_node(state)

    assert len(result["prioritized_bugs"]) == 2
    assert result["prioritized_bugs"][-1]["title"] == (
        "Possible division by zero"
    )
    assert result["repaired_bugs"][0]["status"] == "REPAIRED"
