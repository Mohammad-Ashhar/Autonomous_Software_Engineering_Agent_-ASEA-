import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from git import GitCommandError, InvalidGitRepositoryError, Repo


class RepositoryManager:

    def __init__(self, workspace="workspaces"):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def validate_github_url(self, url):
        parsed = urlparse(url)

        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Invalid GitHub repository URL.") from error

        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Only plain HTTPS GitHub repository URLs are allowed.")

        parts = parsed.path.strip("/").split("/")

        if (
            len(parts) != 2
            or not all(parts)
            or any(part in {".", ".."} for part in parts)
            or any("%" in part or "\\" in part for part in parts)
        ):
            raise ValueError("Invalid GitHub repository URL.")

        return True

    def _existing_clone(self, destination, requested_url):
        """Validate and return an already-published repository clone."""
        if destination.is_symlink():
            raise ValueError(
                f"Existing workspace clone cannot be a symbolic link: "
                f"{destination}"
            )

        try:
            repo = Repo(
                str(destination),
                search_parent_directories=False
            )
            existing_url = repo.remotes.origin.url.rstrip("/")
        except (InvalidGitRepositoryError, AttributeError, IndexError) as error:
            raise ValueError(
                f"Existing workspace directory is not a valid Git clone: "
                f"{destination}"
            ) from error

        if (
            existing_url.removesuffix(".git").lower()
            != requested_url.rstrip("/").removesuffix(".git").lower()
        ):
            raise ValueError(
                "Existing workspace repository has a different remote URL."
            )

        return destination

    def clone_repository(self, url):
        self.validate_github_url(url)

        owner, repository = urlparse(url).path.strip("/").split("/")
        repository = repository.removesuffix(".git")

        # Include the owner to avoid collisions between repositories that
        # share a name but belong to different GitHub accounts.
        destination = self.workspace / f"{owner}__{repository}"

        if destination.exists():
            return self._existing_clone(destination, url)

        # Clone into a unique sibling directory. Publishing with an atomic
        # rename ensures another process never observes a partial clone.
        temporary_path = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.clone_",
                dir=str(self.workspace),
            )
        )

        try:
            Repo.clone_from(url, str(temporary_path))

            try:
                temporary_path.rename(destination)
                return destination
            except FileExistsError:
                # Another worker won the race and published the same clone.
                return self._existing_clone(destination, url)
            except OSError:
                if destination.exists():
                    return self._existing_clone(destination, url)
                raise

        except GitCommandError as error:
            raise RuntimeError(
                f"Repository cloning failed: {error}"
            ) from error
        finally:
            if temporary_path.exists():
                shutil.rmtree(temporary_path, ignore_errors=True)

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
            if path.is_symlink():
                continue

            relative_path = path.relative_to(repo_path)
            if ignored.intersection(relative_path.parts):
                continue

            resolved_path = path.resolve()
            if repo_path not in resolved_path.parents:
                continue

            python_files.append(resolved_path)

        return sorted(python_files)