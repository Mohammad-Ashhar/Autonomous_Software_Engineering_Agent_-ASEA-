"""PostgreSQL-backed worker enrollment, authentication, and lease coordination."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class WorkerProtocolError(Exception):
    """A worker protocol request is invalid or conflicts with current state."""


class CloudWorkerManager:
    """Own worker credentials and fencing leases in the cloud PostgreSQL database."""

    def __init__(self, database_url: str | None = None):
        self.database_url = (database_url or os.getenv("ASEA_DATABASE_URL", "")).strip()
        if not self.database_url:
            raise RuntimeError("ASEA_DATABASE_URL must be configured.")
        self.pepper = os.getenv("ASEA_WORKER_CREDENTIAL_PEPPER", "").encode()
        if len(self.pepper) < 32:
            raise RuntimeError(
                "ASEA_WORKER_CREDENTIAL_PEPPER must contain at least 32 characters."
            )

    def _connect(self):
        return psycopg.connect(
            self.database_url, connect_timeout=10, row_factory=dict_row
        )

    def _digest(self, secret: str) -> bytes:
        return hmac.new(self.pepper, secret.encode(), hashlib.sha256).digest()

    def issue_enrollment_code(self, owner_id: str) -> dict[str, Any]:
        code = secrets.token_urlsafe(32)
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO public.asea_worker_enrollment_codes
                    (owner_id, code_hash, expires_at)
                VALUES (%s, %s, now() + interval '10 minutes')
                RETURNING enrollment_id, expires_at
                """,
                (UUID(owner_id), self._digest(code)),
            ).fetchone()
        return {"enrollment_code": code, "expires_at": row["expires_at"]}

    def enroll(self, code: str, version: str | None, capabilities: dict) -> dict:
        if not isinstance(code, str) or not 20 <= len(code) <= 200:
            raise WorkerProtocolError("Enrollment code is invalid or expired.")
        worker_secret = secrets.token_urlsafe(48)
        with self._connect() as connection:
            enrollment = connection.execute(
                """
                SELECT enrollment_id, owner_id
                FROM public.asea_worker_enrollment_codes
                WHERE code_hash = %s AND consumed_at IS NULL
                  AND expires_at > now()
                FOR UPDATE
                """,
                (self._digest(code),),
            ).fetchone()
            if enrollment is None:
                raise WorkerProtocolError("Enrollment code is invalid or expired.")
            connection.execute(
                "UPDATE public.asea_worker_enrollment_codes "
                "SET consumed_at = now() WHERE enrollment_id = %s",
                (enrollment["enrollment_id"],),
            )
            worker = connection.execute(
                """
                INSERT INTO public.asea_workers
                    (owner_id, credential_hash, worker_version, capabilities)
                VALUES (%s, %s, %s, %s)
                RETURNING worker_id
                """,
                (
                    enrollment["owner_id"],
                    self._digest(worker_secret),
                    version,
                    Jsonb(capabilities),
                ),
            ).fetchone()
        return {"worker_id": str(worker["worker_id"]), "worker_secret": worker_secret}

    def authenticate(self, secret: str) -> dict | None:
        if not isinstance(secret, str) or not 32 <= len(secret) <= 200:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT worker_id, owner_id, status
                FROM public.asea_workers WHERE credential_hash = %s
                """,
                (self._digest(secret),),
            ).fetchone()
        if row is None or row["status"] != "active":
            return None
        return {"worker_id": str(row["worker_id"]), "owner_id": str(row["owner_id"])}

    def list_workers(self, owner_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT worker_id, status, worker_version, capabilities,
                       last_heartbeat_at, created_at, revoked_at
                FROM public.asea_workers WHERE owner_id = %s
                ORDER BY created_at DESC
                """,
                (UUID(owner_id),),
            ).fetchall()
        return rows

    def revoke_worker(self, owner_id: str, worker_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE public.asea_workers SET status = 'revoked', revoked_at = now()
                WHERE owner_id = %s AND worker_id = %s AND status = 'active'
                """,
                (UUID(owner_id), UUID(worker_id)),
            )
            connection.execute(
                """
                UPDATE public.asea_job_leases SET released_at = now()
                WHERE worker_id = %s AND released_at IS NULL
                """,
                (UUID(worker_id),),
            )
            return cursor.rowcount == 1

    def heartbeat(self, identity: dict, version: str | None, capabilities: dict):
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE public.asea_workers
                SET last_heartbeat_at = now(),
                    worker_version = COALESCE(%s, worker_version),
                    capabilities = %s
                WHERE worker_id = %s AND owner_id = %s AND status = 'active'
                """,
                (
                    version, Jsonb(capabilities), UUID(identity["worker_id"]),
                    UUID(identity["owner_id"]),
                ),
            )
            if cursor.rowcount != 1:
                raise WorkerProtocolError("Worker is revoked or no longer available.")

    def claim_job(self, identity: dict, lease_seconds: int = 90) -> dict | None:
        with self._connect() as connection:
            worker = connection.execute(
                """
                SELECT 1 FROM public.asea_workers
                WHERE worker_id = %s AND owner_id = %s AND status = 'active'
                FOR UPDATE
                """,
                (UUID(identity["worker_id"]), UUID(identity["owner_id"])),
            ).fetchone()
            if worker is None:
                raise WorkerProtocolError("Worker is revoked or no longer available.")
            row = connection.execute(
                """
                SELECT job_id, owner_id, repository, publish_requested,
                       workflow_version, checkpoint_reference
                FROM public.asea_repair_jobs
                WHERE owner_id = %s AND status = 'queued'
                  AND NOT EXISTS (
                    SELECT 1 FROM public.asea_job_leases l
                    WHERE l.job_id = asea_repair_jobs.job_id
                      AND l.released_at IS NULL
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.asea_job_leases own_lease
                    WHERE own_lease.worker_id = %s
                      AND own_lease.released_at IS NULL
                  )
                ORDER BY created_at, job_id
                LIMIT 1 FOR UPDATE SKIP LOCKED
                """,
                (UUID(identity["owner_id"]),),
            ).fetchone()
            if row is None:
                return None
            generation = connection.execute(
                """
                SELECT COALESCE(MAX(fencing_generation), 0) + 1 AS next_generation
                FROM public.asea_job_leases WHERE job_id = %s
                """,
                (row["job_id"],),
            ).fetchone()["next_generation"]
            lease = connection.execute(
                """
                INSERT INTO public.asea_job_leases
                    (job_id, worker_id, fencing_generation, expires_at)
                VALUES (%s, %s, %s, now() + (%s * interval '1 second'))
                RETURNING lease_id, expires_at
                """,
                (row["job_id"], UUID(identity["worker_id"]), generation, lease_seconds),
            ).fetchone()
            connection.execute(
                """
                UPDATE public.asea_repair_jobs
                SET status = 'running', message = 'Cloud worker claimed repair job.',
                    updated_at = now()
                WHERE job_id = %s AND status = 'queued'
                """,
                (row["job_id"],),
            )
            return {
                **row,
                "lease_id": str(lease["lease_id"]),
                "lease_expires_at": lease["expires_at"],
                "fencing_generation": generation,
            }

    def renew_lease(
        self, identity: dict, job_id: str, lease_id: str, generation: int,
        lease_seconds: int = 90,
    ) -> datetime:
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE public.asea_job_leases l
                SET expires_at = now() + (%s * interval '1 second')
                FROM public.asea_workers w
                WHERE l.job_id = %s AND l.lease_id = %s
                  AND l.worker_id = %s AND l.fencing_generation = %s
                  AND l.released_at IS NULL AND l.expires_at > now()
                  AND w.worker_id = l.worker_id AND w.owner_id = %s
                  AND w.status = 'active'
                RETURNING l.expires_at
                """,
                (
                    lease_seconds, job_id, UUID(lease_id),
                    UUID(identity["worker_id"]), generation, UUID(identity["owner_id"]),
                ),
            ).fetchone()
        if row is None:
            raise WorkerProtocolError("Lease is expired, revoked, or superseded.")
        return row["expires_at"]

    def append_event(
        self, identity: dict, job_id: str, lease_id: str, generation: int,
        event_type: str, payload: dict, idempotency_key: str,
    ) -> int:
        if len(event_type) > 80 or len(idempotency_key) > 200:
            raise WorkerProtocolError("Event metadata exceeds the allowed size.")
        if len(str(payload).encode()) > 16_384:
            raise WorkerProtocolError("Event payload exceeds 16 KB.")
        with self._connect() as connection:
            lease = connection.execute(
                """
                SELECT l.lease_id, j.owner_id FROM public.asea_job_leases l
                JOIN public.asea_repair_jobs j ON j.job_id = l.job_id
                JOIN public.asea_workers w ON w.worker_id = l.worker_id
                WHERE l.job_id = %s AND l.lease_id = %s
                  AND l.worker_id = %s AND l.fencing_generation = %s
                  AND l.released_at IS NULL AND l.expires_at > now()
                  AND w.status = 'active' AND w.owner_id = %s
                """,
                (
                    job_id, UUID(lease_id), UUID(identity["worker_id"]),
                    generation, UUID(identity["owner_id"]),
                ),
            ).fetchone()
            if lease is None:
                raise WorkerProtocolError("Lease is expired, revoked, or superseded.")
            row = connection.execute(
                """
                INSERT INTO public.asea_job_events
                    (owner_id, job_id, worker_id, lease_id, event_type,
                     payload, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (worker_id, idempotency_key)
                    WHERE worker_id IS NOT NULL AND idempotency_key IS NOT NULL
                DO NOTHING
                RETURNING event_id
                """,
                (
                    UUID(identity["owner_id"]), job_id, UUID(identity["worker_id"]),
                    UUID(lease_id), event_type, Jsonb(payload), idempotency_key,
                ),
            ).fetchone()
            if row:
                return row["event_id"]
            existing = connection.execute(
                """
                SELECT event_id FROM public.asea_job_events
                WHERE worker_id = %s AND idempotency_key = %s
                """,
                (UUID(identity["worker_id"]), idempotency_key),
            ).fetchone()
            return existing["event_id"]
