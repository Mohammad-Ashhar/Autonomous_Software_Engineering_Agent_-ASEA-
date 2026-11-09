import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from asea.tools.node_test_runner import NodeTestRunner
from asea.tools.test_runner import DockerTestRunner
from asea.tools.runner_factory import (
    CompositeTestRunner,
    detect_repository_runtimes,
)


class PatchValidatorAgent:

    def __init__(self):
        self.test_runner = DockerTestRunner()
        self.node_test_runner = NodeTestRunner()

    def create_temporary_copy(
        self,
        source_path: Path,
        destination_path: Path
    ) -> None:
        """Copy the repository while excluding unnecessary files."""

        ignored = shutil.ignore_patterns(
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "artifacts",
            "workspaces",
            "*.pyc"
        )

        shutil.copytree(
            source_path,
            destination_path,
            ignore=ignored
        )

    def get_patch_target(self, patch: str) -> str:
        """Extract the target file path from a unified diff."""

        for line in patch.splitlines():

            if line.startswith("+++ "):

                target = line[4:].split("\t")[0].strip()

                if target == "/dev/null":
                    raise ValueError(
                        "Creating new files is not supported yet."
                    )

                target = target.removeprefix("b/")

                if not target:
                    raise ValueError("Invalid patch target.")

                return target

        raise ValueError("Could not identify the patch target.")

    def apply_patch(
        self,
        repository_path: Path,
        patch: str
    ) -> None:
        """Safely apply a patch to a candidate repository."""

        repository_path = repository_path.resolve()
        target_relative = self.get_patch_target(patch)

        target_file = (
            repository_path / target_relative
        ).resolve()

        try:
            target_file.relative_to(repository_path)
        except ValueError as error:
            raise ValueError(
                "Patch target is outside the repository."
            ) from error

        if not target_file.is_file():

            matches = [
                match.resolve()
                for match in repository_path.rglob(
                    Path(target_relative).name
                )
                if match.is_file()
            ]

            matches = [
                match
                for match in matches
                if repository_path in match.parents
            ]

            if len(matches) == 1:
                target_file = matches[0]

            elif len(matches) > 1:
                raise ValueError(
                    f"Multiple files match patch target: "
                    f"{target_relative}"
                )

            else:
                raise FileNotFoundError(
                    f"Patch target not found: {target_relative}"
                )

        actual_relative = target_file.relative_to(
            repository_path
        ).as_posix()

        updated_lines = []

        for line in patch.splitlines():

            if line.startswith("--- a/"):
                line = f"--- a/{actual_relative}"

            elif line.startswith("+++ b/"):
                line = f"+++ b/{actual_relative}"

            updated_lines.append(line)

        patch = "\n".join(updated_lines) + "\n"

        source = target_file.read_bytes()

        target_file.write_bytes(
            source.replace(b"\r\n", b"\n")
        )

        init_result = subprocess.run(
            ["git", "init"],
            check=False,
            cwd=repository_path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if init_result.returncode != 0:
            raise RuntimeError(
                f"Could not initialize temporary Git repository: "
                f"{init_result.stderr}"
            )

        check_result = subprocess.run(
            [
                "git",
                "apply",
                "--check",
                "--ignore-space-change",
                "--ignore-whitespace",
                "-"
            ],
            check=False,
            cwd=repository_path,
            input=patch,
            capture_output=True,
            text=True,
            timeout=30
        )

        if check_result.returncode != 0:
            raise ValueError(
                f"Patch cannot be applied:\n{check_result.stderr}"
            )

        apply_result = subprocess.run(
            [
                "git",
                "apply",
                "--ignore-space-change",
                "--ignore-whitespace",
                "-"
            ],
            check=False,
            cwd=repository_path,
            input=patch,
            capture_output=True,
            text=True,
            timeout=30
        )

        if apply_result.returncode != 0:
            raise RuntimeError(
                f"Patch application failed:\n{apply_result.stderr}"
            )

    @staticmethod
    def has_no_new_failures(
        baseline: dict[str, Any],
        candidate: dict[str, Any]
    ) -> bool:
        """Check whether a patch introduces new test failures."""

        baseline_failed = set(
            baseline.get("failed_tests", [])
        )

        candidate_failed = set(
            candidate.get("failed_tests", [])
        )

        baseline_count = baseline.get("failed", 0)
        candidate_count = candidate.get("failed", 0)

        baseline_errors = baseline.get("errors", 0)
        candidate_errors = candidate.get("errors", 0)

        if candidate_count > baseline_count:
            return False

        if candidate_errors > baseline_errors:
            return False

        return not candidate_failed or candidate_failed.issubset(
            baseline_failed
        )

    def validate_patch(
        self,
        repository_path: Path,
        patch_result: dict[str, Any],
        working_repo_path: Path | None = None,
        baseline_test_results: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Validate a patch using a candidate copy.

        Accepted patches are preserved in the working repository.
        Rejected patches do not modify the working repository.
        """

        repository_path = Path(repository_path).resolve()

        if not repository_path.is_dir():
            raise FileNotFoundError(
                f"Repository not found: {repository_path}"
            )

        if not isinstance(patch_result, dict):
            raise TypeError(
                "patch_result must be a dictionary."
            )

        patch = patch_result.get("patch")

        if not isinstance(patch, str) or not patch.strip():
            raise ValueError(
                "No valid patch provided for validation."
            )

        working_repo_path = Path(
            working_repo_path or repository_path
        ).resolve()

        if not working_repo_path.is_dir():
            raise FileNotFoundError(
                f"Working repository not found: {working_repo_path}"
            )

        baseline = baseline_test_results or {}

        working_repo_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with tempfile.TemporaryDirectory(
            prefix="asea_candidate_",
            dir=str(working_repo_path.parent)
        ) as temporary_directory:

            candidate_repo = (
                Path(temporary_directory) / "candidate"
            )

            self.create_temporary_copy(
                working_repo_path,
                candidate_repo
            )

            try:
                self.apply_patch(
                    candidate_repo,
                    patch
                )

            except (
                ValueError,
                RuntimeError,
                OSError,
                subprocess.SubprocessError,
            ) as error:

                return {
                    "status": "PATCH_REJECTED",
                    "patch_applied": False,
                    "tests_passed": False,
                    "regression_free": False,
                    "reason": str(error)
                }

            target_extension = Path(
                self.get_patch_target(patch)
            ).suffix.lower()
            is_javascript = target_extension in {
                ".js", ".mjs", ".cjs"
            }

            runtimes = detect_repository_runtimes(candidate_repo)
            if (
                (is_javascript or len(runtimes) > 1)
                and not baseline.get("success", False)
            ):
                return {
                    "status": "PATCH_REJECTED",
                    "patch_applied": True,
                    "tests_passed": False,
                    "regression_free": False,
                    "reason": (
                        "JavaScript and mixed-runtime repairs require a "
                        "passing baseline for every supported runtime. "
                        "Missing tests or baseline failures cannot establish "
                        "a safe comparison."
                    ),
                }

            if runtimes == {"python", "javascript"}:
                runner = CompositeTestRunner(
                    self.test_runner,
                    self.node_test_runner,
                )
            elif "javascript" in runtimes:
                runner = self.node_test_runner
            else:
                runner = self.test_runner
            test_results = runner.run_tests(candidate_repo)

            tests_passed = test_results.get(
                "success",
                False
            )
            if (is_javascript or len(runtimes) > 1) and not tests_passed:
                return {
                    "status": "PATCH_REJECTED",
                    "patch_applied": True,
                    "tests_passed": False,
                    "regression_free": False,
                    "reason": (
                        "Candidate did not pass every required runtime "
                        "syntax check and test suite."
                    ),
                    "test_results": test_results,
                }

            regression_free = self.has_no_new_failures(
                baseline,
                test_results
            )

            if not regression_free:

                return {
                    "status": "PATCH_REJECTED",
                    "patch_applied": True,
                    "tests_passed": tests_passed,
                    "regression_free": False,
                    "reason": (
                        "The candidate patch introduced new "
                        "test failures or errors."
                    ),
                    "test_results": test_results
                }

            backup_repo = (
                working_repo_path.parent
                / f"{working_repo_path.name}_backup"
            )

            if backup_repo.exists():
                shutil.rmtree(backup_repo)

            working_repo_path.rename(backup_repo)

            try:
                candidate_repo.rename(working_repo_path)

            except Exception:
                backup_repo.rename(working_repo_path)
                raise

            shutil.rmtree(
                backup_repo,
                ignore_errors=True
            )

            return {
                "status": "PATCH_VALIDATED",
                "patch_applied": True,
                "tests_passed": tests_passed,
                "regression_free": True,
                "test_results": test_results
            }