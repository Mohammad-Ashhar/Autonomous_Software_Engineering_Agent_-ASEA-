from types import SimpleNamespace

import pytest

from asea.api import cloud_worker_runtime as runtime


class FakeClient:
    def __init__(self):
        self.calls = []

    def append_event(self, *args):
        self.calls.append(("event", args))

    def pause_for_review(self, *args):
        self.calls.append(("pause", args))
        return {"status": "waiting_for_review"}

    def heartbeat(self, *args):
        self.calls.append(("heartbeat", args))

    def renew_lease(self, *args):
        self.calls.append(("renew", args))

    def complete_job(self, *args):
        self.calls.append(("complete", args))


class FakeGraph:
    def __init__(self, interrupts):
        self.interrupts = interrupts

    def invoke(self, *_args, **_kwargs):
        return {"status": "IN_PROGRESS"}

    def get_state(self, _config):
        return SimpleNamespace(
            tasks=(SimpleNamespace(interrupts=self.interrupts),),
            config={
                "configurable": {
                    "checkpoint_id": "checkpoint-123",
                    "checkpoint_ns": "",
                }
            },
        )


def test_checkpoint_reference_is_bound_to_job_and_worker():
    snapshot = SimpleNamespace(
        config={
            "configurable": {
                "checkpoint_id": "checkpoint-123",
                "checkpoint_ns": "repair",
            }
        }
    )

    assert runtime._checkpoint_reference(snapshot, "job-1", "worker-1") == {
        "thread_id": "job-1",
        "worker_id": "worker-1",
        "checkpoint_id": "checkpoint-123",
        "checkpoint_ns": "repair",
    }


def test_checkpoint_reference_omits_missing_checkpoint_metadata():
    snapshot = SimpleNamespace(config=None)

    assert runtime._checkpoint_reference(snapshot, "job-1", "worker-1") == {
        "thread_id": "job-1",
        "worker_id": "worker-1",
    }


def test_claimed_job_pauses_with_durable_worker_affinity(monkeypatch):
    client = FakeClient()
    job = {
        "job_id": "job-1",
        "lease_id": "lease-1",
        "fencing_generation": 4,
        "repository": "https://github.com/example/repo",
        "publish_requested": False,
    }
    review = {"patch": "diff", "summary": "Review this patch"}
    graph = FakeGraph([SimpleNamespace(value=review)])
    monkeypatch.setattr(runtime, "LEASE_RENEW_SECONDS", 300)
    monkeypatch.setattr(runtime, "resolve_repository", lambda _repository: "repo-path")
    monkeypatch.setattr(
        runtime,
        "create_initial_state",
        lambda _path, review_mode: {"review_mode": review_mode},
    )

    outcome = runtime.execute_claimed_job(client, job, "worker-1", graph)

    assert outcome == "waiting_for_review"
    pause = next(call for call in client.calls if call[0] == "pause")
    assert pause[1][0:3] == ("job-1", "lease-1", 4)
    assert pause[1][3] == review
    assert pause[1][4] == {
        "thread_id": "job-1",
        "worker_id": "worker-1",
        "checkpoint_id": "checkpoint-123",
    }


@pytest.mark.parametrize("value", ["0", "-1", "301", "not-a-number"])
def test_poll_interval_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("ASEA_CLOUD_WORKER_POLL_SECONDS", value)

    with pytest.raises(RuntimeError):
        runtime._positive_seconds("ASEA_CLOUD_WORKER_POLL_SECONDS", 5.0)



def test_snapshot_interrupts_are_collected_from_langgraph_tasks():
    interrupt = SimpleNamespace(value={"patch": "diff"})
    snapshot = SimpleNamespace(
        tasks=(
            SimpleNamespace(interrupts=(interrupt,)),
            SimpleNamespace(interrupts=()),
        )
    )

    assert runtime._snapshot_interrupts(snapshot) == [interrupt]


def test_snapshot_interrupts_support_legacy_top_level_shape():
    interrupt = SimpleNamespace(value={"patch": "diff"})
    snapshot = SimpleNamespace(
        interrupts=(interrupt,),
        tasks=(SimpleNamespace(interrupts=()),),
    )

    assert runtime._snapshot_interrupts(snapshot) == [interrupt]


def test_idle_worker_heartbeat_is_sent_when_due(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(runtime, "IDLE_HEARTBEAT_SECONDS", 30)

    last = runtime._heartbeat_if_due(client, 10.0, now=39.0)
    assert last == 10.0
    assert not [call for call in client.calls if call[0] == "heartbeat"]

    last = runtime._heartbeat_if_due(client, last, now=40.0)
    assert last == 40.0
    assert [call[0] for call in client.calls] == ["heartbeat"]


def test_idle_worker_heartbeat_is_not_repeated_before_interval(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(runtime, "IDLE_HEARTBEAT_SECONDS", 30)

    last = runtime._heartbeat_if_due(client, 100.0, now=130.0)
    last = runtime._heartbeat_if_due(client, last, now=159.0)

    assert last == 130.0
    assert len([call for call in client.calls if call[0] == "heartbeat"]) == 1



def test_successful_workflow_sends_top_level_status_when_finalizing(monkeypatch):
    client = FakeClient()
    job = {
        "job_id": "job-1",
        "lease_id": "lease-1",
        "fencing_generation": 4,
        "repository": "https://github.com/example/repo",
        "publish_requested": False,
    }

    class CompletedGraph:
        def invoke(self, *_args, **_kwargs):
            return {"status": "COMPLETED", "repo_path": "repo-path"}

        def get_state(self, _config):
            return SimpleNamespace(tasks=(), config=None)

    monkeypatch.setattr(runtime, "LEASE_RENEW_SECONDS", 300)
    monkeypatch.setattr(runtime, "resolve_repository", lambda _repository: "repo-path")
    monkeypatch.setattr(
        runtime,
        "create_initial_state",
        lambda _path, review_mode: {"review_mode": review_mode},
    )
    monkeypatch.setattr(runtime, "cleanup_repair_workspace", lambda *_args: None)

    outcome = runtime.execute_claimed_job(
        client, job, "worker-1", CompletedGraph()
    )

    assert outcome == "completed"
    completion = next(call for call in client.calls if call[0] == "complete")
    assert completion[1][3]["status"] == "COMPLETED"
    assert completion[1][3]["workflow"]["status"] == "COMPLETED"
