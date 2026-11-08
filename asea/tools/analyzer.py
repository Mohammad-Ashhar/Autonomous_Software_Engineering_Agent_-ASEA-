from pathlib import Path

from asea.tools.parser import RepositoryParser
from asea.tools.repository import RepositoryManager
from asea.tools.repository_inventory import build_repository_inventory


class RepositoryAnalyzer:
    """Build a multi-language symbol map alongside a repository-wide inventory.

    Inventory coverage and parser coverage are intentionally reported
    separately: discovering a language does not imply that ASEA can analyze
    or repair it yet.
    """

    def __init__(self, workspace="workspaces"):
        self.repository_manager = RepositoryManager(workspace)
        self.parser = RepositoryParser()

    def analyze_local_repository(
        self, folder, output_file="artifacts/repo_map.json"
    ):
        repo_path = self.repository_manager.load_local_repository(folder)
        return self._analyze_repository(repo_path, output_file)

    def analyze_cloned_repository(
        self, folder, output_file="artifacts/repo_map.json"
    ):
        repo_path = self.repository_manager.load_cloned_repository(folder)
        return self._analyze_repository(repo_path, output_file)

    def _analyze_repository(self, repo_path, output_file):
        repo_path = Path(repo_path).resolve()
        inventory = build_repository_inventory(repo_path)
        repo_map = self.parser.build_repository_map(repo_path, output_file)

        python_files = repo_map["files"]
        total_functions = sum(len(item["functions"]) for item in python_files)
        total_classes = sum(len(item["classes"]) for item in python_files)
        total_imports = sum(len(item["imports"]) for item in python_files)

        return {
            "repository": repo_map["repository"],
            "path": str(repo_path),
            # Count of source files parsed by a registered adapter.
            "total_files": len(python_files),
            "total_source_files": inventory["total_source_files"],
            "repository_file_count": inventory["total_files"],
            "total_functions": total_functions,
            "total_classes": total_classes,
            "total_imports": total_imports,
            "language_count": inventory["language_count"],
            "languages": inventory["languages"],
            "manifests": inventory["manifests"],
            "parsed_languages": sorted({
                item["language"] for item in python_files
            }),
            "inventory_only_languages": sorted(
                set(inventory["languages"])
                - {item["language"] for item in python_files}
            ),
            "repair_supported_languages": sorted({
                item["language"] for item in python_files
                if item["repair_supported"]
            }),
            "output_file": str(Path(output_file).resolve()),
        }
