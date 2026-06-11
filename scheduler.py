"""Scheduler — the worker heartbeat: fire due schedules AND drain pending runs.

Model (route A): a single APScheduler interval job ticks every TICK_SECONDS. Each
tick does three things: run_due_schedules(now) fires scheduled workflows whose time
has come, run_pending_runs(now) drains any pending runs the web tier enqueued as
manual triggers (the handoff), and run_resuming_runs(now) applies web approve/redo
decisions on awaiting_input runs. Each due schedule (db.list_due_schedules) runs
once and its next_run_at is advanced to the next cron fire STRICTLY AFTER now — so
a process that missed several windows catches up exactly once, not N times.

The tick is INTERACTIVE-paced (a few seconds), not minute-paced: a web "Run now"
or a review approve/redo is a pending/resumable row the worker must pick up, and a
60s wait per click made the UI feel stuck. ONE job keeps execution strictly
sequential (no overlap), which the coding family relies on — its env-scrub mutates
the process-global os.environ for the run window, so a concurrent env-reading run
(e.g. a digest emailing via SMTP_PASSWORD) must never overlap it. Faster cron is a
free side benefit: a due schedule now fires within TICK_SECONDS of its minute.

The DB is the source of truth: schedules can be added/removed without restarting
(picked up on the next tick). All timing logic lives in pure functions with an
injectable `now`, so tests use mock time and never wait on the real clock.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

import db
import runner
from cronutil import compute_next_run  # SDK-free cron math, shared with the API

log = logging.getLogger(__name__)

# Heartbeat granularity. Drives BOTH how fast the web feels (pending/resume drain
# latency) and how precisely cron fires. Default 3s: snappy for a single-user
# self-hosted control plane, negligible idle DB load (three cheap claim queries per
# tick). Override with SCHEDULER_TICK_SECONDS. Stays a SINGLE job (sequential
# execution) — see the module docstring on why concurrency is deliberately avoided.
TICK_SECONDS = max(1, int(os.environ.get("SCHEDULER_TICK_SECONDS", "3")))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def add_schedule(
    workflow: str, cron: str, *, tz: str = "UTC", now: datetime | None = None
) -> db.Schedule:
    """Create a schedule with next_run_at primed to its next fire, so creating it
    does not trigger an immediate catch-up run."""
    moment = now or _utc_now()
    next_run = compute_next_run(cron, tz, moment)
    return db.create_schedule(workflow, cron, tz=tz, next_run_at=next_run, now=moment)


def run_due_schedules(now: datetime) -> list[str]:
    """Run every schedule due at `now`, advancing each next_run_at past `now`.

    Returns the ids of the schedules that ran. Pure w.r.t. time (now injected) —
    advancing next_run_at to the next fire strictly after `now` is what makes a
    missed window catch up exactly once.
    """
    ran: list[str] = []
    for sched in db.list_due_schedules(now):
        log.info("schedule %s due; running", sched.id)
        runner.run_once(trigger="scheduled", workflow=sched.workflow, now=now)
        next_run = compute_next_run(sched.cron, sched.timezone, now)
        db.mark_schedule_ran(sched.id, last_run_at=now, next_run_at=next_run)
        ran.append(sched.id)
    return ran


def run_pending_runs(now: datetime) -> list[str]:
    """Drain pending runs (e.g. manual triggers the web enqueued): claim one,
    execute it, repeat until none remain. Returns the ids that ran.

    This is the worker half of the manual-trigger handoff. The claim is atomic
    (db.claim_next_pending_run): a run another worker grabbed first is simply not
    returned to us, so we never double-execute. Looping drains a backlog (several
    triggers between ticks) in a single tick instead of one-per-tick.
    """
    ran: list[str] = []
    while True:
        run = db.claim_next_pending_run(now=now)
        if run is None:
            break
        runner.execute_claimed_run(run, now=now)
        ran.append(run.id)
    return ran


def run_resuming_runs(now: datetime) -> list[str]:
    """Drain awaiting_input runs that have a web-written decision: claim one, resume
    it, repeat until none remain. Returns the ids that resumed.

    The worker half of the web human-in-the-loop resume handoff (Phase 8), mirroring
    run_pending_runs: the web records the approve/redo decision (db.set_run_decision)
    and the worker claims it atomically (db.claim_next_resumable_run) and resumes.
    """
    ran: list[str] = []
    while True:
        run = db.claim_next_resumable_run(now=now)
        if run is None:
            break
        runner.resume_claimed_run(run, now=now)
        ran.append(run.id)
    return ran


def _tick() -> None:
    now = _utc_now()
    run_due_schedules(now)  # scheduled workflows whose time has come
    run_pending_runs(now)  # manual triggers the web enqueued as pending runs
    run_resuming_runs(now)  # web approve/redo decisions on awaiting_input runs


def build_scheduler(scheduler: BlockingScheduler | None = None) -> BlockingScheduler:
    """Create the heartbeat scheduler (one interval job calling _tick)."""
    scheduler = scheduler or BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        _tick,
        "interval",
        seconds=TICK_SECONDS,
        id="heartbeat",
        max_instances=1,  # never overlap ticks
        coalesce=True,  # collapse missed ticks into one
        next_run_time=_utc_now(),  # run a tick immediately on startup (catch-up)
    )
    return scheduler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    scheduler = build_scheduler()
    log.info("scheduler starting; tick every %ss. Ctrl-C to stop.", TICK_SECONDS)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")


if __name__ == "__main__":
    main()
