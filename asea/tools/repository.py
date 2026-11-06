
import shutil
from pathlib import Path
from urllib.parse import urlparse

from git import GitCommandError, Repo


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

        repo_name = urlparse(url).path.strip("/").split("/")[-1]

        repo_name = repo_name.removesuffix(".git")

        destination = self.workspace / repo_name

        if destination.exists():
            raise FileExistsError(
                f"Repository already exists: {destination}"
            )

        try:
            Repo.clone_from(url, str(destination))
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
