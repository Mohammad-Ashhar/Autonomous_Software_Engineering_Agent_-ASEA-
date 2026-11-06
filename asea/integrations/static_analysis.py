import shutil
import subprocess
from pathlib import Path
from typing import Any


def run_analysis_tool(
    command: list[str],
    repository_path: str | Path,
    timeout: int = 90
) -> dict[str, Any]:
    """
    Execute a static-analysis tool safely without shell execution.

    The repository's source code is not executed by this function.
    """

    executable = shutil.which(command[0])

    if executable is None:

        return {
            "tool": command[0],
            "available": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{command[0]} is not installed",
        }

    try:

        result = subprocess.run(
            [executable, *command[1:]],
            cwd=Path(repository_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        return {
            "tool": command[0],
            "available": True,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except subprocess.TimeoutExpired:

        return {
            "tool": command[0],
            "available": True,
            "returncode": None,
            "stdout": "",
            "stderr": "Analysis timed out",
        }


def run_ruff(
    repository_path: str | Path
) -> dict[str, Any]:
    """Run Ruff Python linting."""

    return run_analysis_tool(
        [
            "ruff",
            "check",
            ".",
            "--output-format",
            "json",
        ],
        repository_path
    )


def run_semgrep(
    repository_path: str | Path
) -> dict[str, Any]:
    """Run Semgrep security and bug-pattern analysis."""

    return run_analysis_tool(
        [
            "semgrep",
            "scan",
            "--config",
            "auto",
            "--json",
            "--quiet",
        ],
        repository_path,
        timeout=180
    )
