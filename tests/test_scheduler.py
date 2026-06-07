"""Offline tests for the scheduler (mock time, no real clock, no waiting).

runner.run_once is monkeypatched to a counter so we test scheduling decisions —
especially the missed-window catch-up semantics — without running the pipeline.
"""

from datetime import datetime, timedelta, timezone

import pytest

import db
import runner
import scheduler

T0 = datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)
NEXT_6AM = datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc)


# --- compute_next_run (no DB) ----------------------------------------------

def test_compute_next_run_utc():
    assert scheduler.compute_next_run("0 6 * * *", "UTC", T0) == NEXT_6AM


def test_compute_next_run_timezone():
    # 06:00 Asia/Shanghai == 22:00Z the previous day
    nxt = scheduler.compute_next_run("0 6 * * *", "Asia/Shanghai", T0)
    assert nxt == datetime(2026, 6, 7, 22, 0, tzinfo=timezone.utc)


def test_compute_next_run_is_strictly_after_boundary():
    on_fire = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    assert scheduler.compute_next_run("0 6 * * *", "UTC", on_fire) == NEXT_6AM


# --- run_due_schedules (DB + mock time) ------------------------------------

@pytest.fixture
def run_calls(monkeypatch):
    """Replace runner.run_once with a counter; return the list of call kwargs."""
    calls = []
    monkeypatch.setattr(runner, "run_once", lambda **kw: calls.append(kw))
    return calls


def test_add_schedule_primes_next_run(database):
    s = scheduler.add_schedule("news", "0 6 * * *", now=T0)
    assert s.next_run_at == NEXT_6AM  # primed to the future, so it won't run now


def test_run_due_runs_only_due_schedules(database, run_calls):
    due = db.create_schedule("news", "0 6 * * *", next_run_at=T0 - timedelta(minutes=1))
    later = db.create_schedule("news", "0 6 * * *", next_run_at=T0 + timedelta(hours=1))

    ran = scheduler.run_due_schedules(T0)

    assert ran == [due.id]
    assert len(run_calls) == 1
    assert run_calls[0]["trigger"] == "scheduled"
    assert later.id not in ran


def test_missed_windows_catch_up_exactly_once(database, run_calls):
    # The process was off for days: next_run_at sits several windows in the past.
    stale = T0 - timedelta(days=3)
    s = db.create_schedule("news", "0 6 * * *", next_run_at=stale)

    ran = scheduler.run_due_schedules(T0)

    # Caught up exactly once (not once per missed day)...
    assert ran == [s.id]
    assert len(run_calls) == 1
    updated = db.get_schedule(s.id)
    assert updated.last_run_at == T0
    # ...and next_run_at jumped to the FUTURE, not the next stale window.
    assert updated.next_run_at == NEXT_6AM
    assert updated.next_run_at > T0

    # A second tick at the same instant must NOT re-run it.
    assert scheduler.run_due_schedules(T0) == []
    assert len(run_calls) == 1


# --- heartbeat wiring (no start, no waiting) -------------------------------

def test_build_scheduler_arms_single_heartbeat():
    sched = scheduler.build_scheduler()
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "heartbeat"
