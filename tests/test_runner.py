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
from agent import Digest, DigestItem
from config import Config
from fetch import FeedItem

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
    monkeypatch.setattr(runner, "summarize", lambda items, n, model: FAKE_DIGEST)


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
