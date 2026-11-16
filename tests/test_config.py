from asea.core.config import load_environment


def test_process_environment_takes_precedence_over_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ASEA_TEST_CONFIG_VALUE=from-dotenv\n"
        "ASEA_TEST_FILE_ONLY=file-value\n"
    )
    monkeypatch.setenv("ASEA_TEST_CONFIG_VALUE", "from-process")
    monkeypatch.delenv("ASEA_TEST_FILE_ONLY", raising=False)

    load_environment(env_file)

    assert __import__("os").environ["ASEA_TEST_CONFIG_VALUE"] == "from-process"
    assert __import__("os").environ["ASEA_TEST_FILE_ONLY"] == "file-value"
