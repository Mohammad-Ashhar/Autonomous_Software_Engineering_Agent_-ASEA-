import json
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser


class PythonRepositoryParser:

    def __init__(self):
        self.language = Language(tspython.language())
        self.parser = Parser(self.language)

    def parse_file(self, file_path):
        file_path = Path(file_path)

        source_code = file_path.read_bytes()
        language = Language(tspython.language())
        local_parser = Parser(language)
        tree = local_parser.parse(source_code)

        result = {
            "file": str(file_path),
            "functions": [],
            "classes": [],
            "imports": []
        }

        # Iterative traversal avoids recursive syntax-tree processing.
        stack = [(tree.root_node, None)]

        while stack:
            node, class_name = stack.pop()

            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")

                if name_node is not None:
                    class_name = source_code[
                        name_node.start_byte:name_node.end_byte
                    ].decode("utf-8")

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
                    ].decode("utf-8")

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
                    ].decode("utf-8")
                )

            # Reverse preserves the original left-to-right traversal order.
            for child in reversed(node.children):
                stack.append((child, class_name))

        return result

    def build_repository_map(self, repo_path, output_file):
        repo_path = Path(repo_path)

        manager_ignored = {
            ".git", ".venv", "venv", "__pycache__",
            "node_modules", "dist", "build",
            ".pytest_cache", ".mypy_cache"
        }

        repo_map = {
            "repository": repo_path.name,
            "files": []
        }

        for file_path in sorted(repo_path.rglob("*.py")):
            if manager_ignored.intersection(
                file_path.relative_to(repo_path).parts
            ):
                continue

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