"""Coding-agent seam (Phase 10a) — the ONLY agent-loop SDK caller.

This is the second seam, mirroring `llm.py`'s discipline for the TOOL-USING path:
`llm.complete` is the single tool-LESS, single-turn caller; `run_coding_agent` is
the single tool-LOOP caller (reason → call a workspace file tool → observe → repeat,
BOUNDED). Both import the Agent SDK and are worker-side; the web tier imports neither
(the no-SDK guard, tests/test_api_no_sdk.py). To swap the model / harness, change
ONLY this module — the coding family and the runner stay untouched.

This is the system's first crossing of the `tools=[]` boundary it deliberately built
around: from a text-in/text-out reasoning pipeline to an agent that acts. Three nets
make an unattended file-editing agent safe enough to run:

  1. CONFINEMENT — cwd=workspace, file tools only (NO Bash), and a `can_use_tool`
     permission callback that DENIES any path resolving outside the workspace
     (`workspace.confine`, realpath-based: rejects ``..`` / absolute / symlink escapes).
  2. BOUNDED LOOP — hard caps on turns / tool-calls / budget; over any cap → stop and
     mark `stopped_limit` (cost + safety; an unbounded agent loop can burn money fast).
  3. DIFF REVIEW — the family routes the diff to a human gate (U2); this module just
     PRODUCES the diff, from a git-free before/after snapshot (`workspace`).

OFFLINE DISCIPLINE (non-negotiable): callers inject a fake with this module's
signature, so the whole coding family runs in tests with NO SDK, NO key, NO spend.
The real Agent SDK runs only in a metered E2E. The bound DECISION (`_classify`) and
the permission callback (`_make_permission_cb`) are pure enough to unit-test offline
without the SDK; the SDK wiring itself (`_arun`) is exercised only in the real E2E.

Auth: the SDK delegates to the Claude Code CLI subscription; do NOT set
ANTHROPIC_API_KEY (that bills the paid API) — same rule as llm.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

import workspace

log = logging.getLogger(__name__)

# Deliberately minimal toolset (blueprint decision B: "only read/write/edit files in
# the workspace, no shell"). It is a PARAMETER so a real E2E can widen it (e.g. add
# read-only Glob/Grep) — real command execution (Bash) is 10b, not here.
DEFAULT_CODING_TOOLS: tuple[str, ...] = ("Read", "Write", "Edit")

# tool_input keys that carry a filesystem path — every one is confined to the
# workspace by the permission callback.
_FILE_PATH_KEYS = ("file_path", "path", "notebook_path")

# Tools that must never be available in 10a even if a caller widens `tools` (belt to
# the `tools` available-set suspenders): no shell / command execution.
_FORBIDDEN_TOOLS = ["Bash", "BashOutput", "KillShell"]

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent working strictly inside a single workspace directory. "
    "Use the provided file tools to accomplish the task. Only read, create, or edit "
    "files INSIDE the workspace — never touch paths outside it, and never use a shell. "
    "Make the smallest change that satisfies the task. When you are done, reply with a "
    "brief plain-text summary of what you changed and why."
)

_AUTH_HINT = (
    "If this is an auth error (e.g. 'Not logged in'), authenticate the Claude Code CLI "
    "with your subscription: run `claude`, then /login. Do NOT set ANTHROPIC_API_KEY "
    "(that bills the paid API)."
)


@dataclass(frozen=True)
class CodingResult:
    """What one coding-agent run produced — the seam's return contract.

    `status` is the family's routing signal:
    - "completed"     : the agent finished within all bounds.
    - "stopped_limit" : a turn / tool-call / budget cap (or a confinement denial) cut
                        the run short; `diff` holds whatever partial work exists. U2
                        routes this to human review; U1 marks the run failed.
    - "failed"        : the SDK call itself errored (auth/transport) — no usable result.
    """

    summary: str
    diff: str
    changed_files: list[str] = field(default_factory=list)
    status: str = "completed"
    turns: int = 0
    tool_calls: int = 0
    cost_usd: float | None = None


@dataclass(frozen=True)
class _Tally:
    """The bound-relevant facts folded out of the SDK message stream (pure data)."""

    summary: str = ""
    turns: int = 0
    tool_calls: int = 0
    cost_usd: float | None = None
    subtype: str | None = None
    is_error: bool = False
    denials: int = 0


def _make_permission_cb(root: Path, tools: tuple[str, ...], max_tool_calls: int, counter: dict):
    """Build the `can_use_tool` callback: the runtime CONFINEMENT + tool-count net.

    Denies (in order, so a path escape never consumes the call budget):
      1. any tool not in the workspace whitelist (e.g. Bash),
      2. any path argument that escapes the workspace (workspace.confine),
      3. any path inside the repo's `.git` (Phase 10b-1: the agent edits a REAL repo —
         git internals are off-limits; a scribble there is invisible to `git diff` and
         can corrupt the repo),
      4. any call once the tool-call cap is exceeded (interrupt=True → hard stop).

    `counter` is shared with the run so the cap survives across calls. Pure w.r.t. the
    SDK (no network); unit-tested offline by calling it directly.
    """
    allowed = set(tools)

    async def can_use_tool(tool_name: str, tool_input: dict, context):  # noqa: ARG001
        if tool_name not in allowed or tool_name in _FORBIDDEN_TOOLS:
            return PermissionResultDeny(message=f"tool {tool_name!r} is not allowed in this workspace")
        for key in _FILE_PATH_KEYS:
            value = tool_input.get(key)
            if not value:
                continue
            if not workspace.confine(root, value):
                return PermissionResultDeny(
                    message=f"path {value!r} escapes the workspace", interrupt=True
                )
            if workspace.in_git_dir(root, value):
                return PermissionResultDeny(
                    message=f"path {value!r} is inside the repo's .git directory", interrupt=True
                )
        counter["n"] = counter.get("n", 0) + 1
        if counter["n"] > max_tool_calls:
            return PermissionResultDeny(
                message=f"tool-call cap ({max_tool_calls}) exceeded", interrupt=True
            )
        return PermissionResultAllow()

    return can_use_tool


def _tally(messages: list) -> _Tally:
    """Fold the produced SDK messages into the bound-relevant facts (no I/O)."""
    text_chunks: list[str] = []
    result_text: str | None = None
    turns = tool_calls = denials = 0
    cost: float | None = None
    subtype: str | None = None
    is_error = False
    for m in messages:
        if isinstance(m, AssistantMessage):
            turns += 1
            for block in m.content:
                if isinstance(block, TextBlock):
                    text_chunks.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls += 1
        elif isinstance(m, ResultMessage):
            subtype = m.subtype
            is_error = bool(m.is_error)
            cost = m.total_cost_usd
            if m.num_turns:
                turns = max(turns, m.num_turns)
            result_text = m.result
            denials = len(m.permission_denials or [])
    summary = (result_text or " ".join(text_chunks)).strip()
    return _Tally(
        summary=summary, turns=turns, tool_calls=tool_calls, cost_usd=cost,
        subtype=subtype, is_error=is_error, denials=denials,
    )


def _classify(tally: _Tally, *, max_turns: int, max_tool_calls: int, max_budget_usd: float | None) -> str:
    """Map a tally + the limits to a status. PURE — the bounded-loop decision, tested
    offline with primitives. Limit signals win over a generic error so a max-turns stop
    reads as `stopped_limit`, not `failed`."""
    over_budget = (
        max_budget_usd is not None and tally.cost_usd is not None and tally.cost_usd >= max_budget_usd
    )
    if (
        tally.subtype == "error_max_turns"
        or tally.turns > max_turns
        or tally.tool_calls > max_tool_calls
        or tally.denials > 0
        or over_budget
    ):
        return "stopped_limit"
    if tally.is_error:
        return "failed"
    return "completed"


async def _arun(task: str, options: ClaudeAgentOptions) -> list:
    """Drive the SDK with a ClaudeSDKClient session and collect the message stream.

    We use ClaudeSDKClient — NOT the one-shot `query()` — because the `can_use_tool`
    permission gate (the confinement net) needs the control channel kept OPEN for the
    whole loop. With `query()` the input stream closes as soon as the one-message prompt
    is sent, so every permission round-trip fails with "Stream closed" and the agent can
    touch nothing (found in the real E2E). `connect()` (via `async with`) opens an empty
    keep-alive stream; `query()` then sends the task over it. We consume
    receive_response() to completion exactly like llm.py's `async for`, so the pure
    classifier sees only already-produced messages (hardening #2).
    """
    messages: list = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(task)
        async for message in client.receive_response():
            messages.append(message)
    return messages


def run_coding_agent(
    task: str,
    workspace_dir: str | Path,
    *,
    model: str,
    tools: tuple[str, ...] = DEFAULT_CODING_TOOLS,
    max_turns: int = 12,
    max_tool_calls: int = 40,
    max_budget_usd: float | None = 1.0,
    system_prompt: str | None = None,
    feedback: str | None = None,
) -> CodingResult:
    """Run ONE bounded, workspace-confined coding-agent loop; return a CodingResult.

    The synchronous wrapper over the async SDK stream (callers — the coding family —
    stay plain sync code, like llm.complete). Snapshots the workspace before/after and
    returns the unified diff. `feedback` (U2 human redo) is appended to the task.
    Callers inject a fake with this signature for offline runs; the real SDK runs only
    in a metered E2E.
    """
    root = Path(workspace_dir)
    root.mkdir(parents=True, exist_ok=True)
    resolved = str(root.resolve())
    prompt = task if not feedback else f"{task}\n\nReviewer feedback to address:\n{feedback}"
    # Tell the agent the CONCRETE workspace path. Without it the model invents a
    # placeholder like `/workspace`, which the path-confinement gate then (correctly)
    # denies — so the agent can never write (found in the real E2E). cwd is set to the
    # same dir, so relative paths also resolve here.
    full_system_prompt = (
        f"{system_prompt or _DEFAULT_SYSTEM_PROMPT}\n\n"
        f"Your workspace (and current working directory) is exactly: {resolved}\n"
        "Create and edit files INSIDE it, using paths relative to it (e.g. `hello.py`, "
        "`src/util.py`) or absolute paths under it. Never use any other directory such "
        "as /workspace."
    )
    before = workspace.snapshot(root)
    counter: dict = {"n": 0}
    options = ClaudeAgentOptions(
        system_prompt=full_system_prompt,
        model=model,
        # `tools` is the AVAILABLE set (llm.py lesson); the whitelist + path checks live
        # in can_use_tool. disallowed_tools is belt-and-suspenders against shell tools.
        tools=list(tools),
        disallowed_tools=list(_FORBIDDEN_TOOLS),
        permission_mode="default",  # can_use_tool is the gate (requires streaming mode)
        can_use_tool=_make_permission_cb(root, tools, max_tool_calls, counter),
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        cwd=str(root),  # the agent's working directory IS the workspace
        add_dirs=[],  # nothing outside the workspace is reachable
        setting_sources=[],  # ignore project/user settings for a clean run (mirror llm.py)
    )
    try:
        messages = anyio.run(_arun, prompt, options)
    except Exception as exc:
        log.exception("coding agent run failed")
        return CodingResult(
            summary=f"coding agent error: {exc}\n{_AUTH_HINT}",
            diff="", changed_files=[], status="failed",
        )
    tally = _tally(messages)
    status = _classify(
        tally, max_turns=max_turns, max_tool_calls=max_tool_calls, max_budget_usd=max_budget_usd
    )
    after = workspace.snapshot(root)
    diff, changed = workspace.compute_diff(before, after)
    log.info(
        "coding run: status=%s turns=%d tool_calls=%d changed=%d",
        status, tally.turns, tally.tool_calls, len(changed),
    )
    return CodingResult(
        summary=tally.summary or "(no summary)",
        diff=diff,
        changed_files=changed,
        status=status,
        turns=tally.turns,
        tool_calls=tally.tool_calls,
        cost_usd=tally.cost_usd,
    )
