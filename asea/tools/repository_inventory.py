"""Repository-wide source and manifest inventory.

This module identifies repository composition without executing project code.
It does not claim that every inventoried language is currently analyzable.
"""
import os
from collections import Counter
from pathlib import Path

IGNORED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".venv", "venv", "env",
    "__pycache__", "node_modules", "vendor", "dist", "build",
    "target", "out", ".next", ".nuxt", ".turbo", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".idea", ".vscode", "coverage", "htmlcov",
}

LANGUAGE_EXTENSIONS = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".c": "C",
    ".h": "C/C++",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sc": "Scala",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hrl": "Erlang",
    ".hs": "Haskell",
    ".lhs": "Haskell",
    ".lua": "Lua",
    ".pl": "Perl",
    ".pm": "Perl",
    ".r": "R",
    ".R": "R",
    ".jl": "Julia",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".sql": "SQL",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

MANIFEST_FILES = {
    "pyproject.toml", "requirements.txt", "Pipfile", "poetry.lock",
    "uv.lock", "setup.py", "setup.cfg",
    "package.json", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "bun.lockb", "bun.lock",
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.properties",
    "composer.json", "Gemfile", "Gemfile.lock",
    "*.sln", "*.csproj", "*.fsproj",
    "Dockerfile", "docker-compose.yml", "compose.yaml",
    "Makefile", "CMakeLists.txt",
}


def build_repository_inventory(repo_path: str | Path) -> dict:
    """Return a deterministic, non-executing inventory of repository files."""
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Repository directory not found: {root}")

    language_counts: Counter[str] = Counter()
    manifest_paths: list[str] = []
    source_file_count = 0
    total_file_count = 0

    for directory, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(directory)
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in IGNORED_DIRECTORIES
            and not (current / name).is_symlink()
        )

        for filename in sorted(filenames):
            path = current / filename
            if path.is_symlink() or not path.is_file():
                continue

            relative = path.relative_to(root).as_posix()
            total_file_count += 1
            language = LANGUAGE_EXTENSIONS.get(path.suffix)
            if language:
                language_counts[language] += 1
                source_file_count += 1

            if filename in MANIFEST_FILES or (
                filename.endswith((".sln", ".csproj", ".fsproj"))
            ):
                manifest_paths.append(relative)

    languages = dict(sorted(language_counts.items()))
    return {
        "total_files": total_file_count,
        "total_source_files": source_file_count,
        "language_count": len(languages),
        "languages": languages,
        "manifests": sorted(manifest_paths),
        "parsed_languages": ["Python"],
        "inventory_only_languages": sorted(
            language for language in languages if language != "Python"
        ),
    }
