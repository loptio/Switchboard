"""Runner — orchestrate ONE full run of the news workflow.

    fetch -> build_digest (summarize+verify, Phase 5) -> render -> write file
          -> save to DB (Output) + record Run status -> email (call point)

This is the only Phase 2 module that drives the pipeline. It does NOT duplicate
pipeline logic — it imports fetch/orchestrator/output and adds the DB writes (via
the `db` data-access layer) and the email call point. Phase 5 swapped the single
`agent.summarize` step for `orchestrator.build_digest`, which returns the same
Digest contract, so everything downstream (render/store/email) is unchanged.

Run status lifecycle: pending (create_run) -> running -> success; or failed if
the pipeline raises. Email is attempted AFTER the digest is saved and the run is
marked success; a failure there is logged but never fails the run or raises
(graceful degradation — the digest is already persisted, brief sec.10.3).

Entry points, all sharing one `_finalize` (render/store/email/mark_success):
- run_once(): create + execute the non-interactive pipeline inline (CLI, scheduler).
- execute_claimed_run(): execute a run the worker already claimed — the manual-
  trigger handoff, where the web wrote a pending Run and the worker picks it up.
- run_review_once() / resume_run(): the OPTIONAL human-in-the-loop path (Phase 5
  Unit 3) — start an interruptible run that suspends at a review gate
  (status=awaiting_input, state held by a checkpointer), then resume it with a
  human decision. Worker-side only; digest scheduled/handoff runs never use it.
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import asdict
from datetime import date, datetime
from functools import partial

import checkpoint
import db
from agent import Digest, summarize_agent
from brief_agent import Brief, perspective_agent, summarize_item_agent
from brief_orchestrator import build_brief
from config import Config, load_config
from fetch import fetch_feed
from mailer import send_brief, send_digest
from orchestrator import ReviewOutcome, build_digest, resume_review_run, start_review_run
from output import render_brief_markdown, render_markdown, write_brief, write_digest
from sources import gather_sources

log = logging.getLogger(__name__)


def _digest_to_data(digest: Digest, feed_url: str, day: date) -> dict:
    """Structured form of the digest for the Output.data (JSONB) column."""
    return {
        "feed_url": feed_url,
        "date": day.isoformat(),
        "items": [asdict(item) for item in digest.items],
    }


def _brief_to_data(brief: Brief) -> dict:
    """Structured form of the brief for the Output.data (JSONB) column."""
    return {"date": brief.date, "items": [asdict(item) for item in brief.items]}


def _finalize(run: db.Run, digest: Digest, cfg: Config, now: datetime | None) -> db.Run:
    """Finish a run from a final `digest`: render → write local file → save Output
    → mark success → email (graceful). Shared by the normal completion path and the
    human-in-the-loop resume path.

    Never raises: a render/write/store failure is recorded (status=failed) and the
    failed Run returned. Email is a side delivery — the digest is already saved and
    the run is success; a failure there is logged, never fatal.
    """
    try:
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
        log.exception("run %s failed during finalize", run.id)
        return db.mark_failed(run.id, str(exc), now=now)

    final = db.mark_success(run.id, now=now)
    log.info("run %s succeeded (%d items)", run.id, len(digest.items))
    try:
        send_digest(digest)
    except Exception as exc:
        log.warning(
            "run %s: email delivery failed (digest still saved): %s", run.id, exc
        )
    return final


def _finalize_brief(run: db.Run, brief: Brief, cfg: Config, now: datetime | None) -> db.Run:
    """Finish a brief run: render → write local file → save Output → mark success →
    email (graceful). The brief counterpart of `_finalize`, reusing the same
    render/store/email pipeline (different renderer + Output type). Never raises."""
    try:
        day = now.date() if now is not None else date.today()
        markdown = render_brief_markdown(brief)
        write_brief(markdown, cfg.output_dir, day)
        db.save_output(run.id, markdown, type="brief", data=_brief_to_data(brief), now=now)
    except Exception as exc:
        log.exception("run %s failed during finalize", run.id)
        return db.mark_failed(run.id, str(exc), now=now)

    final = db.mark_success(run.id, now=now)
    log.info("run %s succeeded (%d items)", run.id, len(brief.items))
    try:
        send_brief(brief)
    except Exception as exc:
        log.warning(
            "run %s: email delivery failed (brief still saved): %s", run.id, exc
        )
    return final


def _run_digest_pipeline(run: db.Run, cfg: Config, now: datetime | None) -> db.Run:
    try:
        items = fetch_feed(cfg.feed_url)
        # Bind the configured output language into the summarizer (one_line_summary
        # is written in that language; title/link stay verbatim from the source).
        digest = build_digest(
            items, cfg.count, cfg.model,
            summarize_fn=partial(summarize_agent, language=cfg.output_language),
        )
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now)
    return _finalize(run, digest, cfg, now)


def _run_brief_pipeline(run: db.Run, cfg: Config, now: datetime | None) -> db.Run:
    try:
        day = now.date() if now is not None else date.today()
        items = gather_sources()
        # Summary + perspective takes are written in the configured language; the
        # filter is language-agnostic and provenance is never translated.
        brief = build_brief(
            items, model=cfg.model, day=day,
            summarize_fn=partial(summarize_item_agent, language=cfg.output_language),
            perspective_fn=partial(perspective_agent, language=cfg.output_language),
        )
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now)
    return _finalize_brief(run, brief, cfg, now)


def _run_pipeline(run: db.Run, cfg: Config, now: datetime | None) -> db.Run:
    """Run the (non-interactive) pipeline for an already-running `run`, DISPATCHING
    on the run's workflow: 'brief' → the brief workflow, anything else (incl. the
    legacy 'news'/'digest' label) → the digest workflow.

    This is the workflow selector (brief §8): a simple dispatch, not the data-driven
    orchestrator (that is Phase 7). Never raises: a failure is caught, recorded
    (status=failed) and the failed Run returned, so one bad run never crashes the
    caller (scheduler tick). The run must already be in the `running` state.
    Shared by run_once and execute_claimed_run.
    """
    if run.workflow == "brief":
        return _run_brief_pipeline(run, cfg, now)
    return _run_digest_pipeline(run, cfg, now)


def run_once(
    *,
    trigger: str = "manual",
    workflow: str = "news",
    config: Config | None = None,
    now: datetime | None = None,
) -> db.Run:
    """Create a run and execute it inline; return the final Run record.

    The CLI and scheduler path: create the Run, mark it running, run the pipeline.
    Pipeline failures are recorded (status=failed) and returned, not raised.
    `config`/`now` are injectable for deterministic, offline tests.
    """
    cfg = config or load_config()
    run = db.create_run(workflow=workflow, trigger=trigger, now=now)
    db.mark_running(run.id, now=now)
    log.info("run %s started (trigger=%s)", run.id, trigger)
    return _run_pipeline(run, cfg, now)


def execute_claimed_run(
    run: db.Run, *, config: Config | None = None, now: datetime | None = None
) -> db.Run:
    """Execute a run the worker already CLAIMED (status=running); return the Run.

    The manual-trigger handoff: the web tier writes a pending Run, the worker
    claims it (db.claim_next_pending_run) and calls this to run the pipeline. The
    web process never calls this — doing so would load the Agent SDK into the web
    tier, which is exactly what the handoff avoids.
    """
    cfg = config or load_config()
    log.info("run %s claimed for execution (trigger=%s)", run.id, run.trigger)
    return _run_pipeline(run, cfg, now)


# --- human-in-the-loop: interruptible run + resume (Phase 5 Unit 3) ----------
# Worker-side only. The web tier never calls these (they load langgraph). digest
# scheduled/handoff runs use the non-interactive path above (no checkpoint).


def _checkpointer_cm(checkpointer):
    """A context manager yielding the checkpointer. Injected (tests: an open
    InMemorySaver) → used as-is; None (runtime) → open a PostgresSaver bound to the
    app DB for the duration of the call."""
    if checkpointer is not None:
        return nullcontext(checkpointer)
    return checkpoint.make_pg_checkpointer()


def _agent_kwargs(cfg: Config, summarize_fn, verify_fn) -> dict:
    # Bind the configured output language into the real digest summarizer unless a
    # caller injected a fake (tests). verify_fn is language-agnostic (a judgment).
    kw = {
        "summarize_fn": summarize_fn
        if summarize_fn is not None
        else partial(summarize_agent, language=cfg.output_language)
    }
    if verify_fn is not None:
        kw["verify_fn"] = verify_fn
    return kw


def _apply_outcome(
    run: db.Run, outcome: ReviewOutcome, cfg: Config, now: datetime | None
) -> tuple[db.Run, ReviewOutcome]:
    if outcome.status == "suspended":
        suspended = db.mark_awaiting_input(run.id)
        log.info("run %s suspended for human review (awaiting_input)", run.id)
        return suspended, outcome
    return _finalize(run, outcome.digest, cfg, now), outcome


def run_review_once(
    *,
    workflow: str = "news",
    config: Config | None = None,
    now: datetime | None = None,
    checkpointer=None,
    summarize_fn=None,
    verify_fn=None,
) -> tuple[db.Run, ReviewOutcome | None]:
    """Create an interruptible run (human-review gate ON) and start it.

    Returns (Run, ReviewOutcome|None). On suspend → the Run is `awaiting_input`
    (graph state held by the checkpointer under thread_id == run.id) and the
    outcome carries the review payload; resume later via `resume_run`. On
    completion → the digest is finalized (rendered/stored/emailed) and the Run is
    `success`. A failure is recorded (status=failed) and (Run, None) returned.
    """
    cfg = config or load_config()
    run = db.create_run(workflow=workflow, trigger="manual", now=now)
    db.mark_running(run.id, now=now)
    log.info("run %s started (trigger=manual, human-review)", run.id)
    try:
        with _checkpointer_cm(checkpointer) as cp:
            items = fetch_feed(cfg.feed_url)
            outcome = start_review_run(
                items,
                cfg.count,
                cfg.model,
                thread_id=run.id,
                checkpointer=cp,
                **_agent_kwargs(cfg, summarize_fn, verify_fn),
            )
            return _apply_outcome(run, outcome, cfg, now)
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now), None


def resume_run(
    run_id: str,
    decision: dict,
    *,
    config: Config | None = None,
    now: datetime | None = None,
    checkpointer=None,
    summarize_fn=None,
    verify_fn=None,
) -> tuple[db.Run, ReviewOutcome | None]:
    """Resume a suspended (`awaiting_input`) run with a human decision.

    `decision` e.g. {"action": "approve"} or {"action": "redo", "feedback": "..."}.
    Re-injects the agents + thread_id (callables are not persisted). On approve →
    finalize (success). On redo → a fresh bounded auto-loop, re-presented
    (awaiting_input again). Returns (Run, ReviewOutcome|None). Raises LookupError
    if the run is missing, ValueError if it is not awaiting_input.
    """
    cfg = config or load_config()
    run = db.get_run(run_id)
    if run is None:
        raise LookupError(f"No run with id {run_id!r}")
    if run.status != "awaiting_input":
        raise ValueError(f"run {run_id} is {run.status!r}, not awaiting_input")
    db.update_run_status(run_id, "running")
    log.info("run %s resumed (decision=%s)", run_id, decision.get("action"))
    try:
        with _checkpointer_cm(checkpointer) as cp:
            outcome = resume_review_run(
                thread_id=run_id,
                checkpointer=cp,
                decision=decision,
                **_agent_kwargs(cfg, summarize_fn, verify_fn),
            )
            return _apply_outcome(run, outcome, cfg, now)
    except Exception as exc:
        log.exception("run %s failed during resume", run_id)
        return db.mark_failed(run_id, str(exc), now=now), None
