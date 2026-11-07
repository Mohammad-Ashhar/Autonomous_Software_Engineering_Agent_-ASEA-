import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class RepairJobManager:
    """Manage repair jobs using persistent SQLite storage."""

    def __init__(self):
        self.db_path = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "asea_jobs.db"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self._initialize_database()

    def _connect(self):
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=30,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self):
        """Create the repair jobs table if it does not exist."""

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS repair_jobs (
                    job_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    review_request TEXT,
                    result TEXT,
                    error TEXT
                )
                """
            )

    @staticmethod
    def _serialize(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, default=str)

    @staticmethod
    def _deserialize(value: str | None) -> Any:
        if value is None:
            return None
        return json.loads(value)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)

        for field in ("review_request", "result", "error"):
            job[field] = RepairJobManager._deserialize(job[field])

        return job

    def create_job(
        self,
        job_id: str,
        repository: str,
    ) -> dict[str, Any]:
        """Register a new repair job."""

        now = datetime.now(timezone.utc).isoformat()

        job = {
            "job_id": job_id,
            "repository": repository,
            "status": "queued",
            "message": "Repair job created.",
            "created_at": now,
            "updated_at": now,
            "review_request": None,
            "result": None,
            "error": None,
        }

        with self.lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO repair_jobs (
                        job_id, repository, status, message,
                        created_at, updated_at,
                        review_request, result, error
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job["job_id"],
                        job["repository"],
                        job["status"],
                        job["message"],
                        job["created_at"],
                        job["updated_at"],
                        None,
                        None,
                        None,
                    ),
                )

        return job.copy()

    def update_job(
        self,
        job_id: str,
        **updates: Any,
    ) -> dict[str, Any]:
        """Update an existing repair job."""

        allowed_fields = {
            "status",
            "message",
            "review_request",
            "result",
            "error",
        }

        invalid_fields = set(updates) - allowed_fields

        if invalid_fields:
            raise ValueError(
                f"Unsupported job fields: {sorted(invalid_fields)}"
            )

        with self.lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM repair_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()

                if row is None:
                    raise KeyError(f"Repair job not found: {job_id}")

                updates["updated_at"] = datetime.now(
                    timezone.utc
                ).isoformat()

                for field in ("review_request", "result", "error"):
                    if field in updates:
                        updates[field] = self._serialize(updates[field])

                assignments = ", ".join(
                    f"{field} = ?" for field in updates
                )

                values = list(updates.values())
                values.append(job_id)

                connection.execute(
                    f"""
                    UPDATE repair_jobs
                    SET {assignments}
                    WHERE job_id = ?
                    """,
                    values,
                )

                updated_row = connection.execute(
                    "SELECT * FROM repair_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()

                return self._row_to_dict(updated_row)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve a repair job."""

        with self.lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM repair_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()

                return self._row_to_dict(row) if row else None


repair_jobs = RepairJobManager()