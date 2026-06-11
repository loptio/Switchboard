# Backlog — deferred items

Tracked-but-not-now items. Each says **why deferred** and **when to revisit**, so
they don't get silently lost or pulled into the wrong phase.

## Before Phase 3

- **Verify on real PostgreSQL — DONE (2026-06-07).** Ran `alembic upgrade head`
  against real Postgres 16 (Docker): schema correct (uuid / jsonb / timestamptz /
  CHECK / FK ON DELETE CASCADE / indexes). Ran the full suite against a throwaway
  DB via `TEST_DATABASE_URL`: 61 passed. This caught and fixed a real dialect gap
  — a malformed (non-UUID) id raised a psycopg `DataError` on the native `uuid`
  column instead of a clean not-found (fixed by validating ids at the data-layer
  boundary). Remaining real-env check is the **live end-to-end** (`cli.py
  run-once` with a real Claude subscription + real SMTP), which is the user's to
  run on their machine.

## Phase 3 refactor

- **Rename / restructure the `news_digest/` folder.** It started as the Phase 1
  project but is now the system backend (DB, scheduler, email, and later the
  FastAPI control plane). The name no longer fits.
  - _Why deferred:_ renaming now is pure churn and risks breaking imports/tests
    mid-build for no functional gain. Do it once, deliberately, as part of the
    Phase 3 backend restructure (e.g. a `backend/` package layout).
  - _Revisit:_ Phase 3, alongside introducing the FastAPI control plane.

## Monitoring / observability

- **Email delivery is a blind spot. — DONE (Phase 11, 2026-06-12).** The delivery
  outcome (sent / skipped / failed) is now persisted onto `runs.meta` and shown as
  an "Email" badge on the run detail page. The run still stays `success` on a
  delivery failure (graceful degradation unchanged) — but the failure is now
  *visible* instead of buried in logs. (Caught a real `Connection refused` to the
  SMTP server live, the moment it shipped.)

- **Digest quality is logged but not persisted. — DONE (Phase 11, 2026-06-12).**
  The verdict (passed / accepted_at_cap / inconclusive / human_approved) is now
  persisted onto `runs.meta` (build_digest_with_verdict → runner._finalize →
  set_run_meta) and shown as a "Quality" badge on the run detail page.

## Checkpointer / orchestration (Phase 5)

- **Completed-run checkpoints are never garbage-collected.** A human-in-the-loop
  run leaves its LangGraph checkpoint rows (`checkpoints*` tables) behind after it
  finishes (Unit 3 doesn't delete them). Harmless at low volume, but the tables
  grow unbounded over time. When it matters, add a cleanup step (delete a thread's
  checkpoints once its run reaches a terminal state, or a periodic sweep of
  checkpoints whose run is success/failed).
  - _Why deferred:_ not needed for the primitive; correctness doesn't depend on it,
    and premature GC risks deleting state a resume still needs.
  - _Revisit:_ when human-in-the-loop runs are used in volume (or when the
    monitoring unit touches run lifecycle anyway).
