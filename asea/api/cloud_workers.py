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

    @staticmethod
    def _validate_capabilities(capabilities: dict) -> None:
        if not isinstance(capabilities, dict) or len(str(capabilities).encode()) > 8192:
            raise WorkerProtocolError(
                "Worker capabilities must be an object under 8 KB."
            )

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
        self._validate_capabilities(capabilities)
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
            worker_was_revoked = cursor.rowcount == 1
            if not worker_was_revoked:
                return False
            connection.execute(
                """
                UPDATE public.asea_repair_jobs
                SET status = 'failed',
                    message = 'Cloud worker was revoked during repair execution.',
                    error = to_jsonb(
                        (
                            'The assigned worker was revoked. Submit a new repair job '
                            'to retry.'
                        )::text
                    ),
                    updated_at = now()
                WHERE (
                    status = 'running' AND job_id IN (
                        SELECT job_id FROM public.asea_job_leases
                        WHERE worker_id = %s AND released_at IS NULL
                    )
                ) OR (
                    status IN ('queued', 'waiting_for_review')
                    AND execution_worker_id = %s
                    AND checkpoint_reference IS NOT NULL
                )
                """,
                (UUID(worker_id), UUID(worker_id)),
            )
            connection.execute(
                """
                UPDATE public.asea_job_leases SET released_at = now()
                WHERE worker_id = %s AND released_at IS NULL
                """,
                (UUID(worker_id),),
            )
            return worker_was_revoked

    def heartbeat(self, identity: dict, version: str | None, capabilities: dict):
        self._validate_capabilities(capabilities)
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
            self._recover_expired_leases(connection)
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
                       workflow_version, checkpoint_reference,
                       review_decision, review_feedback, execution_worker_id
                FROM public.asea_repair_jobs
                WHERE owner_id = %s AND status = 'queued'
                  AND (execution_worker_id IS NULL OR execution_worker_id = %s)
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
                (
                    UUID(identity["owner_id"]),
                    UUID(identity["worker_id"]),
                    UUID(identity["worker_id"]),
                ),
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
                SET status = 'running',
                    execution_worker_id = COALESCE(execution_worker_id, %s),
                    message = 'Cloud worker claimed repair job.',
                    updated_at = now()
                WHERE job_id = %s AND status = 'queued'
                """,
                (UUID(identity["worker_id"]), row["job_id"]),
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



    @staticmethod
    def _validate_review_checkpoint(review_request: dict, checkpoint_reference: dict):
        import json

        if not isinstance(review_request, dict) or not isinstance(
            checkpoint_reference, dict
        ):
            raise WorkerProtocolError(
                "Review request and checkpoint reference must be objects."
            )
        if len(json.dumps(review_request, default=str).encode()) > 262_144:
            raise WorkerProtocolError("Review request exceeds 256 KB.")
        if len(json.dumps(checkpoint_reference, default=str).encode()) > 8192:
            raise WorkerProtocolError("Checkpoint reference exceeds 8 KB.")
        thread_id = checkpoint_reference.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise WorkerProtocolError("Checkpoint reference must include thread_id.")

    def pause_for_review(
        self, identity: dict, job_id: str, lease_id: str, generation: int,
        review_request: dict, checkpoint_reference: dict, idempotency_key: str,
    ) -> dict:
        """Persist a review interrupt and release its lease atomically."""
        self._validate_review_checkpoint(review_request, checkpoint_reference)
        if (
            checkpoint_reference.get("thread_id") != job_id
            or checkpoint_reference.get("worker_id") != identity.get("worker_id")
        ):
            raise WorkerProtocolError(
                "Checkpoint reference does not match this job and worker."
            )
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise WorkerProtocolError(
                "Idempotency key must contain 1 to 200 characters."
            )
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT event_id, job_id, event_type
                FROM public.asea_job_events
                WHERE worker_id = %s AND idempotency_key = %s
                """,
                (UUID(identity["worker_id"]), idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["job_id"] != job_id or existing["event_type"] != "job.waiting_for_review":
                    raise WorkerProtocolError(
                        "Idempotency key was already used for a different operation."
                    )
                return {
                    "status": "waiting_for_review",
                    "event_id": existing["event_id"],
                    "replayed": True,
                }
            lease = connection.execute(
                """
                SELECT l.lease_id
                FROM public.asea_job_leases l
                JOIN public.asea_repair_jobs j ON j.job_id = l.job_id
                JOIN public.asea_workers w ON w.worker_id = l.worker_id
                WHERE l.job_id = %s AND l.lease_id = %s
                  AND l.worker_id = %s AND l.fencing_generation = %s
                  AND l.released_at IS NULL AND l.expires_at > now()
                  AND j.owner_id = %s AND j.status = 'running'
                  AND j.execution_worker_id = l.worker_id
                  AND w.owner_id = j.owner_id AND w.status = 'active'
                FOR UPDATE OF l, j
                """,
                (
                    job_id, UUID(lease_id), UUID(identity["worker_id"]),
                    generation, UUID(identity["owner_id"]),
                ),
            ).fetchone()
            if lease is None:
                raise WorkerProtocolError("Lease is expired, revoked, or superseded.")
            connection.execute(
                """
                UPDATE public.asea_repair_jobs
                SET status = 'waiting_for_review',
                    message = 'ASEA is waiting for patch approval.',
                    review_request = %s,
                    checkpoint_reference = %s,
                    review_decision = NULL,
                    review_feedback = NULL,
                    updated_at = now()
                WHERE job_id = %s AND status = 'running'
                """,
                (Jsonb(review_request), Jsonb(checkpoint_reference), job_id),
            )
            connection.execute(
                "UPDATE public.asea_job_leases SET released_at = now() "
                "WHERE lease_id = %s AND released_at IS NULL",
                (UUID(lease_id),),
            )
            event = connection.execute(
                """
                INSERT INTO public.asea_job_events
                    (owner_id, job_id, worker_id, lease_id, event_type,
                     payload, idempotency_key)
                VALUES (%s, %s, %s, %s, 'job.waiting_for_review', %s, %s)
                RETURNING event_id
                """,
                (
                    UUID(identity["owner_id"]), job_id, UUID(identity["worker_id"]),
                    UUID(lease_id), Jsonb({"status": "waiting_for_review"}),
                    idempotency_key,
                ),
            ).fetchone()
            return {
                "status": "waiting_for_review",
                "event_id": event["event_id"],
                "replayed": False,
            }

    def submit_review(
        self, owner_id: str, job_id: str, decision: str, feedback: str = "",
    ) -> bool:
        """Atomically queue a paused cloud job with its human review decision."""
        if decision not in {"approved", "rejected"}:
            raise WorkerProtocolError("Review decision must be approved or rejected.")
        if not isinstance(feedback, str) or len(feedback) > 2000:
            raise WorkerProtocolError("Review feedback must be at most 2000 characters.")
        if decision == "rejected" and not feedback.strip():
            raise WorkerProtocolError("Feedback is required when rejecting a patch.")
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE public.asea_repair_jobs AS j
                SET status = 'queued',
                    message = 'Patch review submitted; waiting for the assigned worker.',
                    review_decision = %s,
                    review_feedback = %s,
                    updated_at = now()
                WHERE job_id = %s AND owner_id = %s
                  AND status = 'waiting_for_review'
                  AND checkpoint_reference IS NOT NULL
                  AND execution_worker_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM public.asea_workers w
                      WHERE w.worker_id = j.execution_worker_id AND w.status = 'active'
                  )
                RETURNING job_id
                """,
                (decision, feedback.strip(), job_id, UUID(owner_id)),
            ).fetchone()
            return row is not None

    @staticmethod
    def _validate_terminal_result(result: dict) -> None:
        import json

        if not isinstance(result, dict):
            raise WorkerProtocolError("Completion result must be an object.")
        if result.get("status") != "COMPLETED":
            raise WorkerProtocolError(
                "Only a successfully completed ASEA workflow can be finalized."
            )
        if len(json.dumps(result, default=str).encode()) > 1_048_576:
            raise WorkerProtocolError("Completion result exceeds 1 MB.")

    @staticmethod
    def _validate_failure(error: str) -> None:
        if not isinstance(error, str) or not error.strip() or len(error) > 1800:
            raise WorkerProtocolError(
                "Failure diagnostics must contain 1 to 1800 characters."
            )

    def _existing_terminal_event(
        self, connection, identity: dict, job_id: str, idempotency_key: str,
        event_type: str,
    ):
        row = connection.execute(
            """
            SELECT event_id, job_id, event_type
            FROM public.asea_job_events
            WHERE worker_id = %s AND idempotency_key = %s
            """,
            (UUID(identity["worker_id"]), idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["job_id"] != job_id or row["event_type"] != event_type:
            raise WorkerProtocolError(
                "Idempotency key was already used for a different operation."
            )
        return row["event_id"]

    def complete_job(
        self, identity: dict, job_id: str, lease_id: str, generation: int,
        result: dict, idempotency_key: str,
    ) -> dict:
        self._validate_terminal_result(result)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise WorkerProtocolError(
                "Idempotency key must contain 1 to 200 characters."
            )
        event_type = "job.completed"
        with self._connect() as connection:
            existing = self._existing_terminal_event(
                connection, identity, job_id, idempotency_key, event_type
            )
            if existing is not None:
                return {"status": "completed", "event_id": existing, "replayed": True}
            lease = connection.execute(
                """
                SELECT l.lease_id
                FROM public.asea_job_leases l
                JOIN public.asea_repair_jobs j ON j.job_id = l.job_id
                JOIN public.asea_workers w ON w.worker_id = l.worker_id
                WHERE l.job_id = %s AND l.lease_id = %s
                  AND l.worker_id = %s AND l.fencing_generation = %s
                  AND l.released_at IS NULL AND l.expires_at > now()
                  AND j.owner_id = %s AND j.status = 'running'
                  AND w.owner_id = j.owner_id AND w.status = 'active'
                FOR UPDATE OF l, j
                """,
                (
                    job_id, UUID(lease_id), UUID(identity["worker_id"]),
                    generation, UUID(identity["owner_id"]),
                ),
            ).fetchone()
            if lease is None:
                existing = self._existing_terminal_event(
                    connection, identity, job_id, idempotency_key, event_type
                )
                if existing is not None:
                    return {
                        "status": "completed",
                        "event_id": existing,
                        "replayed": True,
                    }
                raise WorkerProtocolError("Lease is expired, revoked, or superseded.")
            updated = connection.execute(
                """
                UPDATE public.asea_repair_jobs
                SET status = 'completed',
                    message = 'ASEA repair workflow completed successfully.',
                    result = %s, error = NULL, updated_at = now()
                WHERE job_id = %s AND status = 'running'
                RETURNING job_id
                """,
                (Jsonb(result), job_id),
            ).fetchone()
            if updated is None:
                raise WorkerProtocolError("Job is no longer running.")
            connection.execute(
                "UPDATE public.asea_job_leases SET released_at = now() "
                "WHERE lease_id = %s AND released_at IS NULL",
                (UUID(lease_id),),
            )
            event = connection.execute(
                """
                INSERT INTO public.asea_job_events
                    (owner_id, job_id, worker_id, lease_id, event_type,
                     payload, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING event_id
                """,
                (
                    UUID(identity["owner_id"]), job_id, UUID(identity["worker_id"]),
                    UUID(lease_id), event_type,
                    Jsonb({"status": "completed"}), idempotency_key,
                ),
            ).fetchone()
            return {
                "status": "completed",
                "event_id": event["event_id"],
                "replayed": False,
            }

    def fail_job(
        self, identity: dict, job_id: str, lease_id: str, generation: int,
        error: str, idempotency_key: str,
    ) -> dict:
        self._validate_failure(error)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise WorkerProtocolError(
                "Idempotency key must contain 1 to 200 characters."
            )
        event_type = "job.failed"
        with self._connect() as connection:
            existing = self._existing_terminal_event(
                connection, identity, job_id, idempotency_key, event_type
            )
            if existing is not None:
                return {"status": "failed", "event_id": existing, "replayed": True}
            lease = connection.execute(
                """
                SELECT l.lease_id
                FROM public.asea_job_leases l
                JOIN public.asea_repair_jobs j ON j.job_id = l.job_id
                JOIN public.asea_workers w ON w.worker_id = l.worker_id
                WHERE l.job_id = %s AND l.lease_id = %s
                  AND l.worker_id = %s AND l.fencing_generation = %s
                  AND l.released_at IS NULL AND l.expires_at > now()
                  AND j.owner_id = %s AND j.status = 'running'
                  AND w.owner_id = j.owner_id AND w.status = 'active'
                FOR UPDATE OF l, j
                """,
                (
                    job_id, UUID(lease_id), UUID(identity["worker_id"]),
                    generation, UUID(identity["owner_id"]),
                ),
            ).fetchone()
            if lease is None:
                existing = self._existing_terminal_event(
                    connection, identity, job_id, idempotency_key, event_type
                )
                if existing is not None:
                    return {
                        "status": "failed",
                        "event_id": existing,
                        "replayed": True,
                    }
                raise WorkerProtocolError("Lease is expired, revoked, or superseded.")
            updated = connection.execute(
                """
                UPDATE public.asea_repair_jobs
                SET status = 'failed',
                    message = 'Cloud worker reported a repair failure.',
                    error = %s, updated_at = now()
                WHERE job_id = %s AND status = 'running'
                RETURNING job_id
                """,
                (Jsonb(error.strip()), job_id),
            ).fetchone()
            if updated is None:
                raise WorkerProtocolError("Job is no longer running.")
            connection.execute(
                "UPDATE public.asea_job_leases SET released_at = now() "
                "WHERE lease_id = %s AND released_at IS NULL",
                (UUID(lease_id),),
            )
            event = connection.execute(
                """
                INSERT INTO public.asea_job_events
                    (owner_id, job_id, worker_id, lease_id, event_type,
                     payload, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING event_id
                """,
                (
                    UUID(identity["owner_id"]), job_id, UUID(identity["worker_id"]),
                    UUID(lease_id), event_type,
                    Jsonb({"message": "Cloud worker reported a repair failure."}),
                    idempotency_key,
                ),
            ).fetchone()
            return {
                "status": "failed",
                "event_id": event["event_id"],
                "replayed": False,
            }

    @staticmethod
    def _recover_expired_leases(connection) -> int:
        rows = connection.execute(
            """
            WITH expired AS (
                UPDATE public.asea_job_leases
                SET released_at = now()
                WHERE released_at IS NULL AND expires_at <= now()
                RETURNING lease_id, job_id, worker_id
            ),
            failed AS (
                UPDATE public.asea_repair_jobs j
                SET status = 'failed',
                    message = 'Cloud worker lease expired; job was stopped safely.',
                    error = to_jsonb(
                        ('The worker lease expired. ASEA did not automatically replay '
                         'the job because local execution may have side effects.')::text
                    ),
                    updated_at = now()
                FROM expired e
                WHERE j.job_id = e.job_id AND j.status = 'running'
                RETURNING j.job_id, j.owner_id, e.worker_id, e.lease_id
            )
            INSERT INTO public.asea_job_events
                (owner_id, job_id, worker_id, lease_id, event_type, payload)
            SELECT owner_id, job_id, worker_id, lease_id, 'job.lease_expired',
                   jsonb_build_object('status', 'failed', 'automatic_replay', false)
            FROM failed
            RETURNING event_id
            """
        ).fetchall()
        return len(rows)

    def recover_expired_leases(self) -> int:
        """Fail expired running jobs conservatively; never replay side effects."""
        with self._connect() as connection:
            return self._recover_expired_leases(connection)
