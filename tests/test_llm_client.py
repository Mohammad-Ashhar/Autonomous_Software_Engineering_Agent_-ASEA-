from asea.tools import llm_client


def test_ollama_client_uses_bounded_request_timeout(monkeypatch):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_client, "OpenAI", FakeOpenAI)
    client = llm_client.OllamaClient()

    assert client is not None
    assert captured["timeout"] == 60.0
    assert captured["max_retries"] == 0
