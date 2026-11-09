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
                    error TEXT,
                    publish_requested INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(repair_jobs)"
                ).fetchall()
            }
            if "publish_requested" not in columns:
                connection.execute(
                    "ALTER TABLE repair_jobs ADD COLUMN "
                    "publish_requested INTEGER NOT NULL DEFAULT 0"
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
        job["publish_requested"] = bool(job.get("publish_requested", False))

        return job

    def create_job(
        self,
        job_id: str,
        repository: str,
        publish_requested: bool = False,
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
            "publish_requested": bool(publish_requested),
        }

        with self.lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO repair_jobs (
                        job_id, repository, status, message,
                        created_at, updated_at,
                        review_request, result, error, publish_requested
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        int(job["publish_requested"]),
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
            "publish_requested",
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


    def mark_interrupted_jobs(self) -> int:
        """Mark queued/running jobs from a previous process as failed.

        Review-paused jobs are intentionally preserved because their
        LangGraph checkpoints can be resumed after the API restarts.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE repair_jobs
                    SET status = 'failed',
                        message = 'Repair interrupted by API process restart.',
                        error = ?,
                        updated_at = ?
                    WHERE status IN ('queued', 'running')
                    """,
                    (
                        "The API restarted before this repair completed. "
                        "Start a new repair; jobs awaiting review are preserved.",
                        now,
                    ),
                )
                return cursor.rowcount

    def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Return a newest-first page of persisted repair jobs."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset cannot be negative")

        with self.lock:
            with self._connect() as connection:
                where_clause = "WHERE status = ?" if status else ""
                parameters: tuple[Any, ...] = (status,) if status else ()
                total = connection.execute(
                    f"SELECT COUNT(*) FROM repair_jobs {where_clause}",
                    parameters,
                ).fetchone()[0]
                rows = connection.execute(
                    f"""
                    SELECT * FROM repair_jobs
                    {where_clause}
                    ORDER BY created_at DESC, job_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (*parameters, limit, offset),
                ).fetchall()

        return {
            "items": [self._row_to_dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }



repair_jobs = RepairJobManager()
