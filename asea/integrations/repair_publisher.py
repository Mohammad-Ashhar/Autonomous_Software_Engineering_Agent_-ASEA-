import base64
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

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
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    ".ssh",
    ".aws",
    ".azure",
    ".kube",
    ".gnupg",
    ".terraform",
    ".vault-token",
    "application_default_credentials.json",
    "service-account.json",
    "service_account.json",
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

SENSITIVE_FILE_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".secret",
    ".secrets",
    ".token",
    ".credentials",
    ".tfstate",
    ".tfstate.backup",
)


def is_excluded_repair_path(path: Path) -> bool:
    """Reject generated, environment, credential, and private-key paths."""

    excluded_parts = {part.casefold() for part in EXCLUDED_PARTS}

    normalized_parts = tuple(part.casefold() for part in path.parts)

    if any(
        normalized_parts[index:index + 2] == (".docker", "config.json")
        for index in range(len(normalized_parts) - 1)
    ):
        return True

    for normalized in normalized_parts:
        if (
            normalized in excluded_parts
            or normalized == ".envrc"
            or normalized.startswith(".env.")
            or normalized.endswith(SENSITIVE_FILE_SUFFIXES)
        ):
            return True

    return False


def normalize_github_repository(repository_url: str) -> str:
    """Extract owner/repository from an HTTPS or SSH GitHub URL."""

    repository_url = repository_url.strip()

    if repository_url.startswith("git@github.com:"):
        repository_path = repository_url.split(":", 1)[1]
        if any(value in repository_path for value in ("?", "#", "\\\\")):
            raise ValueError("Invalid GitHub repository URL.")

    else:
        parsed_url = urlsplit(repository_url)

        if parsed_url.hostname != "github.com":
            raise ValueError(
                "Publishing is allowed only for GitHub repositories."
            )

        if parsed_url.scheme not in ("https", "ssh"):
            raise ValueError("Unsupported GitHub remote URL scheme.")

        if (
            parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.port not in (
                None,
                443 if parsed_url.scheme == "https" else 22,
            )
            or (parsed_url.scheme == "https" and parsed_url.username)
            or (
                parsed_url.scheme == "ssh"
                and parsed_url.username not in (None, "git")
            )
        ):
            raise ValueError("Invalid GitHub repository URL.")

        repository_path = parsed_url.path.strip("/")

    repository_path = repository_path.removesuffix(".git")
    parts = repository_path.split("/")

    if (
        len(parts) != 2
        or not all(parts)
        or any(part in {".", ".."} for part in parts)
        or any(
            not all(char.isalnum() or char in "._-" for char in part)
            for part in parts
        )
    ):
        raise ValueError("Invalid GitHub repository URL.")

    return f"{parts[0].casefold()}/{parts[1].casefold()}"


def validate_source_repository(
    original_repo: Path,
) -> tuple[Repo, Path, str]:
    """Identify the GitHub repository from the user's supplied source checkout."""

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
    except ValueError as error:
        raise RuntimeError(
            "Publishing blocked: source repository is not a supported "
            f"GitHub remote: {error}"
        ) from error

    return source_git_repo, git_root, source_repository


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

        if is_excluded_repair_path(relative_path):
            continue

        working_file = (working_repo / relative_path).resolve()

        try:
            working_file.relative_to(working_repo.resolve())
        except ValueError:
            continue

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

    settings = get_settings()

    if not settings.github_token:
        raise ValueError("GITHUB_TOKEN is missing from .env.")

    # The target is derived from the repository URL/path supplied to ASEA.
    # Credentials authenticate the user; they must not pin ASEA to one repo.
    _, git_root, repository_name = validate_source_repository(original_repo)

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

        base_branch = github_repository.default_branch
        if not base_branch:
            raise RuntimeError(
                "Publishing blocked: GitHub did not report a default branch."
            )
        github_repository.get_branch(base_branch)

        clone_url = github_repository.clone_url

        # Use the token for both private-repository clone and push without
        # embedding credentials in the remote URL or Git configuration.
        encoded_token = base64.b64encode(
            f"x-access-token:{settings.github_token}".encode()
        ).decode("ascii")
        authorization = f"AUTHORIZATION: basic {encoded_token}"
        git_environment = os.environ.copy()
        git_environment["GIT_CONFIG_COUNT"] = "1"
        git_environment["GIT_CONFIG_KEY_0"] = "http.extraheader"
        git_environment["GIT_CONFIG_VALUE_0"] = authorization

        publishing_repo = Repo.clone_from(
            clone_url,
            checkout_path,
            branch=base_branch,
            depth=1,
            env=git_environment,
        )

        # Timestamp keeps branches readable; a random suffix prevents
        # concurrent repairs in the same second from colliding.
        branch_name = (
            "asea/repair-"
            + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid4().hex[:8]
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

            if is_excluded_repair_path(destination_relative):
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

        publishing_repo.index.commit(
            f"ASEA: repair {len(copied_files)} validated file(s)"
        )

        publishing_repo.git.execute(
            [
                "git",
                "push",
                "origin",
                f"{branch_name}:{branch_name}",
            ],
            env=git_environment,
        )

        pull_request = create_pull_request(
            repository=github_repository,
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
            "repository": repository_name,
            "commit": publishing_repo.head.commit.hexsha,
            "files": copied_files,
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