from types import SimpleNamespace

from asea.api import main


class FakeRepairJobs:
    def __init__(self, job=None):
        self.job = job or {"publish_requested": False}
        self.updates = []

    def update_job(self, job_id, **updates):
        self.updates.append((job_id, updates))
        return {"job_id": job_id, **updates}

    def get_job(self, job_id):
        return self.job


class FakeGraph:
    def __init__(self, result, interrupts=None, error=None, values=None):
        self.result = result
        self.interrupts = interrupts or []
        self.error = error
        self.values = values if values is not None else result
        self.configs = []

    def invoke(self, state, config):
        self.configs.append(config)
        if self.error:
            raise self.error
        return self.result

    def get_state(self, config):
        return SimpleNamespace(
            interrupts=self.interrupts,
            values=self.values,
        )


def test_completed_repair_cleans_workspace(monkeypatch):
    jobs = FakeRepairJobs()
    result = {
        "repo_path": "/source",
        "working_repo_path": "/workspaces/asea_run_1/repository",
    }
    graph = FakeGraph(result)
    cleanups = []
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "resolve_repository", lambda _repo: "/source")
    monkeypatch.setattr(main, "create_initial_state", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main.app.state, "graph", graph, raising=False)
    monkeypatch.setattr(
        main,
        "cleanup_repair_workspace",
        lambda repository, state: cleanups.append((repository, state)) or True,
    )

    main.run_repair_job("job-complete", "/source")

    assert cleanups == [("/source", result)]
    assert jobs.updates[-1][1]["status"] == "completed"


def test_review_pause_preserves_workspace(monkeypatch):
    jobs = FakeRepairJobs()
    result = {
        "repo_path": "/source",
        "working_repo_path": "/workspaces/asea_run_2/repository",
    }
    graph = FakeGraph(
        result,
        interrupts=[SimpleNamespace(value={"patch": "diff"})],
    )
    cleanups = []
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "resolve_repository", lambda _repo: "/source")
    monkeypatch.setattr(main, "create_initial_state", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main.app.state, "graph", graph, raising=False)
    monkeypatch.setattr(
        main,
        "cleanup_repair_workspace",
        lambda *args: cleanups.append(args) or True,
    )

    main.run_repair_job("job-review", "/source")

    assert cleanups == []
    assert jobs.updates[-1][1]["status"] == "waiting_for_review"


def test_failed_repair_cleans_checkpoint_workspace(monkeypatch):
    jobs = FakeRepairJobs()
    checkpoint = {
        "repo_path": "/source",
        "working_repo_path": "/workspaces/asea_run_3/repository",
    }
    graph = FakeGraph({}, error=RuntimeError("workflow failed"), values=checkpoint)
    cleanups = []
    monkeypatch.setattr(main, "repair_jobs", jobs)
    monkeypatch.setattr(main, "resolve_repository", lambda _repo: "/source")
    monkeypatch.setattr(main, "create_initial_state", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main.app.state, "graph", graph, raising=False)
    monkeypatch.setattr(
        main,
        "cleanup_repair_workspace",
        lambda repository, state: cleanups.append((repository, state)) or True,
    )

    main.run_repair_job("job-failed", "/source")

    assert cleanups == [("/source", checkpoint)]
    assert jobs.updates[-1][1]["status"] == "failed"
