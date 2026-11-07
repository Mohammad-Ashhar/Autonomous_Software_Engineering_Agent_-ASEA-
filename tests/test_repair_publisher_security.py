from pathlib import Path

import pytest
from git import Repo

from asea.integrations.repair_publisher import (
    normalize_github_repository,
    validate_source_repository,
)


def create_git_repository(
    path: Path,
    remote_url: str | None = None,
) -> Path:
    path.mkdir(parents=True, exist_ok=True)

    repo = Repo.init(path)

    if remote_url:
        repo.create_remote("origin", remote_url)

    return path


def test_normalize_https_github_url():
    result = normalize_github_repository(
        "https://github.com/Mohammad-Ashhar/ASEA.git"
    )

    assert result == "mohammad-ashhar/asea"


def test_normalize_ssh_github_url():
    result = normalize_github_repository(
        "git@github.com:Mohammad-Ashhar/ASEA.git"
    )

    assert result == "mohammad-ashhar/asea"


def test_reject_non_github_url():
    with pytest.raises(ValueError):
        normalize_github_repository(
            "https://gitlab.com/user/project.git"
        )


def test_accept_matching_source_repository(tmp_path):
    repo_path = create_git_repository(
        tmp_path / "ASEA",
        "https://github.com/Mohammad-Ashhar/ASEA.git",
    )

    source_path = repo_path / "demo_repo"
    source_path.mkdir()

    source_git_repo, git_root = validate_source_repository(
        source_path,
        "Mohammad-Ashhar/ASEA",
    )

    assert source_git_repo.working_tree_dir is not None
    assert Path(git_root) == repo_path.resolve()


def test_reject_unrelated_source_repository(tmp_path):
    repo_path = create_git_repository(
        tmp_path / "unrelated",
        "https://github.com/someone/another-project.git",
    )

    with pytest.raises(
        RuntimeError,
        match="does not match",
    ):
        validate_source_repository(
            repo_path,
            "Mohammad-Ashhar/ASEA",
        )


def test_reject_repository_without_origin(tmp_path):
    repo_path = create_git_repository(
        tmp_path / "no_remote"
    )

    with pytest.raises(
        RuntimeError,
        match="no origin remote",
    ):
        validate_source_repository(
            repo_path,
            "Mohammad-Ashhar/ASEA",
        )


def test_reject_directory_outside_git_repository(tmp_path):
    outside_path = tmp_path / "outside"
    outside_path.mkdir()

    with pytest.raises(
        RuntimeError,
        match="not inside a valid Git repository",
    ):
        validate_source_repository(
            outside_path,
            "Mohammad-Ashhar/ASEA",
        )