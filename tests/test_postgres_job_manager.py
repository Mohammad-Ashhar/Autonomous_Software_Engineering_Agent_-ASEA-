import pytest

from asea.api.postgres_job_manager import (
    PostgresRepairJobManager,
    _PostgresConnection,
)


class FakeConnection:
    def __init__(self):
        self.statement = None
        self.parameters = None

    def execute(self, statement, parameters=()):
        self.statement = statement
        self.parameters = parameters
        return "executed"


def test_postgres_connection_translates_qmark_and_job_table():
    raw = FakeConnection()
    connection = _PostgresConnection(raw)

    result = connection.execute(
        "UPDATE repair_jobs SET status = ? WHERE job_id = ?",
        ("running", "job-1"),
    )

    assert result == "executed"
    assert raw.statement == (
        "UPDATE public.asea_repair_jobs SET status = %s WHERE job_id = %s"
    )
    assert raw.parameters == ("running", "job-1")


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///local.db",
        "postgresql://user:password@db.example.com/asea",
        "postgresql://db.example.com/asea?sslmode=disable",
        "postgresql:///asea?sslmode=require",
    ],
)
def test_postgres_manager_rejects_unsafe_or_invalid_database_urls(database_url):
    with pytest.raises(RuntimeError, match="ASEA_DATABASE_URL"):
        PostgresRepairJobManager(database_url)


def test_postgres_manager_requires_database_url(monkeypatch):
    monkeypatch.delenv("ASEA_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="ASEA_DATABASE_URL"):
        PostgresRepairJobManager()
