from pathlib import Path

import asea.agents.test_generation as test_generation
from asea.agents.test_generation import TestGenerationAgent, _validate_test_code


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
        "value = 1\n"
        "def get_value():\n"
        "    return value\n",
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
            "    assert module.get_value() == 1\n"
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

    with pytest.raises(ValueError, match="must call an application"):
        _validate_test_code(
            "def test_placeholder():\n"
            "    assert True\n"
        )


def test_generated_test_must_contain_assertion_or_exception_check():
    import pytest

    with pytest.raises(
        ValueError,
        match="no assertion or pytest.raises exception check",
    ):
        _validate_test_code(
            "def test_application_value():\n"
            "    value = module.get_value()\n"
        )


def test_generated_test_that_exercises_application_is_accepted():
    source = (
        "def test_application_value():\n"
        "    assert module.get_value() == 1\n"
    )

    tree = _validate_test_code(source)

    assert len(tree.body) == 1



def test_run_prioritizes_detected_bug_modules_within_module_limit(
    tmp_path, monkeypatch
):
    for name in ("a_module.py", "b_module.py", "z_bug.py"):
        (tmp_path / name).write_text(
            "def value():\n    return 1\n",
            encoding="utf-8",
        )

    agent = TestGenerationAgent(max_modules=2)
    monkeypatch.setattr(
        agent,
        "_generate_for_module",
        lambda _source, _module: (
            "def test_value():\n"
            "    assert module.value() == 1\n"
        ),
    )

    result = agent.run(tmp_path, priority_files=["z_bug.py"])

    assert result["status"] == "GENERATED"
    assert result["eligible_module_count"] == 3
    assert result["selected_module_count"] == 2
    assert [item["module"] for item in result["generated_tests"]] == [
        "z_bug",
        "a_module",
    ]
    assert result["omitted_modules"] == ["b_module.py"]
    assert "1 eligible module(s) were not selected" in result["reason"]


def test_generation_node_prioritizes_files_from_detected_bugs(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    working_repository = tmp_path / "working"
    bug_file = repository / "src" / "acme" / "logic.py"
    bug_file.parent.mkdir(parents=True)
    bug_file.write_text("def value():\n    return 1\n", encoding="utf-8")
    working_repository.mkdir()
    captured = {}

    class RecordingAgent:
        def run(self, repository_path, *, priority_files):
            captured["repository_path"] = repository_path
            captured["priority_files"] = priority_files
            return {"status": "NO_ELIGIBLE_MODULES"}

    monkeypatch.setattr(
        test_generation,
        "TestGenerationAgent",
        RecordingAgent,
    )

    result = test_generation.test_generation_node({
        "repo_path": str(repository),
        "working_repo_path": str(working_repository),
        "detected_bugs": [{"file": str(bug_file), "title": "Known defect"}],
    })

    assert captured["repository_path"] == str(working_repository)
    assert captured["priority_files"] == [Path("src/acme/logic.py")]
    assert result["test_generation_results"]["status"] == "NO_ELIGIBLE_MODULES"
