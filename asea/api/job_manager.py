import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

TERMINAL_STATUSES = {"completed", "failed"}
ACTIVE_STATUSES = {"queued", "running"}


class RepairJobManager:
    """Manage repair jobs using persistent SQLite storage."""

    # Columns added after the first release (added via migration).
    EXTRA_COLUMNS = {
        "stage": "TEXT",
        "started_at": "TEXT",
        "completed_at": "TEXT",
        "active_seconds": "REAL NOT NULL DEFAULT 0",
        "running_since": "TEXT",
        "timings": "TEXT",
        "publish": "INTEGER NOT NULL DEFAULT 0",
        "publication": "TEXT",
    }

    JSON_FIELDS = ("review_request", "result", "error", "timings", "publication")

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

            # Lightweight migration for databases created by older versions.
            existing = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(repair_jobs)"
                )
            }

            for column, definition in self.EXTRA_COLUMNS.items():
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE repair_jobs "
                        f"ADD COLUMN {column} {definition}"
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

        for field in RepairJobManager.JSON_FIELDS:
            job[field] = RepairJobManager._deserialize(job.get(field))

        job["publish"] = bool(job.get("publish"))

        # Execution time counts only time spent actually working: queue
        # time and time spent waiting for a human reviewer are excluded,
        # and the value is frozen as soon as the job stops running.
        elapsed = float(job.get("active_seconds") or 0.0)
        running_since = job.pop("running_since", None)

        if running_since and job["status"] == "running":
            elapsed += max(
                0.0,
                (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(running_since)
                ).total_seconds(),
            )

        job["elapsed_seconds"] = round(elapsed, 1)
        job["is_active"] = job["status"] in ACTIVE_STATUSES
        job["is_terminal"] = job["status"] in TERMINAL_STATUSES
        job.pop("active_seconds", None)

        return job

    def create_job(
        self,
        job_id: str,
        repository: str,
        publish: bool = False,
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
            "publish": publish,
        }

        with self.lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO repair_jobs (
                        job_id, repository, status, message,
                        created_at, updated_at,
                        review_request, result, error, publish
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
                        int(publish),
                    ),
                )

        return self.get_job(job_id) or job.copy()

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
            "stage",
            "timings",
            "publication",
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

                now = datetime.now(timezone.utc)
                updates["updated_at"] = now.isoformat()

                self._apply_time_tracking(row, updates, now)

                for field in self.JSON_FIELDS:
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
    def _apply_time_tracking(
        row: sqlite3.Row,
        updates: dict[str, Any],
        now: datetime,
    ) -> None:
        """Maintain started_at / active_seconds / completed_at."""

        new_status = updates.get("status")

        if new_status is None:
            return

        old_status = row["status"]
        running_since = row["running_since"]

        if new_status == "running" and old_status != "running":
            updates["running_since"] = now.isoformat()

            if not row["started_at"]:
                updates["started_at"] = now.isoformat()

        elif new_status != "running" and running_since:
            spent = (
                now - datetime.fromisoformat(running_since)
            ).total_seconds()

            updates["active_seconds"] = (
                float(row["active_seconds"] or 0.0) + max(0.0, spent)
            )
            updates["running_since"] = None

        if new_status in TERMINAL_STATUSES:
            updates["completed_at"] = now.isoformat()
            updates["stage"] = updates.get("stage", "finished")

    def fail_interrupted_jobs(self) -> int:
        """Mark jobs that were queued/running when the server stopped.

        Their worker thread died with the process, so without this they
        would show as "running" (and the UI as LIVE) forever.
        """

        now = datetime.now(timezone.utc).isoformat()

        with self.lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE repair_jobs
                    SET status = 'failed',
                        message = 'Interrupted by a server restart.',
                        error = ?,
                        stage = 'finished',
                        running_since = NULL,
                        completed_at = ?,
                        updated_at = ?
                    WHERE status IN ('queued', 'running')
                    """,
                    (
                        json.dumps(
                            "The server restarted while this job was "
                            "running. Please start it again."
                        ),
                        now,
                        now,
                    ),
                )
                return cursor.rowcount

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