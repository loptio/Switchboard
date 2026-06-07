"""Runner — orchestrate ONE full run of the news workflow.

    fetch -> summarize -> render -> write local file (Phase 1, kept)
          -> save to DB (Output) + record Run status -> email (call point)

This is the only Phase 2 module that drives the Phase 1 pipeline. It does NOT
duplicate Phase 1 logic — it imports fetch/agent/output unchanged and adds the
DB writes (via the `db` data-access layer) and the email call point.

Run status lifecycle: pending (create_run) -> running -> success; or failed if
the pipeline raises. Email is attempted AFTER the digest is saved and the run is
marked success; a failure there is logged but never fails the run or raises
(graceful degradation — the digest is already persisted, brief sec.10.3).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime

import db
from agent import Digest, summarize
from config import Config, load_config
from fetch import fetch_feed
from mailer import send_digest
from output import render_markdown, write_digest

log = logging.getLogger(__name__)


def _digest_to_data(digest: Digest, feed_url: str, day: date) -> dict:
    """Structured form of the digest for the Output.data (JSONB) column."""
    return {
        "feed_url": feed_url,
        "date": day.isoformat(),
        "items": [asdict(item) for item in digest.items],
    }


def run_once(
    *,
    trigger: str = "manual",
    workflow: str = "news",
    config: Config | None = None,
    now: datetime | None = None,
) -> db.Run:
    """Execute one full run and return the final Run record.

    Pipeline failures are caught and recorded (status=failed); this returns the
    failed Run rather than raising, so one bad run never crashes a scheduler tick.
    `config`/`now` are injectable for deterministic, offline tests.
    """
    cfg = config or load_config()
    run = db.create_run(workflow=workflow, trigger=trigger, now=now)
    db.mark_running(run.id, now=now)
    log.info("run %s started (trigger=%s)", run.id, trigger)

    try:
        items = fetch_feed(cfg.feed_url)
        digest = summarize(items, cfg.count, cfg.model)
        day = now.date() if now is not None else date.today()
        markdown = render_markdown(digest, cfg.feed_url, day)
        # Phase 1 behaviour preserved: still write the local markdown file...
        write_digest(markdown, cfg.output_dir, day)
        # ...and new in Phase 2: persist the digest to the DB.
        db.save_output(
            run.id,
            markdown,
            type="digest",
            data=_digest_to_data(digest, cfg.feed_url, day),
            now=now,
        )
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now)

    final = db.mark_success(run.id, now=now)
    log.info("run %s succeeded (%d items)", run.id, len(digest.items))

    # Email is a side delivery: the digest is already saved and the run is
    # success. A failure here is logged, never fatal (graceful degradation).
    try:
        send_digest(digest)
    except Exception as exc:
        log.warning(
            "run %s: email delivery failed (digest still saved): %s", run.id, exc
        )

    return final
