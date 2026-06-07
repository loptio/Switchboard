"""Offline tests for checkpoint.py (the testable part: URL normalization).

make_pg_checkpointer / run_setup need a real Postgres, so they are exercised in
the live E2E, not here. The SQLAlchemy→libpq URL transform is pure and testable.
"""

import checkpoint


def test_libpq_url_strips_sqlalchemy_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host:5432/agent")
    assert checkpoint._libpq_url() == "postgresql://u:p@host:5432/agent"


def test_libpq_url_passes_through_plain_form(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/agent")
    assert checkpoint._libpq_url() == "postgresql://u:p@host/agent"
