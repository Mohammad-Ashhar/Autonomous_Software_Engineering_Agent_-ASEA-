import pytest

from asea.api.cloud_workers import CloudWorkerManager, WorkerProtocolError


def test_review_checkpoint_requires_object_payloads_and_thread_id():
    with pytest.raises(WorkerProtocolError, match="objects"):
        CloudWorkerManager._validate_review_checkpoint([], {})
    with pytest.raises(WorkerProtocolError, match="thread_id"):
        CloudWorkerManager._validate_review_checkpoint({}, {"worker_id": "worker"})


def test_review_checkpoint_rejects_oversized_review_request():
    with pytest.raises(WorkerProtocolError, match="256 KB"):
        CloudWorkerManager._validate_review_checkpoint(
            {"patch": "x" * 262_145},
            {"thread_id": "job-1"},
        )


def test_review_checkpoint_rejects_oversized_checkpoint_reference():
    with pytest.raises(WorkerProtocolError, match="8 KB"):
        CloudWorkerManager._validate_review_checkpoint(
            {},
            {"thread_id": "job-1", "metadata": "x" * 8192},
        )
