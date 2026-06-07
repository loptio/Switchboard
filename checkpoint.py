"""Checkpointer — the LangGraph checkpoint store (Phase 5 Unit 3, worker-only).

The checkpointer owns "graph execution state" for human-in-the-loop runs; the
`runs` table owns the business record (status/outputs). They live in the SAME
Postgres but in DIFFERENT tables (LangGraph's own `checkpoints*`), with NO foreign
key — correlated only by thread_id == run_id. Don't couple them.

WORKER-ONLY: this imports LangGraph's PostgresSaver and must never be imported by
the web tier (a test enforces no langgraph in the web process). PostgresSaver is
imported LAZILY (inside functions) so the digest default path and the offline
tests (which use InMemorySaver) never need the postgres extra.
"""

from __future__ import annotations

from contextlib import contextmanager

from db import settings


def _libpq_url() -> str:
    """DATABASE_URL in libpq form for psycopg / PostgresSaver.

    db.settings.database_url() returns the SQLAlchemy form
    (postgresql+psycopg://...); PostgresSaver.from_conn_string wants the plain
    libpq form (postgresql://...). Strip the driver suffix; a URL already in libpq
    form is returned unchanged.
    """
    return settings.database_url().replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def make_pg_checkpointer():
    """Yield an open PostgresSaver bound to the app database (same DB as `runs`).

    A context manager (from_conn_string opens a connection that must stay open for
    the duration of the invoke):

        with make_pg_checkpointer() as cp:
            orchestrator.start_review_run(..., checkpointer=cp)
    """
    from langgraph.checkpoint.postgres import PostgresSaver  # lazy: worker-only extra

    with PostgresSaver.from_conn_string(_libpq_url()) as cp:
        yield cp


def run_setup() -> None:
    """Create the checkpoint tables. Run ONCE as a migration step (e.g. the
    `checkpointer-setup` CLI) — NOT in the run path. PostgresSaver.setup() is
    idempotent (it tracks applied migrations), so re-running is safe.
    """
    with make_pg_checkpointer() as cp:
        cp.setup()
