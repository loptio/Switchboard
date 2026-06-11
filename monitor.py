"""Per-node run monitoring (Phase 11) — opt-in, sequential-safe via a ContextVar.

The engine wraps every workflow node (engine._add_nodes) so that, AS each node
executes, it emits a transition (running → done / failed / awaiting). But the
engine knows nothing about runs or the DB. So a MONITOR callback is installed for
the duration of a run by the worker (runner), read here, and called by the engine
wrapper. When no monitor is installed — every offline test, the agent unit tests —
emit() is a no-op, so node execution is byte-for-byte unchanged (the 450+ existing
tests stay green) and there is zero DB traffic.

WHY A CONTEXTVAR (not threaded through every build_*/start_*/resume_* signature):
execution is strictly SEQUENTIAL — the scheduler runs one job at a time
(scheduler.py: a single heartbeat job, max_instances=1) and LangGraph's sync
`.invoke()` runs nodes in the calling thread. So a ContextVar set by the runner
around the pipeline call is visible to the engine wrapper without threading a
parameter through four orchestrators. This shares the SAME sequential-execution
invariant the coding env-scrub relies on; if the worker is ever made concurrent,
revisit both together.

Emission is BEST-EFFORT: a monitor that raises is logged and swallowed — monitoring
must never crash a real run.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

# node_id, status -> None. None = no monitor installed (the default, offline).
_monitor: contextvars.ContextVar[Callable[[str, str], None] | None] = contextvars.ContextVar(
    "node_monitor", default=None
)


@contextlib.contextmanager
def monitoring(fn: Callable[[str, str], None] | None):
    """Install `fn` as the node monitor for the wrapped block, then restore.

    `fn(node_id, status)` is called by the engine wrapper as each node runs. A None
    `fn` is a clean no-op (the context manager still works), so callers can pass an
    optional monitor without branching.
    """
    token = _monitor.set(fn)
    try:
        yield
    finally:
        _monitor.reset(token)


def emit(node_id: str, status: str) -> None:
    """Report a node transition to the installed monitor (no-op if none). Never
    raises — a failing monitor is logged and swallowed (observability, not
    correctness)."""
    fn = _monitor.get()
    if fn is None:
        return
    try:
        fn(node_id, status)
    except Exception:  # noqa: BLE001 — monitoring must never break a run
        log.warning("node monitor failed for %s/%s", node_id, status, exc_info=True)
