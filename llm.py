"""LLM seam — the single place that talks to a model.

This is the model-agnostic abstraction the architecture blueprint calls for
(§ "模型无关工程"): every agent builds a prompt and calls `complete`; nothing else
imports the Claude Agent SDK. To swap models or add routing later, change ONLY
this module — the agents and the orchestrator stay untouched.

It runs a single-turn, tool-less query: no tools are granted, so the run is
deterministic and non-interactive (Phase 1 lesson: an SDK upgrade once broke the
`tools=[]` one-shot path). Auth delegates to the Claude Code CLI subscription; do
NOT set ANTHROPIC_API_KEY (that bills the paid API).
"""

from __future__ import annotations

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

# Appended to every model-call failure so the operator gets actionable guidance
# regardless of which agent triggered the call.
_AUTH_HINT = (
    "If this is an auth error (e.g. 'Not logged in'), authenticate the Claude "
    "Code CLI with your subscription: run `claude`, then /login. Do NOT set "
    "ANTHROPIC_API_KEY (that bills the paid API)."
)


async def _run_query(prompt: str, system_prompt: str, model: str, max_turns: int) -> str:
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        # `tools=[]` makes NO tools available (CLI: --tools "") so the agent can
        # only reply with text. NOTE: `allowed_tools` is just a permission
        # allow-list, not the available set — it does NOT disable tools. These
        # clean-run guarantees are hardcoded (not parameters) so every agent
        # inherits them identically.
        tools=[],
        permission_mode="bypassPermissions",  # belt-and-suspenders; nothing to permit
        max_turns=max_turns,  # headroom; with no tools the model ends in one turn
        setting_sources=[],  # ignore project/user settings for a clean run
    )

    text_chunks: list[str] = []
    result_text: str | None = None
    error: str | None = None
    # ResultMessage is the terminal message, so let the generator finish
    # naturally; capture errors and raise after the loop rather than mid-
    # iteration (raising inside `async for` aborts the generator mid-run).
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_chunks.append(block.text)
        elif isinstance(message, ResultMessage):
            if message.is_error:
                error = str(message.result or message.errors)
            else:
                result_text = message.result

    if error is not None:
        raise RuntimeError(f"Agent run failed: {error}")

    text = (result_text or "".join(text_chunks)).strip()
    if not text:
        raise RuntimeError("Agent returned no text.")
    return text


def complete(prompt: str, *, system_prompt: str, model: str, max_turns: int = 3) -> str:
    """Run one tool-less, single-turn model call; return the reply text.

    The only seam that touches the Agent SDK. Synchronous wrapper over the async
    SDK generator (callers — agents, orchestrator — stay plain sync code). Any
    failure (auth, transport, empty reply) is re-raised as a RuntimeError with
    operator guidance appended.
    """
    try:
        return anyio.run(_run_query, prompt, system_prompt, model, max_turns)
    except Exception as exc:
        raise RuntimeError(f"Model call failed: {exc}\n{_AUTH_HINT}") from exc
