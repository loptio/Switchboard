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

import json
import logging
from contextlib import nullcontext
from dataclasses import asdict
from datetime import date, datetime
from functools import partial

import agentdefs
import checkpoint
import components
import db
import defs_resolve
import monitor
import workflows
import workspace
from agent import Digest, summarize_agent, verify_agent
from brief_agent import Brief, perspective_agent, summarize_item_agent
from brief_orchestrator import build_brief
from coding_agent import CodingResult
from coding_orchestrator import (
    DEFAULT_MAX_BUDGET_USD,
    DEFAULT_MAX_REVIEW_ROUNDS,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_MAX_TURNS,
    build_coding,
    resume_coding_review_run,
    start_coding_review_run,
)
from config import Config, load_config
from fetch import fetch_feed
from mailer import is_configured as email_configured
from mailer import send_brief, send_digest
from orchestrator import (
    ReviewOutcome,
    build_digest_with_verdict,
    resume_review_run,
    start_review_run,
)
import manifest
import meta_agent
from meta_orchestrator import (
    DEFAULT_MAX_REDOS as META_DEFAULT_MAX_REDOS,
    existing_def_ids as _meta_existing_def_ids,
    resume_meta_review_run,
    start_meta_review_run,
)
from output import (
    render_brief_markdown,
    render_coding_markdown,
    render_markdown,
    render_meta_markdown,
    write_brief,
    write_coding,
    write_digest,
    write_meta,
)
from sources import gather_sources

log = logging.getLogger(__name__)


# --- per-node monitoring (Phase 11) ----------------------------------------
# Install a run-bound node monitor for the duration of a graph execution. The
# engine's node wrapper (engine._monitored) calls it as each node runs; here we
# persist the transition. Best-effort: record_node_event swallows a non-UUID id and
# monitor.emit swallows any raise, so monitoring never breaks a run. Offline tests
# that don't go through these entry points install no monitor → emit() is a no-op.
def _node_monitor(run_id: str, now: datetime | None):
    def emit(node_id: str, status: str) -> None:
        db.record_node_event(run_id, node_id, status, now=now)

    return emit


# --- agent assembly (Phase 8) ----------------------------------------------
# Resolve each agent a workflow references ONCE per run (DB override else code) and
# bind its system_prompt (+ language) into the registered base callable, keyed by the
# node's config_key (which IS the orchestrator build-fn kwarg). The base callable is
# found by the agent's (prompt_builder_ref, parser_ref), so a cloned agent reusing a
# built-in's builder/parser maps to the same callable with the new (DB) prompt.
_LANGUAGE_AWARE_AGENTS = {"summarize", "summarize_item", "perspective"}
_AGENT_BASE_BY_REFS = {
    (adef.prompt_builder_ref, adef.parser_ref): (
        components.AGENTS[aid],
        aid in _LANGUAGE_AWARE_AGENTS,
    )
    for aid, adef in agentdefs.AGENT_DEFS.items()
}


def _make_agent_fn(agent_def, cfg: Config):
    """Bind a resolved AgentDef's system_prompt (+ output language) into its base
    callable. Raises ValueError if the (builder, parser) pair is unregistered."""
    key = (agent_def.prompt_builder_ref, agent_def.parser_ref)
    try:
        base, language_aware = _AGENT_BASE_BY_REFS[key]
    except KeyError:
        raise ValueError(
            f"agent {agent_def.id!r} uses unregistered "
            f"(prompt_builder_ref, parser_ref)={key}; no base agent callable"
        ) from None
    kwargs = {"system_prompt": agent_def.system_prompt}
    if language_aware:
        kwargs["language"] = cfg.output_language
    return partial(base, **kwargs)


def _agent_fns_for(wf, cfg: Config) -> dict:
    """Resolve every agent `wf` references ONCE and build the bound callables, keyed
    by config_key (== the orchestrator build-fn kwarg name)."""
    resolved: dict = {}
    fns: dict = {}
    for agent_ref, config_key in workflows.iter_agent_bindings(wf):
        if not config_key:
            continue
        if agent_ref not in resolved:
            resolved[agent_ref] = defs_resolve.resolve_agent_def(agent_ref)
        fns[config_key] = _make_agent_fn(resolved[agent_ref], cfg)
    return fns


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


# --- observability (Phase 11): email delivery status + run meta -------------
def _deliver(send_fn, payload, run_id: str, kind: str) -> str:
    """Attempt an email delivery; return its status for the run meta. send_* skip
    silently when SMTP is unconfigured and only raise on an actual failure, so a
    no-exception result is 'sent' (configured) vs 'skipped' (not) — never masking a
    failure. Never raises (the artifact is already saved; delivery is a side channel)."""
    try:
        send_fn(payload)
    except Exception as exc:  # noqa: BLE001 — delivery never fails the run
        log.warning("run %s: email delivery failed (%s still saved): %s", run_id, kind, exc)
        return "failed"
    return "sent" if email_configured() else "skipped"


def _record_meta(run_id: str, *, verdict: str | None = None, email: str | None = None) -> None:
    """Persist run-level observability meta (best-effort — bookkeeping never fails a
    run). Only includes the keys provided."""
    meta: dict = {}
    if verdict is not None:
        meta["verdict"] = verdict
    if email is not None:
        meta["email"] = email
    if not meta:
        return
    try:
        db.set_run_meta(run_id, meta)
    except Exception:  # noqa: BLE001
        log.warning("run %s: failed to record observability meta", run_id, exc_info=True)


def _finalize(
    run: db.Run, digest: Digest, cfg: Config, now: datetime | None, *, verdict: str | None = None
) -> db.Run:
    """Finish a run from a final `digest`: render → write local file → save Output
    → mark success → email (graceful) → record meta. Shared by the normal completion
    path and the human-in-the-loop resume path.

    Never raises: a render/write/store failure is recorded (status=failed) and the
    failed Run returned. Email is a side delivery — the digest is already saved and
    the run is success; a failure there is logged, never fatal. `verdict` (Phase 11)
    is the review outcome, persisted onto the Run with the email delivery status.
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
    log.info("run %s succeeded (%d items, verdict=%s)", run.id, len(digest.items), verdict)
    email = _deliver(send_digest, digest, run.id, "digest")
    _record_meta(run.id, verdict=verdict, email=email)
    return final


def _finalize_brief(run: db.Run, brief: Brief, cfg: Config, now: datetime | None) -> db.Run:
    """Finish a brief run: render → write local file → save Output → mark success →
    email (graceful) → record meta. The brief counterpart of `_finalize`, reusing the
    same render/store/email pipeline (different renderer + Output type). The brief has
    no verifier, so it records only the email status (no verdict). Never raises."""
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
    email = _deliver(send_brief, brief, run.id, "brief")
    _record_meta(run.id, email=email)
    return final


def _run_digest_pipeline(
    run: db.Run, cfg: Config, now: datetime | None, *, wf, wf_arg
) -> db.Run:
    try:
        items = fetch_feed(cfg.feed_url)
        # Agents resolved once (DB override else code) and bound with the configured
        # output language; one_line_summary is written in that language while
        # title/link stay verbatim from the source. wf_arg=None => the module graph.
        agent_fns = _agent_fns_for(wf, cfg)
        digest, verdict = build_digest_with_verdict(
            items, cfg.count, cfg.model, wf=wf_arg, **agent_fns
        )
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now)
    return _finalize(run, digest, cfg, now, verdict=verdict)


def _run_brief_pipeline(
    run: db.Run, cfg: Config, now: datetime | None, *, wf, wf_arg
) -> db.Run:
    try:
        day = now.date() if now is not None else date.today()
        items = gather_sources()
        # Summary + perspective takes are written in the configured language; the
        # filter is language-agnostic and provenance is never translated.
        agent_fns = _agent_fns_for(wf, cfg)
        brief = build_brief(items, model=cfg.model, day=day, wf=wf_arg, **agent_fns)
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now)
    return _finalize_brief(run, brief, cfg, now)


def _coding_to_data(result: CodingResult, task: str) -> dict:
    """Structured form of the coding result for the Output.data (JSONB) column — the
    web-facing contract (summary + diff + changed_files + status) plus the per-run task
    (Phase 10b-1) so the web run/review view can show what this run was asked to do."""
    return {**asdict(result), "task": task}


def _coding_inputs(run: db.Run, cfg: Config) -> tuple[str, str]:
    """Resolve the per-run coding task + workspace (Phase 10b-1): the Run's values when
    set, else the Config fallback (CODING_TASK / CODING_WORKSPACE), preserving 10a's
    global-task behaviour. Returns (task, workspace_dir)."""
    task = (run.coding_task or "").strip() or cfg.coding_task
    workspace_dir = run.coding_workspace or str(cfg.coding_workspace)
    return task, workspace_dir


def _coding_precondition_error(workspace_dir: str) -> str | None:
    """Refuse a coding run whose GIT workspace starts dirty (Phase 10b-1): the blanket
    restore-on-reject (git checkout + clean) is only safe — and the diff only cleanly
    attributable to the agent — if the tree is clean to begin with. Returns the error
    string to fail the run with, or None to proceed. A non-git workspace is
    unconstrained (10a snapshot behaviour)."""
    if workspace.is_git_repo(workspace_dir) and not workspace.git_is_clean(workspace_dir):
        return "coding workspace has uncommitted changes — commit or stash first"
    return None


def _coding_bounds(wf) -> dict:
    """The coding agent's bounded-loop caps, from the WorkflowDef params (data).
    Includes the Phase 10c reviewer-round bound; whether the reviewer actually runs is
    the operational `auto_review` toggle (Config), passed separately at the call site."""
    params = wf.params or {}
    return {
        "max_turns": params.get("max_turns", DEFAULT_MAX_TURNS),
        "max_tool_calls": params.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS),
        "max_budget_usd": params.get("max_budget_usd", DEFAULT_MAX_BUDGET_USD),
        "max_review_rounds": params.get("max_review_rounds", DEFAULT_MAX_REVIEW_ROUNDS),
    }


def _finalize_coding(run: db.Run, result: CodingResult, cfg: Config, now: datetime | None) -> db.Run:
    """Finish a coding run: render → write local file → save Output (diff+summary) →
    mark success/failed. The partial diff is ALWAYS persisted (inspectable) even on a
    bounded stop. In U1 a non-`completed` status marks the run failed (no review gate
    yet); U2 routes a `stopped_limit` with partial work to the human-review gate.
    Never raises."""
    try:
        day = now.date() if now is not None else date.today()
        task, _ = _coding_inputs(run, cfg)
        markdown = render_coding_markdown(result)
        write_coding(markdown, cfg.output_dir, day)
        db.save_output(run.id, markdown, type="coding", data=_coding_to_data(result, task), now=now)
    except Exception as exc:
        log.exception("run %s failed during finalize", run.id)
        return db.mark_failed(run.id, str(exc), now=now)

    # Phase 10b-2: a coding command that touched `.git` (hook/config code-exec vector)
    # was neutralised in the orchestrator; REFUSE to finalize such a run regardless of
    # status/approval. The diff + commands + the tampered paths are persisted above for
    # inspection, but the run is failed (never kept/committed).
    if result.git_tampered:
        log.error("run %s: refusing to finalize — .git tampered: %s", run.id, result.git_tampered)
        return db.mark_failed(
            run.id,
            f".git tampered (reverted, run refused): {', '.join(result.git_tampered)}",
            now=now,
        )
    if result.status != "completed":
        log.warning("run %s: coding agent did not complete (status=%s)", run.id, result.status)
        return db.mark_failed(run.id, f"coding agent stopped: {result.status}", now=now)
    final = db.mark_success(run.id, now=now)
    log.info("run %s succeeded (coding, %d file(s) changed)", run.id, len(result.changed_files))
    # Phase 10c + 11: surface the automatic reviewer's outcome on the Run (when it ran).
    if result.review_verdict is not None:
        _record_meta(
            run.id,
            verdict=f"reviewer:{result.review_verdict}",
            email=None,
        )
    return final


def _run_coding_pipeline(
    run: db.Run, cfg: Config, now: datetime | None, *, wf, wf_arg, coding_fn=None
) -> db.Run:
    try:
        # Per-run task/workspace (Phase 10b-1): the Run's values, else the Config
        # fallback (preserving 10a). A coding run with neither is a no-op.
        task, workspace_dir = _coding_inputs(run, cfg)
        if not task.strip():
            return db.mark_failed(
                run.id, "coding run has no task (set --task or CODING_TASK)", now=now
            )
        precondition = _coding_precondition_error(workspace_dir)
        if precondition is not None:
            return db.mark_failed(run.id, precondition, now=now)
        # The seam (coding_agent.run_coding_agent) is the only Agent SDK caller; it is
        # confined to `workspace_dir` and bounded by the WorkflowDef caps. wf_arg=None =>
        # the prebuilt module graph (coding is never web-synthesized). `coding_fn` is
        # injectable for offline tests (a deterministic fake — no SDK); None => the real
        # seam (build_coding's default).
        extra = {"coding_fn": coding_fn} if coding_fn is not None else {}
        result = build_coding(
            task, workspace_dir, model=cfg.model, wf=wf_arg,
            auto_review=cfg.coding_auto_review, **_coding_bounds(wf), **extra,
        )
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now)
    return _finalize_coding(run, result, cfg, now)


def _run_pipeline(
    run: db.Run, cfg: Config, now: datetime | None, *, coding_fn=None
) -> db.Run:
    """Run the non-interactive pipeline with per-node monitoring installed (Phase 11)
    for the duration of the graph execution."""
    with monitor.monitoring(_node_monitor(run.id, now)):
        return _run_pipeline_inner(run, cfg, now, coding_fn=coding_fn)


def _run_pipeline_inner(
    run: db.Run, cfg: Config, now: datetime | None, *, coding_fn=None
) -> db.Run:
    """Run the (non-interactive) pipeline for an already-running `run`.

    Phase 8: resolve the WorkflowDef by id (DB override, else code default), select
    the harness by its `output_ref` ('brief' → the brief workflow, 'coding' → the
    coding family, else digest), and pass the resolved def to the generic engine (None
    for a code default → the orchestrator's prebuilt module graph, byte-for-byte the
    pre-Phase-8 path).

    Never raises: an unknown workflow or a pipeline failure is caught, recorded
    (status=failed) and the failed Run returned, so one bad run never crashes the
    caller (scheduler tick). The run must already be in the `running` state. Shared
    by run_once and execute_claimed_run.
    """
    try:
        wf = defs_resolve.resolve_workflow_def(run.workflow)
    except KeyError:
        log.exception("run %s: unknown workflow %r", run.id, run.workflow)
        return db.mark_failed(run.id, f"unknown workflow {run.workflow!r}", now=now)
    # Code default -> None (use the orchestrator's prebuilt module graph); a DB
    # override -> pass the def so the engine compiles it fresh (load-time guard #2).
    wf_arg = None if wf is workflows.WORKFLOWS.get(run.workflow) else wf
    if wf.output_ref == "brief":
        return _run_brief_pipeline(run, cfg, now, wf=wf, wf_arg=wf_arg)
    if wf.output_ref == "coding":
        return _run_coding_pipeline(run, cfg, now, wf=wf, wf_arg=wf_arg, coding_fn=coding_fn)
    if wf.output_ref == "meta":
        # Phase 9 guardrail: a meta run MUST pass the human gate — there is no
        # straight-through path (auto-persisting agent-written defs is exactly what
        # the blueprint's meta-agent guardrails forbid).
        log.warning("run %s: meta run refused — review gate is mandatory", run.id)
        return db.mark_failed(
            run.id, "meta workflow requires review — trigger it with review enabled", now=now
        )
    return _run_digest_pipeline(run, cfg, now, wf=wf, wf_arg=wf_arg)


# --- meta family (Phase 9): request → proposal → human gate → persist ---------


def _meta_request(run: db.Run) -> str:
    """The meta run's natural-language request rides the runs.coding_task column —
    the Phase 10b-1 per-run-task pipe; the column name is historic. No Config
    fallback: a meta run without a request is refused."""
    return (run.coding_task or "").strip()


def _meta_max_redos(wf) -> int:
    """The drafting redo bound, from the WorkflowDef params (data)."""
    return (wf.params or {}).get("max_redos", META_DEFAULT_MAX_REDOS)


def _finalize_meta(run: db.Run, result: dict, cfg: Config, now: datetime | None) -> db.Run:
    """Finish a meta run. Approved → persist the proposal's defs (agents first, then
    the workflow) via the existing CRUD, save the audit Output, mark success. Not
    approved (give_up) → save the audit Output, mark failed, persist NOTHING.

    Defense in depth before any write (Phase 9 brief §3.5): re-run the full proposal
    validation against the CURRENT palette + taken ids — the DB may have gained a
    colliding def while the run sat awaiting approval, and the DAO has no built-in
    read-only guard of its own (recon: API-router-only), so this re-check IS the
    worker-side guard. Never raises."""
    approved = bool(result.get("approved"))
    proposal = result.get("proposal") or {}

    def _save_audit_output(record: dict) -> None:
        # The audit record must tell the truth about THIS outcome — callers pass an
        # amended copy when the graph's approved/errors no longer match reality
        # (review finding: a collision-failed finalize must not read "approved").
        day = now.date() if now is not None else date.today()
        markdown = render_meta_markdown(record)
        write_meta(markdown, cfg.output_dir, day)
        db.save_output(run.id, markdown, type="meta", data=dict(record), now=now)

    if not approved:
        reason = "; ".join(result.get("errors") or []) or "proposal was not approved"
        try:
            _save_audit_output(result)
        except Exception:  # the audit record must not mask the real failure
            log.exception("run %s: failed to save meta audit output", run.id)
        log.warning("run %s: meta proposal not persisted: %s", run.id, reason)
        return db.mark_failed(run.id, f"meta proposal rejected: {reason}", now=now)

    try:
        wf_ids, ag_ids = _meta_existing_def_ids()
        errors = meta_agent.validate_proposal(
            proposal,
            palette=manifest.build_manifest(),
            existing_workflow_ids=wf_ids,
            existing_agent_ids=ag_ids,
        )
        if errors:
            try:
                _save_audit_output({**result, "approved": False, "errors": errors})
            except Exception:
                log.exception("run %s: failed to save meta audit output", run.id)
            log.error("run %s: approved proposal failed final checks: %s", run.id, errors)
            return db.mark_failed(
                run.id,
                "approved meta proposal failed final checks: " + "; ".join(errors),
                now=now,
            )
        wf_def = proposal["workflow_def"]
        description = f"created by meta-agent (run {run.id})"
        # Agents first: a workflow must never land referencing agents that don't
        # exist yet; a failure mid-way leaves only unreferenced (harmless) agent rows.
        for d in proposal.get("agent_defs") or []:
            db.create_agent_def(d["id"], d, name=d["id"], description=description, now=now)
        db.create_workflow_def(
            wf_def["id"], wf_def, name=wf_def["id"], description=description, now=now
        )
    except Exception as exc:
        log.exception("run %s failed while persisting the approved proposal", run.id)
        # Leave a truthful durable record of the failed persist too (review finding:
        # this path previously skipped the audit output entirely).
        try:
            _save_audit_output(
                {**result, "approved": False, "errors": [f"persist failed: {exc}"]}
            )
        except Exception:
            log.exception("run %s: failed to save meta audit output", run.id)
        return db.mark_failed(run.id, f"meta persist failed: {exc}", now=now)

    try:
        _save_audit_output(result)
    except Exception as exc:
        log.exception("run %s: defs persisted but audit output failed", run.id)
        return db.mark_failed(
            run.id, f"defs persisted; output bookkeeping failed: {exc}", now=now
        )
    final = db.mark_success(run.id, now=now)
    log.info("run %s succeeded (meta: workflow %r persisted)", run.id, wf_def.get("id"))
    return final


def run_once(
    *,
    trigger: str = "manual",
    workflow: str = "news",
    coding_task: str | None = None,
    coding_workspace: str | None = None,
    config: Config | None = None,
    now: datetime | None = None,
) -> db.Run:
    """Create a run and execute it inline; return the final Run record.

    The CLI and scheduler path: create the Run, mark it running, run the pipeline.
    Pipeline failures are recorded (status=failed) and returned, not raised.
    `coding_task`/`coding_workspace` (Phase 10b-1) ride on the Run for a coding run;
    NULL falls back to Config. `config`/`now` are injectable for offline tests.
    """
    cfg = config or load_config()
    run = db.create_run(
        workflow=workflow, trigger=trigger,
        coding_task=coding_task, coding_workspace=coding_workspace, now=now,
    )
    db.mark_running(run.id, now=now)
    log.info("run %s started (trigger=%s)", run.id, trigger)
    return _run_pipeline(run, cfg, now)


def execute_claimed_run(
    run: db.Run,
    *,
    config: Config | None = None,
    now: datetime | None = None,
    checkpointer=None,
    summarize_fn=None,
    verify_fn=None,
    coding_fn=None,
    draft_fn=None,
) -> db.Run:
    """Execute a run the worker already CLAIMED (status=running); return the Run.

    The manual-trigger handoff: the web tier writes a pending Run, the worker
    claims it (db.claim_next_pending_run) and calls this to run the pipeline. The
    web process never calls this — doing so would load the Agent SDK into the web
    tier, which is exactly what the handoff avoids.

    Phase 8: a run flagged `review` (digest family) takes the interruptible path —
    it suspends at the human-review gate (awaiting_input) for a web approve/redo —
    instead of running straight through. `checkpointer`/`summarize_fn`/`verify_fn`
    (and `coding_fn`/`draft_fn` for their families) are injectable for offline
    tests (an InMemorySaver + fakes).
    """
    cfg = config or load_config()
    log.info(
        "run %s claimed for execution (trigger=%s, review=%s)",
        run.id, run.trigger, run.review,
    )
    if run.review:
        return _run_review_claimed(
            run, cfg, now,
            checkpointer=checkpointer, summarize_fn=summarize_fn, verify_fn=verify_fn,
            coding_fn=coding_fn, draft_fn=draft_fn,
        )
    return _run_pipeline(run, cfg, now, coding_fn=coding_fn)


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


def _save_review_payload(run_id: str, payload: dict | None, now: datetime | None) -> None:
    """Persist the candidate awaiting approval as a type="review" Output so the web
    can render it — it otherwise lives only in the langgraph checkpoint, which the web
    can't read. type="review" is EXCLUDED from the deliverable outputs view and is
    never emailed (it is suspend state, not a product)."""
    try:
        db.save_output(
            run_id,
            json.dumps(payload or {}, ensure_ascii=False),
            type="review",
            data=payload or {},
            now=now,
        )
    except Exception as exc:  # never let bookkeeping fail the suspend
        log.warning("run %s: failed to persist review payload: %s", run_id, exc)


def _apply_outcome(
    run: db.Run, outcome: ReviewOutcome, cfg: Config, now: datetime | None
) -> tuple[db.Run, ReviewOutcome]:
    if outcome.status == "suspended":
        _save_review_payload(run.id, outcome.payload, now)
        suspended = db.mark_awaiting_input(run.id)
        log.info("run %s suspended for human review (awaiting_input)", run.id)
        return suspended, outcome
    # A human approved this digest at the review gate (Phase 11 verdict).
    return _finalize(run, outcome.digest, cfg, now, verdict="human_approved"), outcome


def _apply_coding_outcome(run: db.Run, outcome, cfg: Config, now: datetime | None) -> db.Run:
    """Apply a coding review outcome: suspend at the diff gate (awaiting_input, the
    {"coding": <CodingResult>} payload persisted as a type="review" Output) or finalize
    the approved result. Reuses the Phase 8 suspend bookkeeping verbatim."""
    if outcome.status == "suspended":
        _save_review_payload(run.id, outcome.payload, now)
        suspended = db.mark_awaiting_input(run.id)
        log.info("run %s suspended for coding diff review (awaiting_input)", run.id)
        return suspended
    return _finalize_coding(run, outcome.result, cfg, now)


def _apply_meta_outcome(run: db.Run, outcome, cfg: Config, now: datetime | None) -> db.Run:
    """Apply a meta review outcome: suspend at the proposal gate (awaiting_input, the
    {"proposal": ...} payload persisted as a type="review" Output) or finalize —
    persisting the defs ONLY on an approved completion (Phase 9 §3.2)."""
    if outcome.status == "suspended":
        _save_review_payload(run.id, outcome.payload, now)
        suspended = db.mark_awaiting_input(run.id)
        log.info("run %s suspended for meta proposal review (awaiting_input)", run.id)
        return suspended
    return _finalize_meta(run, outcome.result, cfg, now)


def _run_meta_review_claimed(
    run: db.Run,
    cfg: Config,
    now: datetime | None,
    *,
    wf,
    wf_arg,
    checkpointer=None,
    draft_fn=None,
) -> db.Run:
    """Start an interruptible META run the worker claimed: draft + validate (bounded
    redo), then suspend at the proposal gate (awaiting_input, payload persisted) or
    complete as give_up. Mirrors _run_coding_review_claimed for the meta family."""
    request = _meta_request(run)
    if not request:
        return db.mark_failed(
            run.id, "meta run has no request (set --task / the task field)", now=now
        )
    extra = {"draft_fn": draft_fn} if draft_fn is not None else {}
    try:
        with _checkpointer_cm(checkpointer) as cp:
            outcome = start_meta_review_run(
                request, model=cfg.model, thread_id=run.id, checkpointer=cp,
                max_redos=_meta_max_redos(wf), wf=wf_arg, **extra,
            )
        return _apply_meta_outcome(run, outcome, cfg, now)
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now)


def _resume_meta_claimed(
    run: db.Run,
    cfg: Config,
    now: datetime | None,
    decision: dict,
    *,
    wf_arg,
    checkpointer=None,
    draft_fn=None,
) -> db.Run:
    """Resume a claimed awaiting_input META run with its decision: approve →
    finalize (persist the defs); redo (+feedback) → a fresh bounded draft loop,
    re-suspended at the gate."""
    extra = {"draft_fn": draft_fn} if draft_fn is not None else {}
    try:
        with _checkpointer_cm(checkpointer) as cp:
            outcome = resume_meta_review_run(
                thread_id=run.id, checkpointer=cp, decision=decision, wf=wf_arg, **extra
            )
        db.clear_run_decision(run.id)  # consumed
        return _apply_meta_outcome(run, outcome, cfg, now)
    except Exception as exc:
        log.exception("run %s failed during resume", run.id)
        return db.mark_failed(run.id, str(exc), now=now)


def run_review_once(
    *,
    workflow: str = "news",
    task: str | None = None,
    config: Config | None = None,
    now: datetime | None = None,
    checkpointer=None,
    summarize_fn=None,
    verify_fn=None,
    draft_fn=None,
) -> tuple[db.Run, ReviewOutcome | None]:
    """Create an interruptible run (human-review gate ON) and start it.

    Returns (Run, ReviewOutcome|None). On suspend → the Run is `awaiting_input`
    (graph state held by the checkpointer under thread_id == run.id) and the
    outcome carries the review payload; resume later via `resume_run`. On
    completion → the digest is finalized (rendered/stored/emailed) and the Run is
    `success`. A failure is recorded (status=failed) and (Run, None) returned.

    Phase 9: the meta family rides this entry too (`workflow="meta"` + `task` = the
    natural-language request); it dispatches to the meta gate and returns
    (Run, None) — the proposal payload is read back via the run's review Output.
    """
    cfg = config or load_config()
    # Meta dispatch FIRST (resolve degrades to the digest path on any lookup miss,
    # preserving the pre-Phase-9 behaviour byte-for-byte for digest/news).
    try:
        wf = defs_resolve.resolve_workflow_def(workflow)
    except KeyError:
        wf = None
    if wf is not None and wf.output_ref == "meta":
        run = db.create_run(workflow=workflow, trigger="manual", coding_task=task, now=now)
        db.mark_running(run.id, now=now)
        log.info("run %s started (trigger=manual, meta review)", run.id)
        wf_arg = None if wf is workflows.WORKFLOWS.get(workflow) else wf
        with monitor.monitoring(_node_monitor(run.id, now)):
            final = _run_meta_review_claimed(
                run, cfg, now, wf=wf, wf_arg=wf_arg, checkpointer=checkpointer, draft_fn=draft_fn
            )
        return final, None
    run = db.create_run(workflow=workflow, trigger="manual", now=now)
    db.mark_running(run.id, now=now)
    log.info("run %s started (trigger=manual, human-review)", run.id)
    try:
        with monitor.monitoring(_node_monitor(run.id, now)), _checkpointer_cm(checkpointer) as cp:
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
    # Phase 9: a suspended META run resumes through the meta gate (approve →
    # persist the defs). Resolution misses fall through to the digest path,
    # preserving the pre-Phase-9 behaviour for digest/news byte-for-byte.
    try:
        _wf = defs_resolve.resolve_workflow_def(run.workflow)
    except KeyError:
        _wf = None
    if _wf is not None and _wf.output_ref == "meta":
        wf_arg = None if _wf is workflows.WORKFLOWS.get(run.workflow) else _wf
        with monitor.monitoring(_node_monitor(run.id, now)):
            final = _resume_meta_claimed(
                run, cfg, now, decision, wf_arg=wf_arg, checkpointer=checkpointer
            )
        return final, None
    try:
        with monitor.monitoring(_node_monitor(run.id, now)), _checkpointer_cm(checkpointer) as cp:
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


# --- web human-in-the-loop: worker-side start + resume handoff (Phase 8) ------
# The web writes intent (a review-flagged pending Run; a pending_decision on an
# awaiting_input Run); the worker claims and drives the interruptible graph. The web
# never calls these (they load langgraph/the SDK).


def _run_review_claimed(
    run: db.Run,
    cfg: Config,
    now: datetime | None,
    *,
    checkpointer=None,
    summarize_fn=None,
    verify_fn=None,
    coding_fn=None,
    draft_fn=None,
) -> db.Run:
    """Start an interruptible REVIEW run with per-node monitoring (Phase 11)."""
    with monitor.monitoring(_node_monitor(run.id, now)):
        return _run_review_claimed_inner(
            run, cfg, now, checkpointer=checkpointer, summarize_fn=summarize_fn,
            verify_fn=verify_fn, coding_fn=coding_fn, draft_fn=draft_fn,
        )


def _run_review_claimed_inner(
    run: db.Run,
    cfg: Config,
    now: datetime | None,
    *,
    checkpointer=None,
    summarize_fn=None,
    verify_fn=None,
    coding_fn=None,
    draft_fn=None,
) -> db.Run:
    """Start an interruptible REVIEW run the worker claimed: run the auto-loop, then
    suspend at the human-review gate (awaiting_input, payload persisted) or finalize.
    Review is digest-only in U1; U2 adds the coding diff-review gate (coding_fn);
    Phase 9 adds the meta proposal gate (draft_fn). A family with no review gate
    falls back to the straight pipeline."""
    try:
        wf = defs_resolve.resolve_workflow_def(run.workflow)
    except KeyError:
        log.exception("run %s: unknown workflow %r", run.id, run.workflow)
        return db.mark_failed(run.id, f"unknown workflow {run.workflow!r}", now=now)
    if wf.output_ref == "coding":
        wf_arg = None if wf is workflows.WORKFLOWS.get(run.workflow) else wf
        return _run_coding_review_claimed(
            run, cfg, now, wf=wf, wf_arg=wf_arg, checkpointer=checkpointer, coding_fn=coding_fn
        )
    if wf.output_ref == "meta":
        wf_arg = None if wf is workflows.WORKFLOWS.get(run.workflow) else wf
        return _run_meta_review_claimed(
            run, cfg, now, wf=wf, wf_arg=wf_arg, checkpointer=checkpointer, draft_fn=draft_fn
        )
    if wf.output_ref != "digest":
        # a family with no review gate: take the straight pipeline (coding_fn threaded
        # so a non-coding family is unaffected).
        return _run_pipeline(run, cfg, now, coding_fn=coding_fn)
    wf_arg = None if wf is workflows.WORKFLOWS.get(run.workflow) else wf
    try:
        with _checkpointer_cm(checkpointer) as cp:
            items = fetch_feed(cfg.feed_url)
            fns = _agent_fns_for(wf, cfg)
            outcome = start_review_run(
                items,
                cfg.count,
                cfg.model,
                thread_id=run.id,
                checkpointer=cp,
                wf=wf_arg,
                summarize_fn=summarize_fn if summarize_fn is not None else fns.get("summarize_fn", summarize_agent),
                verify_fn=verify_fn if verify_fn is not None else fns.get("verify_fn", verify_agent),
            )
        return _apply_outcome(run, outcome, cfg, now)[0]
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now)


def _run_coding_review_claimed(
    run: db.Run,
    cfg: Config,
    now: datetime | None,
    *,
    wf,
    wf_arg,
    checkpointer=None,
    coding_fn=None,
) -> db.Run:
    """Start an interruptible CODING review run the worker claimed (U2): run one bounded
    coding loop, then suspend at the diff-review gate (awaiting_input, the diff payload
    persisted) or finalize. Mirrors _run_review_claimed for the coding family."""
    task, workspace_dir = _coding_inputs(run, cfg)
    if not task.strip():
        return db.mark_failed(run.id, "coding run has no task (set --task or CODING_TASK)", now=now)
    precondition = _coding_precondition_error(workspace_dir)
    if precondition is not None:
        return db.mark_failed(run.id, precondition, now=now)
    extra = {"coding_fn": coding_fn} if coding_fn is not None else {}
    try:
        with _checkpointer_cm(checkpointer) as cp:
            outcome = start_coding_review_run(
                task, workspace_dir, model=cfg.model, thread_id=run.id, checkpointer=cp,
                wf=wf_arg, auto_review=cfg.coding_auto_review, **_coding_bounds(wf), **extra,
            )
        return _apply_coding_outcome(run, outcome, cfg, now)
    except Exception as exc:
        log.exception("run %s failed", run.id)
        return db.mark_failed(run.id, str(exc), now=now)


def resume_claimed_run(
    run: db.Run,
    *,
    config: Config | None = None,
    now: datetime | None = None,
    checkpointer=None,
    summarize_fn=None,
    verify_fn=None,
    coding_fn=None,
    draft_fn=None,
) -> db.Run:
    """Resume an already-CLAIMED awaiting_input run (Phase 11 monitoring installed)."""
    with monitor.monitoring(_node_monitor(run.id, now)):
        return _resume_claimed_run_inner(
            run, config=config, now=now, checkpointer=checkpointer,
            summarize_fn=summarize_fn, verify_fn=verify_fn, coding_fn=coding_fn,
            draft_fn=draft_fn,
        )


def _resume_claimed_run_inner(
    run: db.Run,
    *,
    config: Config | None = None,
    now: datetime | None = None,
    checkpointer=None,
    summarize_fn=None,
    verify_fn=None,
    coding_fn=None,
    draft_fn=None,
) -> db.Run:
    """Resume an already-CLAIMED awaiting_input run with its web-written
    pending_decision (the worker half of the web resume handoff). Clears the decision
    once consumed; re-suspends (awaiting_input) on a redo, finalizes on approve.
    Dispatches by family: coding diff-review (U2) / meta proposal review (Phase 9)
    vs digest review."""
    cfg = config or load_config()
    decision = run.pending_decision or {}
    try:
        wf = defs_resolve.resolve_workflow_def(run.workflow)
    except KeyError:
        return db.mark_failed(run.id, f"unknown workflow {run.workflow!r}", now=now)
    wf_arg = None if wf is workflows.WORKFLOWS.get(run.workflow) else wf
    log.info("run %s resumed from web (decision=%s)", run.id, decision.get("action"))
    if wf.output_ref == "coding":
        return _resume_coding_claimed(
            run, cfg, now, decision, wf_arg=wf_arg, checkpointer=checkpointer, coding_fn=coding_fn
        )
    if wf.output_ref == "meta":
        return _resume_meta_claimed(
            run, cfg, now, decision, wf_arg=wf_arg, checkpointer=checkpointer, draft_fn=draft_fn
        )
    try:
        with _checkpointer_cm(checkpointer) as cp:
            fns = _agent_fns_for(wf, cfg)
            outcome = resume_review_run(
                thread_id=run.id,
                checkpointer=cp,
                decision=decision,
                wf=wf_arg,
                summarize_fn=summarize_fn if summarize_fn is not None else fns.get("summarize_fn", summarize_agent),
                verify_fn=verify_fn if verify_fn is not None else fns.get("verify_fn", verify_agent),
            )
        db.clear_run_decision(run.id)  # consumed
        return _apply_outcome(run, outcome, cfg, now)[0]
    except Exception as exc:
        log.exception("run %s failed during resume", run.id)
        return db.mark_failed(run.id, str(exc), now=now)


def _resume_coding_claimed(
    run: db.Run,
    cfg: Config,
    now: datetime | None,
    decision: dict,
    *,
    wf_arg,
    checkpointer=None,
    coding_fn=None,
) -> db.Run:
    """Resume a claimed awaiting_input CODING run with its web-written decision (U2):
    approve → finalize the diff; redo (+feedback) → a fresh bounded loop, re-suspended."""
    extra = {"coding_fn": coding_fn} if coding_fn is not None else {}
    try:
        with _checkpointer_cm(checkpointer) as cp:
            outcome = resume_coding_review_run(
                thread_id=run.id, checkpointer=cp, decision=decision, wf=wf_arg, **extra
            )
        db.clear_run_decision(run.id)  # consumed
        return _apply_coding_outcome(run, outcome, cfg, now)
    except Exception as exc:
        log.exception("run %s failed during resume", run.id)
        return db.mark_failed(run.id, str(exc), now=now)
