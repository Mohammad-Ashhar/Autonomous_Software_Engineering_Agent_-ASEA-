from pathlib import Path

from git import Repo
from git.exc import GitCommandError


def clone_repository(
    repository_url: str,
    workspace: str | Path
) -> Path:
    """
    Clone a Git repository into the workspace.

    Args:
        repository_url: GitHub or other Git repository URL.
        workspace: Destination workspace.

    Returns:
        Local repository path.
    """

    workspace_path = Path(workspace).expanduser().resolve()

    workspace_path.mkdir(
        parents=True,
        exist_ok=True
    )

    repository_name = (
        repository_url.rstrip("/")
        .split("/")[-1]
        .removesuffix(".git")
    )

    if not repository_name:
        raise ValueError("Invalid repository URL")

    destination = workspace_path / repository_name

    if destination.exists():

        if (destination / ".git").exists():
            return destination

        raise FileExistsError(
            f"Destination already exists: {destination}"
        )

    try:

        Repo.clone_from(
            repository_url,
            destination,
            depth=1
        )

    except GitCommandError as error:

        raise RuntimeError(
            f"Repository cloning failed: {error}"
        ) from error

    return destination


def validate_local_repository(
    repository_path: str | Path
) -> Path:
    """Validate a local Git repository."""

    path = Path(repository_path).expanduser().resolve()

    if not path.is_dir():
        raise ValueError(
            f"Directory does not exist: {path}"
        )

    try:
        Repo(
            path,
            search_parent_directories=True
        )

    except Exception as error:
        raise ValueError(
            f"Invalid Git repository: {path}"
        ) from error

    return path
