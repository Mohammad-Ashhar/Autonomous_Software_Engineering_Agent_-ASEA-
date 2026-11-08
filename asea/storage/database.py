import json
import sqlite3

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATABASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_runs (

    id INTEGER PRIMARY KEY AUTOINCREMENT,

    repository_path TEXT NOT NULL,

    status TEXT NOT NULL,

    started_at TEXT NOT NULL,

    summary_json TEXT NOT NULL
);
"""


class RunStore:
    """SQLite storage manager for ASEA execution history."""

    def __init__(
        self,
        database_path: str | Path
    ) -> None:

        self.database_path = Path(database_path)

        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with self._connect() as connection:

            connection.executescript(
                DATABASE_SCHEMA
            )

    def _connect(self) -> sqlite3.Connection:
        """Create a SQLite database connection."""

        connection = sqlite3.connect(
            self.database_path
        )

        connection.row_factory = sqlite3.Row

        return connection

    def save_run(
        self,
        repository_path: str,
        status: str,
        summary: dict[str, Any]
    ) -> int:
        """Save an analysis execution record."""

        timestamp = datetime.now(
            timezone.utc
        ).isoformat()

        with self._connect() as connection:

            cursor = connection.execute(
                """
                INSERT INTO analysis_runs
                (
                    repository_path,
                    status,
                    started_at,
                    summary_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    repository_path,
                    status,
                    timestamp,
                    json.dumps(summary),
                )
            )

            return int(cursor.lastrowid)

    def recent_runs(
        self,
        limit: int = 10
    ) -> list[dict[str, Any]]:
        """Retrieve the most recent analysis runs."""

        with self._connect() as connection:

            rows = connection.execute(
                """
                SELECT *
                FROM analysis_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()

        return [
            dict(row)
            for row in rows
        ]
