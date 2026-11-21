import hashlib
import hmac

from asea.api.cloud_workers import CloudWorkerManager


def _manager():
    manager = object.__new__(CloudWorkerManager)
    manager.pepper = b"test-pepper-value-that-is-long-enough"
    return manager


def test_worker_secret_digest_is_deterministic_and_keyed():
    manager = _manager()
    digest = manager._digest("high-entropy-worker-secret")
    assert digest == manager._digest("high-entropy-worker-secret")
    other_digest = hmac.new(
        b"different-pepper-value-long-enough",
        b"high-entropy-worker-secret",
        hashlib.sha256,
    ).digest()
    assert digest != other_digest



def test_completion_requires_successful_workflow_result():
    from asea.api.cloud_workers import WorkerProtocolError

    try:
        CloudWorkerManager._validate_terminal_result({"status": "FAILED"})
    except WorkerProtocolError as error:
        assert "successfully completed" in str(error)
    else:
        raise AssertionError("A failed workflow must not be accepted as completed.")


def test_completion_rejects_oversized_result():
    from asea.api.cloud_workers import WorkerProtocolError

    try:
        CloudWorkerManager._validate_terminal_result(
            {"status": "COMPLETED", "output": "x" * 1_048_577}
        )
    except WorkerProtocolError as error:
        assert "1 MB" in str(error)
    else:
        raise AssertionError("Oversized completion results must be rejected.")


def test_failure_diagnostics_are_bounded():
    from asea.api.cloud_workers import WorkerProtocolError

    CloudWorkerManager._validate_failure("Repair execution failed.")
    for invalid in ("", " " * 3, "x" * 1801, None):
        try:
            CloudWorkerManager._validate_failure(invalid)
        except WorkerProtocolError:
            pass
        else:
            raise AssertionError("Invalid failure diagnostics must be rejected.")
