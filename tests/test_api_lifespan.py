import asyncio
from contextlib import contextmanager

from asea.api import main


class FakeExecutor:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.shutdown_options = None

    def shutdown(self, **kwargs):
        self.shutdown_options = kwargs


class FakeCheckpointer:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def setup(self):
        pass


class FakeSqliteSaver:
    @staticmethod
    def from_conn_string(_path):
        return FakeCheckpointer()


def test_lifespan_creates_fresh_executor_and_drains_it_on_shutdown(
    monkeypatch, tmp_path
):
    executor_instances = []

    def make_executor(**kwargs):
        executor = FakeExecutor(**kwargs)
        executor_instances.append(executor)
        return executor

    monkeypatch.setattr(main, "ThreadPoolExecutor", make_executor)
    monkeypatch.setattr(main, "SqliteSaver", FakeSqliteSaver)
    monkeypatch.setattr(main, "CHECKPOINT_DB_PATH", tmp_path / "checkpoints.db")
    monkeypatch.setattr(main.repair_jobs, "mark_interrupted_jobs", lambda: 0)
    monkeypatch.setattr(main, "build_asea_graph", lambda checkpointer: object())

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            assert main.executor is executor_instances[0]
            assert executor_instances[0].options == {"max_workers": 1}
            assert executor_instances[0].shutdown_options is None

        assert executor_instances[0].shutdown_options == {
            "wait": True,
            "cancel_futures": True,
        }

    asyncio.run(exercise_lifespan())


def test_lifespan_replaces_executor_on_each_startup(monkeypatch, tmp_path):
    executor_instances = []

    def make_executor(**kwargs):
        executor = FakeExecutor(**kwargs)
        executor_instances.append(executor)
        return executor

    monkeypatch.setattr(main, "ThreadPoolExecutor", make_executor)
    monkeypatch.setattr(main, "SqliteSaver", FakeSqliteSaver)
    monkeypatch.setattr(main, "CHECKPOINT_DB_PATH", tmp_path / "checkpoints.db")
    monkeypatch.setattr(main.repair_jobs, "mark_interrupted_jobs", lambda: 0)
    monkeypatch.setattr(main, "build_asea_graph", lambda checkpointer: object())

    async def exercise_two_lifespans():
        async with main.lifespan(main.app):
            first_executor = main.executor
        async with main.lifespan(main.app):
            second_executor = main.executor

        assert first_executor is not second_executor
        assert len(executor_instances) == 2
        assert all(
            executor.shutdown_options == {
                "wait": True,
                "cancel_futures": True,
            }
            for executor in executor_instances
        )

    asyncio.run(exercise_two_lifespans())
