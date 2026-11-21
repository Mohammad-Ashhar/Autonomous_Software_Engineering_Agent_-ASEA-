import pytest

from asea.api.cloud_workers import CloudWorkerManager, WorkerProtocolError


def _manager():
    manager = object.__new__(CloudWorkerManager)
    manager.pepper = b"test-pepper-value-that-is-long-enough"
    return manager


def test_worker_secret_digest_is_deterministic_and_keyed():
    manager = _manager()
    digest = manager._digest("high-entropy-worker-secret")
    assert digest == manager._digest("high-entropy-worker-secret")
    assert digest != CloudWorkerManager._digest(
        type("Other", (), {"pepper": b"different-pepper-value-long-enough"})(),
        "high-entropy-worker-secret",
    )


def test_enroll_rejects_short_code_before_database_access():
    with pytest.raises(WorkerProtocolError, match="invalid or expired"):
        _manager().enroll("short", None, {})


def test_authentication_rejects_malformed_secret_without_database_access():
    assert _manager().authenticate("short") is None


@pytest.mark.parametrize(
    "event_type,idempotency_key,payload",
    [
        ("x" * 81, "event-1", {}),
        ("progress", "x" * 201, {}),
        ("progress", "event-1", {"large": "x" * 17000}),
    ],
)
def test_event_limits_are_checked_before_database_access(
    event_type, idempotency_key, payload
):
    with pytest.raises(WorkerProtocolError):
        _manager().append_event(
            {"worker_id": "00000000-0000-0000-0000-000000000001",
             "owner_id": "00000000-0000-0000-0000-000000000002"},
            "job-1", "00000000-0000-0000-0000-000000000003", 1,
            event_type, payload, idempotency_key,
        )
