import os
import shutil
from pathlib import Path

import pytest

from asea.agents.patch_validator import PatchValidatorAgent
from asea.tools.node_test_runner import NodeTestRunner


pytestmark = pytest.mark.skipif(
    os.environ.get("ASEA_RUN_DOCKER_INTEGRATION") != "1",
    reason="Requires the built Node Docker sandbox image.",
)


class UnusedPythonRunner:
    def run_tests(self, repository_path):
        raise AssertionError("Python runner must not be used for JavaScript.")


def test_dependency_free_npm_project_runs_and_validates_patch(tmp_path):
    fixture = (
        Path(__file__).parent
        / ".."
        / "fixtures"
        / "node_native_project"
    ).resolve()
    original = tmp_path / "original"
    working = tmp_path / "working"
    shutil.copytree(fixture, original)
    shutil.copytree(fixture, working)

    node_runner = NodeTestRunner()
    baseline = node_runner.run_tests(original)
    assert baseline["success"] is True
    assert baseline["package_manager"] == "npm"
    assert baseline["test_files"] == ["tests/math.test.js"]

    validator = PatchValidatorAgent.__new__(PatchValidatorAgent)
    validator.test_runner = UnusedPythonRunner()
    validator.node_test_runner = node_runner

    patch = {
        "patch": (
            "--- a/src/math.js\n"
            "+++ b/src/math.js\n"
            "@@ -1,3 +1,3 @@\n"
            " export function add(left, right) {\n"
            "-  return left + right;\n"
            "+  return Number(left) + Number(right);\n"
            " }\n"
        )
    }
    result = validator.validate_patch(
        original,
        patch,
        working,
        baseline_test_results=baseline,
    )

    assert result["status"] == "PATCH_VALIDATED"
    assert result["tests_passed"] is True
    assert result["regression_free"] is True
    assert "Number(left)" in (working / "src" / "math.js").read_text()
