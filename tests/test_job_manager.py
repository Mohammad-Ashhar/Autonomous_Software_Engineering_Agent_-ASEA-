from pathlib import Path

import pytest

from asea.api.job_manager import JobQueueFullError, RepairJobManager


def create_test_manager(db_path: Path) -> RepairJobManager:
    """Create a job manager using a temporary test database."""

    manager = RepairJobManager()
    manager.db_path = db_path
    manager._initialize_database()

    return manager


def test_create_and_retrieve_job(tmp_path):
    manager = create_test_manager(tmp_path / "test_jobs.db")

    job = manager.create_job("test-001", "demo_repo")

    assert job["job_id"] == "test-001"
    assert job["repository"] == "demo_repo"
    assert job["status"] == "queued"

    retrieved_job = manager.get_job("test-001")

    assert retrieved_job is not None
    assert retrieved_job["job_id"] == "test-001"
    assert retrieved_job["status"] == "queued"


def test_update_job(tmp_path):
    manager = create_test_manager(tmp_path / "test_jobs.db")

    manager.create_job("test-002", "demo_repo")

    updated_job = manager.update_job(
        "test-002",
        status="completed",
        message="Tests passed successfully",
        result={"tests_passed": 6},
    )

    assert updated_job["status"] == "completed"
    assert updated_job["message"] == "Tests passed successfully"
    assert updated_job["result"] == {"tests_passed": 6}


def test_job_persistence_after_new_manager(tmp_path):
    db_path = tmp_path / "persistent_jobs.db"

    manager1 = create_test_manager(db_path)

    manager1.create_job("test-003", "demo_repo")

    manager1.update_job(
        "test-003",
        status="completed",
        result={"tests_passed": 6},
    )

    manager2 = create_test_manager(db_path)

    recovered_job = manager2.get_job("test-003")

    assert recovered_job is not None
    assert recovered_job["status"] == "completed"
    assert recovered_job["result"] == {"tests_passed": 6}


def test_get_nonexistent_job(tmp_path):
    manager = create_test_manager(tmp_path / "test_jobs.db")

    assert manager.get_job("unknown-job") is None

def test_list_jobs_returns_newest_first_with_pagination(tmp_path):
    manager = create_test_manager(tmp_path / "history.db")
    manager.create_job("history-001", "repo-one")
    manager.create_job("history-002", "repo-two")
    manager.create_job("history-003", "repo-three")

    page = manager.list_jobs(limit=2, offset=0)

    assert page["total"] == 3
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert len(page["items"]) == 2
    assert [job["job_id"] for job in page["items"]] == [
        "history-003",
        "history-002",
    ]

    second_page = manager.list_jobs(limit=2, offset=2)
    assert [job["job_id"] for job in second_page["items"]] == ["history-001"]


def test_list_jobs_filters_by_status(tmp_path):
    manager = create_test_manager(tmp_path / "history_status.db")
    manager.create_job("queued-001", "repo-one")
    manager.create_job("done-001", "repo-two")
    manager.update_job("done-001", status="completed", result={"ok": True})

    page = manager.list_jobs(status="completed")

    assert page["total"] == 1
    assert page["items"][0]["job_id"] == "done-001"
    assert page["items"][0]["result"] == {"ok": True}


def test_list_jobs_validates_pagination(tmp_path):
    manager = create_test_manager(tmp_path / "history_validation.db")

    import pytest

    with pytest.raises(ValueError, match="limit"):
        manager.list_jobs(limit=0)
    with pytest.raises(ValueError, match="offset"):
        manager.list_jobs(offset=-1)



def test_publish_preference_persists_across_manager_restart(tmp_path):
    db_path = tmp_path / "publish_preference.db"
    manager1 = create_test_manager(db_path)

    manager1.create_job(
        "publish-001",
        "demo_repo",
        publish_requested=True,
    )

    manager2 = create_test_manager(db_path)
    recovered = manager2.get_job("publish-001")

    assert recovered["publish_requested"] is True


def test_existing_database_is_migrated_with_publish_preference(tmp_path):
    import sqlite3

    db_path = tmp_path / "legacy_jobs.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE repair_jobs (
                job_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                review_request TEXT,
                result TEXT,
                error TEXT
            )
            """
        )

    manager = create_test_manager(db_path)
    job = manager.create_job("legacy-001", "demo_repo")

    assert job["publish_requested"] is False
    assert manager.get_job("legacy-001")["publish_requested"] is False


def test_restart_recovery_fails_only_queued_and_running_jobs(tmp_path):
    manager = create_test_manager(tmp_path / "recovery.db")
    manager.create_job("queued-001", "repo-one")
    manager.create_job("running-001", "repo-two")
    manager.create_job("review-001", "repo-three")
    manager.create_job("done-001", "repo-four")
    manager.update_job("running-001", status="running")
    manager.update_job("review-001", status="waiting_for_review")
    manager.update_job("done-001", status="completed")

    recovered_count = manager.mark_interrupted_jobs()

    assert recovered_count == 2
    assert manager.get_job("queued-001")["status"] == "failed"
    assert manager.get_job("running-001")["status"] == "failed"
    assert manager.get_job("review-001")["status"] == "waiting_for_review"
    assert manager.get_job("done-001")["status"] == "completed"


def test_claim_review_allows_only_one_submission(tmp_path):
    manager = create_test_manager(tmp_path / "review_claim.db")
    manager.create_job("review-claim-001", "demo_repo")
    manager.update_job(
        "review-claim-001",
        status="waiting_for_review",
        review_request={"patch": "diff"},
    )

    assert manager.claim_review("review-claim-001") is True
    assert manager.claim_review("review-claim-001") is False

    job = manager.get_job("review-claim-001")
    assert job["status"] == "running"
    assert job["review_request"] == {"patch": "diff"}


def test_release_review_claim_restores_waiting_state_and_checkpoint(tmp_path):
    manager = create_test_manager(tmp_path / "review_release.db")
    manager.create_job("review-release-001", "demo_repo")
    request = {"patch": "diff", "summary": "candidate"}
    manager.update_job(
        "review-release-001",
        status="waiting_for_review",
        review_request=request,
    )

    assert manager.claim_review("review-release-001") is True
    claimed = manager.get_job("review-release-001")
    assert claimed["status"] == "running"
    assert claimed["review_request"] == request

    assert manager.release_review_claim("review-release-001") is True
    assert manager.release_review_claim("review-release-001") is False
    restored = manager.get_job("review-release-001")
    assert restored["status"] == "waiting_for_review"
    assert restored["review_request"] == request



def test_sqlite_uses_wal_and_query_indexes(tmp_path):
    manager = create_test_manager(tmp_path / "wal.db")
    with manager._connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        indexes = {
            row["name"]
            for row in connection.execute(
                "PRAGMA index_list(repair_jobs)"
            ).fetchall()
        }
    assert "idx_repair_jobs_created" in indexes
    assert "idx_repair_jobs_status_created" in indexes


def test_database_busy_timeout_is_configurable_and_bounded(monkeypatch):
    monkeypatch.setenv("ASEA_SQLITE_BUSY_TIMEOUT_SECONDS", "7.5")
    assert RepairJobManager._database_timeout() == 7.5
    monkeypatch.setenv("ASEA_SQLITE_BUSY_TIMEOUT_SECONDS", "999")
    assert RepairJobManager._database_timeout() == 120.0
    monkeypatch.setenv("ASEA_SQLITE_BUSY_TIMEOUT_SECONDS", "invalid")
    assert RepairJobManager._database_timeout() == 30.0


def test_create_job_validates_identifier_and_repository(tmp_path):
    import pytest

    manager = create_test_manager(tmp_path / "input_validation.db")
    with pytest.raises(ValueError, match="job_id"):
        manager.create_job("", "repo")
    with pytest.raises(ValueError, match="repository"):
        manager.create_job("valid-id", "")


def test_update_job_rejects_unknown_status_and_oversized_message(tmp_path):
    import pytest

    manager = create_test_manager(tmp_path / "update_validation.db")
    manager.create_job("validation-001", "repo")
    with pytest.raises(ValueError, match="status"):
        manager.update_job("validation-001", status="mystery")
    with pytest.raises(ValueError, match="message"):
        manager.update_job("validation-001", message="x" * 2001)


def test_connection_context_closes_sqlite_handle(tmp_path):
    manager = create_test_manager(tmp_path / "close_connection.db")
    with manager._connection() as connection:
        connection.execute("SELECT 1")
    import pytest
    with pytest.raises(Exception, match="closed"):
        connection.execute("SELECT 1")


def test_connection_context_rolls_back_failed_transaction(tmp_path):
    import pytest

    manager = create_test_manager(tmp_path / "rollback.db")
    with pytest.raises(RuntimeError):
        with manager._connection() as connection:
            connection.execute(
                "INSERT INTO repair_jobs "
                "(job_id, repository, status, message, created_at, updated_at) "
                "VALUES ('rollback-001', 'repo', 'queued', 'test', 'now', 'now')"
            )
            raise RuntimeError("force rollback")
    assert manager.get_job("rollback-001") is None


def test_active_job_admission_limit_is_enforced(tmp_path, monkeypatch):
    manager = create_test_manager(tmp_path / "queue_limit.db")
    monkeypatch.setenv("ASEA_MAX_ACTIVE_REPAIR_JOBS", "1")
    manager.create_job("active-001", "repo-one")
    with pytest.raises(JobQueueFullError):
        manager.create_job("active-002", "repo-two")
    manager.update_job("active-001", status="completed")
    assert manager.create_job("active-003", "repo-three")["status"] == "queued"


def test_active_job_limit_is_bounded(monkeypatch):
    monkeypatch.setenv("ASEA_MAX_ACTIVE_REPAIR_JOBS", "999999")
    assert RepairJobManager._maximum_active_jobs() == 500
    monkeypatch.setenv("ASEA_MAX_ACTIVE_REPAIR_JOBS", "invalid")
    assert RepairJobManager._maximum_active_jobs() == 20
    monkeypatch.setenv("ASEA_MAX_ACTIVE_REPAIR_JOBS", "0")
    assert RepairJobManager._maximum_active_jobs() == 1


def test_prune_terminal_jobs_preserves_active_and_review_jobs(tmp_path):
    manager = create_test_manager(tmp_path / "retention.db")
    for job_id in ("queued-retain", "review-retain", "done-prune", "failed-prune"):
        manager.create_job(job_id, "repo")
    manager.update_job("review-retain", status="waiting_for_review")
    manager.update_job("done-prune", status="completed")
    manager.update_job("failed-prune", status="failed")
    with manager._connection() as connection:
        connection.execute(
            "UPDATE repair_jobs SET updated_at = '2000-01-01T00:00:00+00:00' "
            "WHERE job_id IN ('done-prune', 'failed-prune')"
        )
    assert manager.prune_terminal_jobs(retention_days=30) == 2
    assert manager.get_job("done-prune") is None
    assert manager.get_job("failed-prune") is None
    assert manager.get_job("queued-retain") is not None
    assert manager.get_job("review-retain") is not None


def test_prune_terminal_jobs_validates_retention_window(tmp_path):
    manager = create_test_manager(tmp_path / "retention_validation.db")
    with pytest.raises(ValueError, match="retention_days"):
        manager.prune_terminal_jobs(retention_days=0)
