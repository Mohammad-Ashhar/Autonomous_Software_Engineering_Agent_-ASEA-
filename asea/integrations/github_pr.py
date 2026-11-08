from typing import Any

from github import Auth, Github
from github.GithubException import GithubException

from asea.core.config import get_settings


def get_github_repository(repository_name: str | None = None) -> Any:
    """
    Authenticate with GitHub and return a repository.

    Uses ``repository_name`` (owner/repo) when given, otherwise the
    repository configured in .env.
    """

    settings = get_settings()

    if not settings.github_token:
        raise ValueError(
            "GITHUB_TOKEN is missing from the .env file."
        )

    repository_name = repository_name or settings.github_repository

    if not repository_name:
        raise ValueError(
            "No GitHub repository was specified."
        )

    try:
        authentication = Auth.Token(settings.github_token)

        github_client = Github(auth=authentication)

        repository = github_client.get_repo(repository_name)

        return repository

    except GithubException as error:
        raise RuntimeError(
            f"GitHub authentication or repository access failed: {error}"
        ) from error


def create_pull_request(
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    repository_name: str | None = None,
    head_owner: str | None = None,
) -> dict[str, Any]:
    """
    Create a GitHub Pull Request.

    If an open PR already exists for the same source and target
    branches, return the existing PR instead of creating a duplicate.
    """

    if not head_branch.strip():
        raise ValueError("Source branch cannot be empty.")

    if not base_branch.strip():
        raise ValueError("Target branch cannot be empty.")

    if not title.strip():
        raise ValueError("Pull Request title cannot be empty.")

    repository = get_github_repository(repository_name)

    try:
        # Branches pushed to a fork are referenced as "forkowner:branch".
        owner = head_owner or repository.owner.login

        existing_prs = repository.get_pulls(
            state="open",
            head=f"{owner}:{head_branch}",
            base=base_branch,
        )

        for pull_request in existing_prs:
            return {
                "status": "EXISTING",
                "number": pull_request.number,
                "title": pull_request.title,
                "url": pull_request.html_url,
                "head_branch": head_branch,
                "base_branch": base_branch,
            }

        pull_request = repository.create_pull(
            title=title,
            body=body,
            head=f"{owner}:{head_branch}",
            base=base_branch,
        )

        return {
            "status": "CREATED",
            "number": pull_request.number,
            "title": pull_request.title,
            "url": pull_request.html_url,
            "head_branch": head_branch,
            "base_branch": base_branch,
        }

    except GithubException as error:
        raise RuntimeError(
            f"Pull Request creation failed: {error}"
        ) from error