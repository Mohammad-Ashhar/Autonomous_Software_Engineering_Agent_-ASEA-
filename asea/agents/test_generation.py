import ast
import json
from pathlib import Path
from typing import Any

from asea.tools.llm_client import OllamaClient

IGNORED_PARTS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "artifacts", "workspaces"
}

BLOCKED_NAMES = {
    "__import__", "eval", "exec", "compile", "open", "input",
    "globals", "locals", "breakpoint"
}


def _parse_response(response: str) -> str:
    """Extract Python test code from a plain or fenced model response."""
    text = response.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    if text.startswith("{"):
        payload = json.loads(text)
        text = payload.get("tests", "")

    if not isinstance(text, str) or not text.strip():
        raise ValueError("The model returned no test code.")

    return text.strip()


def _validate_test_code(source: str) -> ast.Module:
    """Accept pytest functions without imports or top-level execution."""
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("Generated tests may not add imports.")

    test_functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]

    if not test_functions:
        raise ValueError("Generated output contains no test_ functions.")

    if len(test_functions) != len(tree.body):
        raise ValueError("Generated tests must contain only test functions.")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            raise ValueError(
                f"Generated tests contain a restricted name: {node.id}"
            )

    return tree


class TestGenerationAgent:
    __test__ = False

    """Generate conservative pytest coverage when a repository has no tests."""

    def __init__(self, max_modules: int = 5):
        self.llm = OllamaClient()
        self.max_modules = max_modules

    @staticmethod
    def _has_tests(repository: Path) -> bool:
        for path in repository.rglob("*.py"):
            if not path.is_file():
                continue

            relative = path.relative_to(repository)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue

            is_test_name = (
                path.name.startswith("test_")
                or path.name.endswith("_test.py")
            )
            if is_test_name and (
                "tests" in relative.parts or len(relative.parts) == 1
            ):
                return True

        return False

    @staticmethod
    def _module_name(repository: Path, source_file: Path) -> str | None:
        relative = source_file.relative_to(repository)
        parts = list(relative.with_suffix("").parts)

        if parts[-1] == "__init__":
            parts.pop()

        if not parts or any(not part.isidentifier() for part in parts):
            return None

        parent = repository
        for directory in parts[:-1]:
            parent = parent / directory
            if not (parent / "__init__.py").is_file():
                return None

        return ".".join(parts)

    def _source_modules(self, repository: Path) -> list[tuple[Path, str]]:
        candidates = []

        for path in sorted(repository.rglob("*.py")):
            relative = path.relative_to(repository)

            if any(part in IGNORED_PARTS for part in relative.parts):
                continue

            if "tests" in relative.parts:
                continue

            if path.name == "__init__.py":
                continue

            module_name = self._module_name(repository, path)
            if module_name is None:
                continue

            try:
                source = path.read_text(encoding="utf-8-sig")
                ast.parse(source)
            except (OSError, SyntaxError, UnicodeError):
                continue

            if source.strip():
                candidates.append((path, module_name))

        return candidates[:self.max_modules]

    def _generate_for_module(
        self,
        source_file: Path,
        module_name: str
    ) -> str:
        source = source_file.read_text(
            encoding="utf-8-sig",
            errors="replace"
        )

        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a careful senior Python QA engineer. "
                    "Generate useful pytest tests for the supplied module. "
                    "The test file already defines a variable named 'module' "
                    "that refers to the imported module. Refer to functions "
                    "and classes as module.function or module.Class. "
                    "Return ONLY Python test function definitions. "
                    "Every function name must start with test_. "
                    "Do not include imports, fixtures, file/network/process "
                    "operations, subprocesses, environment changes, or "
                    "filesystem writes. Do not assume undocumented behavior. "
                    "Test only behavior supported by the source code. "
                    "Include normal inputs, boundary cases, and error cases "
                    "only when the source establishes their behavior. "
                    "Do not modify application code. If no meaningful tests "
                    "can be written, return one test function that asserts "
                    "True is not acceptable; instead return an empty response."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Module: {module_name}\n"
                    f"Source file: {source_file.name}\n\n"
                    "Generate a small, deterministic set of tests for this "
                    "module. Use the 'module' variable.\n\n"
                    f"```python\n{source[:10000]}\n```"
                )
            }
        ]

        response = self.llm.chat(
            messages=prompt,
            temperature=0.1,
            max_tokens=1200
        )
        return _parse_response(response)

    def run(self, repository_path: str | Path) -> dict[str, Any]:
        repository = Path(repository_path).resolve()

        if not repository.is_dir():
            raise NotADirectoryError(repository)

        if self._has_tests(repository):
            return {
                "status": "SKIPPED_EXISTING_TESTS",
                "generated_tests": [],
                "reason": "The repository already contains Python tests."
            }

        modules = self._source_modules(repository)
        if not modules:
            return {
                "status": "NO_ELIGIBLE_MODULES",
                "generated_tests": [],
                "reason": "No importable Python application modules were found."
            }

        tests_directory = repository / "tests"
        tests_directory.mkdir(parents=True, exist_ok=True)

        generated = []
        failures = []

        for source_file, module_name in modules:
            try:
                test_code = self._generate_for_module(
                    source_file,
                    module_name
                )
                _validate_test_code(test_code)

                safe_name = module_name.replace(".", "_")
                test_file = (
                    tests_directory / f"test_asea_generated_{safe_name}.py"
                )

                if test_file.exists():
                    failures.append(
                        f"Skipped {module_name}: generated test file already exists."
                    )
                    continue

                content = (
                    '"""Tests generated by ASEA; review before production use."""\n'
                    "import importlib\nimport pytest\n\n"
                    f"module = importlib.import_module({module_name!r})\n\n"
                    f"{test_code}\n"
                )

                ast.parse(content)
                test_file.write_text(content, encoding="utf-8")
                generated.append({
                    "module": module_name,
                    "source_file": str(source_file.relative_to(repository)),
                    "test_file": str(test_file.relative_to(repository)),
                    "test_count": sum(
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name.startswith("test_")
                        for node in ast.parse(test_code).body
                    )
                })

            except Exception as error:  # noqa: BLE001
                failures.append(f"{module_name}: {error}")

        status = "GENERATED" if generated else "GENERATION_FAILED"

        return {
            "status": status,
            "generated_tests": generated,
            "failures": failures,
            "reason": (
                "Generated tests will be executed in the isolated Docker runner."
                if generated
                else "No valid tests could be generated."
            )
        }


def test_generation_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node that generates tests inside the disposable workspace."""
    working_path = state.get("working_repo_path")
    if not working_path:
        raise ValueError("Test generation requires a prepared working repository.")

    result = TestGenerationAgent().run(working_path)
    print("TEST GENERATION:", result["status"])

    return {
        "test_generation_results": result,
        "current_agent": "test_generation",
        "status": "test_generation_completed"
    }
