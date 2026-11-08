import shutil
from pathlib import Path
from urllib.parse import urlparse

from git import GitCommandError, InvalidGitRepositoryError, Repo


class RepositoryManager:

    def __init__(self, workspace="workspaces"):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def validate_github_url(self, url):
        parsed = urlparse(url)

        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Only HTTPS GitHub repository URLs are allowed.")

        parts = parsed.path.strip("/").split("/")

        if len(parts) != 2 or not all(parts):
            raise ValueError("Invalid GitHub repository URL.")

        return True

    def clone_repository(self, url):
        self.validate_github_url(url)

        owner, repo_name = urlparse(url).path.strip("/").split("/")
        repo_name = repo_name.removesuffix(".git")

        # owner__repo avoids collisions between same-named repositories
        # belonging to different owners.
        destination = self.workspace / f"{owner}__{repo_name}"

        # Reuse an existing clone only if it belongs to the requested repository.
        if destination.exists():
            try:
                repo = Repo(
                    str(destination),
                    search_parent_directories=False
                )

                existing_url = repo.remotes.origin.url.rstrip("/")
                requested_url = url.rstrip("/")

                if (
                    existing_url.removesuffix(".git").casefold()
                    != requested_url.removesuffix(".git").casefold()
                ):
                    raise ValueError(
                        "Existing workspace repository has a different remote URL."
                    )

            except (InvalidGitRepositoryError, AttributeError) as error:
                raise ValueError(
                    f"Existing workspace directory is not a valid Git clone: "
                    f"{destination}"
                ) from error

            # Refresh so repairs are made against the latest code.
            # Best effort: a failed fast-forward keeps the existing clone.
            try:
                if not repo.is_dirty(untracked_files=False):
                    repo.git.pull("--ff-only")
            except GitCommandError:
                pass

            return destination

        # Shallow clone: much faster, and ASEA never needs history.
        try:
            Repo.clone_from(url, str(destination), depth=1)
            return destination

        except GitCommandError as error:
            if destination.exists():
                shutil.rmtree(destination)

            raise RuntimeError(
                f"Repository cloning failed: {error}"
            ) from error

    def load_local_repository(self, folder):
        source = Path(folder).resolve()

        if not source.is_dir():
            raise NotADirectoryError(
                f"Directory not found: {source}"
            )

        if source == self.workspace or self.workspace in source.parents:
            raise ValueError(
                "Source repository cannot be inside the workspace."
            )

        return source

    def load_cloned_repository(self, folder):
        source = Path(folder).resolve()

        if not source.is_dir():
            raise NotADirectoryError(
                f"Cloned repository not found: {source}"
            )

        if source.parent != self.workspace:
            raise ValueError(
                "Cloned repository must be directly inside the workspace."
            )

        try:
            repo = Repo(
                str(source),
                search_parent_directories=False
            )

            if Path(repo.working_tree_dir).resolve() != source:
                raise ValueError("Invalid cloned repository path.")

        except (InvalidGitRepositoryError, ValueError) as error:
            raise ValueError(
                "Workspace directory is not a valid Git repository."
            ) from error

        return source

    def get_python_files(self, repo_path):
        repo_path = Path(repo_path).resolve()

        ignored = {
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            "node_modules",
            "dist",
            "build",
            ".pytest_cache",
            ".mypy_cache"
        }

        python_files = []

        for path in repo_path.rglob("*.py"):
            if ignored.intersection(
                path.relative_to(repo_path).parts
            ):
                continue

            python_files.append(path)

        return sorted(python_files)