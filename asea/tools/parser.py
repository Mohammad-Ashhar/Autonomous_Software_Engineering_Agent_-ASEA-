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
            source_text = source_code.decode(source_encoding)
        except (SyntaxError, UnicodeDecodeError) as error:
            result["analysis_errors"].append(
                f"File encoding could not be detected: {type(error).__name__}."
            )
            return result

        language = Language(tspython.language())
        local_parser = Parser(language)
        tree = local_parser.parse(source_code)

        try:
            ast.parse(source_code, filename=str(file_path))
        except SyntaxError as error:
            result["syntax_errors"].append({
                "line": error.lineno or 1,
                "column": error.offset or 1,
                "message": error.msg,
            })
        except (RecursionError, MemoryError) as error:
            result["analysis_errors"].append(
                f"Python AST analysis exceeded a runtime limit: "
                f"{type(error).__name__}."
            )

        # Iterative traversal avoids recursive syntax-tree processing.
        stack = [(tree.root_node, None)]

        while stack:
            node, class_name = stack.pop()

            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")

                if name_node is not None:
                    class_name = source_code[
                        name_node.start_byte:name_node.end_byte
                    ].decode(source_encoding)

                    result["classes"].append({
                        "name": class_name,
                        "start_line": node.start_point.row + 1,
                        "end_line": node.end_point.row + 1
                    })

            elif node.type == "function_definition":
                name_node = node.child_by_field_name("name")

                if name_node is not None:
                    name = source_code[
                        name_node.start_byte:name_node.end_byte
                    ].decode(source_encoding)

                    result["functions"].append({
                        "name": name,
                        "class": class_name,
                        "start_line": node.start_point.row + 1,
                        "end_line": node.end_point.row + 1
                    })

            elif node.type in (
                "import_statement",
                "import_from_statement"
            ):
                result["imports"].append(
                    source_code[
                        node.start_byte:node.end_byte
                    ].decode(source_encoding)
                )

            # Reverse preserves the original left-to-right traversal order.
            for child in reversed(node.children):
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