import ast
import io
import json
import tokenize
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from asea.tools.repository import RepositoryManager

MAX_PYTHON_FILE_SIZE_BYTES = 2 * 1024 * 1024


class PythonRepositoryParser:

    def __init__(self):
        self.language = Language(tspython.language())
        self.parser = Parser(self.language)

    def parse_file(self, file_path):
        file_path = Path(file_path)
        result = {
            "file": str(file_path),
            "functions": [],
            "classes": [],
            "imports": [],
            "syntax_errors": [],
            "analysis_errors": [],
        }

        try:
            if file_path.stat().st_size > MAX_PYTHON_FILE_SIZE_BYTES:
                result["analysis_errors"].append(
                    "File exceeds the 2 MiB analysis limit."
                )
                return result

            source_code = file_path.read_bytes()
        except OSError as error:
            result["analysis_errors"].append(
                f"File could not be read: {type(error).__name__}."
            )
            return result

        # Check again after reading in case the file changed after stat().
        if len(source_code) > MAX_PYTHON_FILE_SIZE_BYTES:
            result["analysis_errors"].append(
                "File exceeds the 2 MiB analysis limit."
            )
            return result

        try:
            source_encoding, _ = tokenize.detect_encoding(
                io.BytesIO(source_code).readline
            )
        except (SyntaxError, UnicodeDecodeError) as error:
            result["analysis_errors"].append(
                f"File encoding could not be detected: {type(error).__name__}."
            )
            return result

        try:
            source_text = source_code.decode(source_encoding)
            syntax_tree = ast.parse(source_text, filename=str(file_path))
        except SyntaxError as error:
            result["syntax_errors"].append({
                "line": error.lineno or 1,
                "column": error.offset or 1,
                "message": error.msg,
            })
            return result
        except (UnicodeDecodeError, RecursionError, MemoryError) as error:
            result["analysis_errors"].append(
                f"Python AST analysis could not process the file: "
                f"{type(error).__name__}."
            )
            return result

        # AST identifiers are decoded according to Python's source-encoding
        # rules; Tree-sitter's byte-based identifier nodes can truncate
        # non-UTF-8 identifiers in legacy-encoded files.
        stack = [(syntax_tree, None)]
        while stack:
            node, class_name = stack.pop()

            if isinstance(node, ast.ClassDef):
                class_name = node.name
                result["classes"].append({
                    "name": node.name,
                    "start_line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result["functions"].append({
                    "name": node.name,
                    "class": class_name,
                    "start_line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                })
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                segment = ast.get_source_segment(source_text, node)
                if segment is not None:
                    result["imports"].append(segment)

            for child in reversed(list(ast.iter_child_nodes(node))):
                stack.append((child, class_name))


        return result

    def build_repository_map(self, repo_path, output_file):
        repo_path = Path(repo_path).resolve()

        repo_map = {
            "repository": repo_path.name,
            "files": []
        }

        for file_path in RepositoryManager.get_python_files(repo_path):

            parsed = self.parse_file(file_path)

            parsed["file"] = str(
                file_path.relative_to(repo_path)
            )

            repo_map["files"].append(parsed)

        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        output_file.write_text(
            json.dumps(repo_map, indent=4),
            encoding="utf-8"
        )

        return repo_map