"""Orchestrator — the deterministic control flow that coordinates the agents.

This is plain code, NOT an LLM deciding the flow (that meta-agent is Phase 6).
`build_digest` replaces the single `summarize` call in the runner: it drives a
bounded summarize → verify → redo loop and returns the SAME Digest contract, so
the rest of the pipeline (render / store / email) is untouched.

Control flow:
    summarize → verify against the source
      pass            → return the digest
      fail (issues)   → feed the critique back, re-summarize        (≤ max_redos)
      cap reached     → accept the last schema-valid digest + log   (verifier LLMs
                        can be wrong / never satisfied, so the cap is the backstop)
      verifier malformed (after a bounded re-verify) → accept current + log
      summarizer never produced a valid digest → raise (run fails; no dirty data)

Every agent call costs SDK budget, so everything here is bounded: at most
(max_redos+1) summarizer calls and at most (max_redos+1)×MAX_VERIFY_ATTEMPTS
verifier calls. No path loops forever or ships schema-dirty data.

The agents are injected (summarize_fn / verify_fn) with the real agents as
defaults — swap the model or the agent without touching this control flow; tests
inject fakes to run fully offline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from agent import (
    AgentContractError,
    Critique,
    CritiqueIssue,
    Digest,
    summarize_agent,
    verify_agent,
)
from fetch import FeedItem

log = logging.getLogger(__name__)

DEFAULT_MAX_REDOS = 2  # 1 initial draft + up to 2 redos (brief §2②)
MAX_VERIFY_ATTEMPTS = 2  # re-verify the SAME digest once if the verifier is malformed

# Fixed, instructional feedback for a malformed summarizer reply. We deliberately
# do NOT feed the raw exception/model output back (the model may just re-emit it,
# and it could carry source text) — a clean instruction is likelier to fix it.
_FORMAT_FEEDBACK = Critique(
    passed=False,
    issues=[
        CritiqueIssue(
            index=None,
            kind="format",
            detail=(
                "Your previous reply was not valid output. Return ONLY a JSON "
                "array with exactly one object per item, in order, each with a "
                'non-empty "one_line_summary".'
            ),
        )
    ],
)


def _summarize_issues(critique: Critique | None) -> str:
    """One-line render of a critique's open issues for the cap-reached log."""
    if critique is None or not critique.issues:
        return "(none)"
    return "; ".join(
        f"[{i.index if i.index else 'overall'}/{i.kind}] {i.detail}"
        for i in critique.issues
    )


def _verify_bounded(
    digest: Digest,
    items: list[FeedItem],
    model: str,
    verify_fn: Callable[..., Critique],
) -> Critique | None:
    """Verify a digest, re-verifying the SAME digest if the verifier is malformed.

    Returns the Critique, or None if the verifier never produced a valid critique
    within MAX_VERIFY_ATTEMPTS (inconclusive — the caller degrades gracefully).
    A single transient malformed reply shouldn't silently disable verification.
    """
    for attempt in range(1, MAX_VERIFY_ATTEMPTS + 1):
        try:
            return verify_fn(digest, items, model)
        except AgentContractError as exc:
            log.warning(
                "verifier produced invalid output (attempt %d/%d): %s",
                attempt,
                MAX_VERIFY_ATTEMPTS,
                exc,
            )
    return None


def build_digest(
    items: list[FeedItem],
    n: int,
    model: str,
    *,
    max_redos: int = DEFAULT_MAX_REDOS,
    summarize_fn: Callable[..., Digest] = summarize_agent,
    verify_fn: Callable[..., Critique] = verify_agent,
) -> Digest:
    """Produce a verified Digest via a bounded summarize → verify → redo loop.

    Drop-in for `agent.summarize` from the runner's perspective: same
    (items, n, model) → Digest contract. See the module docstring for the flow.
    """
    if not items:
        return Digest(items=[])

    feedback: Critique | None = None
    last_digest: Digest | None = None

    for attempt in range(max_redos + 1):  # 1 initial draft + max_redos redos
        try:
            digest = summarize_fn(items, n, model, feedback=feedback)
        except AgentContractError as exc:
            # Summarizer output failed its contract — never ship dirty data.
            log.warning("summarizer invalid output (attempt %d): %s", attempt + 1, exc)
            feedback = _FORMAT_FEEDBACK
            if attempt < max_redos:
                continue
            if last_digest is not None:
                break  # fall through to accept the last schema-valid digest
            raise RuntimeError(
                "summarizer never produced a schema-valid digest after "
                f"{max_redos + 1} attempt(s)"
            ) from exc

        last_digest = digest
        critique = _verify_bounded(digest, items, model, verify_fn)
        if critique is None:
            # Verifier couldn't produce a valid review: accept the summarizer-
            # validated digest (degrades to pre-Phase-5 quality, never masks a
            # failing digest as a pass).
            log.warning(
                "verification inconclusive (attempt %d); accepting "
                "summarizer-validated digest",
                attempt + 1,
            )
            return digest
        if critique.passed:
            log.info("digest accepted on attempt %d", attempt + 1)
            return digest
        log.info(
            "digest rejected on attempt %d (%d issue(s)); redoing",
            attempt + 1,
            len(critique.issues),
        )
        feedback = critique

    # Redo budget exhausted with the digest still failing review: accept the last
    # schema-valid digest and log the open issues (the bounded backstop).
    log.warning(
        "redo limit (%d) reached; accepting last digest with open issues: %s",
        max_redos,
        _summarize_issues(feedback),
    )
    return last_digest
