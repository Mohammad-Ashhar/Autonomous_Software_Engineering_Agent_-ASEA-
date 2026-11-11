import pytest

from asea.agents.patch_validator import PatchValidatorAgent


@pytest.mark.parametrize(
    "results",
    [
        {"status": "NO_TESTS", "total": 0, "success": False},
        {"status": "TIMEOUT", "total": 4, "timed_out": True},
        {"status": "PASSED", "total": 0, "success": True},
        {"status": "ERROR", "total": 3, "success": False},
        {},
    ],
)
def test_unreliable_test_runs_are_not_accepted(results):
    assert not PatchValidatorAgent.has_reliable_test_execution(results)


@pytest.mark.parametrize(
    "results",
    [
        {"status": "PASSED", "total": 12, "success": True},
        {
            "status": "FAILED",
            "total": 12,
            "failed": 1,
            "success": False,
        },
    ],
)
def test_executed_test_suites_are_reliable(results):
    assert PatchValidatorAgent.has_reliable_test_execution(results)


def test_existing_failures_can_be_preserved_without_new_regressions():
    baseline = {
        "failed": 2,
        "errors": 0,
        "failed_tests": ["test_a", "test_b"],
    }
    candidate = {
        "failed": 1,
        "errors": 0,
        "failed_tests": ["test_b"],
    }

    assert PatchValidatorAgent.has_no_new_failures(baseline, candidate)


def test_new_test_failure_is_a_regression():
    baseline = {
        "failed": 1,
        "errors": 0,
        "failed_tests": ["test_a"],
    }
    candidate = {
        "failed": 2,
        "errors": 0,
        "failed_tests": ["test_a", "test_new"],
    }

    assert not PatchValidatorAgent.has_no_new_failures(
        baseline,
        candidate
    )


def test_validated_patch_preserves_git_and_runtime_directories(tmp_path):
    class PassingRunner:
        def run_tests(self, repository_path):
            return {
                "status": "PASSED",
                "total": 1,
                "failed": 0,
                "errors": 0,
                "failed_tests": [],
                "success": True,
            }

    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    for directory, filename, value in (
        (".git", "HEAD", "ref: refs/heads/main\n"),
        ("artifacts", "run.json", '{"run": 1}'),
        ("workspaces", "state.txt", "keep"),
    ):
        target_dir = tmp_path / directory
        target_dir.mkdir()
        (target_dir / filename).write_text(value, encoding="utf-8")

    patch = """--- a/module.py
+++ b/module.py
@@ -1 +1 @@
-value = 1
+value = 2
"""
    # A predictable legacy backup name may contain user data and must
    # never be removed by patch promotion.
    preexisting_backup = tmp_path.parent / f"{tmp_path.name}_backup"
    preexisting_backup.mkdir()
    (preexisting_backup / "user-data.txt").write_text("preserve", encoding="utf-8")

    validator = PatchValidatorAgent()
    validator.test_runner = PassingRunner()
    result = validator.validate_patch(
        tmp_path,
        {"patch": patch},
        baseline_test_results={
            "failed": 0,
            "errors": 0,
            "failed_tests": [],
        },
    )

    assert result["status"] == "PATCH_VALIDATED"
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == "value = 2\n"
    git_head = (tmp_path / ".git" / "HEAD").read_text(encoding="utf-8")
    assert git_head == "ref: refs/heads/main\n"
    artifact = (tmp_path / "artifacts" / "run.json").read_text(encoding="utf-8")
    assert artifact == '{"run": 1}'
    assert (tmp_path / "workspaces" / "state.txt").read_text(encoding="utf-8") == "keep"
    backup_data = (preexisting_backup / "user-data.txt").read_text(encoding="utf-8")
    assert backup_data == "preserve"


def test_syntax_only_patch_is_intermediate_without_reliable_tests(tmp_path):
    source = tmp_path / "module.py"
    source.write_text("def value():\\n    try\\n        return 1\\n", encoding="utf-8")
    patch = """--- a/module.py
+++ b/module.py
@@ -1,3 +1,3 @@
 def value():
-    try
+    try:
         return 1
"""

    class TestsMustNotRun:
        def run_tests(self, _repository_path):
            raise AssertionError("Intermediate syntax repairs must not be marked complete by tests.")

    validator = PatchValidatorAgent()
    validator.test_runner = TestsMustNotRun()
    result = validator.validate_patch(
        tmp_path,
        {
            "patch": patch,
            "file": str(source),
            "repair_scope": "syntax_only",
        },
    )

    assert result["status"] == "PATCH_INTERMEDIATE"
    assert result["patch_applied"] is True
    assert result["tests_passed"] is False
    assert source.read_text(encoding="utf-8") == (
        "def value():\\n    try:\\n        return 1\\n"
    )
