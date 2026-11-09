"""Build a lightweight Python dependency graph and detect cross-file call errors."""

import ast
import importlib.util
from pathlib import Path
from typing import Any


IGNORED_PARTS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "artifacts", "workspaces"
}


def _module_name(root: Path, source: Path) -> str | None:
    relative = source.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    if not parts or any(not part.isidentifier() for part in parts):
        return None
    for parent in [source.parent, *source.parents]:
        if parent == root:
            break
        if not (parent / "__init__.py").is_file():
            return None
    return ".".join(parts)


def _resolve_from_module(module: str, package: str, level: int) -> str:
    if level == 0:
        return module
    relative_name = "." * level + module
    try:
        return importlib.util.resolve_name(relative_name, package)
    except ImportError:
        return ""


def _function_definitions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    definitions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions[node.name] = node
    return definitions


def _call_argument_issue(
    call: ast.Call,
    definition: ast.FunctionDef | ast.AsyncFunctionDef
) -> str | None:
    """Return a deterministic signature mismatch, if one is provable."""
    if any(isinstance(argument, ast.Starred) for argument in call.args):
        return None
    if any(keyword.arg is None for keyword in call.keywords):
        return None

    args = definition.args
    positional = args.posonlyargs + args.args
    has_varargs = args.vararg is not None
    supplied_positional = len(call.args)
    if not has_varargs and supplied_positional > len(positional):
        return (
            f"Call supplies {supplied_positional} positional arguments, "
            f"but the target accepts at most {len(positional)}."
        )

    keyword_names = {keyword.arg for keyword in call.keywords}
    positional_names = {argument.arg for argument in positional}
    supplied_names = {
        positional[index].arg
        for index in range(min(supplied_positional, len(positional)))
    } | keyword_names

    required_positional = len(positional) - len(args.defaults)
    missing = [
        argument.arg
        for index, argument in enumerate(positional[:required_positional])
        if index >= supplied_positional
        and argument.arg not in keyword_names
    ]
    required_keyword_only = [
        argument.arg
        for argument, default in zip(args.kwonlyargs, args.kw_defaults)
        if default is None and argument.arg not in keyword_names
    ]
    if missing or required_keyword_only:
        names = missing + required_keyword_only
        return "Call is missing required argument(s): " + ", ".join(names) + "."

    # A keyword that is not a parameter is an error unless **kwargs exists.
    if args.kwarg is None:
        unexpected = sorted(keyword_names - positional_names - {
            argument.arg for argument in args.kwonlyargs
        })
        if unexpected:
            return "Call uses unexpected keyword argument(s): " + ", ".join(unexpected) + "."
    return None


class PythonDependencyAnalyzer:
    """Index importable project modules and validate statically resolvable calls."""

    def analyze(self, repository_path: str | Path) -> dict[str, Any]:
        root = Path(repository_path).resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)

        module_files: dict[str, Path] = {}
        trees: dict[Path, ast.Module] = {}
        parse_errors = []

        for source in sorted(root.rglob("*.py")):
            relative = source.relative_to(root)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            if "tests" in relative.parts or source.name.startswith("test_"):
                continue
            module = _module_name(root, source)
            if not module:
                continue
            try:
                tree = ast.parse(source.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, SyntaxError) as error:
                parse_errors.append({
                    "file": str(relative),
                    "error": str(error)
                })
                continue
            module_files[module] = source
            trees[source] = tree

        definitions = {
            module: _function_definitions(trees[source])
            for module, source in module_files.items()
        }
        dependency_map: dict[str, set[str]] = {
            module: set() for module in module_files
        }
        findings = []

        for module, source in module_files.items():
            tree = trees[source]
            package = module if source.name == "__init__.py" else module.rpartition(".")[0]
            imported_symbols: dict[str, tuple[str, str]] = {}
            imported_modules: dict[str, str] = {}

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target = alias.name
                        if target in module_files:
                            dependency_map[module].add(target)
                            imported_modules[alias.asname or target.split(".")[0]] = target
                elif isinstance(node, ast.ImportFrom):
                    target_module = _resolve_from_module(
                        node.module or "", package, node.level
                    )
                    if target_module in module_files:
                        dependency_map[module].add(target_module)
                        for alias in node.names:
                            if alias.name in definitions[target_module]:
                                imported_symbols[alias.asname or alias.name] = (
                                    target_module, alias.name
                                )
                    elif target_module:
                        # Support "from package import module" when the
                        # imported module itself is indexed in the project.
                        for alias in node.names:
                            candidate = f"{target_module}.{alias.name}"
                            if candidate in module_files:
                                dependency_map[module].add(candidate)
                                imported_modules[alias.asname or alias.name] = candidate

            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                target = None
                if isinstance(call.func, ast.Name):
                    target = imported_symbols.get(call.func.id)
                elif (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                ):
                    target_module = imported_modules.get(call.func.value.id)
                    if target_module:
                        target = (target_module, call.func.attr)

                if not target:
                    continue
                target_module, target_name = target
                definition = definitions.get(target_module, {}).get(target_name)
                if definition is None:
                    continue
                issue = _call_argument_issue(call, definition)
                if not issue:
                    continue

                target_file = module_files[target_module]
                findings.append({
                    "title": "Cross-file function call signature mismatch",
                    "line": call.lineno,
                    "severity": "high",
                    "classification": "confirmed_bug",
                    "confidence": 0.99,
                    "description": (
                        f"{issue} The imported target is "
                        f"{target_module}.{target_name}, defined in "
                        f"{target_file.relative_to(root)}:{definition.lineno}."
                    ),
                    "suggestion": (
                        f"Update this call to match the signature of "
                        f"{target_module}.{target_name}."
                    ),
                    "file": str(source),
                    "source": "dependency_ast",
                    "related_file": str(target_file),
                    "related_line": definition.lineno,
                    "dependency": target_module
                })

        serialized_map = {
            module: sorted(dependencies)
            for module, dependencies in sorted(dependency_map.items())
        }
        return {
            "dependency_map": serialized_map,
            "module_count": len(module_files),
            "dependency_count": sum(map(len, dependency_map.values())),
            "cross_file_findings": findings,
            "parse_errors": parse_errors
        }
