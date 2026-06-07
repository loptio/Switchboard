"""Email push — Unit 2 placeholder.

The real SMTP implementation lands in Unit 3. For now `send_digest` is a no-op
that just records (via the logger) that email isn't wired yet. The runner calls
it at the end of a successful run and treats any failure as non-fatal — the
digest is already saved — so email never blocks or fails a run (see
runner.run_once for the graceful-degradation wrapper).
"""

from __future__ import annotations

import logging

from agent import Digest

log = logging.getLogger(__name__)


def send_digest(digest: Digest) -> None:
    """Deliver a digest by email. No-op until Unit 3 implements SMTP."""
    log.info(
        "email not wired yet (Unit 3); skipping delivery of %d-item digest",
        len(digest.items),
    )
