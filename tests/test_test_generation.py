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
