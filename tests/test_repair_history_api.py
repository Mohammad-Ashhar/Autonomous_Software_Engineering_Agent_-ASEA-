import pytest
from fastapi import HTTPException

from asea.api import main


class FakeRepairJobs:
    def list_jobs(self, limit=50, offset=0, status=None):
        return {
            "items": [{"job_id": "job-1", "status": status or "queued"}],
            "total": 1,
            "limit": limit,
            "offset": offset,
        }


def test_list_repair_jobs_returns_paginated_history(monkeypatch):
    monkeypatch.setattr(main, "repair_jobs", FakeRepairJobs())

    result = main.list_repair_jobs(limit=10, offset=5, status="completed")

    assert result["total"] == 1
    assert result["limit"] == 10
    assert result["offset"] == 5
    assert result["items"][0]["status"] == "completed"


@pytest.mark.parametrize(
    ("limit", "offset", "status"),
    [
        (0, 0, None),
        (101, 0, None),
        (10, -1, None),
        (10, 0, "unknown"),
    ],
)
def test_list_repair_jobs_rejects_invalid_filters(limit, offset, status):
    with pytest.raises(HTTPException) as error:
        main.list_repair_jobs(limit=limit, offset=offset, status=status)

    assert error.value.status_code == 400
