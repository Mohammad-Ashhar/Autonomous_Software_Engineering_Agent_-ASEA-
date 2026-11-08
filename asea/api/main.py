import logging
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
from asea.tools.analyzer import RepositoryAnalyzer
from asea.tools.repository import RepositoryManager

CHECKPOINT_DB_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "asea_checkpoints.db"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
            )

        else:
            analysis_status = result.get("analysis_status")
            job_status = "failed" if analysis_status in {"failed", "unsupported"} else "completed"
            message = (
                "ASEA has no supported source files in this repository yet."
                if analysis_status == "unsupported"
                else "ASEA analysis could not complete; no repair was attempted."
                if analysis_status == "failed"
                else "ASEA completed with analysis warnings."
                if analysis_status == "partial"
                else "ASEA repair workflow completed."
            )
            repair_jobs.update_job(
                job_id,
                status=job_status,
                message=message,
                result=result,
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
            message="Processing patch review decision.",
            review_request=None,
        )

        config = {
            "configurable": {
                "thread_id": job_id,
            },
            "recursion_limit": 1000,
        }

        result = app.state.graph.invoke(
            Command(
                resume={
                    "decision": decision,
                    "feedback": feedback,
                }
            ),
            config=config,
        )

        snapshot = app.state.graph.get_state(config)

        if snapshot.interrupts:
            review_request = snapshot.interrupts[0].value

            repair_jobs.update_job(
                job_id,
                status="waiting_for_review",
                message="ASEA generated another patch requiring approval.",
                review_request=review_request,
            )

        else:
            analysis_status = result.get("analysis_status")
            job_status = (
                "failed"
                if analysis_status in {"failed", "unsupported"}
                else "completed"
            )
            message = (
                "ASEA has no supported source files in this repository yet."
                if analysis_status == "unsupported"
                else "ASEA analysis could not complete; no repair was attempted."
                if analysis_status == "failed"
                else "ASEA completed with analysis warnings."
                if analysis_status == "partial"
                else "ASEA repair workflow completed."
            )
            repair_jobs.update_job(
                job_id,
                status=job_status,
                message=message,
                result=result,
            )

    except Exception:
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