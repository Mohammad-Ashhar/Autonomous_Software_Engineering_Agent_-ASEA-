from types import SimpleNamespace

from fastapi.testclient import TestClient

from asea.api import main


class InMemoryRepairJobs:
    def __init__(self):
        self.jobs = {}

    def create_job(self, job_id, repository, publish_requested=False):
        job = {
            "job_id": job_id,
            "repository": repository,
            "status": "queued",
            "publish_requested": publish_requested,
        }
        self.jobs[job_id] = job
        return job

    def update_job(self, job_id, **updates):
        self.jobs[job_id].update(updates)
        return self.jobs[job_id]

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def list_jobs(self, limit=50, offset=0, status=None):
        items = list(self.jobs.values())
        if status:
            items = [job for job in items if job["status"] == status]
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "limit": limit,
            "offset": offset,
        }


class SynchronousExecutor:
    def submit(self, function, *args):
        function(*args)


class SuccessfulGraph:
    def invoke(self, initial_state, config):
        assert initial_state == {"repo_path": "/tmp/asea-e2e-repository"}
        assert config["configurable"]["thread_id"]
        return {
            "status": "COMPLETED",
            "working_test_results": {"success": True, "passed": 3, "failed": 0},
            "repaired_bugs": [{"title": "Boundary condition fixed"}],
        }

    def get_state(self, config):
        return SimpleNamespace(interrupts=[])


def test_repair_job_http_lifecycle_from_submission_to_history(
    monkeypatch,
):
    jobs = InMemoryRepairJobs()
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "executor", SynchronousExecutor())
    monkeypatch.setattr(
        main,
        "resolve_repository",
        lambda repository: repository,
    )
    monkeypatch.setattr(
        main,
        "create_initial_state",
        lambda repository, review_mode: {"repo_path": repository},
    )
    monkeypatch.setattr(main.app.state, "graph", SuccessfulGraph(), raising=False)
    monkeypatch.setattr(
        main,
        "cleanup_repair_workspace",
        lambda *_args: True,
    )

    client = TestClient(main.app)
    submitted = client.post(
        "/repair",
        json={
            "repository": "/tmp/asea-e2e-repository",
            "publish": False,
        },
    )

    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]

    status = client.get(f"/repair/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["result"]["workflow"]["working_test_results"] == {
        "success": True,
        "passed": 3,
        "failed": 0,
    }

    history = client.get("/repairs?status=completed")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["job_id"] == job_id
