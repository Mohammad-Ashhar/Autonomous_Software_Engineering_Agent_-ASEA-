"""Local Windows worker for executing queued ASEA repair jobs.

The API and worker must run on the same machine and share the same SQLite
job/checkpoint files. This is a local development/runtime mode, not the
cloud-worker transport used by a future distributed deployment.
"""
import logging
import os
import signal
from threading import Event

from langgraph.checkpoint.sqlite import SqliteSaver

from asea.api.execution import RepairExecutionService
from asea.api.job_manager import repair_jobs
from asea.api.main import (
    CHECKPOINT_DB_PATH,
    _publish_repair_result,
    _safe_error_detail,
    _workflow_job_outcome,
)
from asea.api.workspace_cleanup import cleanup_repair_workspace
from asea.cli import resolve_repository
from asea.core.graph import build_asea_graph, create_initial_state

_STOP = Event()


def _request_shutdown(signum, _frame) -> None:
    """Ask the polling loop to stop after its current job is finished."""
    logging.info("Received shutdown signal %s; finishing current job.", signum)
    _STOP.set()


def _positive_float(name: str, default: float, minimum: float = 0.1) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number.") from error
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")
    return value


def run() -> None:
    """Poll the shared local queue and execute work sequentially."""
    if os.getenv("ASEA_EXECUTION_MODE", "api").strip().lower() != "worker":
        raise RuntimeError(
            "Set ASEA_EXECUTION_MODE=worker in the shared .env file "
            "before starting the local worker."
        )

    poll_seconds = _positive_float("ASEA_WORKER_POLL_SECONDS", 2.0)
    CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGINT, _request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_shutdown)

    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as checkpointer:
        checkpointer.setup()
        graph = build_asea_graph(checkpointer=checkpointer)
        service = RepairExecutionService(
            repair_jobs=repair_jobs,
            graph_provider=lambda: graph,
            resolve_repository=resolve_repository,
            create_initial_state=create_initial_state,
            publish_repair_result=_publish_repair_result,
            cleanup_repair_workspace=cleanup_repair_workspace,
            safe_error_detail=_safe_error_detail,
            workflow_job_outcome=_workflow_job_outcome,
        )

        # A process crash cannot safely resume an arbitrary in-flight Python
        # call. Mark such jobs failed; queued jobs and review-paused jobs remain
        # recoverable and can be picked up on the next worker start.
        interrupted = repair_jobs.mark_orphaned_worker_jobs()
        if interrupted:
            logging.warning(
                "Marked %s interrupted repair job(s) as failed.", interrupted
            )

        logging.info("ASEA local worker started; polling every %.1fs.", poll_seconds)
        while not _STOP.is_set():
            did_work = False

            for pending in repair_jobs.list_pending_reviews():
                if _STOP.is_set():
                    break
                did_work = True
                service.resume_repair_job(
                    pending["job_id"],
                    pending["review_decision"],
                    pending.get("review_feedback") or "",
                )

            for pending in repair_jobs.list_pending_publishing_retries():
                if _STOP.is_set():
                    break
                did_work = True
                service.retry_repair_publishing(pending["job_id"])

            if not _STOP.is_set():
                job = repair_jobs.claim_next_queued_job()
                if job is not None:
                    did_work = True
                    service.run_repair_job(
                        job["job_id"],
                        job["repository"],
                        job["publish_requested"],
                    )

            if not did_work:
                _STOP.wait(poll_seconds)

        logging.info("ASEA local worker stopped cleanly.")


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("ASEA_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run()
