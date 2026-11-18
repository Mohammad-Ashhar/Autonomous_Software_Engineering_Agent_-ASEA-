from types import SimpleNamespace

from asea.api import main


class FakeRepairJobs:
    def __init__(self):
        self.created = []
        self.updates = []
        self.claimed = set()
        self.released = []

    def claim_review(self, job_id, decision=None, feedback=""):
        if job_id in self.claimed:
            return False
        self.claimed.add(job_id)
        self.review_submission = (job_id, decision, feedback)
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
        lambda result, **_kwargs: publish_calls.append(result) or published,
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
        lambda result, **_kwargs: publish_calls.append(result) or published,
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
    assert jobs.review_submission == ("job-review", "approved", "")


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


def test_repair_request_rejects_unknown_fields():
    try:
        main.RepairRequest(
            repository="/tmp/example-repository",
            unexpected=True,
        )
    except Exception as error:
        assert "unexpected" in str(error).lower()
    else:
        raise AssertionError("Unknown request fields must be rejected")


def test_repair_request_rejects_oversized_repository_value():
    try:
        main.RepairRequest(repository="x" * 2049)
    except Exception as error:
        assert "2048" in str(error)
    else:
        raise AssertionError("Oversized repository values must be rejected")


def test_repair_request_strips_repository_whitespace():
    request = main.RepairRequest(repository="  /tmp/example-repository  ")

    assert request.repository == "/tmp/example-repository"

def test_run_repair_job_records_failure_without_exposing_exception(
    monkeypatch,
):
    jobs = FakeRepairJobs()
    monkeypatch.setattr(main, "repair_jobs", jobs)

    def fail_repository_resolution(_repository):
        raise RuntimeError("private filesystem detail")

    monkeypatch.setattr(main, "resolve_repository", fail_repository_resolution)

    main.run_repair_job("job-resolution-failure", "/tmp/private-repository")

    assert jobs.updates[0][1] == {
        "status": "running",
        "message": "Resolving repository and starting repair.",
    }
    assert jobs.updates[-1][1] == {
        "status": "failed",
        "message": "ASEA repair workflow failed.",
        "error": "RuntimeError: private filesystem detail",
    }


def test_resume_repair_job_records_failure_and_keeps_error_generic(
    monkeypatch,
):
    jobs = FakeRepairJobs()
    monkeypatch.setattr(main, "repair_jobs", jobs)

    class FailingGraph:
        def invoke(self, command, config):
            raise RuntimeError("private model-provider response")

        def get_state(self, config):
            raise RuntimeError("checkpoint unavailable")

    monkeypatch.setattr(
        main.app.state,
        "graph",
        FailingGraph(),
        raising=False,
    )

    main.resume_repair_job("job-review-failure", "approved", "")

    assert jobs.updates[-1][1] == {
        "status": "failed",
        "message": "Failed to resume ASEA repair workflow.",
        "error": "RuntimeError: private model-provider response",
    }



def test_publish_repair_result_preserves_existing_pull_request_status(monkeypatch):
    existing = {
        "status": "PULL_REQUEST_EXISTS",
        "pull_request": {
            "status": "EXISTING",
            "number": 42,
            "url": "https://github.com/example/repo/pull/42",
        },
    }
    monkeypatch.setattr(
        main,
        "publish_validated_repairs",
        lambda _result, **_kwargs: existing,
    )

    assert main._publish_repair_result({"status": "COMPLETED"}) == existing



def test_resume_repair_reports_existing_pull_request(monkeypatch):
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
    monkeypatch.setattr(
        main,
        "_publish_repair_result",
        lambda _result, _job_id=None: {"status": "PULL_REQUEST_EXISTS"},
    )
    main.resume_repair_job("job-existing-pr", "approved", "")

    final_update = jobs.updates[-1][1]
    assert final_update["status"] == "completed"
    assert "existing pull request was found" in final_update["message"]


def test_publish_retry_endpoint_claims_and_schedules_job(monkeypatch):
    jobs = FakeRepairJobs()
    jobs.claim_publishing_retry = lambda job_id: job_id == "job-retry"
    monkeypatch.setattr(main, "repair_jobs", jobs)
    executor = FakeExecutor()
    monkeypatch.setattr(main, "executor", executor)

    response = main.retry_repair_publish("job-retry")

    assert response == {
        "message": "Publishing retry submitted.",
        "job_id": "job-retry",
        "status": "publishing_retry_queued",
    }
    assert executor.submitted == [
        (main.retry_repair_publishing, ("job-retry",))
    ]


def test_publish_retry_endpoint_rejects_duplicate_claim(monkeypatch):
    jobs = FakeRepairJobs()
    jobs.claim_publishing_retry = lambda _job_id: False
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "executor", FakeExecutor())

    try:
        main.retry_repair_publish("job-retry")
    except main.HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Duplicate publishing retry must be rejected")



def test_publishing_retry_reuses_saved_workflow_and_cleans_up_on_success(monkeypatch):
    jobs = FakeRepairJobs()
    workflow = {
        "status": "COMPLETED",
        "repo_path": "/tmp/validated-repair",
        "working_test_results": {"success": True},
    }
    jobs.get_job = lambda job_id: {
        "job_id": job_id,
        "status": "completed",
        "result": {
            "workflow": workflow,
            "publishing": {"status": "PUBLISH_FAILED"},
        },
    }
    publish_calls = []
    cleanup_calls = []
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(
        main,
        "_publish_repair_result",
        lambda result, job_id: (
            publish_calls.append((result, job_id))
            or {"status": "PULL_REQUEST_CREATED", "pull_request": {"url": "https://github.com/example/repo/pull/1"}}
        ),
    )
    monkeypatch.setattr(
        main,
        "cleanup_repair_workspace",
        lambda repo_path, result: cleanup_calls.append((repo_path, result)),
    )

    main.retry_repair_publishing("retry-success-001")

    assert publish_calls == [(workflow, "retry-success-001")]
    assert cleanup_calls == [("/tmp/validated-repair", workflow)]
    final_update = jobs.updates[-1][1]
    assert final_update["result"]["publishing"]["status"] == "PULL_REQUEST_CREATED"
    assert final_update["error"] is None
    assert final_update["publishing_retry"] is False


def test_publishing_retry_preserves_workspace_on_failure(monkeypatch):
    jobs = FakeRepairJobs()
    workflow = {
        "status": "COMPLETED",
        "repo_path": "/tmp/validated-repair",
        "working_test_results": {"success": True},
    }
    jobs.get_job = lambda job_id: {
        "job_id": job_id,
        "status": "completed",
        "result": {
            "workflow": workflow,
            "publishing": {"status": "PUBLISH_FAILED"},
        },
    }
    cleanup_calls = []
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(
        main,
        "_publish_repair_result",
        lambda _result, _job_id: {"status": "PUBLISH_FAILED", "error": "network"},
    )
    monkeypatch.setattr(
        main,
        "cleanup_repair_workspace",
        lambda *args: cleanup_calls.append(args),
    )

    main.retry_repair_publishing("retry-failure-001")

    assert cleanup_calls == []
    final_update = jobs.updates[-1][1]
    assert final_update["result"]["publishing"]["status"] == "PUBLISH_FAILED"
    assert final_update["publishing_retry"] is False
    assert "can be retried" in final_update["message"]

def test_publishing_retry_releases_claim_after_unexpected_exception(monkeypatch):
    jobs = FakeRepairJobs()
    workflow = {"status": "COMPLETED", "repo_path": "/tmp/validated-repair"}
    jobs.get_job = lambda _job_id: {
        "job_id": "retry-exception-001",
        "status": "completed",
        "result": {"workflow": workflow},
    }
    monkeypatch.setattr(main, "repair_jobs", jobs)

    def fail_publishing(_result, _job_id):
        raise RuntimeError("temporary publisher failure")

    monkeypatch.setattr(main, "_publish_repair_result", fail_publishing)

    main.retry_repair_publishing("retry-exception-001")

    assert jobs.released == ["retry-exception-001"]
    final_update = jobs.updates[-1][1]
    assert final_update["publishing_retry"] is False
    assert final_update["message"] == (
        "Publishing retry failed unexpectedly; it can be retried."
    )
    assert "temporary publisher failure" in final_update["error"]

