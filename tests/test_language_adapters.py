from pathlib import Path
from unittest.mock import Mock

import pytest

from asea.languages.registry import get_default_registry
from asea.tools.parser import RepositoryParser


def test_default_registry_resolves_python_javascript_and_typescript():
    registry = get_default_registry()

    assert registry.resolve(Path("main.py")).name == "Python"
    assert registry.resolve(Path("app.js")).name == "JavaScript"
    assert registry.resolve(Path("app.ts")).name == "TypeScript"
    assert registry.resolve(Path("app.tsx")).name == "TypeScript"
    assert registry.resolve(Path("Main.java")) is None


def test_only_python_adapter_advertises_repair_support():
    registry = get_default_registry()

    assert registry.resolve("main.py").supports_repair is True
    assert registry.resolve("app.js").supports_repair is False
    assert registry.resolve("app.ts").supports_repair is False


def test_patch_generator_fails_closed_for_unvalidated_language():
    from asea.agents.patch_generator import PatchGeneratorAgent

    agent = PatchGeneratorAgent()
    agent.llm = Mock()

    with pytest.raises(ValueError, match="repair is not enabled"):
        agent.generate_patch(
            Path("app.ts"),
            {"title": "Potential issue"},
        )

    agent.llm.chat.assert_not_called()


def test_repository_parser_keeps_language_and_capability_metadata(tmp_path):
    (tmp_path / "service.py").write_text("def run():\n    return 1\n")
    (tmp_path / "service.js").write_text(
        "export function run() { return 1; }\n"
    )

    parsed = RepositoryParser().build_repository_map(
        tmp_path, tmp_path / "map.json"
    )
    by_language = {item["language"]: item for item in parsed["files"]}

    assert by_language["Python"]["repair_supported"] is True
    assert by_language["JavaScript"]["repair_supported"] is False
