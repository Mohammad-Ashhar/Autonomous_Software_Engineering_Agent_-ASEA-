from types import SimpleNamespace

from fastapi.testclient import TestClient

from asea.api import main


class InMemoryRepairJobs:
    def __init__(self):
        self.jobs = {}
        self.review_claims = set()

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

    def claim_review(self, job_id):
        if job_id in self.review_claims:
            return False
        self.review_claims.add(job_id)
        return True

    def release_review_claim(self, job_id):
        self.review_claims.discard(job_id)
        return True

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



class RecordingExecutor:
    def __init__(self):
        self.submitted = []

    def submit(self, function, *args):
        self.submitted.append((function, args))


def test_patch_review_http_route_claims_and_schedules_once(monkeypatch):
    jobs = InMemoryRepairJobs()
    executor = RecordingExecutor()
    jobs.jobs["review-job"] = {
        "job_id": "review-job",
        "repository": "/tmp/asea-e2e-repository",
        "status": "waiting_for_review",
        "publish_requested": False,
    }
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "executor", executor)

    client = TestClient(main.app)

    missing_feedback = client.post(
        "/repair/review-job/review",
        json={"decision": "rejected", "feedback": "  "},
    )
    assert missing_feedback.status_code == 400

    approved = client.post(
        "/repair/review-job/review",
        json={"decision": "approved", "feedback": ""},
    )
    assert approved.status_code == 202
    assert approved.json()["decision"] == "approved"
    assert len(executor.submitted) == 1
    function, args = executor.submitted[0]
    assert function is main.resume_repair_job
    assert args == ("review-job", "approved", "")

    duplicate = client.post(
        "/repair/review-job/review",
        json={"decision": "approved", "feedback": ""},
    )
    assert duplicate.status_code == 409
    assert len(executor.submitted) == 1


class UnsuccessfulGraph:
    def invoke(self, initial_state, config):
        return {
            "status": "baseline_test_execution_failed",
            "errors": [
                "Baseline test execution did not produce a reliable test result (status=NO_TESTS)."
            ],
            "test_generation_results": {"status": "NO_ELIGIBLE_MODULES"},
        }

    def get_state(self, config):
        return SimpleNamespace(interrupts=[])


def test_unfinished_workflow_is_not_reported_as_completed(monkeypatch):
    jobs = InMemoryRepairJobs()
    monkeypatch.delenv("ASEA_API_KEY", raising=False)
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "executor", SynchronousExecutor())
    monkeypatch.setattr(main, "resolve_repository", lambda repository: repository)
    monkeypatch.setattr(
        main,
        "create_initial_state",
        lambda repository, review_mode: {"repo_path": repository},
    )
    monkeypatch.setattr(main.app.state, "graph", UnsuccessfulGraph(), raising=False)
    monkeypatch.setattr(main, "cleanup_repair_workspace", lambda *_args: True)

    client = TestClient(main.app)
    submitted = client.post(
        "/repair",
        json={"repository": "/tmp/asea-invalid-repository", "publish": False},
    )

    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]

    status = client.get(f"/repair/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "failed"
    assert "did not complete successfully" in status.json()["message"]
    assert "NO_TESTS" in status.json()["error"]
    assert status.json()["result"]["workflow"]["status"] == (
        "baseline_test_execution_failed"
    )

    history = client.get("/repairs?status=completed")
    assert history.json()["total"] == 0
    failed_history = client.get("/repairs?status=failed")
    assert failed_history.json()["total"] == 1
