"""Per-node run monitoring (Phase 11) — engine emission, DAO, runner, API. Offline.

The engine wraps every node so it reports running → done/failed/awaiting to the
installed monitor (monitor.monitoring). With no monitor installed the wrapper is a
no-op (proven by the other 454 tests staying green); here we install collectors and
assert the transitions, persist them via the DAO, drive a real graph run through the
runner (the meta family — fully offline via a fake draft seam), and read them back
through the API.
"""

from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import db
import monitor
import runner
import workflows
from agent import Critique, Digest, DigestItem
from config import Config
from conftest import csrf_headers, login
from fetch import FeedItem
from orchestrator import build_digest

T0 = datetime(2026, 6, 12, 6, 0, tzinfo=timezone.utc)
ITEMS = [FeedItem("t1", "l1", "s1", "p1")]
PASS = Critique(passed=True, issues=[])


def _digest(summary="one"):
    return Digest([DigestItem("t1", "l1", summary)])


class _S:
    def __call__(self, items, n, model, *, feedback=None):
        return _digest()


class _V:
    def __call__(self, digest, items, model):
        return PASS


# --- engine-level emission (no DB) ------------------------------------------

def test_engine_emits_running_then_done_per_node():
    events: list[tuple[str, str]] = []
    with monitor.monitoring(lambda nid, st: events.append((nid, st))):
        build_digest(ITEMS, 1, "m", summarize_fn=_S(), verify_fn=_V())
    # a passing digest visits summarize → verify → finalize_gate, each running→done
    assert ("summarize", "running") in events
    assert ("summarize", "done") in events
    assert ("verify", "done") in events
    assert ("finalize_gate", "done") in events
    # running precedes done for the entry node
    assert events.index(("summarize", "running")) < events.index(("summarize", "done"))


def test_no_monitor_is_a_noop():
    # Without an installed monitor the graph runs identically (no raise, real result).
    digest = build_digest(ITEMS, 1, "m", summarize_fn=_S(), verify_fn=_V())
    assert digest.items[0].one_line_summary == "one"


def test_failed_node_emits_failed():
    events: list[tuple[str, str]] = []

    def boom(items, n, model, *, feedback=None):
        raise RuntimeError("kaboom")

    with monitor.monitoring(lambda nid, st: events.append((nid, st))):
        with pytest.raises(Exception):
            build_digest(ITEMS, 1, "m", summarize_fn=boom, verify_fn=_V())
    assert ("summarize", "running") in events
    assert ("summarize", "failed") in events
    assert ("summarize", "done") not in events


# --- DAO ---------------------------------------------------------------------

def test_record_and_list_node_events_in_seq_order(database):
    run = db.create_run(workflow="news", trigger="manual", now=T0)
    db.record_node_event(run.id, "summarize", "running", now=T0)
    db.record_node_event(run.id, "summarize", "done", now=T0)
    db.record_node_event(run.id, "verify", "running", now=T0)
    evs = db.list_node_events(run.id)
    assert [(e.node_id, e.status) for e in evs] == [
        ("summarize", "running"),
        ("summarize", "done"),
        ("verify", "running"),
    ]
    assert [e.seq for e in evs] == [0, 1, 2]  # per-run monotonic, deterministic order


def test_record_node_event_rejects_bad_status(database):
    run = db.create_run(workflow="news", trigger="manual", now=T0)
    with pytest.raises(ValueError):
        db.record_node_event(run.id, "x", "bogus", now=T0)


def test_record_node_event_non_uuid_is_noop(database):
    assert db.record_node_event("not-a-uuid", "n", "running") is None


# --- runner integration via the meta family (offline, fake draft seam) -------

def _cfg(tmp_path):
    return Config(feed_url="x", count=1, output_dir=tmp_path, model="m")


def _valid_proposal():
    wf = workflows.workflow_def_to_dict(workflows.DIGEST_DEF)
    wf["id"] = "mon-made"
    return {"workflow_def": wf, "agent_defs": [], "explanation": "ok"}


def _fake_draft(*a, **k):
    return _valid_proposal()


def test_meta_run_records_node_events_through_the_runner(database, tmp_path):
    run, _ = runner.run_review_once(
        workflow="meta", task="做个变体", config=_cfg(tmp_path), now=T0,
        checkpointer=InMemorySaver(), draft_fn=_fake_draft,
    )
    assert run.status == "awaiting_input"
    evs = db.list_node_events(run.id)
    by_node = {}
    for e in evs:
        by_node.setdefault(e.node_id, []).append(e.status)
    # draft + validate ran to completion; the gate suspended (awaiting, not failed)
    assert by_node["draft"][-1] == "done"
    assert by_node["validate"][-1] == "done"
    assert by_node["human_review"][-1] == "awaiting"
    assert "failed" not in [s for sts in by_node.values() for s in sts]


# --- API ---------------------------------------------------------------------

def test_progress_endpoint_returns_events(client, user):
    login(client)
    run = db.create_run(workflow="news", trigger="manual")
    db.record_node_event(run.id, "summarize", "running")
    db.record_node_event(run.id, "summarize", "done")
    r = client.get(f"/runs/{run.id}/progress", headers=csrf_headers(client))
    assert r.status_code == 200
    body = r.json()
    assert [(e["node_id"], e["status"]) for e in body] == [
        ("summarize", "running"),
        ("summarize", "done"),
    ]


def test_progress_endpoint_404_for_unknown_run(client, user):
    login(client)
    r = client.get(
        "/runs/00000000-0000-0000-0000-000000000000/progress",
        headers=csrf_headers(client),
    )
    assert r.status_code == 404
