import asyncio
import logging
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from asea.api.job_manager import JobQueueFullError, repair_jobs
from asea.api.protection import RequestProtectionMiddleware
from asea.api.workspace_cleanup import cleanup_repair_workspace
from asea.cli import resolve_repository
from asea.core.graph import (
    build_asea_graph,
    create_initial_state,
)
from asea.integrations.repair_publisher import publish_validated_repairs
from asea.tools.analyzer import RepositoryAnalyzer
from asea.tools.repository import RepositoryManager

CHECKPOINT_DB_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "asea_checkpoints.db"
)


def validate_api_security_configuration(environment: str, api_key: str | None) -> None:
    """Enforce a sufficiently strong API key before production startup."""
    if environment.strip().lower() != "production":
        return

    if api_key is None:
        raise RuntimeError(
            "ASEA_API_KEY must be configured when ASEA_ENV=production."
        )

    normalized_key = api_key.strip()
    if len(normalized_key) < 32:
        raise RuntimeError(
            "ASEA_API_KEY must contain at least 32 non-whitespace characters "
            "when ASEA_ENV=production."
        )


def is_valid_api_key(authorization: str | None, expected_key: str) -> bool:
    """Validate a Bearer token without leaking timing information."""
    if not authorization:
        return False
    scheme, separator, supplied_key = authorization.partition(" ")
    return (
        bool(separator)
        and scheme.lower() == "bearer"
        and bool(supplied_key)
        and secrets.compare_digest(supplied_key, expected_key)
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global executor
    validate_api_security_configuration(
        os.getenv("ASEA_ENV", "development"),
        os.getenv("ASEA_API_KEY"),
    )
    CHECKPOINT_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    # A fresh executor is required for every application lifespan. This keeps
    # test clients and ASGI reloads from reusing an executor already shut down.
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        # Keep the executor cleanup outside the checkpointer context so even a
        # failed database setup or graph compilation cannot leak its worker.
        with SqliteSaver.from_conn_string(
            str(CHECKPOINT_DB_PATH)
        ) as checkpointer:
            checkpointer.setup()
            recovered = repair_jobs.mark_interrupted_jobs()
            publishing_retries = repair_jobs.reset_interrupted_publishing_retries()
            if publishing_retries:
                logging.warning(
                    "Released %s interrupted publishing retries after restart.",
                    publishing_retries,
                )
            if recovered:
                logging.warning(
                    "Marked %s interrupted repair jobs after restart.",
                    recovered,
                )

            app.state.checkpointer = checkpointer
            app.state.graph = build_asea_graph(
                checkpointer=checkpointer
            )
            # Review decisions are committed to SQLite before scheduling. If
            # the process stopped between those steps, resume them on startup.
            for pending_review in repair_jobs.list_pending_reviews():
                try:
                    executor.submit(
                        resume_repair_job,
                        pending_review["job_id"],
                        pending_review["review_decision"],
                        pending_review.get("review_feedback") or "",
                    )
                except RuntimeError:
                    repair_jobs.release_review_claim(pending_review["job_id"])
                    logging.exception(
                        "Could not recover pending patch review %s.",
                        pending_review["job_id"],
                    )

            print("ASEA SQLite checkpoint database initialized.")
            print("ASEA LangGraph workflow compiled successfully.")
            yield
    finally:
        logging.info(
            "ASEA API is shutting down; draining active background work."
        )
        # Keep the SQLite checkpointer open until the running workflow has
        # finished. Cancel queued jobs, but do not abandon active work.
        await asyncio.to_thread(
            executor.shutdown,
            wait=True,
            cancel_futures=True,
        )

executor = ThreadPoolExecutor(max_workers=1)


def _workflow_job_outcome(result: dict[str, object]) -> tuple[str, str, str | None]:
    """Map LangGraph terminal states to truthful API job outcomes."""
    workflow_status = result.get("status")

    if workflow_status == "COMPLETED":
        return (
            "completed",
            "ASEA repair workflow completed successfully.",
            None,
        )

    if workflow_status == "COMPLETED_WITH_REMAINING_FAILURES":
        message = "ASEA finished, but validation found remaining failures."
    else:
        message = (
            "ASEA repair did not complete successfully "
            f"(workflow status: {workflow_status or 'UNKNOWN'})."
        )

    errors = result.get("errors", [])
    details = "; ".join(str(item) for item in errors if item)
    error = details or message
    return "failed", message, error


def _safe_error_detail(error: Exception) -> str:
    """Keep actionable exception details while redacting common credentials."""
    detail = f"{type(error).__name__}: {error}"
    detail = re.sub(
        r"(?i)(gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})",
        "[REDACTED_GITHUB_TOKEN]",
        detail,
    )
    detail = re.sub(
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*",
        r"\1[REDACTED]",
        detail,
    )
    detail = re.sub(
        r"(?i)(sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})",
        "[REDACTED_API_KEY]",
        detail,
    )
    return detail[:1800]


def _publish_repair_result(
    result: dict[str, object],
    job_id: str | None = None,
) -> dict[str, object]:
    """Keep GitHub publishing failures separate from validated repair outcomes."""
    try:
        if job_id is None:
            return publish_validated_repairs(result)
        return publish_validated_repairs(result, job_id=job_id)
    except Exception as error:
        safe_detail = _safe_error_detail(error)
        logging.error(
            "ASEA repair passed validation but publishing failed: %s",
            safe_detail,
        )
        _, separator, safe_message = safe_detail.partition(": ")
        return {
            "status": "PUBLISH_FAILED",
            "error": safe_message if separator else safe_detail,
        }


def run_repair_job(
    job_id: str,
    repository: str,
    publish: bool = False,
):
    """Execute an ASEA repair workflow in the background."""

    graph = None
    config = None
    try:
        repair_jobs.update_job(
            job_id,
            status="running",
            message="Resolving repository and starting repair.",
        )

        repo_path = resolve_repository(repository)

        initial_state = create_initial_state(
            str(repo_path),
            review_mode="api",
        )

        config = {
            "configurable": {
                "thread_id": job_id,
            },
            "recursion_limit": 1000,
        }

        graph = app.state.graph

        result = graph.invoke(
            initial_state,
            config=config,
        )

        snapshot = graph.get_state(config)

        if snapshot.interrupts:
            review_request = snapshot.interrupts[0].value

            repair_jobs.update_job(
                job_id,
                status="waiting_for_review",
                message="ASEA is waiting for patch approval.",
                review_request=review_request,
                result={"publish_requested": publish},
            )

        else:
            job_result = {"workflow": result}
            terminal_status, message, workflow_error = _workflow_job_outcome(result)

            if publish and terminal_status == "completed":
                publishing_result = _publish_repair_result(result, job_id)
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
                cleanup_repair_workspace(result.get("repo_path", ""), result)
            repair_jobs.update_job(
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
                    cleanup_repair_workspace(
                        snapshot.values.get("repo_path", ""),
                        snapshot.values,
                    )
            except Exception:
                logging.exception(
                    "Could not clean failed repair workspace. Job ID: %s",
                    job_id,
                )
        logging.exception(
            "ASEA repair job failed. Job ID: %s",
            job_id,
        )

        repair_jobs.update_job(
            job_id,
            status="failed",
            message="ASEA repair workflow failed.",
            error=_safe_error_detail(error),
        )
def retry_repair_publishing(job_id: str):
    """Retry GitHub publishing from a preserved, validated repair result."""
    try:
        job = repair_jobs.get_job(job_id)
        if job is None:
            repair_jobs.release_publishing_retry(job_id)
            return
        result = (job.get("result") or {}).get("workflow")
        if not isinstance(result, dict):
            repair_jobs.release_publishing_retry(job_id)
            return

        publishing_result = _publish_repair_result(result, job_id)
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
            repair_jobs.update_job(
                job_id,
                message=message,
                result=job_result,
                error=None,
                publishing_retry=False,
            )
            try:
                cleanup_repair_workspace(result.get("repo_path", ""), result)
            except Exception:
                logging.exception(
                    "Publishing succeeded but workspace cleanup failed. Job ID: %s",
                    job_id,
                )
            return

        repair_jobs.update_job(
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
            "Unexpected error during publishing retry. Job ID: %s",
            job_id,
        )
        try:
            repair_jobs.update_job(
                job_id,
                message="Publishing retry failed unexpectedly; it can be retried.",
                error=_safe_error_detail(error),
                publishing_retry=False,
            )
        except Exception:
            logging.exception(
                "Could not persist publishing retry failure. Job ID: %s",
                job_id,
            )
        finally:
            repair_jobs.release_publishing_retry(job_id)


def resume_repair_job(
    job_id: str,
    decision: str,
    feedback: str,
):
    """Resume a paused ASEA workflow after human patch review."""

    graph = None
    config = None
    try:
        job = repair_jobs.get_job(job_id)
        if job is None:
            raise KeyError(f"Repair job not found: {job_id}")
        publish = bool(
            job.get("publish_requested", False)
        )
        if hasattr(repair_jobs, "acknowledge_review_start"):
            if not repair_jobs.acknowledge_review_start(job_id, decision):
                raise RuntimeError(
                    "Review decision is no longer pending for this repair job."
                )
        graph = app.state.graph

        config = {
            "configurable": {
                "thread_id": job_id,
            },
            "recursion_limit": 1000,
        }

        result = graph.invoke(
            Command(
                resume={
                    "decision": decision,
                    "feedback": feedback,
                }
            ),
            config=config,
        )

        snapshot = graph.get_state(config)

        if snapshot.interrupts:
            review_request = snapshot.interrupts[0].value

            repair_jobs.update_job(
                job_id,
                status="waiting_for_review",
                message="ASEA generated another patch requiring approval.",
                review_request=review_request,
                result={"publish_requested": publish},
            )

        else:
            job_result = {"workflow": result}
            terminal_status, message, workflow_error = _workflow_job_outcome(result)
            if publish and terminal_status == "completed":
                publishing_result = _publish_repair_result(result, job_id)
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
                cleanup_repair_workspace(result.get("repo_path", ""), result)
            repair_jobs.update_job(
                job_id,
                status=terminal_status,
                message=message,
                result=job_result,
                error=workflow_error,
            )

    except Exception as error:
        if config is not None:
            try:
                snapshot = graph.get_state(config)
                if snapshot.values:
                    cleanup_repair_workspace(
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
        repair_jobs.update_job(
            job_id,
            status="failed",
            message="Failed to resume ASEA repair workflow.",
            error=_safe_error_detail(error),
        )
app = FastAPI(
    title="ASEA API",
    description="Autonomous Software Engineering Agent REST API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(RequestProtectionMiddleware)
def configured_cors_origins() -> list[str]:
    """Read explicit browser origins; retain local defaults for development."""
    raw_origins = os.getenv("ASEA_CORS_ORIGINS", "")
    if raw_origins.strip():
        origins = [origin.strip() for origin in raw_origins.split(",")]
        if not origins or any(not origin for origin in origins):
            raise RuntimeError(
                "ASEA_CORS_ORIGINS must contain comma-separated http(s) origins."
            )

        for origin in origins:
            parsed = urlsplit(origin)
            try:
                port = parsed.port
            except ValueError as error:
                raise RuntimeError(
                    "ASEA_CORS_ORIGINS contains an invalid origin."
                ) from error

            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or port == 0
                or origin == "*"
            ):
                raise RuntimeError(
                    "ASEA_CORS_ORIGINS must contain exact http(s) origins "
                    "without paths, credentials, queries, or wildcards."
                )

        return [origin.rstrip("/") for origin in origins]
    if os.getenv("ASEA_ENV", "development").strip().lower() == "production":
        return []
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def add_request_correlation(request: Request, call_next):
    """Attach a validated request ID to logs and every API response."""
    supplied = request.headers.get("x-request-id", "")
    request_id = (
        supplied
        if len(supplied) <= 128 and re.fullmatch(r"[A-Za-z0-9._-]+", supplied)
        else str(uuid4())
    )
    request.state.request_id = request_id
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - started_at) * 1000
        logging.exception(
            "API request failed. method=%s path=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            duration_ms,
            request_id,
        )
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    log_method = logging.warning if response.status_code >= 500 else logging.info
    log_method(
        "API request completed. method=%s path=%s status_code=%s "
        "duration_ms=%.2f request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, error: Exception):
    """Return a stable public error while retaining diagnostic server logs."""
    request_id = getattr(request.state, "request_id", "unavailable")
    logging.exception(
        "Unhandled API exception. Request ID: %s",
        request_id,
        exc_info=(type(error), error, error.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal server error occurred.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
    )


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """Protect API operations when an API key is configured."""
    configured_key = os.getenv("ASEA_API_KEY")
    api_key = configured_key.strip() if configured_key else ""
    if api_key and request.url.path != "/health" and request.method != "OPTIONS":
        if not is_valid_api_key(
            request.headers.get("authorization"),
            api_key,
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "A valid Bearer API key is required."},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repository: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Local repository path or HTTPS GitHub repository URL.",
    )


class RepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repository: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Local repository path or HTTPS GitHub repository URL.",
    )
    publish: bool = Field(
        default=False,
        description=(
            "Create a GitHub pull request after all repairs are approved "
            "and validated."
        ),
    )
class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approved", "rejected"]
    feedback: str = Field(
        default="",
        max_length=2000,
        description="Optional feedback explaining the review decision.",
    )   
@app.post("/repair", status_code=202)
def start_repair(request: RepairRequest):
    """Start an ASEA repair job in the background."""

    repository = request.repository.strip()

    if not repository:
        raise HTTPException(
            status_code=400,
            detail="Repository cannot be empty.",
        )

    # Validate repository URLs before creating background jobs.
    if "://" in repository:
        try:
            manager = RepositoryManager()
            manager.validate_github_url(repository)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=str(error),
            ) from error

    job_id = str(uuid4())

    try:
        job = repair_jobs.create_job(
            job_id=job_id,
            repository=repository,
            publish_requested=request.publish,
        )
    except JobQueueFullError as error:
        raise HTTPException(
            status_code=503,
            detail="ASEA is at capacity. Please retry later.",
            headers={"Retry-After": "30"},
        ) from error

    try:
        executor.submit(
            run_repair_job,
            job_id,
            repository,
            request.publish,
        )
    except RuntimeError as error:
        logging.exception(
            "ASEA could not schedule repair job %s.",
            job_id,
        )
        repair_jobs.update_job(
            job_id,
            status="failed",
            message="ASEA could not schedule the repair job.",
            error="The repair could not be scheduled. Please retry.",
        )
        raise HTTPException(
            status_code=503,
            detail="ASEA is temporarily unable to schedule this repair. Please retry.",
        ) from error

    return {
        "message": "Repair job submitted successfully.",
        "job_id": job_id,
        "status": job["status"],
    }

@app.get("/repairs")
def list_repair_jobs(
    limit: int = 50,
    offset: int = 0,
    status: Literal[
        "queued",
        "running",
        "waiting_for_review",
        "completed",
        "failed",
    ] | None = None,
):
    """List persisted repair jobs, newest first."""
    if not 1 <= limit <= 100:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 100.",
        )
    if offset < 0:
        raise HTTPException(
            status_code=400,
            detail="offset cannot be negative.",
        )
    if status is not None and status not in {
        "queued",
        "running",
        "waiting_for_review",
        "completed",
        "failed",
    }:
        raise HTTPException(
            status_code=400,
            detail="Unsupported repair job status.",
        )
    return repair_jobs.list_jobs(
        limit=limit,
        offset=offset,
        status=status,
    )


@app.post("/repair/{job_id}/publish", status_code=202)
def retry_repair_publish(job_id: str):
    """Retry publishing a validated repair without rerunning its workflow."""
    job = repair_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Repair job not found.")
    if not repair_jobs.claim_publishing_retry(job_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "This job is not eligible for a publishing retry, "
                "or a retry is already in progress."
            ),
        )
    try:
        executor.submit(retry_repair_publishing, job_id)
    except RuntimeError as error:
        repair_jobs.release_publishing_retry(job_id)
        raise HTTPException(
            status_code=503,
            detail="ASEA is temporarily unable to schedule publishing. Please retry.",
        ) from error
    return {
        "message": "Publishing retry submitted.",
        "job_id": job_id,
        "status": "publishing_retry_queued",
    }


@app.get("/repair/{job_id}")
def get_repair_status(job_id: str):
    """Retrieve the current status of a repair job."""

    job = repair_jobs.get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Repair job not found.",
        )

    return job        

@app.post("/repair/{job_id}/review", status_code=202)
def review_repair_patch(
    job_id: str,
    request: ReviewRequest,
):
    """Approve or reject a patch and resume the ASEA workflow."""

    job = repair_jobs.get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Repair job not found.",
        )

    if job["status"] != "waiting_for_review":
        raise HTTPException(
            status_code=409,
            detail="This repair job is not waiting for patch review.",
        )

    if request.decision == "rejected" and not request.feedback.strip():
        raise HTTPException(
            status_code=400,
            detail="Feedback is required when rejecting a patch.",
        )

    # Claim the review transition atomically before scheduling work.
    # This closes the race where duplicate requests both observe the
    # waiting_for_review state and enqueue concurrent graph resumes.
    if not repair_jobs.claim_review(
        job_id,
        decision=request.decision,
        feedback=request.feedback.strip(),
    ):
        raise HTTPException(
            status_code=409,
            detail="This repair job review has already been submitted.",
        )

    try:
        executor.submit(
            resume_repair_job,
            job_id,
            request.decision,
            request.feedback.strip(),
        )
    except RuntimeError as error:
        repair_jobs.release_review_claim(job_id)
        raise HTTPException(
            status_code=503,
            detail="ASEA is temporarily unable to schedule this review. Please retry.",
        ) from error

    return {
        "message": "Patch review decision submitted successfully.",
        "job_id": job_id,
        "decision": request.decision,
    }

@app.get("/")
def root():
    return {
        "message": "Welcome to ASEA API",
        "version": "0.1.0",
        "status": "running",
    }


@app.get("/health")
def health_check():
    """Liveness probe: confirms the API process can serve requests."""
    return {
        "status": "healthy",
        "service": "ASEA API",
    }


@app.get("/ready")
def readiness_check():
    """Readiness probe: verifies initialized workflow and job storage."""
    try:
        if not hasattr(app.state, "graph"):
            raise RuntimeError("Workflow graph is not initialized.")
        repair_jobs.check_database()
    except Exception as error:
        logging.warning("ASEA readiness check failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="ASEA is not ready to process requests.",
        ) from error
    return {"status": "ready", "service": "ASEA API"}


@app.post("/analyze")
def analyze_repository(request: AnalyzeRequest):
    try:
        repository = request.repository.strip()

        analyzer = RepositoryAnalyzer()

        if repository.startswith("https://"):
            manager = RepositoryManager()
            repo_path = manager.clone_repository(repository)
            result = analyzer.analyze_cloned_repository(repo_path)
        else:
            result = analyzer.analyze_local_repository(repository)

        return {
            "status": "success",
            "message": "Repository analysis completed successfully.",
            "analysis": result,
        }

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Repository analysis failed.",
        ) from error


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "asea.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )