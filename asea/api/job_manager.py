import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class JobQueueFullError(RuntimeError):
    """Raised when the configured active-job limit is reached."""


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
        timeout = self._database_timeout()
        connection = sqlite3.connect(str(self.db_path), timeout=timeout)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @staticmethod
    def _database_timeout() -> float:
        """Return a bounded SQLite lock wait, configurable for deployments."""
        raw = os.getenv("ASEA_SQLITE_BUSY_TIMEOUT_SECONDS", "30")
        try:
            value = float(raw)
        except ValueError:
            return 30.0
        return min(120.0, max(0.1, value))

    @contextmanager
    def _connection(self):
        """Commit successful operations and always close the SQLite handle."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_database(self):
        """Create the repair jobs table if it does not exist."""

        with self._connection() as connection:
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_repair_jobs_created "
                "ON repair_jobs(created_at DESC, job_id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_repair_jobs_status_created "
                "ON repair_jobs(status, created_at DESC, job_id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_repair_jobs_terminal_updated "
                "ON repair_jobs(status, updated_at)"
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
        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128:
            raise ValueError("job_id must contain between 1 and 128 characters.")
        if (
            not isinstance(repository, str)
            or not repository.strip()
            or len(repository) > 2048
        ):
            raise ValueError("repository must contain between 1 and 2048 characters.")
        if not isinstance(publish_requested, bool):
            raise ValueError("publish_requested must be a boolean.")

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
            with self._connection() as connection:
                # Serialize admission across threads, manager instances, and processes.
                connection.execute("BEGIN IMMEDIATE")
                maximum = self._maximum_active_jobs()
                active = connection.execute(
                    "SELECT COUNT(*) FROM repair_jobs "
                    "WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
                if active >= maximum:
                    raise JobQueueFullError("Repair queue is full.")
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

        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128:
            raise ValueError("job_id must contain between 1 and 128 characters.")

        invalid_fields = set(updates) - allowed_fields

        if invalid_fields:
            raise ValueError(
                f"Unsupported job fields: {sorted(invalid_fields)}"
            )
        if "status" in updates and updates["status"] not in {
            "queued", "running", "waiting_for_review", "completed", "failed"
        }:
            raise ValueError("Unsupported repair job status.")
        if "message" in updates and (
            not isinstance(updates["message"], str)
            or len(updates["message"]) > 2000
        ):
            raise ValueError("Job message must be a string of at most 2000 characters.")
        if "publish_requested" in updates and not isinstance(
            updates["publish_requested"], bool
        ):
            raise ValueError("publish_requested must be a boolean.")

        with self.lock:
            with self._connection() as connection:
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


    @staticmethod
    def _maximum_active_jobs() -> int:
        """Return a bounded admission limit for active jobs."""
        try:
            value = int(os.getenv("ASEA_MAX_ACTIVE_REPAIR_JOBS", "20"))
        except ValueError:
            return 20
        return min(500, max(1, value))

    def prune_terminal_jobs(self, retention_days: int = 30) -> int:
        """Delete old completed/failed jobs; preserve active and review jobs."""
        if not 1 <= retention_days <= 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        with self.lock:
            with self._connection() as connection:
                cursor = connection.execute(
                    "DELETE FROM repair_jobs "
                    "WHERE status IN ('completed', 'failed') AND updated_at < ?",
                    (cutoff_iso,),
                )
                return cursor.rowcount

    def check_database(self) -> None:
        """Raise when the persistent job database cannot be queried."""
        with self._connection() as connection:
            connection.execute("SELECT 1").fetchone()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve a repair job."""

        with self.lock:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM repair_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()

                return self._row_to_dict(row) if row else None


    def claim_review(self, job_id: str) -> bool:
        """Atomically claim a review-paused job without losing its checkpoint."""
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            with self._connection() as connection:
                cursor = connection.execute(
                    """
                    UPDATE repair_jobs
                    SET status = 'running',
                        message = 'Processing patch review decision.',
                        updated_at = ?
                    WHERE job_id = ? AND status = 'waiting_for_review'
                    """,
                    (now, job_id),
                )
                return cursor.rowcount == 1

    def release_review_claim(self, job_id: str) -> bool:
        """Return an unstarted review claim to the review queue."""
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            with self._connection() as connection:
                cursor = connection.execute(
                    """
                    UPDATE repair_jobs
                    SET status = 'waiting_for_review',
                        message = ?,
                        updated_at = ?
                    WHERE job_id = ?
                      AND status = 'running'
                      AND review_request IS NOT NULL
                    """,
                    (
                        "Review submission could not be scheduled; "
                        "awaiting review.",
                        now,
                        job_id,
                    ),
                )
                return cursor.rowcount == 1

    def mark_interrupted_jobs(self) -> int:
        """Mark queued/running jobs from a previous process as failed.

        Review-paused jobs are intentionally preserved because their
        LangGraph checkpoints can be resumed after the API restarts.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            with self._connection() as connection:
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
                        json.dumps(
                            "The API restarted before this repair completed. "
                            "Start a new repair; jobs awaiting review are preserved."
                        ),
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
        valid_statuses = {
            "queued", "running", "waiting_for_review", "completed", "failed"
        }
        if status is not None and status not in valid_statuses:
            raise ValueError("Unsupported repair job status filter.")

        with self.lock:
            with self._connection() as connection:
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
