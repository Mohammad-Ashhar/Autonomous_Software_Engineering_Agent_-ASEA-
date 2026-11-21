"""PostgreSQL-backed implementation of the existing repair-job manager.

This adapter is deliberately opt-in and is not selected by the API yet. The
cloud worker protocol must be implemented before switching production traffic
from the local SQLite manager to this backend.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from asea.api.job_manager import JobQueueFullError, RepairJobManager


class _HybridRow(dict):
    """Mapping row that also supports SQLite-style positional indexing."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def _hybrid_row_factory(cursor):
    names = [column.name for column in cursor.description]

    def make_row(values):
        return _HybridRow(zip(names, values))

    return make_row


class _PostgresConnection:
    """Small DB-API compatibility layer for the shared manager operations."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, statement, parameters=()):
        # The shared manager uses SQLite's qmark placeholders. SQL statements
        # contain no literal question marks, so a direct conversion is safe.
        statement = re.sub(r"\brepair_jobs\b", "public.asea_repair_jobs", statement)
        return self._connection.execute(statement.replace("?", "%s"), parameters)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def close(self):
        self._connection.close()


class PostgresRepairJobManager(RepairJobManager):
    """Use the Supabase PostgreSQL schema without changing local SQLite mode."""

    def __init__(self, database_url: str | None = None):
        self.database_url = (
            database_url or os.getenv("ASEA_DATABASE_URL", "")
        ).strip()
        if not self.database_url:
            raise RuntimeError("ASEA_DATABASE_URL must be configured.")
        parsed = urlparse(self.database_url)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.hostname
            or not parsed.path.strip("/")
            or query.get("sslmode", [""])[0] not in {"require", "verify-ca", "verify-full"}
        ):
            raise RuntimeError(
                "ASEA_DATABASE_URL must be a PostgreSQL URL with sslmode=require "
                "or stronger."
            )
        # Do not call the SQLite constructor: it creates local files and tables.
        from threading import Lock

        self.lock = Lock()
        self._initialize_database()

    def _connect(self):
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "Install ASEA's API dependencies to use PostgreSQL storage."
            ) from error

        connection = psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=_hybrid_row_factory,
        )
        return _PostgresConnection(connection)

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_database(self):
        """Verify the documented migration was applied; never mutate schema."""
        required_tables = {
            "asea_repair_jobs",
            "asea_workers",
            "asea_worker_enrollment_codes",
            "asea_job_leases",
            "asea_job_events",
        }
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name LIKE 'asea_%'
                """
            ).fetchall()
        present = {row["table_name"] for row in rows}
        missing = sorted(required_tables - present)
        if missing:
            raise RuntimeError(
                "ASEA PostgreSQL schema is incomplete. Apply "
                "migrations/postgres/0001_cloud_coordination.sql; missing: "
                + ", ".join(missing)
            )

    @staticmethod
    def _serialize(value: Any):
        if value is None:
            return None
        from psycopg.types.json import Jsonb

        return Jsonb(value)

    @staticmethod
    def _deserialize(value):
        if value is None or isinstance(value, (dict, list, int, float, bool)):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value

    @staticmethod
    def _row_to_dict(row):
        job = dict(row)
        for field in ("review_request", "result", "error"):
            job[field] = PostgresRepairJobManager._deserialize(job[field])
        job["publish_requested"] = bool(job.get("publish_requested", False))
        job["publishing_retry"] = bool(job.get("publishing_retry", False))
        return job

    def create_job(
        self,
        job_id: str,
        repository: str,
        publish_requested: bool = False,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        if not owner_id:
            raise ValueError("owner_id is required for PostgreSQL-backed jobs.")
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

        now = datetime.now(timezone.utc)
        with self._connection() as connection:
            # Serialize the global admission check across API instances.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext('asea_active_job_admission'))"
            )
            active = connection.execute(
                """
                SELECT COUNT(*) AS active_count
                FROM public.asea_repair_jobs
                WHERE status IN ('queued', 'running', 'waiting_for_review')
                """
            ).fetchone()["active_count"]
            if active >= self._maximum_active_jobs():
                raise JobQueueFullError("Repair queue is full.")
            connection.execute(
                """
                INSERT INTO public.asea_repair_jobs (
                    job_id, owner_id, repository, status, message,
                    publish_requested, created_at, updated_at
                )
                VALUES (?, ?, ?, 'queued', 'Repair job created.', ?, ?, ?)
                """,
                (job_id, owner_id, repository, publish_requested, now, now),
            )
        return self.get_job(job_id, owner_id=owner_id)

    def update_job(self, job_id: str, **updates: Any) -> dict[str, Any]:
        # Keep the existing validation and field allow-list. The parent method
        # is reused after converting the storage operations through Jsonb.
        return super().update_job(job_id, **updates)

    def mark_interrupted_jobs(self) -> int:
        now = datetime.now(timezone.utc)
        from psycopg.types.json import Jsonb

        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE public.asea_repair_jobs
                SET status = 'failed',
                    message = 'Repair interrupted by API process restart.',
                    error = ?,
                    updated_at = ?
                WHERE status = 'queued'
                   OR (status = 'running' AND review_decision IS NULL)
                """,
                (
                    Jsonb(
                        "The API restarted before this repair completed. "
                        "Start a new repair; jobs awaiting review are preserved."
                    ),
                    now,
                ),
            )
            return cursor.rowcount

    def claim_next_queued_job(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM public.asea_repair_jobs
                WHERE status = 'queued'
                ORDER BY created_at, job_id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                """
                UPDATE public.asea_repair_jobs
                SET status = 'running',
                    message = 'Local worker claimed repair job.',
                    updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, row["job_id"]),
            )
            if cursor.rowcount != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM public.asea_repair_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
            return self._row_to_dict(claimed)

    def check_database(self) -> None:
        with self._connection() as connection:
            connection.execute("SELECT 1").fetchone()
