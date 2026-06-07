"""Cron timing helpers — SDK-free, so the web tier can use them safely.

`compute_next_run` and `validate_cron` depend only on APScheduler's CronTrigger
(no runner/agent import), which is what lets the control-plane API validate a
cron string and prime `next_run_at` on POST /schedules WITHOUT importing the
scheduler/runner (and thus the Claude Agent SDK) into the web process.

The scheduler imports `compute_next_run` from here so there is exactly one cron
implementation shared by the worker and the API.
"""

from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger


def validate_cron(cron: str, tz: str = "UTC") -> None:
    """Raise ValueError if `cron` is not a valid 5-field crontab in timezone `tz`."""
    try:
        CronTrigger.from_crontab(cron, timezone=tz)
    except Exception as exc:
        raise ValueError(f"invalid cron {cron!r} (tz {tz!r}): {exc}") from exc


def compute_next_run(cron: str, tz: str, after: datetime) -> datetime:
    """Next time `cron` (read in timezone `tz`) fires STRICTLY AFTER `after`, in UTC.

    Passing previous_fire_time=after forces 'strictly after', so a fire exactly
    at the boundary instant is not returned again (no double-run at, e.g., 06:00).
    """
    trigger = CronTrigger.from_crontab(cron, timezone=tz)
    nxt = trigger.get_next_fire_time(after, after)
    return nxt.astimezone(timezone.utc)
