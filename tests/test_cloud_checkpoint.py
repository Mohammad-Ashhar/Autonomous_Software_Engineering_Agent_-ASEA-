from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from asea.api.cloud_checkpoint import (
    open_cloud_checkpointer,
    validate_checkpoint_database_url,
)


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "sqlite:///asea.db",
        "postgresql://db.example.com/asea",
        "postgresql://user:pass@db.example.com/asea?sslmode=disable",
        "postgresql://user:pass@db.example.com/asea?sslmode=prefer",
    ],
)
def test_checkpoint_database_requires_postgres_tls(database_url):
    with pytest.raises(ValueError):
        validate_checkpoint_database_url(database_url)


@pytest.mark.parametrize(
    "sslmode",
    ["require", "verify-ca", "verify-full"],
)
def test_checkpoint_database_accepts_secure_postgres_url(sslmode):
    url = f"postgresql://user:pass@db.example.com/asea?sslmode={sslmode}"
    assert validate_checkpoint_database_url(url) == url


def test_open_cloud_checkpointer_initializes_and_closes(monkeypatch):
    saver = MagicMock()
    events = []

    @contextmanager
    def connection_context(url):
        events.append(("open", url))
        yield saver
        events.append(("close", url))

    saver_class = MagicMock()
    saver_class.from_conn_string.side_effect = connection_context
    monkeypatch.setattr(
        "asea.api.cloud_checkpoint._postgres_saver_class",
        lambda: saver_class,
    )

    url = "postgresql://user:pass@db.example.com/asea?sslmode=require"
    with open_cloud_checkpointer(url) as checkpointer:
        assert checkpointer is saver
        saver.setup.assert_called_once_with()
        assert events == [("open", url)]

    assert events == [("open", url), ("close", url)]


def test_open_cloud_checkpointer_requires_optional_dependency(monkeypatch):
    monkeypatch.setattr(
        "asea.api.cloud_checkpoint._postgres_saver_class",
        lambda: (_ for _ in ()).throw(RuntimeError("optional dependency missing")),
    )
    with pytest.raises(RuntimeError, match="optional dependency missing"):
        with open_cloud_checkpointer(
            "postgresql://user:pass@db.example.com/asea?sslmode=require"
        ):
            pass
