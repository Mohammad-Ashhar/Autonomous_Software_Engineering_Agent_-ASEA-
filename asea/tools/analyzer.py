from pathlib import Path
from uuid import uuid4

from asea.tools.parser import PythonRepositoryParser
from asea.tools.repository import RepositoryManager

DEPENDENCY_MANIFESTS = {
    "Pipfile",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "Pipfile.lock",
    "environment.yml",
    "conda.yml",
}
TEST_CONFIGURATION = {
    "pytest.ini",
    "tox.ini",
    "nose2.cfg",
}


class RepositoryAnalyzer:

    def __init__(self, workspace="workspaces"):
        self.repository_manager = RepositoryManager(workspace)
        self.parser = PythonRepositoryParser()

    def analyze_local_repository(
        self,
        folder,
        output_file=None
    ):
        repo_path = self.repository_manager.load_local_repository(folder)

        return self._analyze_repository(repo_path, output_file)

    def analyze_cloned_repository(
        self,
        folder,
        output_file=None
    ):
        repo_path = self.repository_manager.load_cloned_repository(folder)

        return self._analyze_repository(repo_path, output_file)

    @staticmethod
    def _repository_preflight(repo_path):
        python_files = RepositoryManager.get_python_files(repo_path)
        test_files = [
            path for path in python_files
            if "tests" in path.relative_to(repo_path).parts
            or path.name.startswith("test_")
            or path.name.endswith("_test.py")
        ]

        manifests = sorted(
            path.name
            for path in repo_path.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and (
                path.name in DEPENDENCY_MANIFESTS
                or (
                    path.name.startswith("requirements")
                    and path.suffix == ".txt"
                )
            )
        )
        test_configs = sorted(
            path.name
            for path in repo_path.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and (
                path.name in TEST_CONFIGURATION
                or (
                    path.name == "pyproject.toml"
                    and "[tool.pytest" in path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                )
                or (
                    path.name == "setup.cfg"
                    and "[tool:pytest]" in path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                )
            )
        )

        frameworks = set()
        for test_file in test_files:
            try:
                source = test_file.read_text(
                    encoding="utf-8-sig",
                    errors="replace",
                )
            except OSError:
                continue
            if "unittest" in source:
                frameworks.add("unittest")
            if "pytest" in source or "assert " in source:
                frameworks.add("pytest")

        if "pytest.ini" in test_configs or "tox.ini" in test_configs:
            frameworks.add("pytest")

        for manifest in manifests:
            if manifest not in {
                "requirements.txt",
                "pyproject.toml",
                "setup.cfg",
            }:
                continue
            try:
                content = (repo_path / manifest).read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                continue
            if "pytest" in content.lower():
                frameworks.add("pytest")

        return {
            "python_file_count": len(python_files),
            "test_file_count": len(test_files),
            "test_files": [
                str(path.relative_to(repo_path))
                for path in test_files
            ],
            "test_frameworks": sorted(frameworks),
            "test_configuration": test_configs,
            "dependency_manifests": manifests,
            "has_existing_tests": bool(test_files),
        }

    def _analyze_repository(self, repo_path, output_file):
        # Each analysis gets its own artifact path so simultaneous API/CLI
        # requests cannot overwrite another run's repository map.
        if output_file is None:
            output_file = (
                Path("artifacts")
                / f"repo_map_{uuid4().hex}.json"
            )

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

        syntax_errors = [
            {
                "file": file["file"],
                **error,
            }
            for file in repo_map["files"]
            for error in file.get("syntax_errors", [])
        ]
        analysis_errors = [
            {
                "file": file["file"],
                "message": error,
            }
            for file in repo_map["files"]
            for error in file.get("analysis_errors", [])
        ]

        return {
            "repository": repo_map["repository"],
            "path": str(repo_path),
            "total_files": total_files,
            "total_functions": total_functions,
            "total_classes": total_classes,
            "total_imports": total_imports,
            "syntax_error_count": len(syntax_errors),
            "syntax_errors": syntax_errors,
            "analysis_error_count": len(analysis_errors),
            "analysis_errors": analysis_errors,
            "preflight": self._repository_preflight(repo_path),
            "output_file": str(Path(output_file).resolve())
        }
