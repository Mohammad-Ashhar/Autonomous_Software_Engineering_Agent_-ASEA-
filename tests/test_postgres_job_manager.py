from types import SimpleNamespace

import pytest

from asea.api.postgres_job_manager import (
    PostgresRepairJobManager,
    _hybrid_row_factory,
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


def test_hybrid_row_factory_handles_commands_without_result_columns():
    cursor = SimpleNamespace(description=None)

    row_factory = _hybrid_row_factory(cursor)

    assert row_factory(("ignored",)) == ("ignored",)


def test_hybrid_row_factory_preserves_mapping_and_positional_access():
    cursor = SimpleNamespace(
        description=[SimpleNamespace(name="job_id"), SimpleNamespace(name="status")]
    )

    row_factory = _hybrid_row_factory(cursor)
    row = row_factory(("job-1", "queued"))

    assert row["job_id"] == "job-1"
    assert row[0] == "job-1"
    assert row[1] == "queued"


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


def test_postgres_jsonb_deserializer_preserves_decoded_string_scalars():
    assert PostgresRepairJobManager._deserialize("plain error message") == (
        "plain error message"
    )


def test_postgres_jsonb_deserializer_decodes_json_text():
    assert PostgresRepairJobManager._deserialize('{"status": "failed"}') == {
        "status": "failed"
    }



def test_per_user_active_job_limit_configuration_is_shared_with_postgres_manager(
    monkeypatch,
):
    monkeypatch.setenv("ASEA_MAX_ACTIVE_REPAIR_JOBS_PER_USER", "7")
    assert PostgresRepairJobManager._maximum_active_jobs_per_user() == 7
