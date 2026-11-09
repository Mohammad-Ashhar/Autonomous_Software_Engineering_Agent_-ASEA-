"""Select isolated test runners for all supported repository runtimes."""

# ruff: noqa: I001

from pathlib import Path

from asea.tools.node_test_runner import NodeTestRunner
from asea.tools.test_runner import DockerTestRunner


IGNORED_DIRECTORIES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".next", "dist", "build", "coverage", "workspaces",
}
PYTHON_EXTENSIONS = {".py"}
JAVASCRIPT_EXTENSIONS = {".js", ".mjs", ".cjs"}


class UnsupportedTestEnvironment(ValueError):
    """Raised when a repository needs a runner ASEA cannot safely select."""


def detect_repository_runtimes(repository_path: str | Path) -> set[str]:
    """Return supported runtimes represented by source files."""
    root = Path(repository_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    runtimes = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(
            part in IGNORED_DIRECTORIES
            for part in path.relative_to(root).parts
        ):
            continue
        suffix = path.suffix.lower()
        if suffix in PYTHON_EXTENSIONS:
            runtimes.add("python")
        elif suffix in JAVASCRIPT_EXTENSIONS:
            runtimes.add("javascript")
    return runtimes


class CompositeTestRunner:
    """Run every supported runtime and require every suite to pass."""

    def __init__(self, python_runner=None, node_runner=None):
        self.python_runner = python_runner or DockerTestRunner()
        self.node_runner = node_runner or NodeTestRunner()

    def run_tests(self, repository_path):
        python_result = self.python_runner.run_tests(repository_path)
        node_result = self.node_runner.run_tests(repository_path)
        runtimes = {
            "python": python_result,
            "javascript": node_result,
        }
        success = (
            python_result.get("success", False)
            and node_result.get("success", False)
        )
        failed_tests = [
            f"python::{item}"
            for item in python_result.get("failed_tests", [])
        ] + [
            f"javascript::{item}"
            for item in node_result.get("failed_tests", [])
        ]
        failed = (
            python_result.get("failed", 0)
            + node_result.get("failed", 0)
        )
        errors = (
            python_result.get("errors", 0)
            + node_result.get("errors", 0)
        )
        return {
            "status": "PASSED" if success else "FAILED",
            "success": success,
            "runtimes": runtimes,
            "failed": failed,
            "errors": errors,
            "failed_tests": failed_tests,
            "duration": round(
                python_result.get("duration", 0)
                + node_result.get("duration", 0),
                2,
            ),
        }


def get_repository_test_runner(repository_path: str | Path):
    """Return a single or composite isolated runner for repository runtimes."""
    root = Path(repository_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    runtimes = detect_repository_runtimes(root)

    if runtimes == {"python", "javascript"}:
        return CompositeTestRunner()
    if "javascript" in runtimes:
        return NodeTestRunner()
    if "python" in runtimes:
        return DockerTestRunner()
    raise UnsupportedTestEnvironment(
        "No supported Python or plain JavaScript runtime was found."
    )
