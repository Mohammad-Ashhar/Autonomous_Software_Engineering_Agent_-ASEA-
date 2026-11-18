import ast
import json
from pathlib import Path

from asea.tools.repository import RepositoryManager
from asea.tools.source_reader import read_python_source


class PythonRepositoryParser:

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
            source_text = read_python_source(file_path)
        except OSError as error:
            result["analysis_errors"].append(
                f"File could not be read: {type(error).__name__}."
            )
            return result
        except ValueError as error:
            result["analysis_errors"].append(str(error))
            return result
        except (SyntaxError, UnicodeDecodeError) as error:
            result["analysis_errors"].append(
                f"File encoding could not be detected: {type(error).__name__}."
            )
            return result

        try:
            syntax_tree = ast.parse(source_text, filename=str(file_path))
        except SyntaxError as error:
            result["syntax_errors"].append({
                "line": error.lineno or 1,
                "column": error.offset or 1,
                "message": error.msg,
            })
            return result
        except (RecursionError, MemoryError) as error:
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

            # A function nested inside another function is a local function,
            # not a method of the surrounding class. A class nested inside a
            # function establishes its own method context when visited.
            child_class_name = (
                None
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                else class_name
            )
            for child in reversed(list(ast.iter_child_nodes(node))):
                stack.append((child, child_class_name))

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