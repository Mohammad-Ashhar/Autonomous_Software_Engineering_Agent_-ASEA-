import base64
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from git import Repo
from git.exc import GitCommandError

from asea.core.config import get_settings
from asea.integrations.github_pr import create_pull_request

EXCLUDED_PARTS = {
    ".git",
    ".env",
    ".env.example",
    ".env.local",
    ".env.development",
    ".env.production",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "artifacts",
    "workspaces",
    "node_modules",
    "dist",
    "build",
}


def get_approved_repair_files(
    state: dict[str, Any],
    original_repo: Path,
    working_repo: Path,
) -> list[Path]:
    """Return files belonging to successfully approved repairs."""

    repaired_bugs = state.get("repaired_bugs", [])
    review_history = state.get("review_history", [])

    latest_decisions = {}

    for item in review_history:
        bug_key = item.get("bug_key")

        if bug_key:
            latest_decisions[bug_key] = item


    approved_files = set()

    for bug in repaired_bugs:

        if bug.get("status") != "REPAIRED":
            continue

        bug_key = bug.get("bug_key")

        latest_review = latest_decisions.get(bug_key)

        if not latest_review or latest_review.get("decision") != "approved":
            continue

        if latest_review.get("attempt", 1) != bug.get("attempts", 1):
            continue

        file_value = bug.get("file")

        if not file_value:
            continue

        original_file = Path(file_value)

        try:
            relative_path = original_file.resolve().relative_to(
                original_repo.resolve()
            )
        except ValueError:
            continue

        if any(part in EXCLUDED_PARTS for part in relative_path.parts):
            continue

        working_file = working_repo / relative_path

        if not working_file.is_file():
            continue

        if (
        original_file.is_file()
        and original_file.read_bytes() == working_file.read_bytes()
        ):
            continue

        approved_files.add(relative_path)

    return sorted(approved_files)


def publish_validated_repairs(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Publish approved repairs through a GitHub Pull Request."""

    if state.get("status") != "COMPLETED":
        raise RuntimeError(
            "Publishing blocked: ASEA workflow did not complete successfully."
        )

    test_results = state.get("working_test_results", {})

    if not test_results.get("success", False):
        raise RuntimeError(
            "Publishing blocked: final tests did not pass."
        )

    original_repo = Path(state["repo_path"]).resolve()
    working_repo = Path(state["working_repo_path"]).resolve()

    if not original_repo.is_dir() or not working_repo.is_dir():
        raise FileNotFoundError(
            "Original repository or validated working repository is missing."
        )

    repair_files = get_approved_repair_files(
        state,
        original_repo,
        working_repo,
    )

    if not repair_files:
        raise RuntimeError(
            "No approved and validated file changes are available to publish."
        )

    settings = get_settings()

    if not settings.github_token:
        raise ValueError("GITHUB_TOKEN is missing from .env.")

    if not settings.github_repository:
        raise ValueError("GITHUB_REPOSITORY is missing from .env.")

    repository_name = settings.github_repository.strip()

    temporary_root = Path(
        tempfile.mkdtemp(prefix="asea_publish_")
    )

    checkout_path = temporary_root / "repository"

    try:
        from github import Auth, Github

        github_client = Github(
            auth=Auth.Token(settings.github_token)
        )

        github_repository = github_client.get_repo(repository_name)

        base_branch = "MohammadAshhar/deepseek-langraph"

        github_repository.get_branch(base_branch)

        clone_url = github_repository.clone_url

        publishing_repo = Repo.clone_from(
            clone_url,
            checkout_path,
            branch=base_branch,
            depth=1,
        )

        branch_name = (
            "asea/repair-"
            + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        )

        publishing_repo.git.checkout(
            "-b",
            branch_name,
        )

        source_git_repo = Repo(
            original_repo,
            search_parent_directories=True,
        )

        git_root = Path(
            source_git_repo.working_tree_dir
        ).resolve()

        repository_prefix = original_repo.relative_to(git_root)

        copied_files = []

        for relative_path in repair_files:

            source_file = working_repo / relative_path

            destination_relative = (
                repository_prefix / relative_path
            )

            destination_file = (
                checkout_path / destination_relative
            ).resolve()

            try:
                destination_file.relative_to(checkout_path.resolve())
            except ValueError:
                raise ValueError(
                    "Unsafe repair file path detected."
                )

            destination_file.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            shutil.copy2(
                source_file,
                destination_file,
            )

            copied_files.append(
                destination_relative.as_posix()
            )

        if not copied_files:
            raise RuntimeError(
                "No repair files were copied to the publishing checkout."
            )

        publishing_repo.index.add(copied_files)

        if not publishing_repo.is_dirty(
            index=True,
            working_tree=False,
        ):
            raise RuntimeError(
                "No Git changes were detected after copying repair files."
            )

        publishing_repo.index.commit(
            f"ASEA: repair {len(copied_files)} validated file(s)"
        )

        encoded_token = base64.b64encode(
            f"x-access-token:{settings.github_token}".encode()
        ).decode("ascii")

        authorization = (
            f"AUTHORIZATION: basic {encoded_token}"
        )

        publishing_repo.git.execute(
            [
                "git",
                "-c",
                f"http.extraheader={authorization}",
                "push",
                "origin",
                f"{branch_name}:{branch_name}",
            ]
        )

        pull_request = create_pull_request(
            head_branch=branch_name,
            base_branch=base_branch,
            title="ASEA: Automated validated code repairs",
            body=(
                "## Automated Repair Summary\n\n"
                "This pull request contains repairs generated by ASEA.\n\n"
                f"- Repaired files: {len(copied_files)}\n"
                f"- Successfully repaired bugs: "
                f"{len(state.get('repaired_bugs', []))}\n"
                "- Human review: approved\n"
                "- Final validation: passed\n\n"
                "All changes were validated in an isolated Docker workspace. "
                "Please review before merging."
            ),
        )

        return {
            "status": "PULL_REQUEST_CREATED",
            "branch": branch_name,
            "base_branch": base_branch,
            "commit": publishing_repo.head.commit.hexsha,
            "files": copied_files,
            "pull_request": pull_request,
        }

    except GitCommandError as error:
        raise RuntimeError(
            f"Git publishing operation failed: {error}"
        ) from error

    finally:
        shutil.rmtree(
            temporary_root,
            ignore_errors=True,
        )