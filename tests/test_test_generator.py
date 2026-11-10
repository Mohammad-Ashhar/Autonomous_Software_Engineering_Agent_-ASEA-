from pathlib import Path

import pytest

from asea.agents.test_generation import (
    TestGenerationAgent,
    _parse_response,
    _validate_test_code,
)


def test_parse_plain_python_response():
    source = "def test_example():\n    assert 2 + 2 == 4"
    assert _parse_response(source) == source


def test_parse_fenced_python_response():
    response = "```python\ndef test_example():\n    assert True\n```"
    assert _parse_response(response).startswith("def test_example()")


def test_parse_json_response():
    response = '{"tests": "def test_example():\\n    assert True"}'
    assert _parse_response(response).startswith("def test_example()")


def test_reject_generated_code_without_test_functions():
    with pytest.raises(ValueError, match="no test_ functions"):
        _validate_test_code("value = 10")


def test_reject_generated_imports():
    with pytest.raises(ValueError, match="may not add imports"):
        _validate_test_code("import os\ndef test_example():\n    assert True")


def test_reject_restricted_builtins():
    with pytest.raises(ValueError, match="restricted name"):
        _validate_test_code(
            "def test_example():\n    result = open('secret.txt')"
        )


def test_accept_simple_test_functions():
    tree = _validate_test_code(
        "def test_example():\n    assert 2 + 2 == 4"
    )
    assert len(tree.body) == 1


def test_existing_tests_are_detected(tmp_path: Path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n",
        encoding="utf-8"
    )

    assert TestGenerationAgent._has_tests(tmp_path)


def test_agent_file_is_not_mistaken_for_a_test(tmp_path: Path):
    agent_dir = tmp_path / "asea" / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "test_generator.py").write_text(
        "class TestGenerationAgent:\n    pass\n",
        encoding="utf-8"
    )

    assert not TestGenerationAgent._has_tests(tmp_path)


def test_package_module_name_is_resolved(tmp_path: Path):
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    source = package / "logic.py"
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    assert TestGenerationAgent._module_name(tmp_path, source) == "sample.logic"


def test_reject_generated_decorators():
    with pytest.raises(ValueError, match="decorators"):
        _validate_test_code(
            "@dangerous()\\ndef test_example():\\n    assert True"
        )


def test_reject_generated_default_argument_execution():
    with pytest.raises(ValueError, match="default arguments"):
        _validate_test_code(
            "def test_example(value=dangerous()):\\n    assert value"
        )


@pytest.mark.parametrize(
    "source",
    [
        "def test_example():\\n    return ().__class__",
        "def test_example():\\n    module.os.system('whoami')",
        "def test_example():\\n    getattr(module, 'value')",
    ],
)
def test_reject_generated_introspection_and_process_access(source):
    with pytest.raises(ValueError, match="restricted"):
        _validate_test_code(source)
