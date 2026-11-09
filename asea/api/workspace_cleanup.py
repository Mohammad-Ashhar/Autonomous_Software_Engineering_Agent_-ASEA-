import shutil
from pathlib import Path


def cleanup_repair_workspace(repository_path: str | Path, state: dict) -> bool:
    """Remove a terminal repair's isolated workspace, never a repository."""
    working_path = state.get("working_repo_path")
    if not working_path:
        return False

    repository = Path(repository_path).resolve()
    workspace_root = repository.parent / "workspaces"
    working_repo = Path(working_path).absolute()

    # Check the original path components before resolving anything: resolve()
    # follows symlinks and would hide whether the cleanup target was redirected.
    run_directory = working_repo.parent
    if (
        workspace_root.is_symlink()
        or run_directory.is_symlink()
        or working_repo.is_symlink()
        or run_directory.parent != workspace_root
        or working_repo.name != "repository"
        or not run_directory.name.startswith("asea_run_")
        or not working_repo.is_dir()
    ):
        return False

    shutil.rmtree(run_directory)
    return True
