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

- **Email delivery is a blind spot.** A failed email is only logged and the Run
  still shows `success` (the digest is saved — graceful degradation by design,
  Units 2/3). Nothing records whether delivery actually happened. When delivery
  confirmation matters, persist a delivery status onto the Run (or a future
  Event row) so `success` reflects end-to-end delivery, not just that the digest
  was produced.
  - _Revisit:_ when email reliability becomes operationally important (after
    Unit 3, before relying on the push in anger).

- **Digest quality is logged but not persisted.** The orchestrator's review
  verdict (passed / accepted-at-cap-with-open-issues / verification-inconclusive)
  only goes to the run log (Phase 5 Unit 1). Persist it onto the Run (or a future
  Event row) so the control-plane UI can surface digest *quality*, not just
  success/failed.
  - _Revisit:_ the monitoring/observability unit (same place as the email
    delivery-status item above).

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
