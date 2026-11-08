from pathlib import Path

from asea.tools.parser import PythonRepositoryParser
from asea.tools.repository import RepositoryManager


class RepositoryAnalyzer:

    def __init__(self, workspace="workspaces"):
        self.repository_manager = RepositoryManager(workspace)
        self.parser = PythonRepositoryParser()

    def analyze_local_repository(
        self,
        folder,
        output_file="artifacts/repo_map.json"
    ):
        repo_path = self.repository_manager.load_local_repository(folder)

        return self._analyze_repository(repo_path, output_file)

    def analyze_cloned_repository(
        self,
        folder,
        output_file="artifacts/repo_map.json"
    ):
        repo_path = self.repository_manager.load_cloned_repository(folder)

        return self._analyze_repository(repo_path, output_file)

    def analyze_path(
        self,
        folder,
        output_file="artifacts/repo_map.json"
    ):
        """Analyse any repository folder (cloned or local)."""

        repo_path = Path(folder).resolve()

        if not repo_path.is_dir():
            raise NotADirectoryError(f"Directory not found: {repo_path}")

        return self._analyze_repository(repo_path, output_file)

    def _analyze_repository(self, repo_path, output_file):
        repo_map = self.parser.build_repository_map(
            repo_path,
            output_file
        )

        total_files = len(repo_map["files"])

        total_functions = sum(
            len(file["functions"])
            for file in repo_map["files"]
        )

        total_classes = sum(
            len(file["classes"])
            for file in repo_map["files"]
        )

        total_imports = sum(
            len(file["imports"])
            for file in repo_map["files"]
        )

        return {
            "repository": repo_map["repository"],
            "path": str(repo_path),
            "total_files": total_files,
            "total_functions": total_functions,
            "total_classes": total_classes,
            "total_imports": total_imports,
            "output_file": str(Path(output_file).resolve())
        }