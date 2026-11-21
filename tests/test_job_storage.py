import sys
from types import SimpleNamespace

from asea.api import job_storage


def test_storage_defaults_to_sqlite_without_database_url(monkeypatch):
    monkeypatch.delenv("ASEA_DATABASE_URL", raising=False)

    storage = job_storage.ConfiguredRepairJobManager()

    assert storage.backend == "sqlite"


def test_storage_selects_postgres_only_when_database_url_is_configured(
    monkeypatch,
):
    monkeypatch.setenv(
        "ASEA_DATABASE_URL",
        "postgresql://asea:secret@db.example.com/asea?sslmode=require",
    )

    storage = job_storage.ConfiguredRepairJobManager()

    assert storage.backend == "postgresql"


def test_sqlite_manager_is_created_lazily_and_reused(monkeypatch):
    instances = []

    class FakeManager:
        def __init__(self):
            instances.append(self)

        def check_database(self):
            return "ready"

    monkeypatch.delenv("ASEA_DATABASE_URL", raising=False)
    monkeypatch.setattr(job_storage, "RepairJobManager", FakeManager)
    storage = job_storage.ConfiguredRepairJobManager()

    assert instances == []
    assert storage.check_database() == "ready"
    assert storage.check_database() == "ready"
    assert len(instances) == 1


def test_postgres_manager_is_selected_lazily_and_reused(monkeypatch):
    instances = []

    class FakePostgresManager:
        def __init__(self):
            instances.append(self)

        def check_database(self):
            return "postgres-ready"

    monkeypatch.setenv(
        "ASEA_DATABASE_URL",
        "postgresql://asea:secret@db.example.com/asea?sslmode=require",
    )
    monkeypatch.setitem(
        sys.modules,
        "asea.api.postgres_job_manager",
        SimpleNamespace(PostgresRepairJobManager=FakePostgresManager),
    )
    storage = job_storage.ConfiguredRepairJobManager()

    assert instances == []
    assert storage.check_database() == "postgres-ready"
    assert storage.check_database() == "postgres-ready"
    assert len(instances) == 1
