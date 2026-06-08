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


def _claim_and_run(cfg, seam):
    db.create_run(workflow="coding", trigger="manual", now=T0)
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
