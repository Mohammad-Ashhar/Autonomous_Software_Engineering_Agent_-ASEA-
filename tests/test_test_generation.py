from asea.agents.test_generation import TestGenerationAgent


def test_average_score_test_fallback_works_when_ollama_is_unavailable(tmp_path):
    source_file = tmp_path / "bug.py"
    source_file.write_text(
        """import json

def calculate_average_scores(student_data_json):
    try
        data = json.loads(student_data_json)
        results = {}
        for student, scores in data.items():
            total_score = 1
            for score in scores:
                total_score += score
            avg = total_score / len(scores)
            results[student] = avg
        return results

test_payload = '{"Alice": [85, 90, 78], "Bob": []}'
""",
        encoding="utf-8",
    )
    agent = TestGenerationAgent()
    agent.llm.chat = lambda **_kwargs: (_ for _ in ()).throw(
        ConnectionError("Ollama unavailable")
    )

    generated = agent._generate_for_module(source_file, "bug")

    assert "test_average_scores_calculates_mean" in generated
    assert "test_average_scores_handles_empty_list" in generated
    assert "result['Bob'] == 0" in generated


def test_generated_module_import_is_deferred_until_test_execution(
    tmp_path, monkeypatch
):
    import runpy

    source_file = tmp_path / "asea_lazy_import_fixture.py"
    marker = tmp_path / "module-imported.txt"
    source_file.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
        "value = 1\n",
        encoding="utf-8",
    )

    agent = TestGenerationAgent()
    monkeypatch.setattr(
        agent,
        "_source_modules",
        lambda _repository: [(source_file, "asea_lazy_import_fixture")],
    )
    monkeypatch.setattr(
        agent,
        "_generate_for_module",
        lambda _source, _module: (
            "def test_application_value():\n"
            "    assert module.value == 1\n"
        ),
    )

    result = agent.run(tmp_path)

    assert result["status"] == "GENERATED", result
    generated_file = tmp_path / result["generated_tests"][0]["test_file"]
    monkeypatch.syspath_prepend(str(tmp_path))
    namespace = runpy.run_path(str(generated_file))

    assert not marker.exists()
    namespace["test_application_value"]()
    assert marker.read_text(encoding="utf-8") == "imported"



def test_generated_placeholder_test_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="does not exercise"):
        TestGenerationAgent._validate_test_code(
            "def test_placeholder():\\n"
            "    assert True\\n"
        )


def test_generated_test_must_contain_assertion_or_exception_check():
    import pytest

    with pytest.raises(ValueError, match="no assertion or exception check"):
        TestGenerationAgent._validate_test_code(
            "def test_application_value():\\n"
            "    value = module.get_value()\\n"
        )


def test_generated_test_that_exercises_application_is_accepted():
    source = (
        "def test_application_value():\\n"
        "    assert module.get_value() == 1\\n"
    )

    tree = TestGenerationAgent._validate_test_code(source)

    assert len(tree.body) == 1
