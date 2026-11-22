"""Cloud worker polling and execution runtime.

The runtime executes ASEA graphs only on the enrolled machine. Coordination,
leases, and checkpoints remain in the cloud PostgreSQL database.
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time

from langgraph.types import Command

from asea.api.cloud_checkpoint import open_cloud_checkpointer
from asea.api.cloud_worker_client import (
    CloudWorkerAPIError,
    CloudWorkerClient,
    load_worker_credentials,
)
from asea.api.main import (
    _publish_repair_result,
    _safe_error_detail,
    _workflow_job_outcome,
)
from asea.api.workspace_cleanup import cleanup_repair_workspace
from asea.cli import resolve_repository
from asea.core.graph import build_asea_graph, create_initial_state

_LOG = logging.getLogger(__name__)
_STOP = threading.Event()
LEASE_RENEW_SECONDS = 25
IDLE_HEARTBEAT_SECONDS = 30
WORKER_VERSION = "0.1.0"


def _heartbeat_if_due(client, last_heartbeat: float, now: float | None = None) -> float:
    """Keep idle workers visible without relying on job lease renewals."""
    current = time.monotonic() if now is None else now
    if current - last_heartbeat < IDLE_HEARTBEAT_SECONDS:
        return last_heartbeat
    client.heartbeat(WORKER_VERSION, {"runtime": "cloud"})
    return current


def _positive_seconds(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{name} must be a number.") from error
    if not 0.5 <= value <= 300:
        raise RuntimeError(f"{name} must be between 0.5 and 300 seconds.")
    return value


class LeaseGuard:
    """Renew worker and job leases while a graph invocation is active."""

    def __init__(self, client, job: dict):
        self.client = client
        self.job = job
        self.lost = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=f"asea-lease-{job['job_id']}",
            daemon=True,
        )

    def _run(self):
        while not self.stop.wait(LEASE_RENEW_SECONDS):
            try:
                self.client.heartbeat(WORKER_VERSION, {"runtime": "cloud"})
                self.client.renew_lease(
                    self.job["job_id"],
                    self.job["lease_id"],
                    self.job["fencing_generation"],
                )
            except Exception as error:  # noqa: BLE001
                _LOG.error(
                    "Cloud lease renewal failed for job %s (%s).",
                    self.job["job_id"],
                    type(error).__name__,
                )
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.stop.set()
        self.thread.join(timeout=2)


def _snapshot_interrupts(snapshot) -> list:
    """Return pending LangGraph interrupts across checkpoint tasks.

    StateSnapshot exposes pending interrupts on each PregelTask, not as a
    top-level "interrupts" attribute. The top-level fallback keeps adapters
    and older test doubles compatible.
    """
    direct = getattr(snapshot, "interrupts", None)
    if direct is not None:
        return list(direct)
    pending = []
    for task in getattr(snapshot, "tasks", ()) or ():
        pending.extend(getattr(task, "interrupts", ()) or ())
    return pending

def _checkpoint_reference(snapshot, job_id: str, worker_id: str) -> dict:
    config = getattr(snapshot, "config", None) or {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    reference = {"thread_id": job_id, "worker_id": worker_id}
    for key in ("checkpoint_id", "checkpoint_ns"):
        value = configurable.get(key)
        if isinstance(value, str) and value:
            reference[key] = value
    return reference


def execute_claimed_job(
    client: CloudWorkerClient,
    job: dict,
    worker_id: str,
    graph,
) -> str:
    """Execute or resume one leased job; return its resulting state."""
    job_id = str(job["job_id"])
    lease_id = str(job["lease_id"])
    generation = int(job["fencing_generation"])
    config = {"configurable": {"thread_id": job_id}, "recursion_limit": 1000}

    with LeaseGuard(client, job) as lease:
        client.append_event(
            job_id, lease_id, generation, "job.execution_started",
            {"worker_id": worker_id}, f"{job_id}:{generation}:started",
        )
        try:
            decision = job.get("review_decision")
            if decision in {"approved", "rejected"}:
                result = graph.invoke(
                    Command(
                        resume={
                            "decision": decision,
                            "feedback": job.get("review_feedback") or "",
                        }
                    ),
                    config=config,
                )
            else:
                repository_path = resolve_repository(job["repository"])
                initial_state = create_initial_state(
                    str(repository_path), review_mode="api"
                )
                result = graph.invoke(initial_state, config=config)

            snapshot = graph.get_state(config)
            interrupts = _snapshot_interrupts(snapshot)
            if lease.lost.is_set():
                _LOG.error(
                    "Stopping finalization for job %s because its lease was lost.",
                    job_id,
                )
                return "lease_lost"

            if interrupts:
                reference = _checkpoint_reference(snapshot, job_id, worker_id)
                client.pause_for_review(
                    job_id,
                    lease_id,
                    generation,
                    interrupts[0].value,
                    reference,
                    f"{job_id}:{generation}:review",
                )
                return "waiting_for_review"

            terminal_status, message, workflow_error = _workflow_job_outcome(result)
            if terminal_status != "completed":
                client.fail_job(
                    job_id,
                    lease_id,
                    generation,
                    workflow_error or message,
                    f"{job_id}:{generation}:failed",
                )
                cleanup_repair_workspace(result.get("repo_path", ""), result)
                return "failed"

            # The server validates the terminal status at the top level;
            # retain the workflow result under its existing nested key as well.
            payload = {"status": result.get("status"), "workflow": result}
            if job.get("publish_requested"):
                payload["publishing"] = _publish_repair_result(result, job_id)
            if lease.lost.is_set():
                _LOG.error(
                    "Lease lost before completion could be committed for job %s.",
                    job_id,
                )
                return "lease_lost"
            client.complete_job(
                job_id,
                lease_id,
                generation,
                payload,
                f"{job_id}:{generation}:completed",
            )
            if payload.get("publishing", {}).get("status") != "PUBLISH_FAILED":
                cleanup_repair_workspace(result.get("repo_path", ""), result)
            return "completed"
        except CloudWorkerAPIError:
            raise
        except Exception as error:  # noqa: BLE001
            if lease.lost.is_set():
                _LOG.exception("Job %s failed after its lease was lost.", job_id)
                return "lease_lost"
            safe_detail = _safe_error_detail(error)
            _LOG.exception("Cloud repair job %s failed.", job_id)
            client.fail_job(
                job_id,
                lease_id,
                generation,
                safe_detail,
                f"{job_id}:{generation}:failed",
            )
            return "failed"


def run(api_url: str, profile: str = "default") -> None:
    """Poll the cloud queue using one named account profile."""
    database_url = os.getenv("ASEA_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "Set ASEA_DATABASE_URL to the Supabase PostgreSQL URL with TLS enabled."
        )
    credentials = load_worker_credentials(api_url, profile)
    if credentials is None:
        raise RuntimeError(
            f"This machine has no credentials for profile '{profile}'. "
            "Run 'asea cloud-worker enroll' with the same --profile."
        )
    client = CloudWorkerClient(api_url, credentials["worker_secret"])
    poll_seconds = _positive_seconds("ASEA_CLOUD_WORKER_POLL_SECONDS", 5.0)
    signal.signal(signal.SIGINT, lambda *_: _STOP.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: _STOP.set())

    with open_cloud_checkpointer(database_url) as checkpointer:
        graph = build_asea_graph(checkpointer=checkpointer)
        client.heartbeat(WORKER_VERSION, {"runtime": "cloud"})
        last_heartbeat = time.monotonic()
        _LOG.info("ASEA cloud worker %s started.", credentials["worker_id"])
        while not _STOP.is_set():
            try:
                last_heartbeat = _heartbeat_if_due(client, last_heartbeat)
                job_response = client.claim_job()
                job = job_response.get("job")
                if job is None:
                    until_heartbeat = IDLE_HEARTBEAT_SECONDS - (
                        time.monotonic() - last_heartbeat
                    )
                    _STOP.wait(min(poll_seconds, max(0.1, until_heartbeat)))
                    continue
                _LOG.info("Claimed cloud repair job %s.", job["job_id"])
                outcome = execute_claimed_job(
                    client, job, credentials["worker_id"], graph
                )
                _LOG.info("Cloud repair job %s ended: %s.", job["job_id"], outcome)
            except CloudWorkerAPIError as error:
                if error.status_code == 401:
                    raise RuntimeError(
                        "Cloud worker credentials were rejected or revoked."
                    ) from error
                _LOG.warning("Cloud worker API request failed: %s", error)
                _STOP.wait(poll_seconds)
            except Exception:
                _LOG.exception("Cloud worker polling cycle failed.")
                _STOP.wait(poll_seconds)

    _LOG.info("ASEA cloud worker stopped cleanly.")
