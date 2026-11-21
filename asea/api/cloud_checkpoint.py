"""Durable LangGraph checkpoint storage for the ASEA cloud worker."""

from contextlib import contextmanager
from os import getenv
from typing import Iterator
from urllib.parse import parse_qs, urlparse


def validate_checkpoint_database_url(database_url: str) -> str:
    """Require a PostgreSQL URL with TLS before opening checkpoint storage."""
    if not isinstance(database_url, str) or not database_url.strip():
        raise ValueError("ASEA_DATABASE_URL must be configured.")
    parsed = urlparse(database_url.strip())
    query = parse_qs(parsed.query)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not parsed.path.strip("/")
        or query.get("sslmode", [""])[0]
        not in {"require", "verify-ca", "verify-full"}
    ):
        raise ValueError(
            "ASEA_DATABASE_URL must be a PostgreSQL URL with sslmode=require "
            "or stronger."
        )
    return database_url.strip()


def _postgres_saver_class():
    """Load the optional PostgreSQL checkpointer only when cloud mode is used."""
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as error:
        raise RuntimeError(
            'Install cloud-worker dependencies with "pip install -e .[cloud-worker]".'
        ) from error
    return PostgresSaver


@contextmanager
def open_cloud_checkpointer(
    database_url: str | None = None,
) -> Iterator[object]:
    """Open and initialize a shared PostgreSQL-backed LangGraph checkpointer.

    The context must remain open for the lifetime of graph execution. Calling
    setup() is idempotent and creates/updates the official checkpoint tables.
    """
    configured_url = database_url or getenv("ASEA_DATABASE_URL", "")
    validated_url = validate_checkpoint_database_url(configured_url)
    saver_class = _postgres_saver_class()
    with saver_class.from_conn_string(validated_url) as checkpointer:
        checkpointer.setup()
        yield checkpointer
