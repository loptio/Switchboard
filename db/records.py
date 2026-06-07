"""Read-model records returned by the data-access layer.

Callers get plain frozen dataclasses — never live SQLAlchemy rows or sessions —
so the rest of the system depends only on this shape, not on the DB library.
Mirrors the Phase 1 style (FeedItem / Digest are frozen dataclasses too).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


def _utc(dt: datetime | None) -> datetime | None:
    """Normalize a stored timestamp to tz-aware UTC.

    SQLite drops tzinfo and returns naive datetimes; Postgres returns aware
    ones. Since the data layer always stores UTC, naive values are UTC — so we
    re-attach UTC and convert aware values to UTC for a consistent surface.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Run:
    id: str
    workflow: str
    status: str
    trigger: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Run":
        return cls(
            id=str(row["id"]),
            workflow=row["workflow"],
            status=row["status"],
            trigger=row["trigger"],
            created_at=_utc(row["created_at"]),
            started_at=_utc(row["started_at"]),
            finished_at=_utc(row["finished_at"]),
            error=row["error"],
        )


@dataclass(frozen=True)
class Output:
    id: str
    run_id: str
    type: str
    content: str
    data: dict | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Output":
        return cls(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            type=row["type"],
            content=row["content"],
            data=row["data"],
            created_at=_utc(row["created_at"]),
        )


@dataclass(frozen=True)
class Schedule:
    id: str
    workflow: str
    cron: str
    timezone: str
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Schedule":
        return cls(
            id=str(row["id"]),
            workflow=row["workflow"],
            cron=row["cron"],
            timezone=row["timezone"],
            enabled=bool(row["enabled"]),
            last_run_at=_utc(row["last_run_at"]),
            next_run_at=_utc(row["next_run_at"]),
            created_at=_utc(row["created_at"]),
        )
