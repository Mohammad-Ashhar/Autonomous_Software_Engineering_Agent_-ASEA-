"""Outbound HTTPS runtime for a Windows ASEA cloud worker.

The worker keeps repository execution, checkpoints, Ollama, Docker, and GitHub
credentials on the user's machine. Only job metadata and bounded results cross
the cloud API boundary.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import platform
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver

from asea.api.execution import RepairExecutionService
from asea.api.job_manager import RepairJobManager
from asea.api.main import (
    CHECKPOINT_DB_PATH,
    _publish_repair_result,
    _safe_error_detail,
    _workflow_job_outcome,
)
from asea.api.workspace_cleanup import cleanup_repair_workspace
from asea.cli import resolve_repository
from asea.core.graph import build_asea_graph, create_initial_state

_LOG = logging.getLogger(__name__)
_KEYRING_SERVICE = "ASEA Cloud Worker"


class CloudWorkerAPIError(RuntimeError):
    """An HTTP or protocol error returned by the cloud API."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


class CloudWorkerClient:
    """Small standard-library HTTPS client for the worker-only API."""

    def __init__(self, base_url: str, worker_secret: str, timeout: float = 20):
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "https"
            and parsed.hostname not in {"localhost", "127.0.0.1"}
        ):
            raise ValueError("Cloud worker API URL must use HTTPS.")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Cloud worker API URL is invalid.")
        if not worker_secret:
            raise ValueError("Worker credential is required.")
        self.base_url = base_url.rstrip("/")
        self.worker_secret = worker_secret
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.worker_secret}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Request-ID": str(uuid4()),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1_100_000)
        except urllib.error.HTTPError as error:
            raw = error.read(16_384)
            try:
                detail = json.loads(raw.decode("utf-8")).get("detail", "Cloud API request failed.")
            except (ValueError, AttributeError):
                detail = "Cloud API request failed."
            raise CloudWorkerAPIError(error.code, str(detail)) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise CloudWorkerAPIError(0, "Cloud API is temporarily unreachable.") from error

        if len(raw) > 1_048_576:
            raise CloudWorkerAPIError(502, "Cloud API response exceeded the size limit.")
        try:
            result = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError) as error:
            raise CloudWorkerAPIError(502, "Cloud API returned invalid JSON.") from error
        if not isinstance(result, dict):
            raise CloudWorkerAPIError(502, "Cloud API returned an invalid response.")
        return result

    def enroll(self, code: str, version: str, capabilities: dict) -> dict:
        return self.request(
            "POST",
            "/v1/worker/enroll",
            {"code": code, "version": version, "capabilities": capabilities},
        )

    def heartbeat(self, version: str, capabilities: dict) -> None:
        self.request(
            "POST",
            "/v1/worker/heartbeat",
            {"version": version, "capabilities": capabilities},
        )

    def claim(self) -> dict | None:
        result = self.request("POST", "/v1/worker/jobs/claim", {})
        job = result.get("job")
        if job is not None and not isinstance(job, dict):
            raise CloudWorkerAPIError(502, "Cloud API returned an invalid job.")
        return job

    def renew(self, job: dict) -> None:
        self.request(
            "POST",
            f"/v1/worker/jobs/{job['job_id']}/heartbeat",
            {
                "lease_id": job["lease_id"],
                "fencing_generation": job["fencing_generation"],
            },
        )

    def event(self, job: dict, event_type: str, payload: dict) -> None:
        self.request(
            "POST",
            f"/v1/worker/jobs/{job['job_id']}/events",
            {
                "lease_id": job["lease_id"],
                "fencing_generation": job["fencing_generation"],
                "event_type": event_type,
                "payload": payload,
                "idempotency_key": str(uuid4()),
            },
        )

    def complete(self, job: dict, result: dict) -> dict:
        return self.request(
            "POST",
            f"/v1/worker/jobs/{job['job_id']}/complete",
            {
                "lease_id": job["lease_id"],
                "fencing_generation": job["fencing_generation"],
                "result": result,
                "idempotency_key": str(uuid4()),
            },
        )

    def fail(self, job: dict, error: str) -> dict:
        return self.request(
            "POST",
            f"/v1/worker/jobs/{job['job_id']}/fail",
            {
                "lease_id": job["lease_id"],
                "fencing_generation": job["fencing_generation"],
                "error": error[:1800],
                "idempotency_key": str(uuid4()),
            },
        )


def _credential_store():
    """Load keyring lazily so importing the local worker needs no cloud extras."""
    try:
        import keyring
    except ImportError as error:
        raise RuntimeError(
            "Install the cloud worker extra with: pip install -e '.[cloud-worker]'"
        ) from error
    return keyring


def enroll_worker(api_url: str, code: str) -> str:
    """Exchange a one-time enrollment code and save the secret in OS keyring."""
    if not code or len(code) > 200:
        raise ValueError("A valid one-time enrollment code is required.")
    client = CloudWorkerClient(api_url, "enrollment-does-not-use-bearer")
    enrolled = client.enroll(
        code,
        version="0.1.0",
        capabilities={
            "os": os.name,
            "platform": platform.platform(),
            "execution": ["local-repository", "ollama", "docker", "github"],
        },
    )
    worker_id = str(enrolled["worker_id"])
    secret = str(enrolled["worker_secret"])
    if not worker_id or not secret:
        raise RuntimeError("Cloud API returned incomplete worker credentials.")
    _credential_store().set_password(_KEYRING_SERVICE, worker_id, secret)
    _LOG.info("Worker %s enrolled. Credential saved in the OS keyring.", worker_id)
    return worker_id


def _worker_secret(worker_id: str | None = None) -> tuple[str, str]:
    keyring = _credential_store()
    selected_id = worker_id or os.getenv("ASEA_CLOUD_WORKER_ID", "").strip()
    if not selected_id:
        raise RuntimeError(
            "Set ASEA_CLOUD_WORKER_ID to the ID returned by the enroll command."
        )
    secret = keyring.get_password(_KEYRING_SERVICE, selected_id)
    if not secret:
        raise RuntimeError("No saved credential found for this worker ID.")
    return selected_id, secret


class CloudWorkerRuntime:
    """Claim and execute cloud jobs locally, reporting fenced outcomes."""

    def __init__(
        self,
        client: CloudWorkerClient,
        poll_seconds: float = 5,
        heartbeat_seconds: float = 20,
    ):
        if poll_seconds < 1 or heartbeat_seconds < 5:
            raise ValueError("Polling must be >=1s and heartbeat must be >=5s.")
        self.client = client
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.stop_event = threading.Event()
        self.local_jobs = RepairJobManager()

    def stop(self) -> None:
        self.stop_event.set()

    def _capabilities(self) -> dict:
        return {
            "os": os.name,
            "platform": __import__("platform").platform(),
            "execution": ["local-repository", "ollama", "docker", "github"],
        }

    def _lease_heartbeat(
        self, job: dict, lost: threading.Event, finished: threading.Event
    ) -> None:
        while not finished.wait(self.heartbeat_seconds):
            try:
                self.client.renew(job)
                self.client.heartbeat("0.1.0", self._capabilities())
            except CloudWorkerAPIError as error:
                _LOG.error("Cloud lease heartbeat failed: %s", error)
                lost.set()
                return

    def _execute(self, job: dict) -> None:
        required = ("job_id", "repository", "lease_id", "fencing_generation")
        if any(not job.get(field) for field in required):
            raise ValueError("Claimed job is missing required execution metadata.")
        job_id = str(job["job_id"])
        if self.local_jobs.get_job(job_id) is not None:
            raise RuntimeError(
                "A local record already exists for this cloud job. "
                "Automatic checkpoint recovery is not enabled."
            )

        self.local_jobs.create_job(
            job_id=job_id,
            repository=job["repository"],
            publish_requested=bool(job.get("publish_requested", False)),
        )
        lost = threading.Event()
        finished = threading.Event()
        heartbeat = threading.Thread(
            target=self._lease_heartbeat,
            args=(job, lost, finished),
            name=f"asea-lease-{job_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            self.client.event(
                job, "job.started", {"message": "Local execution started."}
            )
            with SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as checkpointer:
                checkpointer.setup()
                graph = build_asea_graph(checkpointer=checkpointer)

                def guarded_publish(result: dict, current_job_id: str):
                    if lost.is_set():
                        raise RuntimeError(
                            "Cloud lease was lost; GitHub publishing was blocked."
                        )
                    return _publish_repair_result(result, current_job_id)

                service = RepairExecutionService(
                    repair_jobs=self.local_jobs,
                    graph_provider=lambda: graph,
                    resolve_repository=resolve_repository,
                    create_initial_state=create_initial_state,
                    publish_repair_result=guarded_publish,
                    cleanup_repair_workspace=cleanup_repair_workspace,
                    safe_error_detail=_safe_error_detail,
                    workflow_job_outcome=_workflow_job_outcome,
                )
                service.run_repair_job(
                    job_id,
                    job["repository"],
                    bool(job.get("publish_requested", False)),
                )
            if lost.is_set():
                _LOG.warning("Lease lost for job %s; refusing cloud finalization.", job_id)
                return

            local_job = self.local_jobs.get_job(job_id)
            if local_job is None:
                raise RuntimeError("Local execution did not persist a job result.")
            if local_job["status"] == "waiting_for_review":
                self.client.fail(
                    job,
                    "Repair reached a human patch-review checkpoint. "
                    "Cloud review pause/resume is not implemented yet; "
                    "no patch was approved or published. Submit a new job after "
                    "the cloud review workflow is available.",
                )
                return
            if local_job["status"] == "completed":
                result = local_job.get("result") or {}
                workflow = result.get("workflow")
                if not isinstance(workflow, dict) or workflow.get("status") != "COMPLETED":
                    self.client.fail(
                        job,
                        "Local execution did not produce a successful terminal workflow result.",
                    )
                    return
                cloud_result = {"status": "COMPLETED", "workflow": workflow}
                if result.get("publishing") is not None:
                    cloud_result["publishing"] = result["publishing"]
                self.client.complete(job, cloud_result)
                return
            self.client.fail(
                job,
                str(local_job.get("error") or local_job.get("message") or "Repair failed."),
            )
        finally:
            finished.set()
            heartbeat.join(timeout=2)

    def run(self) -> None:
        CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        while not self.stop_event.is_set():
            try:
                self.client.heartbeat("0.1.0", self._capabilities())
                job = self.client.claim()
                if job is None:
                    self.stop_event.wait(self.poll_seconds)
                    continue
                self._execute(job)
            except CloudWorkerAPIError as error:
                if error.status in {401, 403}:
                    _LOG.error("Worker authentication failed; stopping: %s", error)
                    return
                _LOG.warning("Cloud worker request failed: %s", error)
                self.stop_event.wait(self.poll_seconds)
            except Exception:
                _LOG.exception("Cloud worker could not process a claimed job.")
                self.stop_event.wait(self.poll_seconds)


def run() -> None:
    """Command entry point: enroll once, then run the cloud worker."""
    parser = argparse.ArgumentParser(prog="asea-cloud-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    enroll_parser = subparsers.add_parser("enroll")
    enroll_parser.add_argument("--code", required=True)
    enroll_parser.add_argument("--api-url", default=os.getenv("ASEA_CLOUD_API_URL", ""))
    worker_parser = subparsers.add_parser("run")
    worker_parser.add_argument("--worker-id", default=None)
    worker_parser.add_argument("--api-url", default=os.getenv("ASEA_CLOUD_API_URL", ""))
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("ASEA_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.api_url:
        parser.error("Set ASEA_CLOUD_API_URL or pass --api-url.")
    if args.command == "enroll":
        worker_id = enroll_worker(args.api_url, args.code)
        print(f"Worker enrolled. Worker ID: {worker_id}")
        return

    worker_id, secret = _worker_secret(args.worker_id)
    _LOG.info("Starting ASEA cloud worker %s.", worker_id)
    client = CloudWorkerClient(args.api_url, secret)
    runtime = CloudWorkerRuntime(
        client,
        poll_seconds=float(os.getenv("ASEA_CLOUD_WORKER_POLL_SECONDS", "5")),
        heartbeat_seconds=float(os.getenv("ASEA_CLOUD_WORKER_HEARTBEAT_SECONDS", "20")),
    )
    signal.signal(signal.SIGINT, lambda *_: runtime.stop())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: runtime.stop())
    runtime.run()


if __name__ == "__main__":
    run()
