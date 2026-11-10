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
            ignore=ignored,
            symlinks=True,
        )

    @staticmethod
    def _resolve_patch_target(
        repository_path: Path,
        patch_path: str,
    ) -> Path:
        """Resolve one diff path without allowing repository traversal."""

        if patch_path == "/dev/null":
            raise ValueError(
                "Creating or deleting files is not supported yet."
            )

        normalized = patch_path.removeprefix("a/").removeprefix("b/")
        relative_path = Path(normalized)

        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("Patch target is outside the repository.")

        target = (repository_path / relative_path).resolve()

        try:
            target.relative_to(repository_path)
        except ValueError as error:
            raise ValueError(
                "Patch target is outside the repository."
            ) from error

        if target.is_file():
            return target

        matches = [
            match.resolve()
            for match in repository_path.rglob(relative_path.name)
            if match.is_file()
            and repository_path in match.resolve().parents
        ]

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 1:
            raise ValueError(
                f"Multiple files match patch target: {patch_path}"
            )

        raise FileNotFoundError(
            f"Patch target not found: {patch_path}"
        )

    def apply_patch(
        self,
        repository_path: Path,
        patch: str
    ) -> None:
        """Apply a patch transactionally; failures leave the workspace untouched."""

        repository_path = Path(repository_path).resolve()
        if not repository_path.is_dir():
            raise FileNotFoundError(f"Repository not found: {repository_path}")

        with tempfile.TemporaryDirectory(
            prefix="asea_patch_transaction_",
            dir=str(repository_path.parent),
        ) as transaction_dir:
            transaction_root = Path(transaction_dir)
            staged_repo = transaction_root / "staged"
            backup_repo = transaction_root / "backup"
            self.create_temporary_copy(repository_path, staged_repo)

            # All parsing, normalization and git operations happen on the stage.
            self._apply_patch_in_place(staged_repo, patch)

            repository_path.rename(backup_repo)
            try:
                staged_repo.rename(repository_path)

                # The staged copy intentionally excludes bulky or runtime
                # directories. Restore top-level exclusions so applying a
                # patch never deletes the user's Git metadata, virtualenv,
                # artifacts, or persisted workspaces.
                preserved_directories = (
                    ".git",
                    ".venv",
                    "venv",
                    "artifacts",
                    "workspaces",
                )
                for directory_name in preserved_directories:
                    original_directory = backup_repo / directory_name
                    if original_directory.exists():
                        destination = repository_path / directory_name
                        # Patch application initializes a temporary Git
                        # repository in the staged copy. Replace that
                        # temporary metadata with the user's original .git.
                        if directory_name == ".git" and destination.exists():
                            shutil.rmtree(destination)
                        elif destination.exists():
                            raise FileExistsError(
                                f"Cannot restore preserved directory: {directory_name}"
                            )
                        original_directory.rename(destination)
            except Exception:
                # Return any already-restored directories to the backup before
                # putting the original repository tree back in place.
                for directory_name in (
                    ".git",
                    ".venv",
                    "venv",
                    "artifacts",
                    "workspaces",
                ):
                    restored = repository_path / directory_name
                    original = backup_repo / directory_name
                    if restored.exists() and not original.exists():
                        restored.rename(original)
                if repository_path.exists():
                    repository_path.rename(staged_repo)
                backup_repo.rename(repository_path)
                raise

            shutil.rmtree(backup_repo, ignore_errors=True)

    def _apply_patch_in_place(
        self,
        repository_path: Path,
        patch: str
    ) -> None:
        """Apply a validated patch to a disposable staging directory."""

        repository_path = repository_path.resolve()
        if not repository_path.is_dir():
            raise FileNotFoundError(
                f"Repository not found: {repository_path}"
            )

        lines = patch.splitlines()
        rewritten_lines = []
        target_files = []
        index = 0

        while index < len(lines):
            line = lines[index]

            if line.startswith("--- "):
                if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                    raise ValueError("Malformed unified diff file headers.")

                old_path = line[4:].split("\t")[0].strip()
                new_path = lines[index + 1][4:].split("\t")[0].strip()

                if old_path == "/dev/null" or new_path == "/dev/null":
                    raise ValueError(
                        "Creating or deleting files is not supported yet."
                    )

                old_target = self._resolve_patch_target(
                    repository_path,
                    old_path,
                )
                new_target = self._resolve_patch_target(
                    repository_path,
                    new_path,
                )

                if old_target != new_target:
                    raise ValueError(
                        "File renames are not supported in repair patches."
                    )

                actual_path = old_target.relative_to(
                    repository_path
                ).as_posix()
                rewritten_lines.extend([
                    f"--- a/{actual_path}",
                    f"+++ b/{actual_path}",
                ])
                target_files.append(old_target)
                index += 2
                continue

            rewritten_lines.append(line)
            index += 1

        if not target_files:
            raise ValueError("Patch contains no supported file changes.")

        normalized_patch = "\n".join(rewritten_lines) + "\n"

        # Normalize line endings only inside the isolated candidate copy.
        for target_file in set(target_files):
            source = target_file.read_bytes()
            target_file.write_bytes(source.replace(b"\\r\\n", b"\\n"))

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
                "Could not initialize temporary Git repository: "
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
            input=normalized_patch,
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
            input=normalized_patch,
            capture_output=True,
            text=True,
            timeout=30
        )

        if apply_result.returncode != 0:
            raise RuntimeError(
                f"Patch application failed:\n{apply_result.stderr}"
            )

    @staticmethod
    def has_reliable_test_execution(
        results: dict[str, Any]
    ) -> bool:
        """Reject test runs that did not execute a usable test suite."""

        return (
            results.get("status") in {"PASSED", "FAILED"}
            and results.get("total", 0) > 0
            and not results.get("timed_out", False)
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

            test_results = self.test_runner.run_tests(
                candidate_repo
            )

            tests_passed = test_results.get(
                "success",
                False
            )

            if not self.has_reliable_test_execution(test_results):
                return {
                    "status": "PATCH_REJECTED",
                    "patch_applied": True,
                    "tests_passed": False,
                    "regression_free": False,
                    "reason": (
                        "Candidate validation did not execute a usable "
                        "test suite. Add or repair tests before accepting "
                        "this patch."
                    ),
                    "test_results": test_results
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

                # Candidate copies omit runtime state by design. Restore it
                # only after the validated source tree is promoted.
                preserved_directories = (
                    ".git",
                    ".venv",
                    "venv",
                    "artifacts",
                    "workspaces",
                )
                for directory_name in preserved_directories:
                    original_directory = backup_repo / directory_name
                    if original_directory.exists():
                        destination = working_repo_path / directory_name
                        if destination.exists():
                            raise FileExistsError(
                                f"Cannot restore preserved directory: {directory_name}"
                            )
                        original_directory.rename(destination)

            except Exception:
                # Roll back any runtime directories already moved, then
                # restore the complete original workspace.
                for directory_name in (
                    ".git",
                    ".venv",
                    "venv",
                    "artifacts",
                    "workspaces",
                ):
                    restored = working_repo_path / directory_name
                    original = backup_repo / directory_name
                    if restored.exists() and not original.exists():
                        restored.rename(original)
                if working_repo_path.exists():
                    working_repo_path.rename(candidate_repo)
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