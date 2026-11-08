"""Python language adapter backed by the standard-library AST."""

import ast
from pathlib import Path

from asea.languages.base import ParsedSource


class PythonAdapter:
    name = "Python"
    extensions = (".py", ".pyi")
    supports_repair = True

    def parse(self, file_path: Path, source: str | None = None) -> ParsedSource:
        path = Path(file_path)
        text = source if source is not None else path.read_text(
            encoding="utf-8-sig", errors="replace"
        )
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            return ParsedSource(
                path=path,
                language=self.name,
                syntax_valid=False,
                syntax_error=f"line {exc.lineno}: {exc.msg}",
            )

        result = ParsedSource(path=path, language=self.name)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent_class = next(
                    (
                        parent.name
                        for parent in ast.walk(tree)
                        if isinstance(parent, ast.ClassDef)
                        and parent.lineno <= node.lineno <= parent.end_lineno
                    ),
                    None,
                )
                result.functions.append({
                    "name": node.name,
                    "class": parent_class,
                    "start_line": node.lineno,
                    "end_line": node.end_lineno,
                })
            elif isinstance(node, ast.ClassDef):
                result.classes.append({
                    "name": node.name,
                    "start_line": node.lineno,
                    "end_line": node.end_lineno,
                })
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                result.imports.append(ast.get_source_segment(text, node) or "")
        return result

    def validate_source(self, source: str, file_path: Path) -> None:
        try:
            ast.parse(source, filename=str(file_path))
        except SyntaxError as exc:
            raise ValueError(
                f"Generated source contains invalid Python: {exc}"
            ) from exc

    def repair_instructions(self) -> str:
        return (
            "Return complete corrected Python source code only. "
            "Preserve existing behavior and change only what is necessary."
        )
