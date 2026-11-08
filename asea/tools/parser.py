"""Repository parsing through registered language adapters."""

import json
from pathlib import Path

from asea.languages.python import PythonAdapter
from asea.languages.registry import LanguageRegistry, get_default_registry


class RepositoryParser:
    """Parse all source files for which an adapter is registered."""

    IGNORED_DIRECTORIES = {
        ".git", ".venv", "venv", "__pycache__", "node_modules",
        "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "artifacts", "workspaces",
    }

    def __init__(self, registry: LanguageRegistry | None = None):
        self.registry = registry or get_default_registry()

    def parse_file(self, file_path):
        path = Path(file_path)
        adapter = self.registry.resolve(path)
        if adapter is None:
            raise ValueError(f"No language adapter registered for {path.suffix}")
        parsed = adapter.parse(path)
        return {
            "file": str(path),
            "language": parsed.language,
            "functions": parsed.functions,
            "classes": parsed.classes,
            "imports": parsed.imports,
            "syntax_valid": parsed.syntax_valid,
            "syntax_error": parsed.syntax_error,
            "repair_supported": adapter.supports_repair,
        }

    def build_repository_map(self, repo_path, output_file):
        root = Path(repo_path).resolve()
        repo_map = {"repository": root.name, "files": []}
        extensions = set(self.registry.supported_extensions())

        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file() or file_path.is_symlink():
                continue
            if file_path.suffix.lower() not in extensions:
                continue
            if self.IGNORED_DIRECTORIES.intersection(
                file_path.relative_to(root).parts
            ):
                continue

            parsed = self.parse_file(file_path)
            parsed["file"] = file_path.relative_to(root).as_posix()
            repo_map["files"].append(parsed)

        destination = Path(output_file)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(repo_map, indent=4),
            encoding="utf-8",
        )
        return repo_map


class PythonRepositoryParser(RepositoryParser):
    """Backward-compatible Python-only parser facade."""

    def __init__(self):
        self.registry = LanguageRegistry([PythonAdapter()])
