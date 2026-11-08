"""Select a test runner only when the repository has one clear runtime."""

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


def get_repository_test_runner(repository_path: str | Path):
    """Return the isolated runner for a Python-only or plain-JS repository.

    Mixed-runtime repositories are deliberately blocked until ASEA can
    orchestrate both test ecosystems and compare their combined outcomes.
    """
    root = Path(repository_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    has_python = False
    has_javascript = False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts):
            continue
        suffix = path.suffix.lower()
        has_python = has_python or suffix in PYTHON_EXTENSIONS
        has_javascript = has_javascript or suffix in JAVASCRIPT_EXTENSIONS

    if has_python and has_javascript:
        raise UnsupportedTestEnvironment(
            "Mixed Python/JavaScript repositories are not yet supported "
            "for automated repair validation."
        )
    if has_javascript:
        return NodeTestRunner()
    if has_python:
        return DockerTestRunner()
    raise UnsupportedTestEnvironment(
        "No supported Python or plain JavaScript runtime was found."
    )
