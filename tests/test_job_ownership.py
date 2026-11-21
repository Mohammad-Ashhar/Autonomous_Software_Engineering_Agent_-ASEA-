from threading import Lock

from asea.api.job_manager import RepairJobManager


def make_manager(tmp_path):
    manager = RepairJobManager.__new__(RepairJobManager)
    manager.db_path = tmp_path / "jobs.db"
    manager.lock = Lock()
    manager._initialize_database()
    return manager


def test_job_reads_and_history_are_scoped_to_owner(tmp_path):
    manager = make_manager(tmp_path)
    manager.create_job("job-a", "/repo/a", owner_id="user-a")
    manager.create_job("job-b", "/repo/b", owner_id="user-b")

    assert manager.get_job("job-a", owner_id="user-b") is None
    assert manager.get_job("job-a", owner_id="user-a")["owner_id"] == "user-a"

    history = manager.list_jobs(owner_id="user-a")
    assert history["total"] == 1
    assert [job["job_id"] for job in history["items"]] == ["job-a"]


def test_review_claim_cannot_be_submitted_by_another_owner(tmp_path):
    manager = make_manager(tmp_path)
    manager.create_job("review-job", "/repo", owner_id="owner-a")
    manager.update_job(
        "review-job",
        status="waiting_for_review",
        review_request={"patch": "example"},
    )

    assert not manager.claim_review(
        "review-job", decision="approved", owner_id="owner-b"
    )
    assert manager.get_job("review-job")["status"] == "waiting_for_review"
    assert manager.claim_review(
        "review-job", decision="approved", owner_id="owner-a"
    )


def test_publishing_retry_cannot_be_claimed_by_another_owner(tmp_path):
    manager = make_manager(tmp_path)
    manager.create_job(
        "publish-job", "/repo", publish_requested=True, owner_id="owner-a"
    )
    manager.update_job(
        "publish-job",
        status="completed",
        result={"publishing": {"status": "PUBLISH_FAILED"}},
    )

    assert not manager.claim_publishing_retry("publish-job", owner_id="owner-b")
    assert manager.claim_publishing_retry("publish-job", owner_id="owner-a")
