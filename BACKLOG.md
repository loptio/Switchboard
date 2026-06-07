# Backlog — deferred items

Tracked-but-not-now items. Each says **why deferred** and **when to revisit**, so
they don't get silently lost or pulled into the wrong phase.

## Before Phase 3

- **Verify on real PostgreSQL.** Phase 2 Unit 1 (the DB layer) is so far verified
  only on in-memory SQLite (full offline test suite) plus an offline render of
  the Alembic migration as Postgres DDL (`alembic upgrade head --sql`). Before
  going to the cloud / actually running on PostgreSQL, take one real Postgres
  instance and run, against it:
  - `alembic upgrade head` (apply the migration for real), and
  - the full test suite with `TEST_DATABASE_URL` pointed at a throwaway DB
    (`pytest tests/test_db.py`), to catch any SQLite↔Postgres dialect gaps.
  - _Why deferred:_ no local Postgres yet; Phase 2 is a local long-running
    process. _Revisit:_ at the start of Phase 3 (cloud hosting), before relying
    on Postgres in anger.

## Phase 3 refactor

- **Rename / restructure the `news_digest/` folder.** It started as the Phase 1
  project but is now the system backend (DB, scheduler, email, and later the
  FastAPI control plane). The name no longer fits.
  - _Why deferred:_ renaming now is pure churn and risks breaking imports/tests
    mid-build for no functional gain. Do it once, deliberately, as part of the
    Phase 3 backend restructure (e.g. a `backend/` package layout).
  - _Revisit:_ Phase 3, alongside introducing the FastAPI control plane.
