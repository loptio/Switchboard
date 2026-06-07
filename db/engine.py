"""Engine lifecycle — one configurable SQLAlchemy engine for the data layer.

Core, not ORM: the DAO runs explicit statements inside `engine.begin()`
transactions and returns dataclasses. Tests point the engine at an in-memory
SQLite database; runtime points it at PostgreSQL via DATABASE_URL.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from . import settings
from .models import metadata

_engine: Engine | None = None

# In-memory SQLite forms that need a single shared connection.
_SQLITE_MEMORY = ("sqlite://", "sqlite:///:memory:")


def _make_engine(url: str) -> Engine:
    if url in _SQLITE_MEMORY:
        # One shared in-memory database across all connections; the default pool
        # would hand each connection its own empty database.
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(url, pool_pre_ping=True)


def configure(url: str) -> Engine:
    """(Re)create the engine bound to `url`, disposing any previous one."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = _make_engine(url)
    return _engine


def get_engine() -> Engine:
    """Return the configured engine, lazily creating one from DATABASE_URL."""
    global _engine
    if _engine is None:
        _engine = _make_engine(settings.database_url())
    return _engine


def init_db(url: str | None = None) -> Engine:
    """Create all tables. Used by tests and handy for a fresh local DB.

    Production should apply Alembic migrations (`alembic upgrade head`) instead.
    """
    engine = configure(url) if url is not None else get_engine()
    metadata.create_all(engine)
    return engine


def drop_db() -> None:
    """Drop all tables — tests/teardown only."""
    metadata.drop_all(get_engine())
