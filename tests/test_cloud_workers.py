import hashlib
import hmac

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
    other_digest = hmac.new(\n        b"different-pepper-value-long-enough",\n        b"high-entropy-worker-secret",\n        hashlib.sha256,\n    ).digest()\n    assert digest != other_digest
