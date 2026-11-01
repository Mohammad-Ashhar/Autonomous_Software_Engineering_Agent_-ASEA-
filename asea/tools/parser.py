
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
        tree = self.parser.parse(source_code)

        result = {
            "file": str(file_path),
            "functions": [],
            "classes": [],
            "imports": []
        }

        def traverse(node, class_name=None):
            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                name = source_code[
                    name_node.start_byte:name_node.end_byte
                ].decode("utf-8")

                result["classes"].append({
                    "name": name,
                    "start_line": node.start_point.row + 1,
                    "end_line": node.end_point.row + 1
                })

                for child in node.children:
                    traverse(child, name)

                return

            if node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                name = source_code[
                    name_node.start_byte:name_node.end_byte
                ].decode("utf-8")

                result["functions"].append({
                    "name": name,
                    "class": class_name,
                    "start_line": node.start_point.row + 1,
                    "end_line": node.end_point.row + 1
                })

            if node.type in (
                "import_statement",
                "import_from_statement"
            ):
                result["imports"].append(
                    source_code[
                        node.start_byte:node.end_byte
                    ].decode("utf-8")
                )

            for child in node.children:
                traverse(child, class_name)

        traverse(tree.root_node)

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
