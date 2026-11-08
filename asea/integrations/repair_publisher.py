import base64
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from git import Actor, Repo
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


def normalize_github_repository(repository_url: str) -> str:
    """Extract owner/repository from an HTTPS or SSH GitHub URL."""

    repository_url = repository_url.strip()

    if repository_url.startswith("git@github.com:"):
        repository_path = repository_url.split(":", 1)[1]

    else:
        parsed_url = urlsplit(repository_url)

        if parsed_url.hostname != "github.com":
            raise ValueError(
                "Publishing is allowed only for GitHub repositories."
            )

        if parsed_url.scheme not in ("https", "ssh"):
            raise ValueError("Unsupported GitHub remote URL scheme.")

        repository_path = parsed_url.path.strip("/")

    repository_path = repository_path.removesuffix(".git")

    parts = repository_path.split("/")

    if len(parts) != 2 or not all(parts):
        raise ValueError("Invalid GitHub repository URL.")

    return "/".join(parts).casefold()


def validate_source_repository(
    original_repo: Path,
    configured_repository: str | None = None,
) -> tuple[Repo, Path]:
    """Verify the source is a GitHub-backed Git repository.

    When ``configured_repository`` is given, the origin remote must also
    point at that repository. When it is omitted, the repository is
    whatever ``origin`` points at, so ASEA works for any GitHub repo.
    """

    try:
        source_git_repo = Repo(
            original_repo,
            search_parent_directories=True,
        )
    except Exception as error:
        raise RuntimeError(
            "Publishing blocked: source directory is not inside a valid Git repository."
        ) from error

    if "origin" not in source_git_repo.remotes:
        raise RuntimeError(
            "Publishing blocked: source repository has no origin remote."
        )

    git_root = Path(
        source_git_repo.working_tree_dir
    ).resolve()

    try:
        original_repo.relative_to(git_root)
    except ValueError as error:
        raise RuntimeError(
            "Publishing blocked: source directory is outside its Git repository."
        ) from error

    remote_url = source_git_repo.remotes.origin.url

    try:
        source_repository = normalize_github_repository(remote_url)
        expected_repository = (
            normalize_github_repository(
                f"https://github.com/{configured_repository}"
            )
            if configured_repository
            else source_repository
        )
    except ValueError as error:
        raise RuntimeError(
            f"Publishing blocked: repository identity validation failed: {error}"
        ) from error

    if source_repository != expected_repository:
        raise RuntimeError(
            "Publishing blocked: source repository does not match "
            "GITHUB_REPOSITORY."
        )

    return source_git_repo, git_root


def _no_new_failures(
    baseline: dict[str, Any],
    final: dict[str, Any],
) -> bool:
    """True when ``final`` has no test failure that ``baseline`` lacked."""

    if final.get("timed_out"):
        return False

    if final.get("failed", 0) > baseline.get("failed", 0):
        return False

    if final.get("errors", 0) > baseline.get("errors", 0):
        return False

    return set(final.get("failed_tests", [])).issubset(
        set(baseline.get("failed_tests", []))
    )


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


def _ensure_fork(github_client, upstream, base_branch: str):
    """Fork ``upstream`` for the token's user and wait until it is usable."""

    import time

    fork = github_client.get_user().create_fork(upstream)

    for _ in range(30):
        try:
            fork.get_branch(base_branch)
            return fork
        except Exception:  # noqa: BLE001 - fork still being created
            time.sleep(2)

    raise RuntimeError(
        "Publishing blocked: the fork was created but is not ready yet. "
        "Retry in a minute."
    )


def publish_validated_repairs(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Publish approved repairs through a GitHub Pull Request."""

    status = state.get("status")
    test_results = state.get("working_test_results", {})

    if status == "COMPLETED_WITH_REMAINING_FAILURES":
        # Repositories often have failing tests that were already failing
        # before ASEA touched them. Allow publishing as long as the
        # repairs introduced no new failures compared with the baseline.
        baseline = state.get("test_results")

        if not baseline or not test_results or not _no_new_failures(
            baseline, test_results
        ):
            raise RuntimeError(
                "Publishing blocked: ASEA workflow did not complete "
                "successfully."
            )

    elif status != "COMPLETED":
        raise RuntimeError(
            "Publishing blocked: ASEA workflow did not complete successfully."
        )

    elif not test_results.get("success", False):
        raise RuntimeError(
            "Publishing blocked: final tests did not pass."
        )

    original_repo = Path(state["repo_path"]).resolve()
    working_repo = Path(state["working_repo_path"]).resolve()

    if not original_repo.is_dir() or not working_repo.is_dir():
        raise FileNotFoundError(
            "Original repository or validated working repository is missing."
        )

    settings = get_settings()

    if not settings.github_token:
        raise ValueError("GITHUB_TOKEN is missing from .env.")

    # The target is whatever the analysed repository's origin points at,
    # so publishing works for any GitHub repository, not one in .env.
    source_git_repo, git_root = validate_source_repository(original_repo)
    repository_name = normalize_github_repository(
        source_git_repo.remotes.origin.url
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

        if (
            github_repository.full_name.casefold()
            != repository_name.casefold()
        ):
            raise RuntimeError(
                "Publishing blocked: GitHub repository identity mismatch."
            )

        # Always target the repository's own default branch.
        base_branch = github_repository.default_branch

        can_push_directly = bool(
            getattr(github_repository.permissions, "push", False)
        )

        push_remote = "origin"
        head_owner = None

        if not can_push_directly:
            # No write access: publish from the user's fork instead.
            fork_repository = _ensure_fork(
                github_client, github_repository, base_branch
            )
            head_owner = fork_repository.owner.login

        publishing_repo = Repo.clone_from(
            github_repository.clone_url,
            checkout_path,
            branch=base_branch,
            depth=1,
        )

        if not can_push_directly:
            publishing_repo.create_remote(
                "fork", fork_repository.clone_url
            )
            push_remote = "fork"

        branch_name = (
            "asea/repair-"
            + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid4().hex[:6]
        )

        publishing_repo.git.checkout(
            "-b",
            branch_name,
        )

        repository_prefix = original_repo.relative_to(git_root)

        copied_files = []

        for relative_path in repair_files:

            source_file = (working_repo / relative_path).resolve()

            try:
                source_file.relative_to(working_repo)
            except ValueError as error:
                raise ValueError(
                "Unsafe source repair file path detected."
                ) from error

            if not source_file.is_file():
                raise FileNotFoundError(
                    f"Validated repair file is missing: {relative_path}"
                )

            destination_relative = (
                repository_prefix / relative_path
            )

            destination_file = (
                checkout_path / destination_relative
            ).resolve()

            try:
                destination_file.relative_to(checkout_path.resolve())
            except ValueError as error:
                raise ValueError(
        "Unsafe destination repair file path detected."
    ) from error

            if any(
                part in EXCLUDED_PARTS
                for part in destination_relative.parts
            ):
                raise ValueError(
                    "Publishing blocked: sensitive or excluded file detected."
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

        asea_actor = Actor("ASEA", "asea@users.noreply.github.com")

        publishing_repo.index.commit(
            f"ASEA: repair {len(copied_files)} validated file(s)",
            author=asea_actor,
            committer=asea_actor,
        )

        encoded_token = base64.b64encode(
            f"x-access-token:{settings.github_token}".encode()
        ).decode("ascii")

        authorization = f"AUTHORIZATION: basic {encoded_token}"

        git_environment = os.environ.copy()
        git_environment["GIT_CONFIG_COUNT"] = "1"
        git_environment["GIT_CONFIG_KEY_0"] = "http.extraheader"
        git_environment["GIT_CONFIG_VALUE_0"] = authorization

        publishing_repo.git.execute(
            [
                "git",
                "push",
                push_remote,
                f"{branch_name}:{branch_name}",
            ],
            env=git_environment,
        )

        pull_request = create_pull_request(
            head_branch=branch_name,
            base_branch=base_branch,
            repository_name=repository_name,
            head_owner=head_owner,
            title="ASEA: Automated validated code repairs",
            body=(
                "## Automated Repair Summary\n\n"
                "This pull request contains repairs generated by ASEA.\n\n"
                f"- Repaired files: {len(copied_files)}\n"
                f"- Successfully repaired bugs: "
                f"{len(state.get('repaired_bugs', []))}\n"
                "- Human review: approved\n"
                + (
                    "- Final validation: passed\n\n"
                    if status == "COMPLETED"
                    else "- Final validation: no new test failures "
                    "(some tests were already failing before ASEA)\n\n"
                )
                +
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
            "repository": repository_name,
            "pushed_to_fork": not can_push_directly,
            "pull_request": pull_request,
        }

    except GitCommandError:
        raise RuntimeError(
        "Git publishing operation failed. "
        "Check GitHub permissions, network connectivity, "
        "and branch conflicts. Credentials were not included."
    ) from None

    finally:
        shutil.rmtree(
            temporary_root,
            ignore_errors=True,
        )