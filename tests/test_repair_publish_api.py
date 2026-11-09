from types import SimpleNamespace

from asea.api import main


class FakeRepairJobs:
    def __init__(self):
        self.created = []
        self.updates = []
        self.claimed = set()
        self.released = []

    def claim_review(self, job_id):
        if job_id in self.claimed:
            return False
        self.claimed.add(job_id)
        return True

    def release_review_claim(self, job_id):
        self.released.append(job_id)
        return True

    def create_job(self, job_id, repository, publish_requested=False):
        job = {
            "job_id": job_id,
            "repository": repository,
            "status": "queued",
            "publish_requested": publish_requested,
        }
        self.created.append(job)
        return job

    def get_job(self, job_id):
        return {
            "job_id": job_id,
            "status": "waiting_for_review",
            "result": None,
            "publish_requested": True,
        }

    def update_job(self, job_id, **updates):
        self.updates.append((job_id, updates))
        return {"job_id": job_id, **updates}


class FakeExecutor:
    def __init__(self):
        self.submitted = []

    def submit(self, function, *args):
        self.submitted.append((function, args))


def test_start_repair_forwards_explicit_publish_choice(monkeypatch):
    jobs = FakeRepairJobs()
    executor = FakeExecutor()
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "executor", executor)

    response = main.start_repair(
        main.RepairRequest(
            repository="/tmp/example-repository",
            publish=True,
        )
    )

    assert response["status"] == "queued"
    assert len(executor.submitted) == 1
    function, args = executor.submitted[0]
    assert function is main.run_repair_job
    assert jobs.created[0]["publish_requested"] is True
    assert args[1:] == ("/tmp/example-repository", True)


def test_run_repair_job_publishes_only_when_requested(monkeypatch):
    jobs = FakeRepairJobs()
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(
        main,
        "resolve_repository",
        lambda repository: repository,
    )
    monkeypatch.setattr(
        main,
        "create_initial_state",
        lambda repository, review_mode: {
            "repo_path": repository,
            "review_mode": review_mode,
        },
    )

    workflow_result = {
        "status": "COMPLETED",
        "working_test_results": {"success": True},
        "repaired_bugs": [{"status": "REPAIRED"}],
    }

    class FakeGraph:
        def invoke(self, initial_state, config):
            assert initial_state["review_mode"] == "api"
            return workflow_result

        def get_state(self, config):
            return SimpleNamespace(interrupts=[])

    monkeypatch.setattr(main.app.state, "graph", FakeGraph(), raising=False)

    published = {
        "status": "PULL_REQUEST_CREATED",
        "pull_request": {"url": "https://github.com/example/repo/pull/1"},
    }
    publish_calls = []
    monkeypatch.setattr(
        main,
        "publish_validated_repairs",
        lambda result: publish_calls.append(result) or published,
    )

    main.run_repair_job(
        "job-publish",
        "/tmp/example-repository",
        publish=True,
    )

    assert publish_calls == [workflow_result]
    final_update = jobs.updates[-1][1]
    assert final_update["status"] == "completed"
    assert final_update["result"]["workflow"] == workflow_result
    assert final_update["result"]["publishing"] == published
    assert "pull request created" in final_update["message"]


def test_run_repair_job_does_not_publish_by_default(monkeypatch):
    jobs = FakeRepairJobs()
    monkeypatch.setattr(main, "repair_jobs", jobs)
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

    workflow_result = {
        "status": "COMPLETED",
        "working_test_results": {"success": True},
    }

    class FakeGraph:
        def invoke(self, initial_state, config):
            return workflow_result

        def get_state(self, config):
            return SimpleNamespace(interrupts=[])

    monkeypatch.setattr(main.app.state, "graph", FakeGraph(), raising=False)

    def unexpected_publish(_result):
        raise AssertionError("Publishing must remain opt-in.")

    monkeypatch.setattr(main, "publish_validated_repairs", unexpected_publish)

    main.run_repair_job("job-no-publish", "/tmp/example-repository")

    final_update = jobs.updates[-1][1]
    assert final_update["status"] == "completed"
    assert final_update["result"] == {"workflow": workflow_result}



def test_resume_repair_publishes_after_final_human_approval(monkeypatch):
    jobs = FakeRepairJobs()
    monkeypatch.setattr(main, "repair_jobs", jobs)

    workflow_result = {
        "status": "COMPLETED",
        "working_test_results": {"success": True},
    }

    class FakeGraph:
        def invoke(self, command, config):
            return workflow_result

        def get_state(self, config):
            return SimpleNamespace(interrupts=[])

    monkeypatch.setattr(main.app.state, "graph", FakeGraph(), raising=False)

    published = {"status": "PULL_REQUEST_CREATED"}
    publish_calls = []
    monkeypatch.setattr(
        main,
        "publish_validated_repairs",
        lambda result: publish_calls.append(result) or published,
    )

    main.resume_repair_job("job-resume-publish", "approved", "")

    assert publish_calls == [workflow_result]
    final_update = jobs.updates[-1][1]
    assert final_update["status"] == "completed"
    assert final_update["result"] == {
        "workflow": workflow_result,
        "publishing": published,
    }
    assert "pull request created" in final_update["message"]


def test_review_submission_claims_job_before_scheduling(monkeypatch):
    jobs = FakeRepairJobs()
    executor = FakeExecutor()
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "executor", executor)

    response = main.review_repair_patch(
        "job-review",
        main.ReviewRequest(decision="approved"),
    )

    assert response["decision"] == "approved"
    assert len(executor.submitted) == 1
    assert jobs.claimed == {"job-review"}


class RejectingExecutor:
    def submit(self, function, *args):
        raise RuntimeError("executor is shutting down")


def test_review_submission_restores_job_when_scheduling_fails(monkeypatch):
    jobs = FakeRepairJobs()
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "executor", RejectingExecutor())

    try:
        main.review_repair_patch(
            "job-review-schedule-failure",
            main.ReviewRequest(decision="approved"),
        )
    except main.HTTPException as error:
        assert error.status_code == 503
    else:
        raise AssertionError("Scheduling failure should return HTTP 503")

    assert jobs.claimed == {"job-review-schedule-failure"}
    assert jobs.released == ["job-review-schedule-failure"]



def test_start_repair_marks_job_failed_when_scheduling_fails(monkeypatch):
    jobs = FakeRepairJobs()
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "executor", RejectingExecutor())

    try:
        main.start_repair(
            main.RepairRequest(repository="/tmp/example-repository")
        )
    except main.HTTPException as error:
        assert error.status_code == 503
    else:
        raise AssertionError("Scheduling failure should return HTTP 503")

    assert len(jobs.created) == 1
    job_id = jobs.created[0]["job_id"]
    assert jobs.updates == [
        (
            job_id,
            {
                "status": "failed",
                "message": "ASEA could not schedule the repair job.",
                "error": "The repair could not be scheduled. Please retry.",
            },
        )
    ]
