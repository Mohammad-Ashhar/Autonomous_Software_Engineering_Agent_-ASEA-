from pathlib import Path
from typing import Any

import tree_sitter_python
from tree_sitter import Language, Parser


PYTHON_LANGUAGE = Language(
    tree_sitter_python.language()
)


def create_parser() -> Parser:
    """Create a Tree-sitter Python parser."""

    return Parser(PYTHON_LANGUAGE)


def get_node_text(source: bytes, node: Any) -> str:
    """Extract source text corresponding to a syntax node."""

    return source[
        node.start_byte:node.end_byte
    ].decode("utf-8", errors="replace")


def parse_python_file(
    file_path: str | Path
) -> dict[str, Any]:
    """
    Parse a Python source file using Tree-sitter.

    Extracts:
        - Functions
        - Classes
        - Syntax errors
        - Source line information
    """

    path = Path(file_path)

    source = path.read_bytes()

    parser = create_parser()

    tree = parser.parse(source)

    declarations = []
    syntax_errors = []

    def traverse(node: Any) -> None:

        if node.type in {
            "function_definition",
            "class_definition"
        }:

            name_node = node.child_by_field_name("name")

            name = (
                get_node_text(source, name_node)
                if name_node
                else "<unknown>"
            )

            declarations.append({
                "kind": (
                    "function"
                    if node.type == "function_definition"
                    else "class"
                ),
                "name": name,
                "start_line": node.start_point.row + 1,
                "end_line": node.end_point.row + 1,
            })

        if node.type == "ERROR" or node.is_missing:

            syntax_errors.append({
                "line": node.start_point.row + 1,
                "column": node.start_point.column + 1,
            })

        for child in node.children:
            traverse(child)

    traverse(tree.root_node)

    return {
        "file": str(path),
        "has_syntax_errors": tree.root_node.has_error,
        "declarations": declarations,
        "syntax_errors": syntax_errors,
    }


def parse_repository(
    repo_path: str | Path,
    python_files: list[str]
) -> list[dict[str, Any]]:
    """Parse all Python files discovered in a repository."""

    root = Path(repo_path).resolve()

    results = []

    for relative_path in python_files:

        file_path = (root / relative_path).resolve()

        if root not in file_path.parents:
            results.append({
                "file": relative_path,
                "error": "Path escaped repository root",
            })
            continue

        try:

            result = parse_python_file(file_path)

            results.append(result)

        except (OSError, ValueError) as error:

            results.append({
                "file": relative_path,
                "error": str(error),
            })

    return results

