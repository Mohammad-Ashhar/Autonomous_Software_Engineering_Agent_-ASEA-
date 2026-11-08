"""End-to-end API test with Docker and the LLM stubbed out."""

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asea.api import main as api_main
from asea.api.job_manager import repair_jobs
from asea.tools.llm_client import OllamaClient
from asea.tools.test_runner import DockerTestRunner

DEMO_REPO = Path(__file__).resolve().parents[1] / "demo_repo"


def fake_run_tests(self, repository_path):
    """Run pytest locally instead of in Docker."""

    process = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rA"],
        cwd=repository_path,
        capture_output=True,
        text=True,
    )
    parsed = self.parse_results(process.stdout, process.returncode)
    return {
        **parsed,
        "success": process.returncode == 0,
        "timed_out": False,
        "exit_code": process.returncode,
        "duration": 0.1,
        "output": process.stdout,
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(api_main, "CHECKPOINT_DB_PATH", tmp_path / "cp.db")
    monkeypatch.setattr(repair_jobs, "db_path", tmp_path / "jobs.db")
    repair_jobs._initialize_database()
    monkeypatch.setattr(DockerTestRunner, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(DockerTestRunner, "run_tests", fake_run_tests)

    def unavailable(self, *args, **kwargs):
        raise RuntimeError("LLM disabled in tests")

    monkeypatch.setattr(OllamaClient, "chat", unavailable)

    published = []

    def fake_publish(values):
        published.append(values)
        return {
            "status": "PULL_REQUEST_CREATED",
            "repository": "someone/else",
            "branch": "asea/repair-x",
            "base_branch": "main",
            "commit": "abc",
            "files": ["buggy_example.py"],
            "pull_request": {"url": "https://github.com/someone/else/pull/1"},
        }

    monkeypatch.setattr(api_main, "publish_validated_repairs", fake_publish)

    repo = tmp_path / "target"
    shutil.copytree(DEMO_REPO, repo)
    # The committed demo already contains the fix; put the bug back.
    (repo / "buggy_example.py").write_text(
        "def is_even(number: int) -> bool:\n"
        "    return number % 2 == 1\n"
    )

    with TestClient(api_main.app) as test_client:
        test_client.published = published
        test_client.repo = repo
        yield test_client


def wait_for(client, job_id, wanted, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/repair/{job_id}").json()
        if job["status"] in wanted:
            return job
        assert job["status"] != "failed", job
        time.sleep(0.2)
    raise AssertionError("timed out waiting for " + str(wanted))


def test_full_flow_publishes_and_freezes_timer(client):
    response = client.post(
        "/repair", json={"repository": str(client.repo), "publish": True}
    )
    job_id = response.json()["job_id"]

    review = wait_for(client, job_id, {"waiting_for_review"})
    assert review["is_active"] is False
    assert review["stage"] == "Waiting for patch review"
    assert review["timings"]  # per-stage timings are recorded

    frozen = review["elapsed_seconds"]
    time.sleep(1.2)
    assert client.get(f"/repair/{job_id}").json()["elapsed_seconds"] == frozen

    client.post(f"/repair/{job_id}/review", json={"decision": "approved"})
    done = wait_for(client, job_id, {"completed"})

    assert done["is_terminal"] and not done["is_active"]
    assert done["completed_at"]
    assert done["publication"]["status"] == "PULL_REQUEST_CREATED"
    assert "output" not in done["result"]["working_test_results"]
    assert len(client.published) == 1

    final = done["elapsed_seconds"]
    time.sleep(1.2)
    assert client.get(f"/repair/{job_id}").json()["elapsed_seconds"] == final


def test_publish_can_be_disabled(client):
    job_id = client.post(
        "/repair", json={"repository": str(client.repo), "publish": False}
    ).json()["job_id"]

    wait_for(client, job_id, {"waiting_for_review"})
    client.post(f"/repair/{job_id}/review", json={"decision": "approved"})
    done = wait_for(client, job_id, {"completed"})

    assert done["publication"] is None
    assert client.published == []


def test_interrupted_jobs_are_failed_on_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(repair_jobs, "db_path", tmp_path / "jobs.db")
    repair_jobs._initialize_database()
    repair_jobs.create_job("stale", "x")
    repair_jobs.update_job("stale", status="running")

    assert repair_jobs.fail_interrupted_jobs() == 1

    job = repair_jobs.get_job("stale")
    assert job["status"] == "failed"
    assert job["is_active"] is False
