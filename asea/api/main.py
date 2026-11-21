import asyncio
import logging
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, ConfigDict, Field

from asea.api.cloud_workers import CloudWorkerManager, WorkerProtocolError
from asea.api.execution import RepairExecutionService
from asea.api.job_manager import JobQueueFullError
from asea.api.job_storage import repair_jobs
from asea.api.protection import RequestProtectionMiddleware
from asea.api.supabase_auth import (
    SupabaseAuthError,
    supabase_auth_configuration,
    verify_supabase_access_token,
)
from asea.api.workspace_cleanup import cleanup_repair_workspace
from asea.cli import resolve_repository
from asea.core.graph import (
    build_asea_graph,
    create_initial_state,
)
from asea.integrations.repair_publisher import publish_validated_repairs
from asea.tools.analyzer import RepositoryAnalyzer
from asea.tools.repository import RepositoryManager

load_dotenv()

CHECKPOINT_DB_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "asea_checkpoints.db"
)


def validate_api_security_configuration(
    environment: str,
    api_key: str | None,
) -> None:
    """Require Supabase identity verification before production startup."""
    if environment.strip().lower() != "production":
        return
    try:
        supabase_auth_configuration()
    except SupabaseAuthError as error:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be configured "
            "when ASEA_ENV=production."
        ) from error


def is_valid_api_key(authorization: str | None, expected_key: str) -> bool:
    """Validate a Bearer API key without leaking timing information."""
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
    CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # A fresh executor is required for every application lifespan.
    executor = ThreadPoolExecutor(max_workers=1)
    worker_mode = (
        os.getenv("ASEA_EXECUTION_MODE", "api").strip().lower() == "worker"
    )
    cloud_storage = bool(os.getenv("ASEA_DATABASE_URL", "").strip())
    if worker_mode and cloud_storage:
        raise RuntimeError(
            "The local SQLite worker cannot use ASEA_DATABASE_URL. "
            "Cloud workers use the HTTPS worker protocol."
        )

    try:
        checkpointer_context = (
            nullcontext(None)
            if worker_mode or cloud_storage
            else SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH))
        )
        with checkpointer_context as checkpointer:
            if checkpointer is not None:
                checkpointer.setup()
            if cloud_storage:
                repair_jobs.check_database()
            recovered = (
                0
                if worker_mode or cloud_storage
                else repair_jobs.mark_interrupted_jobs()
            )
            publishing_retries = (
                0
                if worker_mode or cloud_storage
                else repair_jobs.reset_interrupted_publishing_retries()
            )
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
            app.state.graph = (
                build_asea_graph(checkpointer=checkpointer)
                if checkpointer is not None
                else None
            )
            if not worker_mode and not cloud_storage:
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
        await asyncio.to_thread(
            executor.shutdown,
            wait=True,
            cancel_futures=True,
        )


_cloud_worker_manager = None


def _cloud_workers() -> CloudWorkerManager:
    global _cloud_worker_manager
    if _cloud_worker_manager is None:
        _cloud_worker_manager = CloudWorkerManager()
    return _cloud_worker_manager


def _worker_identity(request: Request) -> dict:
    authorization = request.headers.get("authorization", "")
    scheme, separator, secret = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401, detail="Worker bearer credential required."
        )
    identity = _cloud_workers().authenticate(secret.strip())
    if identity is None:
        raise HTTPException(
            status_code=401, detail="Worker credential is invalid or revoked."
        )
    return identity


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


def _request_owner_id(request: Request | None) -> str | None:
    """Return the identity attached by authentication middleware, if present."""
    return getattr(getattr(request, "state", None), "user_id", None)


def _get_request_job(job_id: str, owner_id: str | None):
    """Keep direct local/test callers compatible while enforcing cloud ownership."""
    if owner_id is None:
        return repair_jobs.get_job(job_id)
    return repair_jobs.get_job(job_id, owner_id=owner_id)


def _list_request_jobs(
    limit: int,
    offset: int,
    status: str | None,
    owner_id: str | None,
):
    if owner_id is None:
        return repair_jobs.list_jobs(limit=limit, offset=offset, status=status)
    return repair_jobs.list_jobs(
        limit=limit, offset=offset, status=status, owner_id=owner_id
    )


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


def _repair_execution_service() -> RepairExecutionService:
    """Build the reusable workflow service from the API's current adapters."""
    return RepairExecutionService(
        repair_jobs=repair_jobs,
        graph_provider=lambda: app.state.graph,
        resolve_repository=resolve_repository,
        create_initial_state=create_initial_state,
        publish_repair_result=_publish_repair_result,
        cleanup_repair_workspace=cleanup_repair_workspace,
        safe_error_detail=_safe_error_detail,
        workflow_job_outcome=_workflow_job_outcome,
    )


def run_repair_job(
    job_id: str,
    repository: str,
    publish: bool = False,
) -> None:
    """Compatibility adapter used by the API executor and existing callers."""
    _repair_execution_service().run_repair_job(job_id, repository, publish)


def retry_repair_publishing(job_id: str) -> None:
    """Compatibility adapter for a publishing retry."""
    _repair_execution_service().retry_repair_publishing(job_id)


def resume_repair_job(
    job_id: str,
    decision: str,
    feedback: str,
) -> None:
    """Compatibility adapter for a human-reviewed workflow resume."""
    _repair_execution_service().resume_repair_job(job_id, decision, feedback)


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
async def require_api_identity(request: Request, call_next):
    """Authenticate API requests and attach a verified owner identity."""
    if (
        request.url.path in {"/health", "/ready"}
        or request.url.path.startswith("/v1/worker/")
        or request.method == "OPTIONS"
    ):
        return await call_next(request)
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_ANON_KEY", "").strip()
    if supabase_url or supabase_key:
        authorization = request.headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            return JSONResponse(
                status_code=401,
                content={"detail": "A valid Supabase Bearer access token is required."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            request.state.user_id = verify_supabase_access_token(token)
        except SupabaseAuthError as error:
            invalid = "invalid or expired" in str(error).lower()
            return JSONResponse(
                status_code=401 if invalid else 503,
                content={"detail": str(error)},
                headers={"Cache-Control": "no-store"},
            )
        return await call_next(request)
    configured_key = os.getenv("ASEA_API_KEY")
    api_key = configured_key.strip() if configured_key else ""
    if api_key and not is_valid_api_key(request.headers.get("authorization"), api_key):
        return JSONResponse(
            status_code=401,
            content={"detail": "A valid Bearer API key is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.user_id = None
    return await call_next(request)


class WorkerEnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., min_length=20, max_length=200)
    version: str | None = Field(default=None, max_length=80)
    capabilities: dict = Field(default_factory=dict)


class WorkerHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str | None = Field(default=None, max_length=80)
    capabilities: dict = Field(default_factory=dict)


class WorkerLeaseHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: str
    fencing_generation: int = Field(..., ge=1)


class WorkerEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: str
    fencing_generation: int = Field(..., ge=1)
    event_type: str = Field(..., min_length=1, max_length=80)
    payload: dict = Field(default_factory=dict)
    idempotency_key: str = Field(..., min_length=1, max_length=200)


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
@app.post("/v1/workers/enrollment-code")
def create_worker_enrollment_code(http_request: Request):
    owner_id = _request_owner_id(http_request)
    if owner_id is None:
        raise HTTPException(
            status_code=401, detail="Supabase user authentication required."
        )
    try:
        return _cloud_workers().issue_enrollment_code(owner_id)
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="Cloud worker storage is not configured.",
        ) from error


@app.get("/v1/workers")
def get_cloud_workers(http_request: Request):
    owner_id = _request_owner_id(http_request)
    if owner_id is None:
        raise HTTPException(
            status_code=401, detail="Supabase user authentication required."
        )
    try:
        return {"items": _cloud_workers().list_workers(owner_id)}
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="Cloud worker storage is not configured.",
        ) from error


@app.post("/v1/workers/{worker_id}/revoke")
def revoke_cloud_worker(worker_id: str, http_request: Request):
    owner_id = _request_owner_id(http_request)
    if owner_id is None:
        raise HTTPException(
            status_code=401, detail="Supabase user authentication required."
        )
    try:
        if not _cloud_workers().revoke_worker(owner_id, worker_id):
            raise HTTPException(
                status_code=404, detail="Worker not found or already revoked."
            )
        return {"status": "revoked", "worker_id": worker_id}
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid worker ID.") from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="Cloud worker storage is not configured.",
        ) from error


@app.post("/v1/worker/enroll")
def enroll_cloud_worker(request: WorkerEnrollmentRequest):
    try:
        return _cloud_workers().enroll(
            request.code, request.version, request.capabilities
        )
    except WorkerProtocolError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="Cloud worker storage is not configured.",
        ) from error


@app.post("/v1/worker/heartbeat")
def cloud_worker_heartbeat(request: WorkerHeartbeatRequest, http_request: Request):
    try:
        _cloud_workers().heartbeat(
            _worker_identity(http_request), request.version, request.capabilities
        )
        return {"status": "ok"}
    except WorkerProtocolError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/worker/jobs/claim")
def claim_cloud_worker_job(http_request: Request):
    try:
        job = _cloud_workers().claim_job(_worker_identity(http_request))
        return {"job": job}
    except WorkerProtocolError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/worker/jobs/{job_id}/heartbeat")
def heartbeat_cloud_worker_job(
    job_id: str, request: WorkerLeaseHeartbeatRequest, http_request: Request
):
    try:
        expires_at = _cloud_workers().renew_lease(
            _worker_identity(http_request), job_id, request.lease_id,
            request.fencing_generation,
        )
        return {"lease_expires_at": expires_at}
    except WorkerProtocolError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/worker/jobs/{job_id}/events")
def append_cloud_worker_event(
    job_id: str, request: WorkerEventRequest, http_request: Request
):
    try:
        event_id = _cloud_workers().append_event(
            _worker_identity(http_request), job_id, request.lease_id,
            request.fencing_generation, request.event_type, request.payload,
            request.idempotency_key,
        )
        return {"event_id": event_id}
    except WorkerProtocolError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/repair", status_code=202)
def start_repair(
    request: RepairRequest,
    http_request: Request = None,
):
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
        owner_id = _request_owner_id(http_request)
        job_arguments = {
            "job_id": job_id,
            "repository": repository,
            "publish_requested": request.publish,
        }
        if owner_id is not None:
            job_arguments["owner_id"] = owner_id
        job = repair_jobs.create_job(**job_arguments)
    except JobQueueFullError as error:
        raise HTTPException(
            status_code=503,
            detail="ASEA is at capacity. Please retry later.",
            headers={"Retry-After": "30"},
        ) from error

    try:
        if (
            os.getenv("ASEA_EXECUTION_MODE", "api").strip().lower() != "worker"
            and not os.getenv("ASEA_DATABASE_URL", "").strip()
        ):
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
    http_request: Request = None,
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
    return _list_request_jobs(
        limit, offset, status, _request_owner_id(http_request)
    )


@app.post("/repair/{job_id}/publish", status_code=202)
def retry_repair_publish(
    job_id: str,
    http_request: Request = None,
):
    """Retry publishing a validated repair without rerunning its workflow."""
    if os.getenv("ASEA_DATABASE_URL", "").strip():
        raise HTTPException(
            status_code=409,
            detail=(
                "Cloud publishing retries will be enabled with cloud job "
                "lifecycle support."
            ),
        )
    owner_id = _request_owner_id(http_request)
    job = _get_request_job(job_id, owner_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Repair job not found.")
    claimed = (
        repair_jobs.claim_publishing_retry(job_id)
        if owner_id is None
        else repair_jobs.claim_publishing_retry(job_id, owner_id=owner_id)
    )
    if not claimed:
        raise HTTPException(
            status_code=409,
            detail=(
                "This job is not eligible for a publishing retry, "
                "or a retry is already in progress."
            ),
        )
    try:
        if os.getenv("ASEA_EXECUTION_MODE", "api").strip().lower() != "worker":
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
def get_repair_status(
    job_id: str,
    http_request: Request = None,
):
    """Retrieve the current status of a repair job."""

    job = _get_request_job(job_id, _request_owner_id(http_request))

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
    http_request: Request = None,
):
    """Approve or reject a patch and resume the ASEA workflow."""

    if os.getenv("ASEA_DATABASE_URL", "").strip():
        raise HTTPException(
            status_code=409,
            detail=(
                "Cloud review handoff will be enabled with cloud job "
                "lifecycle support."
            ),
        )
    owner_id = _request_owner_id(http_request)
    job = _get_request_job(job_id, owner_id)

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
    review_arguments = {
        "decision": request.decision,
        "feedback": request.feedback.strip(),
    }
    if owner_id is not None:
        review_arguments["owner_id"] = owner_id
    if not repair_jobs.claim_review(job_id, **review_arguments):
        raise HTTPException(
            status_code=409,
            detail="This repair job review has already been submitted.",
        )

    try:
        if os.getenv("ASEA_EXECUTION_MODE", "api").strip().lower() != "worker":
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
        worker_mode = (
            os.getenv("ASEA_EXECUTION_MODE", "api").strip().lower() == "worker"
        )
        cloud_storage = bool(os.getenv("ASEA_DATABASE_URL", "").strip())
        if (
            not worker_mode
            and not cloud_storage
            and not getattr(app.state, "graph", None)
        ):
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