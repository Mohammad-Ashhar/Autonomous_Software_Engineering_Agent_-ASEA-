"""Select durable repair-job storage without changing local SQLite mode."""
import os
from threading import Lock

from asea.api.job_manager import RepairJobManager


class ConfiguredRepairJobManager:
    """Lazily select PostgreSQL when configured, otherwise use local SQLite."""

    def __init__(self):
        self._manager = None
        self._lock = Lock()

    @property
    def backend(self) -> str:
        return "postgresql" if os.getenv("ASEA_DATABASE_URL", "").strip() else "sqlite"

    def _get_manager(self):
        if self._manager is None:
            with self._lock:
                if self._manager is None:
                    if self.backend == "postgresql":
                        from asea.api.postgres_job_manager import (
                            PostgresRepairJobManager,
                        )

                        self._manager = PostgresRepairJobManager()
                    else:
                        self._manager = RepairJobManager()
        return self._manager

    def __getattr__(self, name):
        return getattr(self._get_manager(), name)


repair_jobs = ConfiguredRepairJobManager()
