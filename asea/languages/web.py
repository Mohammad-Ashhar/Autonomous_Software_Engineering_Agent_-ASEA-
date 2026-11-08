"""Tree-sitter adapter for JavaScript and TypeScript source inventory."""

from pathlib import Path

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Parser

from asea.languages.base import ParsedSource


class TreeSitterWebAdapter:
    """Parse JavaScript/TypeScript symbols without claiming repair support."""

    supports_repair = False

    def __init__(self, name: str, extensions: tuple[str, ...], grammar):
        self.name = name
        self.extensions = extensions
        self._parser = Parser(Language(grammar()))

    def parse(self, file_path: Path, source: str | None = None) -> ParsedSource:
        path = Path(file_path)
        text = source if source is not None else path.read_text(
            encoding="utf-8-sig", errors="replace"
        )
        encoded = text.encode("utf-8")
        tree = self._parser.parse(encoded)
        result = ParsedSource(
            path=path,
            language=self.name,
            syntax_valid=not tree.root_node.has_error,
            syntax_error=(
                "Tree-sitter reported syntax errors"
                if tree.root_node.has_error else None
            ),
        )

        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in {"function_declaration", "method_definition"}:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    result.functions.append({
                        "name": encoded[
                            name_node.start_byte:name_node.end_byte
                        ].decode("utf-8", errors="replace"),
                        "class": None,
                        "start_line": node.start_point.row + 1,
                        "end_line": node.end_point.row + 1,
                    })
            elif node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    result.classes.append({
                        "name": encoded[
                            name_node.start_byte:name_node.end_byte
                        ].decode("utf-8", errors="replace"),
                        "start_line": node.start_point.row + 1,
                        "end_line": node.end_point.row + 1,
                    })
            elif node.type == "import_statement":
                result.imports.append(
                    encoded[node.start_byte:node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                )
            stack.extend(reversed(node.children))
        return result

    def validate_source(self, source: str, file_path: Path) -> None:
        parsed = self.parse(file_path, source)
        if not parsed.syntax_valid:
            raise ValueError(
                f"Generated {self.name} source is syntactically invalid."
            )

    def repair_instructions(self) -> str:
        raise NotImplementedError(
            f"{self.name} repair is not enabled until a matching "
            "test runner and validation policy are configured."
        )


def javascript_adapter() -> TreeSitterWebAdapter:
    return TreeSitterWebAdapter(
        "JavaScript",
        (".js", ".jsx", ".mjs", ".cjs"),
        tree_sitter_javascript.language,
    )


def typescript_adapter() -> TreeSitterWebAdapter:
    return TreeSitterWebAdapter(
        "TypeScript",
        (".ts",),
        tree_sitter_typescript.language_typescript,
    )


def tsx_adapter() -> TreeSitterWebAdapter:
    return TreeSitterWebAdapter(
        "TypeScript",
        (".tsx",),
        tree_sitter_typescript.language_tsx,
    )
