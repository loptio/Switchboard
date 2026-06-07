"""Thin-wiring tests for the operator CLI — each subcommand dispatches to the
data layer / runner / scheduler. No DB, network, or real execution."""

from datetime import datetime, timezone

import cli
import db
import runner
import scheduler

NOW = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)


def _run(**kw):
    base = dict(
        id="r1", workflow="news", status="success", trigger="manual",
        created_at=NOW, started_at=NOW, finished_at=NOW, error=None,
    )
    base.update(kw)
    return db.Run(**base)


def test_run_once_dispatches_and_reports_success(monkeypatch, capsys):
    monkeypatch.setattr(runner, "run_once", lambda **kw: _run())
    monkeypatch.setattr(db, "list_outputs", lambda run_id: [])

    assert cli.main(["run-once"]) == 0
    assert "success" in capsys.readouterr().out


def test_run_once_returns_nonzero_on_failure(monkeypatch):
    monkeypatch.setattr(runner, "run_once", lambda **kw: _run(status="failed", error="boom"))
    monkeypatch.setattr(db, "list_outputs", lambda run_id: [])

    assert cli.main(["run-once"]) == 1


def test_add_schedule_dispatches(monkeypatch, capsys):
    seen = {}

    def fake_add(workflow, cron, *, tz="UTC", now=None):
        seen.update(workflow=workflow, cron=cron, tz=tz)
        return db.Schedule(
            id="s1", workflow=workflow, cron=cron, timezone=tz, enabled=True,
            last_run_at=None, next_run_at=NOW, created_at=NOW,
        )

    monkeypatch.setattr(scheduler, "add_schedule", fake_add)

    assert cli.main(["add-schedule", "--cron", "0 6 * * *", "--tz", "UTC"]) == 0
    assert seen == {"workflow": "news", "cron": "0 6 * * *", "tz": "UTC"}
    assert "s1" in capsys.readouterr().out
