"""Offline end-to-end for the coding family through the runner + DB (Phase 10a).

The whole family runs with a deterministic FAKE seam (no SDK, no key, no spend): the
fake actually writes a file into the workspace and returns the REAL git-free diff, so
this proves "produces real changes + a diff stored as an Output" end to end. Mirrors
test_web_review's worker-side shape (create pending run -> claim -> execute).

Pins: a coding run stores a type="coding" Output (diff + summary) and succeeds; a
bounded `stopped_limit` stop marks the run failed (U1, no review gate yet) while still
persisting the partial diff; a missing task / a seam error fail cleanly.
"""

from datetime import datetime, timezone
from pathlib import Path

import db
import runner
import workspace
from coding_agent import CodingResult
from config import Config

T0 = datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc)


def _cfg(tmp_path, task="add a hello module") -> Config:
    return Config(
        feed_url="x", count=1, output_dir=tmp_path / "out", model="m",
        coding_task=task, coding_workspace=tmp_path / "ws",
    )


class _FakeSeam:
    """Deterministic stand-in for run_coding_agent: writes a real file into the
    workspace and returns the real before/after diff, so the family produces genuine
    changes offline. `status` lets a test drive the bounded-stop path."""

    def __init__(self, status="completed"):
        self.status = status
        self.calls = 0

    def __call__(self, task, workspace_dir, *, model, max_turns, max_tool_calls,
                 max_budget_usd, feedback=None, **kw):
        self.calls += 1
        root = Path(workspace_dir)
        root.mkdir(parents=True, exist_ok=True)
        before = workspace.snapshot(root)
        (root / "hello.py").write_text(f"# {task}\ndef hello():\n    return 'hi'\n", encoding="utf-8")
        after = workspace.snapshot(root)
        diff, changed = workspace.compute_diff(before, after)
        return CodingResult(
            summary=f"created hello.py for task: {task}",
            diff=diff, changed_files=changed, status=self.status,
        )


def _claim_and_run(cfg, seam, *, coding_task=None, coding_workspace=None):
    db.create_run(
        workflow="coding", trigger="manual",
        coding_task=coding_task, coding_workspace=coding_workspace, now=T0,
    )
    claimed = db.claim_next_pending_run(now=T0)
    runner.execute_claimed_run(claimed, config=cfg, now=T0, coding_fn=seam)
    return db.get_run(claimed.id)


def test_coding_run_succeeds_and_stores_diff_output(database, tmp_path):
    cfg = _cfg(tmp_path)
    seam = _FakeSeam()
    done = _claim_and_run(cfg, seam)

    assert seam.calls == 1
    assert done.status == "success"
    # the agent's change is real on disk
    assert (tmp_path / "ws" / "hello.py").exists()
    # a type="coding" Output holds the diff + summary
    outs = db.list_outputs(done.id)
    coding = [o for o in outs if o.type == "coding"]
    assert len(coding) == 1
    data = coding[0].data
    assert data["status"] == "completed"
    assert data["changed_files"] == ["hello.py"]
    assert "+def hello" in data["diff"]
    assert "hello.py" in coding[0].content  # rendered markdown


def test_coding_run_stopped_limit_marks_failed_but_keeps_partial(database, tmp_path):
    cfg = _cfg(tmp_path)
    seam = _FakeSeam(status="stopped_limit")
    done = _claim_and_run(cfg, seam)

    assert done.status == "failed"
    assert "stopped" in (done.error or "")
    # the partial diff is still persisted for inspection (bounded stop, not data loss)
    coding = [o for o in db.list_outputs(done.id) if o.type == "coding"]
    assert len(coding) == 1 and coding[0].data["status"] == "stopped_limit"


def test_coding_run_without_task_fails_without_calling_seam(database, tmp_path):
    cfg = _cfg(tmp_path, task="   ")  # blank task
    seam = _FakeSeam()
    done = _claim_and_run(cfg, seam)

    assert done.status == "failed"
    assert "task" in (done.error or "")
    assert seam.calls == 0  # never reached the seam


def test_coding_run_seam_error_marks_failed(database, tmp_path):
    cfg = _cfg(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("seam blew up")

    db.create_run(workflow="coding", trigger="manual", now=T0)
    claimed = db.claim_next_pending_run(now=T0)
    runner.execute_claimed_run(claimed, config=cfg, now=T0, coding_fn=boom)
    done = db.get_run(claimed.id)
    assert done.status == "failed" and "seam blew up" in (done.error or "")


# --- Phase 10b-1: per-run task/workspace, with Config as the fallback -----------

def test_coding_run_uses_per_run_task_over_config(database, tmp_path):
    # A Run that carries its own task uses it, NOT the Config global.
    cfg = _cfg(tmp_path, task="CONFIG TASK")
    seam = _FakeSeam()
    done = _claim_and_run(cfg, seam, coding_task="PER-RUN TASK")

    assert done.status == "success"
    coding = [o for o in db.list_outputs(done.id) if o.type == "coding"][0]
    assert coding.data["task"] == "PER-RUN TASK"  # the per-run task is recorded
    # the seam actually SAW the per-run task (it writes "# {task}" into the file)
    assert "# PER-RUN TASK" in coding.data["diff"]


def test_coding_run_uses_per_run_workspace_over_config(database, tmp_path):
    # A Run that carries its own workspace runs the agent THERE, not in the Config dir.
    cfg = _cfg(tmp_path)  # Config workspace = tmp_path/ws
    seam = _FakeSeam()
    other = tmp_path / "other_ws"
    done = _claim_and_run(cfg, seam, coding_workspace=str(other))

    assert done.status == "success"
    assert (other / "hello.py").exists()  # wrote into the per-run workspace
    assert not (tmp_path / "ws" / "hello.py").exists()  # NOT the Config one


def test_coding_run_falls_back_to_config_when_run_unset(database, tmp_path):
    # No per-run task/workspace on the Run -> the Config fallback (10a behaviour).
    cfg = _cfg(tmp_path, task="FALLBACK TASK")
    seam = _FakeSeam()
    done = _claim_and_run(cfg, seam)  # create_run with NO coding fields

    assert done.status == "success"
    coding = [o for o in db.list_outputs(done.id) if o.type == "coding"][0]
    assert coding.data["task"] == "FALLBACK TASK"
    assert "# FALLBACK TASK" in coding.data["diff"]
    assert (tmp_path / "ws" / "hello.py").exists()  # the Config workspace


def test_coding_run_blank_per_run_task_falls_back_to_config(database, tmp_path):
    # A whitespace-only per-run task is treated as unset -> Config fallback.
    cfg = _cfg(tmp_path, task="FALLBACK TASK")
    seam = _FakeSeam()
    done = _claim_and_run(cfg, seam, coding_task="   ")

    assert done.status == "success"
    coding = [o for o in db.list_outputs(done.id) if o.type == "coding"][0]
    assert coding.data["task"] == "FALLBACK TASK"


# --- Phase 10b-1: git-aware diff + the clean-tree precondition ------------------

def test_coding_run_in_a_git_workspace_uses_git_diff(database, tmp_path, git_repo):
    # Pointed at a real git repo, the stored diff comes from GIT (not the snapshot).
    cfg = _cfg(tmp_path)
    seam = _FakeSeam()  # writes hello.py
    done = _claim_and_run(cfg, seam, coding_workspace=str(git_repo))

    assert done.status == "success"
    coding = [o for o in db.list_outputs(done.id) if o.type == "coding"][0]
    assert coding.data["changed_files"] == ["hello.py"]   # from git status
    assert "diff --git" in coding.data["diff"]             # git-native format, not snapshot
    assert "+def hello" in coding.data["diff"]
    # the agent's change is real on disk and KEPT uncommitted (no auto-commit).
    assert (git_repo / "hello.py").exists()
    assert not workspace.git_is_clean(git_repo)


def test_coding_run_non_git_workspace_keeps_snapshot_diff(database, tmp_path):
    # A plain (non-git) workspace falls back to the 10a snapshot/difflib diff.
    cfg = _cfg(tmp_path)
    seam = _FakeSeam()
    done = _claim_and_run(cfg, seam)  # cfg workspace = tmp_path/ws (not git)

    coding = [o for o in db.list_outputs(done.id) if o.type == "coding"][0]
    assert "+def hello" in coding.data["diff"]
    assert "diff --git" not in coding.data["diff"]  # snapshot/difflib, NOT git (= 10a)


def test_coding_run_refuses_a_dirty_git_workspace(database, tmp_path, git_repo):
    # Precondition: a git workspace must start clean so restore-on-reject is safe and
    # the diff is fully attributable. A dirty tree fails the run before the seam.
    (git_repo / "wip.txt").write_text("uncommitted work\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    seam = _FakeSeam()
    done = _claim_and_run(cfg, seam, coding_workspace=str(git_repo))

    assert done.status == "failed"
    assert "uncommitted changes" in (done.error or "")
    assert seam.calls == 0  # never reached the seam
    assert (git_repo / "wip.txt").exists()  # the user's work is untouched


# --- Phase 10b-2: command capture + the .git integrity guard --------------------

class _CommandSeam:
    """A fake seam that 'ran commands' (writes a file, reports the commands it ran)."""

    def __call__(self, task, workspace_dir, *, model, max_turns, max_tool_calls,
                 max_budget_usd, feedback=None, **kw):
        (Path(workspace_dir) / "hello.py").write_text("x = 1\n", encoding="utf-8")
        return CodingResult(
            summary="ran tests", diff="", changed_files=[], status="completed",
            commands=["python -m pytest -q", "ruff check ."],
        )


class _GitTamperSeam:
    """A fake seam that injects a .git hook — the un-sandboxable code-exec vector — to
    prove the worker-side integrity guard detects, neutralises, and refuses it."""

    def __init__(self):
        self.calls = 0

    def __call__(self, task, workspace_dir, *, model, max_turns, max_tool_calls,
                 max_budget_usd, feedback=None, **kw):
        self.calls += 1
        hook = Path(workspace_dir) / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
        return CodingResult(
            summary="(tried to tamper)", diff="", changed_files=[], status="completed",
            commands=["printf '...' > .git/hooks/pre-commit"],
        )


def test_coding_run_captures_commands_in_output(database, tmp_path, git_repo):
    done = _claim_and_run(_cfg(tmp_path), _CommandSeam(), coding_workspace=str(git_repo))
    assert done.status == "success"
    coding = [o for o in db.list_outputs(done.id) if o.type == "coding"][0]
    assert coding.data["commands"] == ["python -m pytest -q", "ruff check ."]


def test_coding_run_refuses_and_neutralises_git_tampering(database, tmp_path, git_repo):
    seam = _GitTamperSeam()
    done = _claim_and_run(_cfg(tmp_path), seam, coding_workspace=str(git_repo))

    assert seam.calls == 1
    assert done.status == "failed"
    assert "tampered" in (done.error or "")
    # neutralised on disk: the injected hook is gone (vector closed before the run is kept)
    assert not (git_repo / ".git" / "hooks" / "pre-commit").exists()
    # the attempt is still persisted for inspection (commands + the tampered .git paths)
    coding = [o for o in db.list_outputs(done.id) if o.type == "coding"][0]
    assert coding.data["git_tampered"] and "pre-commit" in coding.data["git_tampered"][0]
    assert coding.data["commands"]


def test_coding_run_non_git_workspace_has_no_tamper_field(database, tmp_path):
    # the .git guard is git-only; a non-git workspace is unaffected (= 10a/10b-1).
    done = _claim_and_run(_cfg(tmp_path), _FakeSeam())
    coding = [o for o in db.list_outputs(done.id) if o.type == "coding"][0]
    assert coding.data["git_tampered"] == []


def test_run_once_dispatches_to_coding(database, tmp_path, monkeypatch):
    # the CLI/scheduler entry (run_once) also routes a coding workflow to the harness.
    monkeypatch.setattr(
        runner, "load_config", lambda: _cfg(tmp_path)
    )
    captured = {}
    monkeypatch.setattr(
        runner, "build_coding",
        lambda task, ws, **kw: (captured.update({"task": task, "ws": ws, "kw": kw}),
                                CodingResult(summary="s", diff="", changed_files=[], status="completed"))[1],
    )
    run = runner.run_once(workflow="coding", now=T0)
    assert run.status == "success"
    assert captured["task"] == "add a hello module"
    assert captured["kw"]["wf"] is None  # code default -> prebuilt module graph


# --- Phase 10c: auto-review threading + verdict in run meta -----------------

def test_auto_review_threads_from_config(database, tmp_path, monkeypatch):
    # Config.coding_auto_review flows into build_coding as auto_review=True.
    captured = {}

    def fake_build(task, workspace_dir, *, model, wf=None, auto_review=False, **kw):
        captured["auto_review"] = auto_review
        return CodingResult(summary="s", diff="d", changed_files=["f"], status="completed")

    monkeypatch.setattr(runner, "build_coding", fake_build)
    cfg = Config(
        feed_url="x", count=1, output_dir=tmp_path / "out", model="m",
        coding_task="t", coding_workspace=tmp_path / "ws", coding_auto_review=True,
    )
    db.create_run(workflow="coding", trigger="manual", coding_task="t",
                  coding_workspace=str(tmp_path / "ws"), now=T0)
    claimed = db.claim_next_pending_run(now=T0)
    runner.execute_claimed_run(claimed, config=cfg, now=T0)
    assert captured["auto_review"] is True


def test_reviewer_verdict_recorded_in_run_meta(database, tmp_path):
    # A coding result carrying the reviewer's verdict surfaces on runs.meta (Phase 11).
    run = db.create_run(workflow="coding", trigger="manual", now=T0)
    db.mark_running(run.id, now=T0)
    result = CodingResult(
        summary="ok", diff="--- a\n+++ b\n", changed_files=["f.py"], status="completed",
        review_verdict="approved", review_rounds=2,
    )
    cfg = Config(feed_url="x", count=1, output_dir=tmp_path / "out", model="m")
    final = runner._finalize_coding(run, result, cfg, T0)
    assert final.status == "success"
    assert db.get_run(run.id).meta == {"verdict": "reviewer:approved"}


# --- Phase 10b-2: auto-commit a successful coding run -----------------------

def test_auto_commit_commits_and_records_the_hash(database, tmp_path, git_repo):
    cfg = Config(
        feed_url="x", count=1, output_dir=tmp_path / "out", model="m",
        coding_task="add hello", coding_workspace=git_repo, coding_auto_commit=True,
    )
    seam = _FakeSeam()  # writes hello.py
    done = _claim_and_run(cfg, seam, coding_workspace=str(git_repo))

    assert done.status == "success"
    # the agent's change is now COMMITTED (tree clean) and the hash is on the Run meta.
    assert workspace.git_is_clean(git_repo) is True
    meta = db.get_run(done.id).meta
    assert meta and meta.get("commit") and len(meta["commit"]) >= 7
    # the commit message carries the agent's summary + the run id (provenance).
    import subprocess
    msg = subprocess.run(
        ["git", "-C", str(git_repo), "log", "-1", "--pretty=%B"],
        capture_output=True, text=True,
    ).stdout
    assert "hello.py" in msg or "hello" in msg.lower()
    assert done.id in msg


def test_auto_commit_off_by_default_leaves_diff_uncommitted(database, tmp_path, git_repo):
    cfg = _cfg(tmp_path)  # coding_auto_commit defaults False
    seam = _FakeSeam()
    done = _claim_and_run(cfg, seam, coding_workspace=str(git_repo))
    assert done.status == "success"
    assert not workspace.git_is_clean(git_repo)  # change kept, not committed
    assert (db.get_run(done.id).meta or {}).get("commit") is None
