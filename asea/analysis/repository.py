import os
from pathlib import Path
from typing import Any

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}


def inspect_repository(repo_path: str | Path) -> dict[str, Any]:
    """
    Analyze repository structure without executing source code.

    Args:
        repo_path: Path to the cloned repository.

    Returns:
        Dictionary containing repository metadata.
    """

    root = Path(repo_path).expanduser().resolve()

    if not root.exists() or not root.is_dir():
        raise ValueError(f"Invalid repository path: {root}")

    python_files = []
    directories = []
    extensions = {}

    for current, dirnames, filenames in os.walk(root):

        dirnames[:] = sorted(
            name for name in dirnames
            if name not in IGNORED_DIRS
        )

        relative_dir = Path(current).relative_to(root)

        if str(relative_dir) != ".":
            directories.append(relative_dir.as_posix())

        for filename in sorted(filenames):

            file_path = Path(current) / filename

            relative_path = file_path.relative_to(root).as_posix()

            extension = file_path.suffix.lower() or "[no extension]"

            extensions[extension] = (
                extensions.get(extension, 0) + 1
            )

            if extension == ".py":
                python_files.append(relative_path)

    return {
        "root": str(root),
        "directory_count": len(directories),
        "file_count": sum(extensions.values()),
        "python_file_count": len(python_files),
        "python_files": python_files,
        "extensions": dict(sorted(extensions.items())),
    }


def get_python_files(repo_path: str | Path) -> list[str]:
    """Return all discovered Python files."""

    inventory = inspect_repository(repo_path)

    return inventory["python_files"]
