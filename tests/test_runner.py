"""Offline tests for the runner (no network, no SDK, no SMTP).

The Phase 1 pipeline (fetch/summarize) and email are monkeypatched, so these
exercise the runner's orchestration: Run lifecycle, DB Output, the preserved
local file, and graceful email degradation.
"""

import logging
from datetime import datetime, timezone

import pytest

import db
import runner
from agent import Critique, Digest, DigestItem
from brief_agent import Brief, BriefItem, Perspective
from config import Config
from fetch import FeedItem
from sources import SourceItem

T0 = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)

FAKE_ITEMS = [
    FeedItem("A", "https://e/a", "sa", "p"),
    FeedItem("B", "https://e/b", "sb", "p"),
]
FAKE_DIGEST = Digest(
    [
        DigestItem("A", "https://e/a", "one"),
        DigestItem("B", "https://e/b", "two"),
    ]
)


def _cfg(tmp_path) -> Config:
    return Config(
        feed_url="https://feed.example/rss",
        count=2,
        output_dir=tmp_path,
        model="test-model",
    )


def _raise(msg):
    def _f(*args, **kwargs):
        raise RuntimeError(msg)

    return _f


@pytest.fixture
def fake_pipeline(monkeypatch):
    monkeypatch.setattr(runner, "fetch_feed", lambda url: FAKE_ITEMS)
    # The runner now calls the orchestrator (summarize+verify); same I/O contract.
    # **kw absorbs the language-bound summarize_fn the runner injects.
    monkeypatch.setattr(runner, "build_digest", lambda items, n, model, **kw: FAKE_DIGEST)


def test_run_once_success(database, fake_pipeline, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(runner, "send_digest", lambda d: sent.append(d))

    run = runner.run_once(trigger="manual", config=_cfg(tmp_path), now=T0)

    assert run.status == "success"
    assert run.trigger == "manual"

    outputs = db.list_outputs(run.id)
    assert len(outputs) == 1
    out = outputs[0]
    assert out.type == "digest"
    assert "one" in out.content and "two" in out.content
    assert out.data["items"][0]["one_line_summary"] == "one"
    assert out.data["feed_url"] == "https://feed.example/rss"

    # Phase 1 behaviour preserved: the local markdown file is still written.
    assert (tmp_path / "digest-2026-06-07.md").exists()
    # Email call point fired with the digest.
    assert sent == [FAKE_DIGEST]


def test_run_once_email_failure_is_graceful(database, fake_pipeline, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(runner, "send_digest", _raise("smtp down"))

    with caplog.at_level(logging.WARNING):
        run = runner.run_once(config=_cfg(tmp_path), now=T0)

    # Email blew up, but the run is still success and the Output is still saved.
    assert run.status == "success"
    assert len(db.list_outputs(run.id)) == 1
    assert any("email delivery failed" in r.message for r in caplog.records)


def test_run_once_pipeline_failure_records_failed(database, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "fetch_feed", _raise("network down"))

    run = runner.run_once(config=_cfg(tmp_path), now=T0)

    assert run.status == "failed"
    assert "network down" in run.error
    assert db.list_outputs(run.id) == []  # nothing saved on failure


# --- human-in-the-loop: review-run + resume (Unit 3) -----------------------


def _summarize_pass(items, n, model, *, feedback=None):
    return FAKE_DIGEST


def _verify_pass(digest, items, model):
    return Critique(passed=True, issues=[])


def test_review_run_suspends_then_resume_approve_finalizes(database, tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    monkeypatch.setattr(runner, "fetch_feed", lambda url: FAKE_ITEMS)
    sent = []
    monkeypatch.setattr(runner, "send_digest", lambda d: sent.append(d))
    saver = InMemorySaver()  # one store shared by suspend + resume
    cfg = _cfg(tmp_path)

    run, outcome = runner.run_review_once(
        config=cfg, now=T0, checkpointer=saver,
        summarize_fn=_summarize_pass, verify_fn=_verify_pass,
    )
    # suspended at the review gate: no DELIVERABLE output yet, but the review payload
    # is persisted (type="review") so the web can render the candidate (Phase 8).
    assert run.status == "awaiting_input"
    assert outcome.status == "suspended"
    assert outcome.payload["digest"]["items"][0]["one_line_summary"] == "one"
    assert [o for o in db.list_outputs(run.id) if o.type != "review"] == []
    reviews = [o for o in db.list_outputs(run.id) if o.type == "review"]
    assert len(reviews) == 1
    assert reviews[0].data["digest"]["items"][0]["one_line_summary"] == "one"
    assert sent == []

    run2, outcome2 = runner.resume_run(
        run.id, {"action": "approve"}, config=cfg, now=T0, checkpointer=saver,
        summarize_fn=_summarize_pass, verify_fn=_verify_pass,
    )
    assert outcome2.status == "completed"
    assert run2.status == "success"
    outs = [o for o in db.list_outputs(run.id) if o.type != "review"]
    assert len(outs) == 1 and "one" in outs[0].content
    assert (tmp_path / "digest-2026-06-07.md").exists()  # local file written on finalize
    assert sent == [FAKE_DIGEST]  # emailed once, on finalize


def test_resume_run_rejects_non_awaiting(database, tmp_path):
    run = db.create_run(now=T0)  # pending, not awaiting_input
    with pytest.raises(ValueError):
        runner.resume_run(run.id, {"action": "approve"}, config=_cfg(tmp_path))


def test_resume_run_missing_raises(database, tmp_path):
    with pytest.raises(LookupError):
        runner.resume_run(
            "00000000-0000-0000-0000-000000000000",
            {"action": "approve"},
            config=_cfg(tmp_path),
        )


# --- brief workflow dispatch (Phase 6) -------------------------------------

FAKE_SOURCE_ITEMS = [SourceItem("A", "https://e/a", "Src", "科技", "pub", "text a")]
FAKE_BRIEF = Brief(
    date="2026-06-07",
    items=[
        BriefItem(
            "A", "https://e/a", "Src", "科技", "sum A",
            [Perspective("商业", "biz"), Perspective("政策", "pol"), Perspective("技术", "tech")],
        )
    ],
)


@pytest.fixture
def fake_brief_pipeline(monkeypatch):
    monkeypatch.setattr(runner, "gather_sources", lambda: FAKE_SOURCE_ITEMS)
    monkeypatch.setattr(runner, "build_brief", lambda items, *, model, day, **kw: FAKE_BRIEF)


def test_run_once_brief_success(database, fake_brief_pipeline, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(runner, "send_brief", lambda b: sent.append(b))

    run = runner.run_once(trigger="manual", workflow="brief", config=_cfg(tmp_path), now=T0)

    assert run.status == "success" and run.workflow == "brief"
    outs = db.list_outputs(run.id)
    assert len(outs) == 1 and outs[0].type == "brief"
    assert "sum A" in outs[0].content and "商业" in outs[0].content and "biz" in outs[0].content
    assert outs[0].data["items"][0]["summary"] == "sum A"
    assert outs[0].data["items"][0]["perspectives"][0]["stance"] == "商业"
    assert (tmp_path / "brief-2026-06-07.md").exists()
    assert sent == [FAKE_BRIEF]


def test_run_once_brief_email_failure_is_graceful(
    database, fake_brief_pipeline, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(runner, "send_brief", _raise("smtp down"))
    with caplog.at_level(logging.WARNING):
        run = runner.run_once(workflow="brief", config=_cfg(tmp_path), now=T0)
    assert run.status == "success"
    assert len(db.list_outputs(run.id)) == 1
    assert any("email delivery failed" in r.message for r in caplog.records)


def test_run_once_brief_pipeline_failure_records_failed(database, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "gather_sources", _raise("network down"))
    run = runner.run_once(workflow="brief", config=_cfg(tmp_path), now=T0)
    assert run.status == "failed" and "network down" in run.error
    assert db.list_outputs(run.id) == []


def test_workflow_dispatch_picks_the_right_pipeline(database, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(runner, "fetch_feed", lambda url: FAKE_ITEMS)
    monkeypatch.setattr(
        runner, "build_digest", lambda items, n, model, **kw: called.append("digest") or FAKE_DIGEST
    )
    monkeypatch.setattr(runner, "gather_sources", lambda: FAKE_SOURCE_ITEMS)
    monkeypatch.setattr(
        runner, "build_brief", lambda items, *, model, day, **kw: called.append("brief") or FAKE_BRIEF
    )
    monkeypatch.setattr(runner, "send_digest", lambda d: None)
    monkeypatch.setattr(runner, "send_brief", lambda b: None)

    runner.run_once(workflow="news", config=_cfg(tmp_path), now=T0)
    runner.run_once(workflow="brief", config=_cfg(tmp_path), now=T0)
    runner.run_once(workflow="digest", config=_cfg(tmp_path), now=T0)  # legacy alias of digest

    assert called == ["digest", "brief", "digest"]
