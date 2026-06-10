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


def test_run_once_default_workflow_is_digest(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "run_once", lambda **kw: seen.update(kw) or _run())
    monkeypatch.setattr(db, "list_outputs", lambda run_id: [])

    assert cli.main(["run-once"]) == 0
    assert seen["workflow"] == "digest"


def test_run_once_workflow_brief_dispatches(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "run_once", lambda **kw: seen.update(kw) or _run(workflow="brief"))
    monkeypatch.setattr(db, "list_outputs", lambda run_id: [])

    assert cli.main(["run-once", "--workflow", "brief"]) == 0
    assert seen["workflow"] == "brief"


def test_run_once_coding_passes_task_and_workspace(monkeypatch):
    # Phase 10b-1: --task / --workspace ride through run_once onto the Run.
    seen = {}
    monkeypatch.setattr(runner, "run_once", lambda **kw: seen.update(kw) or _run(workflow="coding"))
    monkeypatch.setattr(db, "list_outputs", lambda run_id: [])

    rc = cli.main(["run-once", "--workflow", "coding",
                   "--task", "add a hello module", "--workspace", "/repos/proj"])
    assert rc == 0
    assert seen["workflow"] == "coding"
    assert seen["coding_task"] == "add a hello module"
    assert seen["coding_workspace"] == "/repos/proj"


def test_run_once_without_coding_flags_passes_none(monkeypatch):
    # No --task/--workspace -> None reaches run_once (worker uses the Config fallback).
    seen = {}
    monkeypatch.setattr(runner, "run_once", lambda **kw: seen.update(kw) or _run())
    monkeypatch.setattr(db, "list_outputs", lambda run_id: [])

    cli.main(["run-once"])
    assert seen["coding_task"] is None and seen["coding_workspace"] is None


def test_run_once_brief_with_review_is_rejected(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(runner, "run_review_once", lambda **kw: called.append(True) or (_run(), None))

    assert cli.main(["run-once", "--workflow", "brief", "--review"]) == 1
    assert called == []  # the digest-only review path is never invoked for brief
    assert "digest only" in capsys.readouterr().err


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


def _user(**kw):
    base = dict(id="u1", username="admin", password_hash="h", created_at=NOW)
    base.update(kw)
    return db.User(**base)


def test_create_user_hashes_and_dispatches(monkeypatch, capsys):
    from api.security import verify_password

    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "s3cret")
    captured = {}

    def fake_create(username, password_hash, **kw):
        captured.update(username=username, password_hash=password_hash)
        return _user(username=username, password_hash=password_hash)

    monkeypatch.setattr(db, "create_user", fake_create)

    assert cli.main(["create-user", "--username", "admin"]) == 0
    assert captured["username"] == "admin"
    # Stored value is a real hash (not the plaintext) that verifies the password.
    assert captured["password_hash"] != "s3cret"
    assert verify_password("s3cret", captured["password_hash"])
    assert "created user admin" in capsys.readouterr().out


def test_create_user_password_mismatch_aborts(monkeypatch):
    answers = iter(["abc", "xyz"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    called = []
    monkeypatch.setattr(db, "create_user", lambda *a, **k: called.append(a))

    assert cli.main(["create-user", "--username", "admin"]) == 1
    assert called == []  # mismatch aborts before touching the DB


def test_set_password_dispatches(monkeypatch, capsys):
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "newpw")
    captured = {}
    monkeypatch.setattr(
        db, "set_user_password",
        lambda username, password_hash: captured.update(
            username=username, password_hash=password_hash
        ),
    )

    assert cli.main(["set-password", "--username", "admin"]) == 0
    assert captured["username"] == "admin"
    assert "updated password for admin" in capsys.readouterr().out


# --- human-in-the-loop CLI (Unit 3) ----------------------------------------

from orchestrator import ReviewOutcome  # noqa: E402


def test_run_once_review_suspended_reports_and_hints(monkeypatch, capsys):
    run = _run(id="rX", status="awaiting_input")
    payload = {"digest": {"items": [{"title": "T", "one_line_summary": "S"}]}, "issues": []}
    monkeypatch.setattr(
        runner, "run_review_once",
        lambda **kw: (run, ReviewOutcome(status="suspended", payload=payload)),
    )
    assert cli.main(["run-once", "--review"]) == 0
    out = capsys.readouterr().out
    assert "awaiting_input" in out and "S" in out and "resume-run rX" in out


def test_run_once_review_completed(monkeypatch, capsys):
    run = _run(id="rY", status="success")
    monkeypatch.setattr(
        runner, "run_review_once",
        lambda **kw: (run, ReviewOutcome(status="completed", digest=None)),
    )
    monkeypatch.setattr(db, "list_outputs", lambda run_id: [])
    assert cli.main(["run-once", "--review"]) == 0
    assert "success" in capsys.readouterr().out


def test_run_once_review_failed_returns_nonzero(monkeypatch):
    run = _run(status="failed", error="boom")
    monkeypatch.setattr(runner, "run_review_once", lambda **kw: (run, None))
    assert cli.main(["run-once", "--review"]) == 1


def test_resume_run_approve_dispatches(monkeypatch, capsys):
    seen = {}
    run = _run(id="rZ", status="success")

    def fake_resume(run_id, decision, **kw):
        seen.update(run_id=run_id, decision=decision)
        return run, ReviewOutcome(status="completed", digest=None)

    monkeypatch.setattr(runner, "resume_run", fake_resume)
    monkeypatch.setattr(db, "list_outputs", lambda run_id: [])
    assert cli.main(["resume-run", "rZ", "--decision", "approve"]) == 0
    assert seen["run_id"] == "rZ" and seen["decision"] == {"action": "approve"}


def test_resume_run_redo_passes_feedback(monkeypatch):
    seen = {}
    run = _run(id="rW", status="awaiting_input")

    def fake_resume(run_id, decision, **kw):
        seen["decision"] = decision
        return run, ReviewOutcome(status="suspended", payload={"digest": {"items": []}, "issues": []})

    monkeypatch.setattr(runner, "resume_run", fake_resume)
    assert cli.main(["resume-run", "rW", "--decision", "redo", "--feedback", "more"]) == 0
    assert seen["decision"] == {"action": "redo", "feedback": "more"}


def test_resume_run_error_returns_nonzero(monkeypatch):
    def fake_resume(run_id, decision, **kw):
        raise LookupError("no such run")

    monkeypatch.setattr(runner, "resume_run", fake_resume)
    assert cli.main(["resume-run", "bad", "--decision", "approve"]) == 1


def test_checkpointer_setup_dispatches(monkeypatch, capsys):
    import checkpoint

    called = []
    monkeypatch.setattr(checkpoint, "run_setup", lambda: called.append(True))
    assert cli.main(["checkpointer-setup"]) == 0
    assert called == [True]
    assert "ready" in capsys.readouterr().out


def test_run_once_accepts_a_db_def_workflow_id(monkeypatch):
    """Phase 8 made workflow ids dynamic (DB defs); the CLI must not whitelist the
    built-ins — a synthesizer/meta-created id passes through to the runner, which
    resolves it (DB-override-else-code) and fails an unknown id cleanly."""
    seen = {}
    monkeypatch.setattr(
        runner, "run_once", lambda **kw: seen.update(kw) or _run(workflow="cautious-digest")
    )
    assert cli.main(["run-once", "--workflow", "cautious-digest"]) == 0
    assert seen["workflow"] == "cautious-digest"
