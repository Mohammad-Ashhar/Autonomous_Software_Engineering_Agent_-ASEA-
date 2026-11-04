import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from asea.tools.test_runner import DockerTestRunner


class PatchValidatorAgent:

    def __init__(self):
        self.test_runner = DockerTestRunner()

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

                if target.startswith("b/"):
                    target = target[2:]

                if not target:
                    raise ValueError("Invalid patch target.")

                return target

        raise ValueError("Could not identify the patch target.")

    def apply_patch(
        self,
        repository_path: Path,
        patch: str
    ) -> None:
        """Safely apply a patch to the temporary repository."""

        target_relative = self.get_patch_target(patch)

        target_file = (
            repository_path / target_relative
        ).resolve()

        # Prevent path traversal outside the temporary repository.
        try:
            target_file.relative_to(repository_path.resolve())
        except ValueError:
            raise ValueError(
                "Patch target is outside the repository."
            )

        if not target_file.is_file():
            raise FileNotFoundError(
                f"Patch target not found: {target_relative}"
            )

        print("TEMPORARY REPOSITORY:", repository_path)
        print("PATCH TARGET:", target_relative)
        print("PATCH TARGET EXISTS:", target_file.exists())

        # Normalize source line endings in the temporary copy.
        source = target_file.read_bytes()

        target_file.write_bytes(
            source.replace(b"\r\n", b"\n")
        )

        # Normalize patch line endings.
        patch = patch.replace("\r\n", "\n")

        init_result = subprocess.run(
            ["git", "init"],
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

    def validate_patch(
        self,
        repository_path: Path,
        patch_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate a patch without modifying the original repository."""

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

        with tempfile.TemporaryDirectory(
            prefix="asea_validation_"
        ) as temporary_directory:

            temporary_repo = (
                Path(temporary_directory) / "repository"
            )

            self.create_temporary_copy(
                repository_path,
                temporary_repo
            )

            try:
                self.apply_patch(
                    temporary_repo,
                    patch
                )

            except Exception as error:
                return {
                    "status": "PATCH_REJECTED",
                    "patch_applied": False,
                    "tests_passed": False,
                    "reason": str(error)
                }

            test_results = self.test_runner.run_tests(
                temporary_repo
            )

            tests_passed = test_results.get(
                "success",
                False
            )

            return {
                "status": (
                    "PATCH_VALIDATED"
                    if tests_passed
                    else "PATCH_REJECTED"
                ),
                "patch_applied": True,
                "tests_passed": tests_passed,
                "test_results": test_results
            }