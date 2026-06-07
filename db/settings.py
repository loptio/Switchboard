"""DB connection settings — URLs come from env vars, never from code/Git.

Same rule as Phase 1's config.py: load ONLY this project's .env, never walk up
to a parent directory's .env (which holds unrelated API keys, and this project
must never see ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# This file is news_digest/db/settings.py, so the project root is two levels up.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# In-memory SQLite: the offline default for tests — needs no running service.
DEFAULT_TEST_DATABASE_URL = "sqlite://"


def database_url() -> str:
    """The runtime database URL (SQLAlchemy form). Required.

    Credentials live in the environment only, e.g.
        postgresql+psycopg://user:pass@localhost:5432/agent
    """
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at your PostgreSQL database, e.g.\n"
            "  export DATABASE_URL='postgresql+psycopg://user:pass@localhost:5432/agent'\n"
            "See .env.example. Credentials go in env / .env — never in code or Git."
        )
    return url


def test_database_url() -> str:
    """Database URL for the test suite.

    Defaults to in-memory SQLite (fully offline, no service). Set
    TEST_DATABASE_URL to a throwaway PostgreSQL database to run the same suite
    against real Postgres for dialect fidelity.
    """
    return (os.getenv("TEST_DATABASE_URL") or "").strip() or DEFAULT_TEST_DATABASE_URL
