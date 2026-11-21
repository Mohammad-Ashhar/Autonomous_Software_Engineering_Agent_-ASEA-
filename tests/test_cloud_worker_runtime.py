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


class FakeGraph:
    def __init__(self, interrupts):
        self.interrupts = interrupts

    def invoke(self, *_args, **_kwargs):
        return {"status": "IN_PROGRESS"}

    def get_state(self, _config):
        return SimpleNamespace(
            interrupts=self.interrupts,
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
