"""Web human-in-the-loop — start review + resume handoff (Phase 8, U3), offline.

Two halves joined by the DB (mirrors test_handoff):
  - web: POST /runs {review:true} enqueues a review run; POST /runs/{id}/resume
    records an approve/redo decision — neither executes anything.
  - worker: claims the review run -> suspends (awaiting_input, payload persisted);
    claims the resumable run -> resumes (approve -> success, redo -> re-suspend).

The worker halves run in-process with an injected InMemorySaver + fake agents, so
the suspend and resume share one checkpoint with no Postgres/SDK.
"""

from datetime import datetime, timezone

import db
import runner
from agent import Critique, Digest, DigestItem
from config import Config
from conftest import csrf_headers, login
from fetch import FeedItem

T0 = datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc)
ITEMS = [FeedItem("t1", "l1", "s1", "p1")]
PASS = Critique(passed=True, issues=[])


def _digest(summary: str) -> Digest:
    return Digest([DigestItem("t1", "l1", summary)])


def _cfg(tmp_path) -> Config:
    return Config(feed_url="x", count=1, output_dir=tmp_path, model="m")


class _S:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, items, n, model, *, feedback=None):
        self.calls.append(feedback)
        return self.outcomes.pop(0)


class _V:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def __call__(self, digest, items, model):
        return self.outcomes.pop(0)


# --- web side: review flag + resume / review endpoints ---------------------

def test_post_runs_review_flag_enqueues_review_run(client, user):
    login(client)
    r = client.post("/runs", json={"workflow": "news", "review": True}, headers=csrf_headers(client))
    assert r.status_code == 202
    stored = db.get_run(r.json()["id"])
    assert stored.review is True and stored.status == "pending"


def test_resume_requires_login(client):
    assert client.post("/runs/x/resume", json={"action": "approve"}).status_code == 401


def test_resume_requires_csrf(client, user):
    login(client)
    assert client.post("/runs/x/resume", json={"action": "approve"}).status_code == 403


def test_resume_non_awaiting_409(client, user):
    login(client)
    run = db.create_run(workflow="news", trigger="manual")  # pending, not awaiting_input
    r = client.post(f"/runs/{run.id}/resume", json={"action": "approve"}, headers=csrf_headers(client))
    assert r.status_code == 409


def test_resume_missing_404(client, user):
    login(client)
    r = client.post(
        "/runs/00000000-0000-0000-0000-000000000000/resume",
        json={"action": "approve"},
        headers=csrf_headers(client),
    )
    assert r.status_code == 404


def test_review_endpoint_and_output_excludes_review(client, user):
    login(client)
    run = db.create_run(workflow="news", trigger="manual")
    db.save_output(run.id, "{}", type="review", data={"digest": {"items": []}, "issues": []})
    db.save_output(run.id, "deliverable md", type="digest", data={})

    rev = client.get(f"/runs/{run.id}/review", headers=csrf_headers(client))
    assert rev.status_code == 200 and rev.json()["digest"] == {"items": []}

    out = client.get(f"/runs/{run.id}/output", headers=csrf_headers(client))
    assert {o["type"] for o in out.json()} == {"digest"}  # review excluded


# --- worker side: suspend + resume in one process --------------------------

def test_worker_review_suspends_then_resume_approve_finalizes(database, tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    monkeypatch.setattr(runner, "fetch_feed", lambda url: ITEMS)
    monkeypatch.setattr(runner, "send_digest", lambda d: None)
    monkeypatch.setattr(runner, "write_digest", lambda *a, **k: None)
    saver = InMemorySaver()
    cfg = _cfg(tmp_path)

    # web enqueues a review run; worker claims + executes -> suspends.
    db.create_run(workflow="news", trigger="manual", review=True, now=T0)
    claimed = db.claim_next_pending_run(now=T0)
    assert claimed.review is True
    runner.execute_claimed_run(
        claimed, config=cfg, now=T0, checkpointer=saver,
        summarize_fn=_S(_digest("one")), verify_fn=_V(PASS),
    )
    suspended = db.get_run(claimed.id)
    assert suspended.status == "awaiting_input"
    reviews = [o for o in db.list_outputs(claimed.id) if o.type == "review"]
    assert len(reviews) == 1
    assert reviews[0].data["digest"]["items"][0]["one_line_summary"] == "one"

    # web records approve; worker drains the resumable run -> finalize.
    db.set_run_decision(claimed.id, {"action": "approve"})
    resumable = db.claim_next_resumable_run(now=T0)
    assert resumable.id == claimed.id
    runner.resume_claimed_run(
        resumable, config=cfg, now=T0, checkpointer=saver,
        summarize_fn=_S(), verify_fn=_V(),  # approve re-runs no agent
    )
    done = db.get_run(claimed.id)
    assert done.status == "success"
    assert done.pending_decision is None  # decision consumed
    deliver = [o for o in db.list_outputs(claimed.id) if o.type != "review"]
    assert len(deliver) == 1 and "one" in deliver[0].content


def test_worker_review_redo_re_suspends(database, tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    monkeypatch.setattr(runner, "fetch_feed", lambda url: ITEMS)
    monkeypatch.setattr(runner, "send_digest", lambda d: None)
    monkeypatch.setattr(runner, "write_digest", lambda *a, **k: None)
    saver = InMemorySaver()
    cfg = _cfg(tmp_path)

    db.create_run(workflow="news", trigger="manual", review=True, now=T0)
    claimed = db.claim_next_pending_run(now=T0)
    runner.execute_claimed_run(
        claimed, config=cfg, now=T0, checkpointer=saver,
        summarize_fn=_S(_digest("one")), verify_fn=_V(PASS),
    )
    assert db.get_run(claimed.id).status == "awaiting_input"

    # web asks for a redo with feedback; worker resumes -> fresh loop -> re-suspend.
    db.set_run_decision(claimed.id, {"action": "redo", "feedback": "more detail"})
    resumable = db.claim_next_resumable_run(now=T0)
    runner.resume_claimed_run(
        resumable, config=cfg, now=T0, checkpointer=saver,
        summarize_fn=_S(_digest("two")), verify_fn=_V(PASS),
    )
    again = db.get_run(claimed.id)
    assert again.status == "awaiting_input"
    assert again.pending_decision is None
    reviews = [o for o in db.list_outputs(claimed.id) if o.type == "review"]
    assert reviews[-1].data["digest"]["items"][0]["one_line_summary"] == "two"
