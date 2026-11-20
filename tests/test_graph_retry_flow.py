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


def test_intermediate_syntax_repair_reanalyzes_without_marking_bug_repaired(
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

    class IntermediateValidator:
        def validate_patch(self, **_kwargs):
            return {
                "status": "PATCH_INTERMEDIATE",
                "patch_applied": True,
                "tests_passed": False,
                "reason": "Syntax parsed; semantic checks pending.",
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

    monkeypatch.setattr(graph, "PatchValidatorAgent", IntermediateValidator)
    monkeypatch.setattr(graph, "BugDetectorAgent", FollowUpDetector)
    result = graph.patch_validation_node(state)

    assert result["current_validation"]["status"] == "PATCH_INTERMEDIATE"
    assert len(result["prioritized_bugs"]) == 2
    assert result["repaired_bugs"] == []
    assert result["processed_bug_keys"] == [key]
    assert graph.route_after_validation({
        "current_validation": {"status": "PATCH_INTERMEDIATE"},
    }) == "next_bug"



def test_final_validation_overwrites_stale_test_results_and_refreshes_analysis(
    tmp_path, monkeypatch
):
    working_repository = tmp_path / "working"
    working_repository.mkdir()
    final_tests = {
        "status": "PASSED",
        "success": True,
        "total": 2,
        "passed": 2,
        "failed": 0,
        "errors": 0,
    }

    class PassingRunner:
        def run_tests(self, _repository):
            return final_tests

    class DependencyAnalyzer:
        def analyze(self, repository):
            assert repository == working_repository
            return {
                "module_count": 1,
                "dependency_count": 0,
                "cross_file_findings": [],
                "dependency_map": {},
                "parse_errors": [],
            }

    monkeypatch.setattr(graph, "DockerTestRunner", PassingRunner)
    monkeypatch.setattr(graph, "PythonDependencyAnalyzer", DependencyAnalyzer)
    result = graph.final_validation_node({
        "working_repo_path": str(working_repository),
        "test_results": {"status": "FAILED", "success": False},
        "working_test_results": {"status": "FAILED", "success": False},
        "baseline_test_results": {"status": "FAILED", "success": False},
        "dependency_analysis": {
            "parse_errors": [{"file": "bug.py", "error": "expected ':'"}],
        },
        "errors": [],
    })

    assert result["status"] == "COMPLETED"
    assert result["test_results"] == final_tests
    assert result["working_test_results"] == final_tests
    assert result["final_validation_results"] == final_tests
    assert result["dependency_analysis"]["parse_errors"] == []
    assert result["post_repair_analysis"]["module_count"] == 1


def test_final_validation_does_not_claim_success_when_no_tests_passed(
    tmp_path, monkeypatch
):
    working_repository = tmp_path / "working"
    working_repository.mkdir()

    class NoTestsRunner:
        def run_tests(self, _repository):
            return {
                "status": "NO_TESTS",
                "success": False,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "errors": 0,
            }

    class DependencyAnalyzer:
        def analyze(self, _repository):
            return {
                "module_count": 0,
                "dependency_count": 0,
                "cross_file_findings": [],
                "dependency_map": {},
                "parse_errors": [],
            }

    monkeypatch.setattr(graph, "DockerTestRunner", NoTestsRunner)
    monkeypatch.setattr(graph, "PythonDependencyAnalyzer", DependencyAnalyzer)
    result = graph.final_validation_node({
        "working_repo_path": str(working_repository),
        "errors": [],
    })

    assert result["status"] == "COMPLETED_WITH_REMAINING_FAILURES"
    assert result["test_results"]["status"] == "NO_TESTS"
    assert result["test_results"]["success"] is False



def test_partial_validation_refreshes_baseline_for_next_repair(
    tmp_path, monkeypatch
):
    candidate_tests = {
        "status": "FAILED",
        "success": False,
        "total": 2,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "failed_tests": ["test_empty_scores"],
        "timed_out": False,
    }
    state, _, key = _state(tmp_path)
    state["patches"] = [{
        "bug_key": key,
        "repair_scope": "complete",
        "patch": "diff",
        "attempt": 1,
    }]
    state["detected_bugs"] = [state["current_bug"]]
    state["prioritized_bugs"] = [state["current_bug"]]
    state["processed_bug_keys"] = []
    state["repaired_bugs"] = []
    state["validation_results"] = []
    state["working_test_results"] = {
        "status": "FAILED",
        "total": 2,
        "failed": 2,
        "errors": 0,
        "failed_tests": ["test_average", "test_empty_scores"],
    }

    class PartialValidator:
        def validate_patch(self, **_kwargs):
            return {
                "status": "PATCH_INTERMEDIATE",
                "patch_applied": True,
                "tests_passed": False,
                "regression_free": True,
                "test_results": candidate_tests,
            }

    monkeypatch.setattr(graph, "PatchValidatorAgent", PartialValidator)
    result = graph.patch_validation_node(state)

    assert result["working_test_results"] == candidate_tests
    assert result["processed_bug_keys"] == [key]
    assert result["repaired_bugs"] == []
    assert result["current_validation"]["status"] == "PATCH_INTERMEDIATE"
    assert graph.route_after_validation(result) == "next_bug"



def test_duplicate_repair_diff_stops_without_repeating_human_review(
    tmp_path, monkeypatch
):
    feedback = {
        "status": "PATCH_REJECTED",
        "reason": "The candidate introduced new test failures.",
        "test_results": {"failed": 2, "errors": 0, "total": 3},
    }
    state, _, key = _state(tmp_path, feedback, retries=1, max_retries=3)
    same_patch = "--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-return 1\n+return 2\n"
    state["patches"] = [{
        "bug_key": key,
        "patch": same_patch,
        "attempt": 1,
    }]

    class RepeatingGenerator:
        def generate_patch(self, **_kwargs):
            return {
                "file": "module.py",
                "patch": same_patch,
                "status": "PATCH_GENERATED",
            }

    monkeypatch.setattr(graph, "PatchGeneratorAgent", RepeatingGenerator)
    result = graph.patch_generation_node(state)

    assert result["status"] == "patch_generation_failed"
    assert result["retry_counts"][key] == 3
    assert result["patches"][-1]["duplicate_patch"] is True
    assert "identical diff" in result["current_validation"]["reason"]
    assert graph.route_after_generation(result) == "escalate"


def test_validation_node_prints_failure_reason_and_test_counts(
    tmp_path, monkeypatch, capsys
):
    state, _, key = _state(tmp_path)
    state["patches"] = [{
        "bug_key": key,
        "patch": "diff",
        "attempt": 1,
    }]

    class RejectingValidator:
        def validate_patch(self, **_kwargs):
            return {
                "status": "PATCH_REJECTED",
                "patch_applied": False,
                "reason": "Candidate added a failing test.",
                "test_results": {
                    "passed": 1,
                    "failed": 2,
                    "errors": 0,
                    "total": 3,
                    "failed_tests": ["test_average", "test_empty_scores"],
                },
            }

    monkeypatch.setattr(graph, "PatchValidatorAgent", RejectingValidator)
    graph.patch_validation_node(state)
    output = capsys.readouterr().out

    assert "PATCH VALIDATION: PATCH_REJECTED" in output
    assert "failed=2" in output
    assert "test_empty_scores" in output


def test_final_validation_promotes_intermediate_repairs_after_full_suite_passes(
    tmp_path, monkeypatch
):
    working_repository = tmp_path / "working"
    working_repository.mkdir()
    source_file = tmp_path / "repo" / "module.py"
    source_file.parent.mkdir()
    source_file.write_text("def value():\\n    return 1\\n", encoding="utf-8")

    bugs = [
        {
            "file": str(source_file),
            "line": 2,
            "title": "Syntax repair",
            "severity": "high",
        },
        {
            "file": str(source_file),
            "line": 3,
            "title": "Accumulator repair",
            "severity": "high",
        },
        {
            "file": str(source_file),
            "line": 4,
            "title": "Empty input repair",
            "severity": "high",
        },
    ]
    keys = [graph.get_bug_key(bug) for bug in bugs]
    validations = [
        {
            "bug_key": keys[0],
            "bug_title": bugs[0]["title"],
            "status": "PATCH_INTERMEDIATE",
            "attempt": 1,
        },
        {
            "bug_key": keys[1],
            "bug_title": bugs[1]["title"],
            "status": "PATCH_INTERMEDIATE",
            "attempt": 2,
        },
        {
            "bug_key": keys[2],
            "bug_title": bugs[2]["title"],
            "status": "PATCH_VALIDATED",
            "attempt": 1,
        },
    ]
    existing_repair = {
        "bug_key": keys[2],
        "title": bugs[2]["title"],
        "severity": "high",
        "file": str(source_file),
        "status": "REPAIRED",
        "attempts": 1,
    }
    existing_event = {
        "agent": "patch_validation",
        "event": "bug_repaired",
        "bug_key": keys[2],
    }

    class PassingRunner:
        def run_tests(self, _repository):
            return {
                "status": "PASSED",
                "success": True,
                "total": 2,
                "passed": 2,
                "failed": 0,
                "errors": 0,
            }

    class DependencyAnalyzer:
        def analyze(self, _repository):
            return {
                "module_count": 1,
                "dependency_count": 0,
                "cross_file_findings": [],
                "dependency_map": {},
                "parse_errors": [],
            }

    monkeypatch.setattr(graph, "DockerTestRunner", PassingRunner)
    monkeypatch.setattr(graph, "PythonDependencyAnalyzer", DependencyAnalyzer)
    state = {
        "working_repo_path": str(working_repository),
        "detected_bugs": bugs,
        "prioritized_bugs": bugs,
        "validation_results": validations,
        "repaired_bugs": [existing_repair],
        "audit_log": [existing_event],
        "errors": [],
    }

    result = graph.final_validation_node(state)

    assert result["status"] == "COMPLETED"
    assert {item["bug_key"] for item in result["repaired_bugs"]} == set(keys)
    assert len(result["repaired_bugs"]) == 3
    repaired_events = [
        item for item in result["audit_log"]
        if item.get("event") == "bug_repaired"
    ]
    assert {item["bug_key"] for item in repaired_events} == set(keys)
    assert len(repaired_events) == 3
    promoted_events = [
        item for item in repaired_events
        if item.get("confirmed_by_final_validation")
    ]
    assert {item["bug_key"] for item in promoted_events} == set(keys[:2])

    # Re-running final validation must not duplicate repair or audit records.
    repeated = graph.final_validation_node({**state, **result})
    assert len(repeated["repaired_bugs"]) == 3
    assert len([
        item for item in repeated["audit_log"]
        if item.get("event") == "bug_repaired"
    ]) == 3
