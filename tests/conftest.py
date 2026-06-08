"""Shared fixtures for the data-layer tests.

Offline by default: a fresh in-memory SQLite database per test, so the suite
needs no running service and stays deterministic. Set TEST_DATABASE_URL to a
throwaway PostgreSQL database to run the very same tests against real Postgres.
"""

import subprocess

import pytest

import db
from db import settings


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A hermetic temp git repo with one commit (Phase 10b-1 git-aware coding tests).

    Offline + deterministic: no SDK, no network, no spend — just local git. Global and
    system git config are neutralised and the commit identity is pinned via env, so the
    repo behaves identically regardless of the developer's machine. The SAME env is
    inherited by the worker's git calls (workspace.git_diff / git_restore / is_git_repo)
    since they run in this process. Returns the repo root Path.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "no-system-gitconfig"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    return repo


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
