import difflib
import os
import re
import subprocess
import sys
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

import asea.core.graph as graph


class LocalPytestRunner:
    """Run fixture tests locally while exercising the real graph orchestration."""

    def run_tests(self, repository):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(repository), environment.get("PYTHONPATH", "")]
        )
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = completed.stdout + completed.stderr
        passed_match = re.search(r"(\\d+) passed", output)
        passed = int(passed_match.group(1)) if passed_match else 0
        total = passed + sum(
            int(count)
            for count in re.findall(r"(\\d+) (?:failed|error)", output)
        )
        failed = sum(
            int(count)
            for count in re.findall(r"(\d+) (?:failed|error)", output)
        )
        return {
            "status": "PASSED" if completed.returncode == 0 else "FAILED",
            "success": completed.returncode == 0 and total > 0,
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": 0,
            "failed_tests": [],
            "timed_out": False,
            "output": output,
        }


def test_full_graph_repairs_unfamiliar_src_package_after_human_approval(
    tmp_path, monkeypatch
):
    repository = tmp_path / "unfamiliar_python_project"
    source_file = repository / "src" / "atlas" / "math_ops.py"
    source_file.parent.mkdir(parents=True)
    original_source = (
        "def is_even(number):\n"
        "    return number % 2 == 1\n"
    )
    repaired_source = (
        "def is_even(number):\n"
        "    return number % 2 == 0\n"
    )
    source_file.write_text(original_source, encoding="utf-8")

    bug = {
        "file": str(source_file),
        "line": 2,
        "title": "Incorrect even-number check",
        "severity": "high",
        "classification": "confirmed_bug",
        "confidence": 1.0,
        "description": "The function returns True for odd numbers.",
        "suggestion": "Compare the remainder with zero.",
    }

    monkeypatch.setattr(
        graph,
        "repository_analysis_node",
        lambda _state: {
            "repo_map": {"files": ["src/atlas/math_ops.py"]},
            "baseline_results": {"file_count": 1},
            "current_agent": "repository_analysis",
            "status": "repository_analysis_completed",
        },
    )
    monkeypatch.setattr(
        graph,
        "bug_detector_node",
        lambda _state: {
            "detected_bugs": [bug],
            "current_agent": "bug_detector",
            "status": "bug_detection_completed",
        },
    )
    monkeypatch.setattr(
        graph.TestGenerationAgent,
        "_generate_for_module",
        lambda _self, _source, _module: (
            "def test_even_number_returns_true_for_even_input():\n"
            "    assert module.is_even(2) is True\n"
        ),
    )
    monkeypatch.setattr(graph, "DockerTestRunner", LocalPytestRunner)

    class DeterministicPatchGenerator:
        last_attempt_trace = []

        def generate_patch(self, *, file_path, diff_path, **_kwargs):
            current = file_path.read_text(encoding="utf-8")
            patch = "".join(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    repaired_source.splitlines(keepends=True),
                    fromfile=f"a/{diff_path}",
                    tofile=f"b/{diff_path}",
                )
            )
            return {
                "file": str(file_path),
                "patch": patch,
                "status": "PATCH_GENERATED",
                "generator": "deterministic_test_fixture",
                "repair_scope": "complete",
            }

    monkeypatch.setattr(
        graph,
        "PatchGeneratorAgent",
        DeterministicPatchGenerator,
    )

    def validate_with_real_patch_application(
        self,
        *,
        repository_path,
        patch_result,
        working_repo_path,
        baseline_test_results,
    ):
        self.apply_patch(
            working_repo_path,
            patch_result["patch"],
        )
        candidate_results = LocalPytestRunner().run_tests(working_repo_path)
        return {
            "status": (
                "PATCH_VALIDATED"
                if candidate_results["success"]
                else "PATCH_REJECTED"
            ),
            "patch_applied": candidate_results["success"],
            "tests_passed": candidate_results["success"],
            "regression_free": candidate_results["success"],
            "test_results": candidate_results,
            "reason": candidate_results["output"],
        }

    monkeypatch.setattr(
        graph.PatchValidatorAgent,
        "validate_patch",
        validate_with_real_patch_application,
    )

    with SqliteSaver.from_conn_string(":memory:") as checkpointer:
        checkpointer.setup()
        workflow = graph.build_asea_graph(checkpointer=checkpointer)
        config = {
            "configurable": {
                "thread_id": "unfamiliar-project-human-review",
            },
            "recursion_limit": 100,
        }
        workflow.invoke(
            graph.create_initial_state(str(repository), review_mode="api"),
            config=config,
        )

        paused = workflow.get_state(config)
        assert paused.interrupts
        assert paused.values["baseline_test_results"]["status"] == "FAILED"
        assert paused.values["test_generation_results"]["status"] == "GENERATED"
        assert paused.values["patches"][-1]["generator"] == (
            "deterministic_test_fixture"
        )

        result = workflow.invoke(
            Command(resume={"decision": "approved", "feedback": ""}),
            config=config,
        )

    assert result["status"] == "COMPLETED"
    assert result["review_history"][-1]["decision"] == "approved"
    assert result["repaired_bugs"][0]["status"] == "REPAIRED"
    assert result["working_test_results"]["success"] is True
    assert result["working_test_results"]["passed"] == 1
    repaired_file = (
        Path(result["working_repo_path"]) / "src" / "atlas" / "math_ops.py"
    )
    assert repaired_file.read_text(encoding="utf-8") == repaired_source
    assert source_file.read_text(encoding="utf-8") == original_source
