"""The manual-trigger handoff, end to end and offline.

Two halves, joined by the DB:
  - web: POST /runs writes a *pending* Run and returns — it never executes.
  - worker: scheduler.run_pending_runs claims pending runs and executes them via
    runner.execute_claimed_run (pipeline monkeypatched — no network, no SDK).

Also pins the atomic claim and that the heartbeat tick drives both halves.
"""

from datetime import datetime, timedelta, timezone

import pytest

import db
import runner
import scheduler
from agent import Digest, DigestItem
from config import Config
from conftest import csrf_headers, login
from fetch import FeedItem

T0 = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)

FAKE_ITEMS = [FeedItem("A", "https://e/a", "sa", "p")]
FAKE_DIGEST = Digest([DigestItem("A", "https://e/a", "one")])


# --- web half: POST /runs enqueues, never executes -------------------------

def test_post_runs_enqueues_pending_without_executing(client, user):
    login(client)
    r = client.post("/runs", headers=csrf_headers(client))

    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending" and body["trigger"] == "manual"
    # Persisted as pending, and nothing ran in the web process (no output).
    stored = db.get_run(body["id"])
    assert stored.status == "pending"
    assert stored.started_at is None
    assert db.list_outputs(body["id"]) == []


def test_post_runs_custom_workflow(client, user):
    login(client)
    r = client.post("/runs", json={"workflow": "news"}, headers=csrf_headers(client))
    assert r.status_code == 202 and r.json()["workflow"] == "news"


def test_post_runs_requires_csrf(client, user):
    login(client)
    assert client.post("/runs").status_code == 403  # authenticated, no CSRF header


def test_post_runs_requires_login(client):
    assert client.post("/runs").status_code == 401


# --- worker half: claim + execute ------------------------------------------

@pytest.fixture
def fake_pipeline(monkeypatch, tmp_path):
    """Stub the Phase 1 pipeline + email + config so the drain runs fully offline
    (no network, no SDK, no SMTP) and writes only into tmp_path."""
    monkeypatch.setattr(runner, "fetch_feed", lambda url: FAKE_ITEMS)
    # **kw absorbs the language-bound summarize_fn the runner injects.
    monkeypatch.setattr(runner, "build_digest", lambda items, n, model, **kw: FAKE_DIGEST)
    monkeypatch.setattr(runner, "send_digest", lambda digest: None)
    cfg = Config(feed_url="https://feed/rss", count=1, output_dir=tmp_path, model="m")
    monkeypatch.setattr(runner, "load_config", lambda: cfg)
    return cfg


def test_worker_drains_pending_runs(database, fake_pipeline):
    a = db.create_run(workflow="news", trigger="manual", now=T0)
    b = db.create_run(workflow="news", trigger="manual", now=T0 + timedelta(minutes=1))

    ran = scheduler.run_pending_runs(T0)

    assert set(ran) == {a.id, b.id}
    assert db.get_run(a.id).status == "success"
    assert db.get_run(b.id).status == "success"
    assert len(db.list_outputs(a.id)) == 1
    # Idempotent: a second drain finds nothing pending.
    assert scheduler.run_pending_runs(T0) == []


def test_worker_records_failed_run(database, fake_pipeline, monkeypatch):
    monkeypatch.setattr(
        runner, "fetch_feed", lambda url: (_ for _ in ()).throw(RuntimeError("net down"))
    )
    run = db.create_run(workflow="news", trigger="manual", now=T0)

    ran = scheduler.run_pending_runs(T0)

    assert ran == [run.id]
    failed = db.get_run(run.id)
    assert failed.status == "failed" and "net down" in failed.error


# --- the atomic claim + heartbeat wiring -----------------------------------

def test_claim_next_pending_run_oldest_first_then_none(database):
    a = db.create_run(trigger="manual", now=T0)
    b = db.create_run(trigger="manual", now=T0 + timedelta(minutes=1))

    first = db.claim_next_pending_run(now=T0)
    assert first.id == a.id and first.status == "running" and first.started_at == T0
    assert db.claim_next_pending_run(now=T0).id == b.id
    assert db.claim_next_pending_run(now=T0) is None  # drained


def test_claim_skips_already_running(database):
    run = db.create_run(trigger="manual", now=T0)
    db.mark_running(run.id, now=T0)  # already taken
    assert db.claim_next_pending_run(now=T0) is None


def test_heartbeat_tick_runs_due_then_pending(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "run_due_schedules", lambda now: calls.append(("due", now)))
    monkeypatch.setattr(scheduler, "run_pending_runs", lambda now: calls.append(("pending", now)))
    monkeypatch.setattr(scheduler, "run_resuming_runs", lambda now: calls.append(("resuming", now)))

    scheduler._tick()

    assert [c[0] for c in calls] == ["due", "pending", "resuming"]
    assert calls[0][1] == calls[1][1] == calls[2][1]  # one `now` shared by all halves
