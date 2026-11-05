import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Central application configuration."""

    PROJECT_NAME = "ASEA"

    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv(
        "DEEPSEEK_BASE_URL",
        "https://api.deepseek.com"
    )
    DEEPSEEK_MODEL = os.getenv(
        "DEEPSEEK_MODEL",
        "deepseek-chat"
    )

    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

    DATABASE_PATH = Path(
        os.getenv("ASEA_DB_PATH", ".asea/asea.db")
    )

    WORKSPACE_PATH = Path(".workspace")

    MAX_RETRIES = 3

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
