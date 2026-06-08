"""Offline tests for the data-access layer (no network, no real Postgres).

Runs against an in-memory SQLite database (see conftest.py). Exercises every
public DAO function, the validation/error paths, and the time-comparison logic
in list_due_schedules using injected timestamps (no time-mocking library).
"""

from datetime import datetime, timedelta, timezone

import pytest

import db

# A fixed, tz-aware reference time so tests are deterministic.
T0 = datetime(2026, 6, 7, 6, 0, 0, tzinfo=timezone.utc)


# --- Runs ------------------------------------------------------------------

def test_create_run_defaults(database):
    run = db.create_run(now=T0)
    assert run.workflow == "news"
    assert run.status == "pending"
    assert run.trigger == "manual"
    assert run.created_at == T0
    assert run.started_at is None and run.finished_at is None and run.error is None
    # round-trips through the DB
    assert db.get_run(run.id) == run


def test_create_run_brief_workflow(database):
    # the workflow selector (Phase 6) stores 'brief' alongside the default 'news'
    run = db.create_run(workflow="brief", now=T0)
    assert run.workflow == "brief"
    assert db.get_run(run.id).workflow == "brief"
    assert [r.id for r in db.list_runs(workflow="brief")] == [run.id]


def test_create_run_rejects_bad_trigger(database):
    with pytest.raises(ValueError):
        db.create_run(trigger="cron")


def test_get_run_missing_returns_none(database):
    assert db.get_run("00000000-0000-0000-0000-000000000000") is None


def test_update_run_status_lifecycle(database):
    run = db.create_run(trigger="scheduled", now=T0)
    started = T0 + timedelta(seconds=1)
    running = db.update_run_status(run.id, "running", started_at=started)
    assert running.status == "running"
    assert running.started_at == started
    assert running.finished_at is None

    finished = T0 + timedelta(seconds=5)
    done = db.update_run_status(run.id, "success", finished_at=finished)
    assert done.status == "success"
    # started_at is untouched by the second update
    assert done.started_at == started
    assert done.finished_at == finished


def test_update_run_status_rejects_bad_status(database):
    run = db.create_run()
    with pytest.raises(ValueError):
        db.update_run_status(run.id, "done")


def test_update_run_status_missing_run(database):
    with pytest.raises(LookupError):
        db.update_run_status("nope", "running")


def test_mark_helpers(database):
    run = db.create_run(now=T0)
    db.mark_running(run.id, now=T0)
    failed = db.mark_failed(run.id, "boom", now=T0 + timedelta(seconds=2))
    assert failed.status == "failed"
    assert failed.error == "boom"
    assert failed.finished_at == T0 + timedelta(seconds=2)

    run2 = db.create_run(now=T0)
    ok = db.mark_success(run2.id, now=T0 + timedelta(seconds=3))
    assert ok.status == "success" and ok.finished_at == T0 + timedelta(seconds=3)


def test_awaiting_input_in_statuses():
    assert "awaiting_input" in db.RUN_STATUSES


def test_awaiting_input_suspend_resume_lifecycle(database):
    # Human-in-the-loop: running -> awaiting_input (suspend) -> running (resume).
    run = db.create_run(now=T0)
    started = T0 + timedelta(seconds=1)
    db.update_run_status(run.id, "running", started_at=started)

    suspended = db.mark_awaiting_input(run.id)
    assert suspended.status == "awaiting_input"
    assert suspended.started_at == started  # unchanged
    assert suspended.finished_at is None  # not terminal

    resumed = db.update_run_status(run.id, "running")
    assert resumed.status == "running"
    assert resumed.started_at == started  # still the original start
    done = db.mark_success(run.id, now=T0 + timedelta(seconds=9))
    assert done.status == "success"


def test_list_runs_filters_and_order(database):
    a = db.create_run(workflow="news", now=T0)
    b = db.create_run(workflow="news", now=T0 + timedelta(seconds=1))
    c = db.create_run(workflow="other", now=T0 + timedelta(seconds=2))
    db.mark_running(b.id)

    newest_first = [r.id for r in db.list_runs()]
    assert newest_first == [c.id, b.id, a.id]
    assert [r.id for r in db.list_runs(workflow="news")] == [b.id, a.id]
    assert [r.id for r in db.list_runs(status="running")] == [b.id]
    assert len(db.list_runs(limit=1)) == 1


# --- Outputs ---------------------------------------------------------------

def test_save_and_list_outputs(database):
    run = db.create_run(now=T0)
    out = db.save_output(
        run.id, "# Digest\n\nhello", data={"items": [{"title": "x"}]}, now=T0
    )
    assert out.type == "digest"
    assert out.content == "# Digest\n\nhello"
    assert out.data == {"items": [{"title": "x"}]}
    assert out.created_at == T0

    listed = db.list_outputs(run.id)
    assert listed == [out]


def test_save_output_unknown_run(database):
    with pytest.raises(LookupError):
        db.save_output("missing", "content")


def test_outputs_listed_oldest_first(database):
    run = db.create_run(now=T0)
    o1 = db.save_output(run.id, "first", now=T0)
    o2 = db.save_output(run.id, "second", now=T0 + timedelta(seconds=1))
    assert [o.id for o in db.list_outputs(run.id)] == [o1.id, o2.id]


# --- Schedules -------------------------------------------------------------

def test_create_schedule_defaults(database):
    s = db.create_schedule("news", "0 6 * * *", now=T0)
    assert s.cron == "0 6 * * *"
    assert s.timezone == "UTC"
    assert s.enabled is True
    assert s.last_run_at is None and s.next_run_at is None
    assert db.get_schedule(s.id) == s


def test_create_schedule_custom_timezone(database):
    s = db.create_schedule("news", "0 6 * * *", tz="Asia/Shanghai", now=T0)
    assert s.timezone == "Asia/Shanghai"


def test_set_schedule_enabled(database):
    s = db.create_schedule("news", "0 6 * * *", now=T0)
    disabled = db.set_schedule_enabled(s.id, False)
    assert disabled.enabled is False
    assert db.get_schedule(s.id).enabled is False


def test_mark_schedule_ran_updates_last_and_next(database):
    s = db.create_schedule("news", "0 6 * * *", now=T0)
    nxt = T0 + timedelta(days=1)
    updated = db.mark_schedule_ran(s.id, last_run_at=T0, next_run_at=nxt)
    assert updated.last_run_at == T0
    assert updated.next_run_at == nxt


def test_mark_schedule_ran_leaves_next_untouched_when_omitted(database):
    s = db.create_schedule(
        "news", "0 6 * * *", next_run_at=T0 + timedelta(days=1), now=T0
    )
    updated = db.mark_schedule_ran(s.id, last_run_at=T0)
    assert updated.last_run_at == T0
    assert updated.next_run_at == T0 + timedelta(days=1)  # unchanged


def test_list_enabled_excludes_disabled(database):
    a = db.create_schedule("news", "0 6 * * *", now=T0)
    b = db.create_schedule("news", "0 7 * * *", enabled=False, now=T0)
    ids = [s.id for s in db.list_enabled_schedules()]
    assert a.id in ids and b.id not in ids


def test_list_due_schedules(database):
    # never-run schedule (next_run_at is NULL) -> due
    fresh = db.create_schedule("news", "0 6 * * *", now=T0)
    # next_run_at in the past -> due
    past = db.create_schedule(
        "news", "0 6 * * *", next_run_at=T0 - timedelta(minutes=1), now=T0
    )
    # next_run_at in the future -> not due
    future = db.create_schedule(
        "news", "0 6 * * *", next_run_at=T0 + timedelta(minutes=1), now=T0
    )
    # disabled, even though its time has passed -> excluded
    disabled = db.create_schedule(
        "news", "0 6 * * *", enabled=False,
        next_run_at=T0 - timedelta(minutes=1), now=T0,
    )

    due_ids = {s.id for s in db.list_due_schedules(T0)}
    assert fresh.id in due_ids
    assert past.id in due_ids
    assert future.id not in due_ids
    assert disabled.id not in due_ids


def test_list_due_schedules_boundary_is_inclusive(database):
    s = db.create_schedule("news", "0 6 * * *", next_run_at=T0, now=T0)
    # next_run_at == now counts as due (<=)
    assert s.id in {x.id for x in db.list_due_schedules(T0)}
    assert s.id not in {x.id for x in db.list_due_schedules(T0 - timedelta(seconds=1))}


def test_due_comparison_is_utc_normalized(database):
    # next_run_at given in a non-UTC zone; "now" given in UTC. They denote the
    # same instant, so the schedule must be exactly at the due boundary.
    shanghai = timezone(timedelta(hours=8))
    s = db.create_schedule(
        "news", "0 6 * * *",
        next_run_at=datetime(2026, 6, 7, 14, 0, 0, tzinfo=shanghai),  # == 06:00Z
        now=T0,
    )
    assert s.id in {x.id for x in db.list_due_schedules(T0)}  # 06:00Z
    assert s.id not in {
        x.id for x in db.list_due_schedules(T0 - timedelta(seconds=1))
    }


def test_timestamps_returned_as_utc_aware(database):
    run = db.create_run(now=T0)
    fetched = db.get_run(run.id)
    assert fetched.created_at.tzinfo is not None
    assert fetched.created_at == T0


def test_malformed_id_is_treated_as_not_found(database):
    # A non-UUID id can never match a stored id. Reads return empty; writes
    # raise LookupError — on BOTH SQLite and Postgres (native uuid column),
    # where comparing against a malformed literal would otherwise be a DataError.
    assert db.get_run("not-a-uuid") is None
    assert db.get_schedule("not-a-uuid") is None
    assert db.list_outputs("not-a-uuid") == []
    with pytest.raises(LookupError):
        db.update_run_status("not-a-uuid", "running")
    with pytest.raises(LookupError):
        db.save_output("not-a-uuid", "content")
    with pytest.raises(LookupError):
        db.set_schedule_enabled("not-a-uuid", False)


def test_writes_to_valid_but_missing_id_raise(database):
    ghost = "00000000-0000-0000-0000-000000000000"  # well-formed, nonexistent
    with pytest.raises(LookupError):
        db.update_run_status(ghost, "running")
    with pytest.raises(LookupError):
        db.save_output(ghost, "content")
    with pytest.raises(LookupError):
        db.set_schedule_enabled(ghost, False)
