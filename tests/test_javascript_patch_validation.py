from asea.agents.patch_validator import PatchValidatorAgent


class PassingRunner:
    def __init__(self):
        self.calls = 0

    def run_tests(self, repository_path):
        self.calls += 1
        return {
            "status": "PASSED",
            "success": True,
            "failed": 0,
            "errors": 0,
            "failed_tests": [],
        }


def javascript_patch():
    return {
        "patch": (
            "--- a/src/math.js\n"
            "+++ b/src/math.js\n"
            "@@ -1 +1 @@\n"
            "-export function add(a, b) { return a - b; }\n"
            "+export function add(a, b) { return a + b; }\n"
        )
    }


def create_repositories(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"
    (original / "src").mkdir(parents=True)
    (working / "src").mkdir(parents=True)
    source = "export function add(a, b) { return a - b; }\n"
    (original / "src" / "math.js").write_text(source)
    (working / "src" / "math.js").write_text(source)
    return original, working


def test_javascript_patch_uses_node_runner_and_preserves_validated_copy(tmp_path):
    original, working = create_repositories(tmp_path)
    validator = PatchValidatorAgent.__new__(PatchValidatorAgent)
    validator.test_runner = PassingRunner()
    validator.node_test_runner = PassingRunner()

    result = validator.validate_patch(
        original,
        javascript_patch(),
        working,
        baseline_test_results={
            "status": "PASSED",
            "success": True,
            "failed": 0,
            "errors": 0,
            "failed_tests": [],
        },
    )

    assert result["status"] == "PATCH_VALIDATED"
    assert validator.node_test_runner.calls == 1
    assert validator.test_runner.calls == 0
    assert "a + b" in (working / "src" / "math.js").read_text()


def test_javascript_patch_is_rejected_without_passing_baseline(tmp_path):
    original, working = create_repositories(tmp_path)
    validator = PatchValidatorAgent.__new__(PatchValidatorAgent)
    validator.test_runner = PassingRunner()
    validator.node_test_runner = PassingRunner()

    result = validator.validate_patch(
        original,
        javascript_patch(),
        working,
        baseline_test_results={
            "status": "NO_TESTS",
            "success": False,
        },
    )

    assert result["status"] == "PATCH_REJECTED"
    assert "passing baseline" in result["reason"]
    assert validator.node_test_runner.calls == 0
    assert "a - b" in (working / "src" / "math.js").read_text()


def test_mixed_runtime_patch_runs_both_suites_before_preserving(tmp_path):
    original, working = create_repositories(tmp_path)
    (original / "service.py").write_text("def health():\n    return True\n")
    (working / "service.py").write_text("def health():\n    return True\n")
    validator = PatchValidatorAgent.__new__(PatchValidatorAgent)
    validator.test_runner = PassingRunner()
    validator.node_test_runner = PassingRunner()

    result = validator.validate_patch(
        original,
        javascript_patch(),
        working,
        baseline_test_results={
            "status": "PASSED",
            "success": True,
            "failed": 0,
            "errors": 0,
            "failed_tests": [],
            "runtimes": {
                "python": {"success": True},
                "javascript": {"success": True},
            },
        },
    )

    assert result["status"] == "PATCH_VALIDATED"
    assert validator.test_runner.calls == 1
    assert validator.node_test_runner.calls == 1
    assert "a + b" in (working / "src" / "math.js").read_text()
