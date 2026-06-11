"""Coding diff-review human-in-the-loop (Phase 10a, U2) — offline, fake seam.

Reuses the Phase 8 web-HITL mechanism for the coding family: the web enqueues a
review-flagged coding run + records an approve/redo decision; the worker claims,
runs ONE bounded coding loop, suspends at the diff gate (awaiting_input, the
{"coding": <CodingResult>} payload persisted), then resumes — approve → finalize,
redo → fresh bounded loop re-suspended. An InMemorySaver + a deterministic fake seam
keep it offline (no Postgres, no SDK). Mirrors test_web_review.
"""

from datetime import datetime, timezone
from pathlib import Path

import db
import runner
import workspace
from coding_agent import CodingResult
from config import Config
from conftest import csrf_headers, login

T0 = datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc)


def _cfg(tmp_path, task="add a hello module") -> Config:
    return Config(
        feed_url="x", count=1, output_dir=tmp_path / "out", model="m",
        coding_task=task, coding_workspace=tmp_path / "ws",
    )


class _FakeSeam:
    """Writes a real file (content varies with feedback so a redo yields a real diff)
    and returns the real before/after diff; records calls + the feedback it saw."""

    def __init__(self, status="completed"):
        self.status = status
        self.calls = 0
        self.feedbacks: list = []

    def __call__(self, task, workspace_dir, *, model, max_turns, max_tool_calls,
                 max_budget_usd, feedback=None, **kw):
        self.calls += 1
        self.feedbacks.append(feedback)
        root = Path(workspace_dir)
        root.mkdir(parents=True, exist_ok=True)
        before = workspace.snapshot(root)
        content = f"# {task}\n"
        if feedback:
            content += f"# feedback: {feedback}\n"
        content += "def hello():\n    return 'hi'\n"
        (root / "hello.py").write_text(content, encoding="utf-8")
        after = workspace.snapshot(root)
        diff, changed = workspace.compute_diff(before, after)
        return CodingResult(
            summary=f"wrote hello.py ({task})", diff=diff, changed_files=changed, status=self.status
        )


class _AccumulatingSeam:
    """Writes a DIFFERENT file each call (file1.py, file2.py, …) and lets the graph
    compute the git diff. With restore-on-redo the tree is clean before each call, so
    only the latest file survives; WITHOUT restore the files would accumulate — the
    discriminator the redo-restore test asserts on."""

    def __init__(self):
        self.calls = 0

    def __call__(self, task, workspace_dir, *, model, max_turns, max_tool_calls,
                 max_budget_usd, feedback=None, **kw):
        self.calls += 1
        name = f"file{self.calls}.py"
        (Path(workspace_dir) / name).write_text(f"# {name}\nx = {self.calls}\n", encoding="utf-8")
        # diff/changed_files left empty: the git workspace path overrides them in the node.
        return CodingResult(summary=name, diff="", changed_files=[], status="completed")


def _saver():
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


def _enqueue_and_suspend(cfg, seam, saver):
    db.create_run(workflow="coding", trigger="manual", review=True, now=T0)
    claimed = db.claim_next_pending_run(now=T0)
    assert claimed.review is True
    runner.execute_claimed_run(claimed, config=cfg, now=T0, checkpointer=saver, coding_fn=seam)
    return claimed


# --- worker side: suspend + resume in one process --------------------------

def test_coding_review_suspends_then_approve_finalizes(database, tmp_path):
    cfg = _cfg(tmp_path)
    seam = _FakeSeam()
    saver = _saver()

    claimed = _enqueue_and_suspend(cfg, seam, saver)
    suspended = db.get_run(claimed.id)
    assert suspended.status == "awaiting_input"
    reviews = [o for o in db.list_outputs(claimed.id) if o.type == "review"]
    assert len(reviews) == 1
    payload = reviews[0].data["coding"]
    assert payload["status"] == "completed"
    assert payload["changed_files"] == ["hello.py"]
    assert "+def hello" in payload["diff"]
    assert seam.calls == 1

    # web approves; worker drains the resumable run -> finalize.
    db.set_run_decision(claimed.id, {"action": "approve"})
    resumable = db.claim_next_resumable_run(now=T0)
    assert resumable.id == claimed.id
    runner.resume_claimed_run(resumable, config=cfg, now=T0, checkpointer=saver, coding_fn=seam)
    done = db.get_run(claimed.id)
    assert done.status == "success"
    assert done.pending_decision is None  # decision consumed
    assert seam.calls == 1  # approve re-runs no agent
    deliver = [o for o in db.list_outputs(claimed.id) if o.type == "coding"]
    assert len(deliver) == 1 and "hello.py" in deliver[0].content


def test_coding_review_redo_re_suspends_with_feedback(database, tmp_path):
    cfg = _cfg(tmp_path)
    seam = _FakeSeam()
    saver = _saver()

    claimed = _enqueue_and_suspend(cfg, seam, saver)
    assert db.get_run(claimed.id).status == "awaiting_input"

    # web asks for a redo with feedback; worker resumes -> fresh loop -> re-suspend.
    db.set_run_decision(claimed.id, {"action": "redo", "feedback": "use a class"})
    resumable = db.claim_next_resumable_run(now=T0)
    runner.resume_claimed_run(resumable, config=cfg, now=T0, checkpointer=saver, coding_fn=seam)

    again = db.get_run(claimed.id)
    assert again.status == "awaiting_input"
    assert again.pending_decision is None
    assert seam.calls == 2  # the redo re-ran the coding loop
    assert seam.feedbacks == [None, "use a class"]  # feedback reached the seam
    reviews = [o for o in db.list_outputs(claimed.id) if o.type == "review"]
    assert "use a class" in reviews[-1].data["coding"]["diff"]  # real diff reflects the redo


def test_coding_review_stopped_limit_routes_to_review_not_failure(database, tmp_path):
    # hardening #3 / decision F3: a bounded stop with partial work goes to the human
    # gate (so the diff is inspectable), NOT a hard failure.
    cfg = _cfg(tmp_path)
    seam = _FakeSeam(status="stopped_limit")
    saver = _saver()

    claimed = _enqueue_and_suspend(cfg, seam, saver)
    suspended = db.get_run(claimed.id)
    assert suspended.status == "awaiting_input"  # not failed
    reviews = [o for o in db.list_outputs(claimed.id) if o.type == "review"]
    assert reviews[-1].data["coding"]["status"] == "stopped_limit"


# --- Phase 10b-1: git-aware review (git diff in the payload + restore on redo) ---

def test_coding_review_uses_git_diff_in_payload(database, tmp_path, git_repo):
    cfg = _cfg(tmp_path)
    seam = _FakeSeam()  # writes hello.py
    saver = _saver()
    db.create_run(workflow="coding", trigger="manual", review=True,
                  coding_workspace=str(git_repo), now=T0)
    claimed = db.claim_next_pending_run(now=T0)
    runner.execute_claimed_run(claimed, config=cfg, now=T0, checkpointer=saver, coding_fn=seam)

    assert db.get_run(claimed.id).status == "awaiting_input"
    payload = [o for o in db.list_outputs(claimed.id) if o.type == "review"][-1].data["coding"]
    assert payload["changed_files"] == ["hello.py"]
    assert "diff --git" in payload["diff"]   # the review diff is git-native
    assert payload["task"] == "add a hello module"  # per-run task surfaced (Phase 10b-1)


def test_coding_review_redo_restores_the_git_workspace(database, tmp_path, git_repo):
    # The discriminator for restore-on-reject: an accumulating seam writes file1.py then
    # file2.py. WITH restore, file1.py is reverted before the redo, so only file2.py
    # remains and the second diff is file2.py alone (not [file1, file2]).
    cfg = _cfg(tmp_path)
    seam = _AccumulatingSeam()
    saver = _saver()
    db.create_run(workflow="coding", trigger="manual", review=True,
                  coding_workspace=str(git_repo), now=T0)
    claimed = db.claim_next_pending_run(now=T0)
    runner.execute_claimed_run(claimed, config=cfg, now=T0, checkpointer=saver, coding_fn=seam)

    first = [o for o in db.list_outputs(claimed.id) if o.type == "review"][-1].data["coding"]
    assert first["changed_files"] == ["file1.py"]
    assert (git_repo / "file1.py").exists()

    db.set_run_decision(claimed.id, {"action": "redo", "feedback": "try again"})
    resumable = db.claim_next_resumable_run(now=T0)
    runner.resume_claimed_run(resumable, config=cfg, now=T0, checkpointer=saver, coding_fn=seam)

    again = db.get_run(claimed.id)
    assert again.status == "awaiting_input"
    assert seam.calls == 2
    # restore worked: file1.py was reverted before the redo; only file2.py remains.
    assert not (git_repo / "file1.py").exists()
    assert (git_repo / "file2.py").exists()
    second = [o for o in db.list_outputs(claimed.id) if o.type == "review"][-1].data["coding"]
    assert second["changed_files"] == ["file2.py"]


# --- Phase 10b-2: commands + the .git integrity guard in the review path --------

class _CommandReviewSeam:
    def __call__(self, task, workspace_dir, *, model, max_turns, max_tool_calls,
                 max_budget_usd, feedback=None, **kw):
        (Path(workspace_dir) / "hello.py").write_text("x = 1\n", encoding="utf-8")
        return CodingResult(summary="ran tests", diff="", changed_files=[], status="completed",
                            commands=["python -m pytest -q"])


class _TamperReviewSeam:
    def __call__(self, task, workspace_dir, *, model, max_turns, max_tool_calls,
                 max_budget_usd, feedback=None, **kw):
        (Path(workspace_dir) / ".git" / "hooks" / "pre-commit").write_text(
            "#!/bin/sh\necho pwned\n", encoding="utf-8")
        return CodingResult(summary="(tamper)", diff="", changed_files=[], status="completed")


def _enqueue_git_review(git_repo, seam, saver):
    db.create_run(workflow="coding", trigger="manual", review=True,
                  coding_workspace=str(git_repo), now=T0)
    claimed = db.claim_next_pending_run(now=T0)
    runner.execute_claimed_run(claimed, config=_cfg(git_repo.parent), now=T0,
                               checkpointer=saver, coding_fn=seam)
    return claimed


def test_coding_review_payload_includes_commands(database, git_repo):
    claimed = _enqueue_git_review(git_repo, _CommandReviewSeam(), _saver())
    assert db.get_run(claimed.id).status == "awaiting_input"
    payload = [o for o in db.list_outputs(claimed.id) if o.type == "review"][-1].data["coding"]
    assert payload["commands"] == ["python -m pytest -q"]


def test_coding_review_git_tamper_is_flagged_then_approve_still_refuses(database, git_repo):
    saver = _saver()
    claimed = _enqueue_git_review(git_repo, _TamperReviewSeam(), saver)
    # suspended at the gate, flagged, and already neutralised on disk
    assert db.get_run(claimed.id).status == "awaiting_input"
    payload = [o for o in db.list_outputs(claimed.id) if o.type == "review"][-1].data["coding"]
    assert payload["git_tampered"] and "pre-commit" in payload["git_tampered"][0]
    assert not (git_repo / ".git" / "hooks" / "pre-commit").exists()
    # approving REFUSES to finalize — a tampering run can never be kept
    db.set_run_decision(claimed.id, {"action": "approve"})
    resumable = db.claim_next_resumable_run(now=T0)
    runner.resume_claimed_run(resumable, config=_cfg(git_repo.parent), now=T0,
                              checkpointer=saver, coding_fn=_TamperReviewSeam())
    done = db.get_run(claimed.id)
    assert done.status == "failed" and "tampered" in (done.error or "")


# --- web side: the generic review endpoint round-trips the coding payload ----

def test_review_endpoint_returns_coding_payload(client, user):
    login(client)
    run = db.create_run(workflow="coding", trigger="manual")
    db.save_output(
        run.id, "{}", type="review",
        data={"coding": {"summary": "s", "diff": "--- a\n+++ b\n+x\n", "changed_files": ["x.py"], "status": "completed"}},
    )
    rev = client.get(f"/runs/{run.id}/review", headers=csrf_headers(client))
    assert rev.status_code == 200
    assert rev.json()["coding"]["changed_files"] == ["x.py"]


# --- Phase 10b-2: auto-commit on the review-approve path (review BLOCKER fix) ----

def test_auto_commit_on_review_approve_commits_only_the_agent_file(database, tmp_path, git_repo):
    """A coding review run that auto-commits on approval must commit ONLY the agent's
    file — even if a user edits an UNRELATED file while the run is suspended (the
    adversarial-review blocker: git add -A would have swept the user's file in)."""
    cfg = Config(
        feed_url="x", count=1, output_dir=tmp_path / "out", model="m",
        coding_task="add a hello module", coding_workspace=git_repo,
        coding_auto_commit=True,
    )
    seam = _FakeSeam()  # the agent writes hello.py
    saver = _saver()
    db.create_run(workflow="coding", trigger="manual", review=True,
                  coding_workspace=str(git_repo), now=T0)
    claimed = db.claim_next_pending_run(now=T0)
    runner.execute_claimed_run(claimed, config=cfg, now=T0, checkpointer=saver, coding_fn=seam)
    assert db.get_run(claimed.id).status == "awaiting_input"

    # while suspended for review, a human edits an UNRELATED file in the workspace.
    (git_repo / "user_unrelated.py").write_text("# human's work\n", encoding="utf-8")

    db.set_run_decision(claimed.id, {"action": "approve"})
    resumable = db.claim_next_resumable_run(now=T0)
    runner.resume_claimed_run(resumable, config=cfg, now=T0, checkpointer=saver, coding_fn=seam)

    done = db.get_run(claimed.id)
    assert done.status == "success"
    assert done.meta and done.meta.get("commit")
    # ONLY hello.py was committed; the user's unrelated edit is left uncommitted.
    import subprocess
    committed = subprocess.run(
        ["git", "-C", str(git_repo), "show", "--name-only", "--pretty=", "HEAD"],
        capture_output=True, text=True,
    ).stdout.split()
    assert committed == ["hello.py"]
    assert (git_repo / "user_unrelated.py").exists()
    assert not workspace.git_is_clean(git_repo)  # the user's file is still there, uncommitted
