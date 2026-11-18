from asea.agents.test_generation import TestGenerationAgent


def test_module_name_accepts_nested_namespace_package_paths(tmp_path):
    source_file = tmp_path / "src" / "acme" / "services" / "math_ops.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("def add(left, right):\\n    return left + right\\n")

    assert TestGenerationAgent._module_name(tmp_path, source_file) == (
        "src.acme.services.math_ops"
    )


def test_generated_tests_import_modules_from_src_namespace_packages(
    tmp_path, monkeypatch
):
    import runpy

    source_file = tmp_path / "src" / "acme" / "services" / "math_ops.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        "def add(left, right):\\n    return left + right\\n",
        encoding="utf-8",
    )

    agent = TestGenerationAgent()
    monkeypatch.setattr(
        agent,
        "_generate_for_module",
        lambda _source, _module: (
            "def test_add():\\n"
            "    assert module.add(2, 3) == 5\\n"
        ),
    )

    result = agent.run(tmp_path)

    assert result["status"] == "GENERATED", result
    assert result["generated_tests"][0]["module"] == (
        "src.acme.services.math_ops"
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    generated_file = tmp_path / result["generated_tests"][0]["test_file"]
    namespace = runpy.run_path(str(generated_file))
    namespace["test_add"]()
