import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=True)


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    ollama_model: str
    github_token: str
    github_repository: str


def get_settings() -> Settings:
    return Settings(
        ollama_base_url=os.getenv(
            "OLLAMA_BASE_URL",
            "http://localhost:11434/v1"
        ),
        ollama_model=os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5-coder:1.5b"
        ),
        github_token=os.getenv(
            "GITHUB_TOKEN",
            ""
        ),
        github_repository=os.getenv(
            "GITHUB_REPOSITORY",
            ""
        ),
    )