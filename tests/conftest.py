"""Shared fixtures for the data-layer tests.

Offline by default: a fresh in-memory SQLite database per test, so the suite
needs no running service and stays deterministic. Set TEST_DATABASE_URL to a
throwaway PostgreSQL database to run the very same tests against real Postgres.
"""

import pytest

import db
from db import settings


@pytest.fixture
def database():
    """Build a clean schema for one test, then drop it.

    Yields nothing — tests call the `db` data-access functions, which use the
    engine this fixture configured.
    """
    db.init_db(settings.test_database_url())  # configure engine + create tables
    try:
        yield
    finally:
        db.drop_db()
        db.get_engine().dispose()
