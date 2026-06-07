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


# --- API fixtures (Phase 3) -----------------------------------------------

TEST_PASSWORD = "s3cret-pw"


@pytest.fixture
def api_settings():
    """Fixed, offline API settings — a known secret, no Secure flag, no CORS."""
    from api.settings import APISettings

    return APISettings(secret_key="test-secret-key", cookie_secure=False)


@pytest.fixture
def client(database, api_settings):
    """A TestClient over a freshly built app sharing the in-memory test DB."""
    from fastapi.testclient import TestClient

    from api.app import create_app

    with TestClient(create_app(api_settings)) as c:
        yield c


@pytest.fixture
def user(database):
    """The single login user, with a real bcrypt hash of TEST_PASSWORD."""
    from api.security import hash_password

    return db.create_user("admin", hash_password(TEST_PASSWORD))


def login(client, username="admin", password=TEST_PASSWORD):
    """Log a client in; returns the response. Leaves session + CSRF cookies set."""
    return client.post("/auth/login", json={"username": username, "password": password})


def csrf_headers(client):
    """The header a logged-in client must send on state-changing requests."""
    return {"X-CSRF-Token": client.cookies["csrftoken"]}
