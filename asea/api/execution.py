"""Reusable execution service for ASEA repair jobs.

This module deliberately has no FastAPI dependency. The API currently supplies
its local job store, graph, and workflow integrations; a local worker can later
supply the same dependencies without moving execution into the cloud API.
"""
import logging
from typing import Any, Callable

from langgraph.types import Command


class RepairExecutionService:
    """Run, resume, and publish repair workflows outside API route handlers."""

    def __init__(
        self,
        *,
        repair_jobs: Any,
        graph_provider: Callable[[], Any],
        resolve_repository: Callable[[str], Any],
        create_initial_state: Callable[..., dict[str, Any]],
        publish_repair_result: Callable[..., dict[str, Any]],
        cleanup_repair_workspace: Callable[..., Any],
        safe_error_detail: Callable[[Exception], str],
        workflow_job_outcome: Callable[
            [dict[str, Any]], tuple[str, str, str | None]
        ],
    ) -> None:
        self.repair_jobs = repair_jobs
        self.graph_provider = graph_provider
        self.resolve_repository = resolve_repository
        self.create_initial_state = create_initial_state
        self.publish_repair_result = publish_repair_result
        self.cleanup_repair_workspace = cleanup_repair_workspace
        self.safe_error_detail = safe_error_detail
        self.workflow_job_outcome = workflow_job_outcome

    def run_repair_job(
        self,
        job_id: str,
        repository: str,
        publish: bool = False,
    ) -> None:
        """Execute a new repair workflow and persist its outcome."""
        graph = None
        config = None
        try:
            self.repair_jobs.update_job(
                job_id,
                status="running",
                message="Resolving repository and starting repair.",
            )
            repo_path = self.resolve_repository(repository)
            initial_state = self.create_initial_state(
                str(repo_path),
                review_mode="api",
            )
            config = {
                "configurable": {"thread_id": job_id},
                "recursion_limit": 1000,
            }
            graph = self.graph_provider()
            result = graph.invoke(initial_state, config=config)
            snapshot = graph.get_state(config)

            if snapshot.interrupts:
                self.repair_jobs.update_job(
                    job_id,
                    status="waiting_for_review",
                    message="ASEA is waiting for patch approval.",
                    review_request=snapshot.interrupts[0].value,
                    result={"publish_requested": publish},
                )
                return

            job_result = {"workflow": result}
            terminal_status, message, workflow_error = self.workflow_job_outcome(result)
            if publish and terminal_status == "completed":
                publishing_result = self.publish_repair_result(result, job_id)
                job_result["publishing"] = publishing_result
                if publishing_result["status"] == "PULL_REQUEST_CREATED":
                    message = "ASEA repair completed and pull request created."
                elif publishing_result["status"] == "PULL_REQUEST_EXISTS":
                    message = (
                        "ASEA repair completed; an existing pull request was found."
                    )
                else:
                    message = (
                        "ASEA repair completed and validation passed, "
                        "but GitHub publishing failed."
                    )

            if not (
                publish
                and job_result.get("publishing", {}).get("status") == "PUBLISH_FAILED"
            ):
                self.cleanup_repair_workspace(result.get("repo_path", ""), result)
            self.repair_jobs.update_job(
                job_id,
                status=terminal_status,
                message=message,
                result=job_result,
                error=workflow_error,
            )
        except Exception as error:
            if graph is not None and config is not None:
                try:
                    snapshot = graph.get_state(config)
                    if snapshot.values:
                        self.cleanup_repair_workspace(
                            snapshot.values.get("repo_path", ""),
                            snapshot.values,
                        )
                except Exception:
                    logging.exception(
                        "Could not clean failed repair workspace. Job ID: %s",
                        job_id,
                    )
            logging.exception("ASEA repair job failed. Job ID: %s", job_id)
            self.repair_jobs.update_job(
                job_id,
                status="failed",
                message="ASEA repair workflow failed.",
                error=self.safe_error_detail(error),
            )

    def retry_repair_publishing(self, job_id: str) -> None:
        """Retry publishing from a preserved, validated workflow result."""
        try:
            job = self.repair_jobs.get_job(job_id)
            if job is None:
                self.repair_jobs.release_publishing_retry(job_id)
                return
            result = (job.get("result") or {}).get("workflow")
            if not isinstance(result, dict):
                self.repair_jobs.release_publishing_retry(job_id)
                return

            publishing_result = self.publish_repair_result(result, job_id)
            job_result = dict(job.get("result") or {})
            job_result["publishing"] = publishing_result
            if publishing_result.get("status") in {
                "PULL_REQUEST_CREATED",
                "PULL_REQUEST_EXISTS",
            }:
                message = (
                    "ASEA repair completed and pull request created."
                    if publishing_result["status"] == "PULL_REQUEST_CREATED"
                    else "ASEA repair completed; an existing pull request was found."
                )
                self.repair_jobs.update_job(
                    job_id,
                    message=message,
                    result=job_result,
                    error=None,
                    publishing_retry=False,
                )
                try:
                    self.cleanup_repair_workspace(result.get("repo_path", ""), result)
                except Exception:
                    logging.exception(
                        "Publishing succeeded but workspace cleanup failed. Job ID: %s",
                        job_id,
                    )
                return

            self.repair_jobs.update_job(
                job_id,
                message=(
                    "ASEA repair remains validated, but GitHub publishing failed. "
                    "Publishing can be retried."
                ),
                result=job_result,
                publishing_retry=False,
            )
        except Exception as error:
            logging.exception(
                "Unexpected error during publishing retry. Job ID: %s", job_id
            )
            try:
                self.repair_jobs.update_job(
                    job_id,
                    message="Publishing retry failed unexpectedly; it can be retried.",
                    error=self.safe_error_detail(error),
                    publishing_retry=False,
                )
            except Exception:
                logging.exception(
                    "Could not persist publishing retry failure. Job ID: %s",
                    job_id,
                )
            finally:
                self.repair_jobs.release_publishing_retry(job_id)

    def resume_repair_job(
        self,
        job_id: str,
        decision: str,
        feedback: str,
    ) -> None:
        """Resume a checkpointed workflow after its human review decision."""
        graph = None
        config = None
        try:
            job = self.repair_jobs.get_job(job_id)
            if job is None:
                raise KeyError(f"Repair job not found: {job_id}")
            publish = bool(job.get("publish_requested", False))
            if hasattr(self.repair_jobs, "acknowledge_review_start"):
                if not self.repair_jobs.acknowledge_review_start(job_id, decision):
                    raise RuntimeError(
                        "Review decision is no longer pending for this repair job."
                    )

            graph = self.graph_provider()
            config = {
                "configurable": {"thread_id": job_id},
                "recursion_limit": 1000,
            }
            result = graph.invoke(
                Command(resume={"decision": decision, "feedback": feedback}),
                config=config,
            )
            snapshot = graph.get_state(config)

            if snapshot.interrupts:
                self.repair_jobs.update_job(
                    job_id,
                    status="waiting_for_review",
                    message="ASEA generated another patch requiring approval.",
                    review_request=snapshot.interrupts[0].value,
                    result={"publish_requested": publish},
                )
                return

            job_result = {"workflow": result}
            terminal_status, message, workflow_error = self.workflow_job_outcome(result)
            if publish and terminal_status == "completed":
                publishing_result = self.publish_repair_result(result, job_id)
                job_result["publishing"] = publishing_result
                if publishing_result["status"] == "PULL_REQUEST_CREATED":
                    message = "ASEA repair completed and pull request created."
                elif publishing_result["status"] == "PULL_REQUEST_EXISTS":
                    message = (
                        "ASEA repair completed; an existing pull request was found."
                    )
                else:
                    message = (
                        "ASEA repair completed and validation passed, "
                        "but GitHub publishing failed."
                    )
            if not (
                publish
                and job_result.get("publishing", {}).get("status") == "PUBLISH_FAILED"
            ):
                self.cleanup_repair_workspace(result.get("repo_path", ""), result)
            self.repair_jobs.update_job(
                job_id,
                status=terminal_status,
                message=message,
                result=job_result,
                error=workflow_error,
            )
        except Exception as error:
            if graph is not None and config is not None:
                try:
                    snapshot = graph.get_state(config)
                    if snapshot.values:
                        self.cleanup_repair_workspace(
                            snapshot.values.get("repo_path", ""),
                            snapshot.values,
                        )
                except Exception:
                    logging.exception(
                        "Could not clean failed repair workspace. Job ID: %s",
                        job_id,
                    )
            logging.exception(
                "ASEA failed while resuming the reviewed repair. Job ID: %s",
                job_id,
            )
            self.repair_jobs.update_job(
                job_id,
                status="failed",
                message="Failed to resume ASEA repair workflow.",
                error=self.safe_error_detail(error),
            )
