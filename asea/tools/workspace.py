from pathlib import Path


def repair_workspace_root(repository_path: str | Path) -> Path:
    """Resolve the shared workspace root without nesting workspaces twice."""
    repository = Path(repository_path).resolve()
    managed_root = Path("workspaces").resolve()
    if repository.parent == managed_root:
        return managed_root
    return repository.parent / "workspaces"
