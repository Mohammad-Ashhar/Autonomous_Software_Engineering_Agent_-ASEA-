from pathlib import Path

from asea.agents.patch_validator import PatchValidatorAgent


def _setup_repositories(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"
    original.mkdir()
    working.mkdir()
    (original / "bug.py").write_text("value = 1\n", encoding="utf-8")
    (working / "bug.py").write_text("value = 1\n", encoding="utf-8")
    return original, working


def _patch_result():
    return {
        "file": "bug.py",
        "patch": "synthetic patch",
        "repair_scope": "complete",
        "attempt": 1,
    }


def test_existing_test_failures_cannot_be_accepted_as_validated(
    tmp_path, monkeypatch
):
    original, working = _setup_repositories(tmp_path)

    class FailingRunner:
        def run_tests(self, _repository):
            return {
                "status": "FAILED",
                "success": False,
                "total": 1,
                "passed": 0,
                "failed": 0,
                "errors": 1,
                "failed_tests": [],
                "timed_out": False,
            }

    def apply_candidate(_self, candidate, _patch):
        (candidate / "bug.py").write_text("value = 2\n", encoding="utf-8")

    monkeypatch.setattr(PatchValidatorAgent, "apply_patch", apply_candidate)
    agent = PatchValidatorAgent()
    agent.test_runner = FailingRunner()

    result = agent.validate_patch(
        repository_path=original,
        working_repo_path=working,
        patch_result=_patch_result(),
        baseline_test_results={
            "status": "FAILED",
            "total": 1,
            "failed": 0,
            "errors": 1,
            "failed_tests": [],
        },
    )

    assert result["status"] == "PATCH_REJECTED"
    assert result["patch_applied"] is False
    assert result["tests_passed"] is False
    assert result["regression_free"] is True
    assert "test suite still fails" in result["reason"]
    assert (working / "bug.py").read_text(encoding="utf-8") == "value = 1\n"


def test_passing_candidate_is_promoted(tmp_path, monkeypatch):
    original, working = _setup_repositories(tmp_path)

    class PassingRunner:
        def run_tests(self, _repository):
            return {
                "status": "PASSED",
                "success": True,
                "total": 1,
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "failed_tests": [],
                "timed_out": False,
            }

    def apply_candidate(_self, candidate, _patch):
        (candidate / "bug.py").write_text("value = 2\n", encoding="utf-8")

    monkeypatch.setattr(PatchValidatorAgent, "apply_patch", apply_candidate)
    agent = PatchValidatorAgent()
    agent.test_runner = PassingRunner()

    result = agent.validate_patch(
        repository_path=original,
        working_repo_path=working,
        patch_result=_patch_result(),
        baseline_test_results={"status": "FAILED", "total": 1, "errors": 1},
    )

    assert result["status"] == "PATCH_VALIDATED"
    assert result["tests_passed"] is True
    assert (working / "bug.py").read_text(encoding="utf-8") == "value = 2\n"
