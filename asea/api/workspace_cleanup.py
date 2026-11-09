import shutil
from pathlib import Path


def cleanup_repair_workspace(repository_path: str | Path, state: dict) -> bool:
    """Remove a terminal repair's isolated workspace, never a repository."""
    working_path = state.get("working_repo_path")
    if not working_path:
        return False

    repository = Path(repository_path).resolve()
    workspace_root = (repository.parent / "workspaces").resolve()
    working_repo = Path(working_path).resolve()

    try:
        relative = working_repo.relative_to(workspace_root)
    except ValueError:
        return False

    if (
        len(relative.parts) != 2
        or not relative.parts[0].startswith("asea_run_")
        or relative.parts[1] != "repository"
        or working_repo.is_symlink()
        or not working_repo.is_dir()
    ):
        return False

    run_directory = working_repo.parent
    if run_directory.is_symlink():
        return False

    shutil.rmtree(run_directory)
    return True
