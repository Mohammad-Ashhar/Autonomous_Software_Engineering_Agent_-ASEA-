from pathlib import Path

from asea.api.job_manager import RepairJobManager


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
