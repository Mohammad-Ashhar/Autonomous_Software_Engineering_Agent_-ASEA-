import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from asea.api.job_manager import repair_jobs
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


STAGE_LABELS = {
    "repository_analysis": "Analysing repository",
    "bug_detector": "Detecting bugs",
    "test_execution": "Running baseline tests",
    "workspace_preparation": "Preparing workspace",
    "bug_prioritization": "Prioritising bugs",
    "bug_selector": "Selecting next bug",
    "patch_generation": "Generating patch",
    "human_patch_review": "Waiting for patch review",
    "patch_validation": "Validating patch in sandbox",
    "escalation": "Escalating unrepaired bug",
    "final_validation": "Final validation",
}


def summarize_result(values: dict) -> dict:
    """Return a compact, UI-friendly view of the final workflow state.

    The full state can be several MB (test output, repository map...) and
    used to be stored and re-sent on every status poll.
    """

    def slim_tests(tests):
        if not isinstance(tests, dict):
            return tests
        return {k: v for k, v in tests.items() if k != "output"}

    validations = []
    for item in values.get("validation_results", []):
        item = dict(item)
        if "test_results" in item:
            item["test_results"] = slim_tests(item["test_results"])
        validations.append(item)

    return {
        "status": values.get("status"),
        "repo_path": values.get("repo_path"),
        "detected_bug_count": len(values.get("detected_bugs", [])),
        "repaired_bugs": values.get("repaired_bugs", []),
        "escalated_bugs": values.get("escalated_bugs", []),
        "errors": values.get("errors", []),
        "review_history": values.get("review_history", []),
        "audit_log": values.get("audit_log", []),
        "patches": values.get("patches", []),
        "validation_results": validations,
        "test_results": slim_tests(values.get("test_results")),
        "working_test_results": slim_tests(
            values.get("working_test_results")
        ),
    }


def drive_graph(job_id: str, graph_input, config: dict):
    """Run the workflow, recording the live stage and per-stage timings."""

    graph = app.state.graph

    job = repair_jobs.get_job(job_id) or {}
    timings = dict(job.get("timings") or {})

    def mark_stage(label: str):
        repair_jobs.update_job(
            job_id,
            stage=label,
            message=f"{label}...",
            timings=timings,
        )

    last = time.perf_counter()

    for update in graph.stream(
        graph_input,
        config=config,
        stream_mode="updates",
    ):
        now = time.perf_counter()

        for node in update:
            if node.startswith("__"):
                continue
            timings[node] = round(timings.get(node, 0.0) + now - last, 2)

        last = now

        upcoming = graph.get_state(config).next

        if upcoming:
            mark_stage(STAGE_LABELS.get(upcoming[0], upcoming[0]))


def finish_job(job_id: str, config: dict, interrupted_message: str):
    """Move the job to review / completed after the graph stops."""

    graph = app.state.graph
    snapshot = graph.get_state(config)

    if snapshot.interrupts:
        repair_jobs.update_job(
            job_id,
            status="waiting_for_review",
            stage="Waiting for patch review",
            message=interrupted_message,
            review_request=snapshot.interrupts[0].value,
        )
        return

    values = dict(snapshot.values)
    job = repair_jobs.get_job(job_id) or {}

    publication = None

    if job.get("publish"):
        repair_jobs.update_job(
            job_id,
            stage="Publishing pull request",
            message="Publishing approved repairs to GitHub...",
        )
        publication = publish_repairs(values)

    message = "ASEA repair workflow completed."

    if publication and publication.get("status") == "PULL_REQUEST_CREATED":
        message += " Pull request created."
    elif publication and publication.get("status") == "FAILED":
        message += " Publishing failed — see the publication details."

    repair_jobs.update_job(
        job_id,
        status="completed",
        message=message,
        result=summarize_result(values),
        publication=publication,
    )


def publish_repairs(values: dict) -> dict:
    """Open a pull request with the approved repairs, never raising."""

    if not values.get("repaired_bugs"):
        return {
            "status": "SKIPPED",
            "reason": "No approved and validated repairs to publish.",
        }

    try:
        result = publish_validated_repairs(values)

        return {
            "status": result["status"],
            "repository": result.get("repository"),
            "branch": result.get("branch"),
            "base_branch": result.get("base_branch"),
            "commit": result.get("commit"),
            "files": result.get("files", []),
            "pushed_to_fork": result.get("pushed_to_fork", False),
            "pull_request": result.get("pull_request"),
        }

    except Exception as error:  # noqa: BLE001
        logging.exception("ASEA publishing failed.")

        return {
            "status": "FAILED",
            "reason": str(error),
        }


@asynccontextmanager
async def lifespan(app: FastAPI):
    interrupted = repair_jobs.fail_interrupted_jobs()

    if interrupted:
        print(f"ASEA marked {interrupted} interrupted job(s) as failed.")

    CHECKPOINT_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with SqliteSaver.from_conn_string(
        str(CHECKPOINT_DB_PATH)
    ) as checkpointer:
        checkpointer.setup()

        app.state.checkpointer = checkpointer
        app.state.graph = build_asea_graph(
            checkpointer=checkpointer
        )

        print("ASEA SQLite checkpoint database initialized.")
        print("ASEA LangGraph workflow compiled successfully.")

        yield

executor = ThreadPoolExecutor(max_workers=1)


def run_repair_job(job_id: str, repository: str):
    """Execute an ASEA repair workflow in the background."""

    try:
        repair_jobs.update_job(
            job_id,
            status="running",
            stage="Resolving repository",
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

        drive_graph(job_id, initial_state, config)

        finish_job(
            job_id,
            config,
            "ASEA is waiting for patch approval.",
        )

    except Exception:
        logging.exception(
            "ASEA repair job failed. Job ID: %s",
            job_id,
        )

        repair_jobs.update_job(
            job_id,
            status="failed",
            message="ASEA repair workflow failed.",
            error="An internal error occurred during repair.",
        )
def resume_repair_job(
    job_id: str,
    decision: str,
    feedback: str,
):
    """Resume a paused ASEA workflow after human patch review."""

    try:
        repair_jobs.update_job(
            job_id,
            status="running",
            stage="Processing review decision",
            message="Processing patch review decision.",
            review_request=None,
        )

        config = {
            "configurable": {
                "thread_id": job_id,
            },
            "recursion_limit": 1000,
        }

        drive_graph(
            job_id,
            Command(
                resume={
                    "decision": decision,
                    "feedback": feedback,
                }
            ),
            config,
        )

        finish_job(
            job_id,
            config,
            "ASEA generated another patch requiring approval.",
        )

    except Exception:
        logging.exception(
            "ASEA review resume failed. Job ID: %s",
            job_id,
        )

        repair_jobs.update_job(
            job_id,
            status="failed",
            message="Failed to resume ASEA repair workflow.",
            error="An internal error occurred while processing the review.",
        )        
app = FastAPI(
    title="ASEA API",
    description="Autonomous Software Engineering Agent REST API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        *[
            origin.strip()
            for origin in os.getenv("ASEA_CORS_ORIGINS", "").split(",")
            if origin.strip()
        ],
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    repository: str = Field(
        ...,
        min_length=1,
        description="Local repository path or HTTPS GitHub repository URL.",
    )
class RepairRequest(BaseModel):
    repository: str = Field(
        ...,
        min_length=1,
        description="Local repository path or HTTPS GitHub repository URL.",
    )
    publish: bool = Field(
        default=True,
        description=(
            "Open a pull request with the approved repairs when the "
            "workflow finishes."
        ),
    )
class ReviewRequest(BaseModel):
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

    job = repair_jobs.create_job(
        job_id=job_id,
        repository=repository,
        publish=request.publish,
    )

    executor.submit(
        run_repair_job,
        job_id,
        repository,
    )

    return {
        "message": "Repair job submitted successfully.",
        "job_id": job_id,
        "status": job["status"],
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

    executor.submit(
        resume_repair_job,
        job_id,
        request.decision,
        request.feedback.strip(),
    )

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
    return {
        "status": "healthy",
        "service": "ASEA API",
    }


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