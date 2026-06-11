"""Coding-agent seam (Phase 10a) — the ONLY agent-loop SDK caller.

This is the second seam, mirroring `llm.py`'s discipline for the TOOL-USING path:
`llm.complete` is the single tool-LESS, single-turn caller; `run_coding_agent` is
the single tool-LOOP caller (reason → call a workspace file tool → observe → repeat,
BOUNDED). Both import the Agent SDK and are worker-side; the web tier imports neither
(the no-SDK guard, tests/test_api_no_sdk.py). To swap the model / harness, change
ONLY this module — the coding family and the runner stay untouched.

This is the system's first crossing of the `tools=[]` boundary it deliberately built
around: from a text-in/text-out reasoning pipeline to an agent that acts. As of Phase
10b-2 it can also run SHELL commands (Bash) — the real capability jump, and the most
dangerous step. Several nets make an unattended, command-running agent safe enough:

  1. CONFINEMENT — cwd=workspace; a `can_use_tool` callback DENIES any FILE-tool path
     resolving outside the workspace or into `.git` (`workspace.confine` + `in_git_dir`,
     realpath-based: rejects ``..`` / absolute / symlink escapes).
  2. SANDBOX (Phase 10b-2, BORROWED not built — blueprint decision 12): the SDK/CLI's
     native bash sandbox (Seatbelt on macOS, bubblewrap on Linux) confines every COMMAND's
     filesystem to the workspace and DENIES network. We only wire it on (`_build_options`);
     the containment is a real OS mechanism, verified hands-on, not in offline tests.
  3. BOUNDED LOOP — hard caps on turns / tool-calls / budget + a per-command timeout
     (`BASH_*_TIMEOUT_MS`); over any cap → `stopped_limit` (cost + anti-hang).
  4. DIFF + COMMAND REVIEW — the family routes the diff AND the captured commands to a
     human gate; commands' side effects are not in the diff, so they are shown alongside.
     A worker-side `.git` integrity check (the family) refuses a run that touched git
     internals via a command (the un-sandboxable hook code-execution vector).

OFFLINE DISCIPLINE (non-negotiable): callers inject a fake with this module's
signature, so the whole coding family runs in tests with NO SDK, NO key, NO spend.
The real Agent SDK runs only in a metered E2E. The bound DECISION (`_classify`), the
permission callback (`_make_permission_cb`), the command fold (`_tally`) and the option
wiring (`_build_options`) are pure enough to unit-test offline without the SDK; the SDK
wiring itself (`_arun`) is exercised only in the real E2E. Offline tests verify the
WIRING (sandbox set, Bash available, commands captured); they cannot verify CONTAINMENT.

Auth: the SDK delegates to the Claude Code CLI subscription; do NOT set
ANTHROPIC_API_KEY (that bills the paid API) — same rule as llm.py.
"""

from __future__ import annotations

import contextlib
import logging
import os
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

# Toolset (Phase 10b-2: Bash added — the agent can run commands, contained by the
# sandbox). A PARAMETER so a real E2E can widen it. `Bash` is synchronous-only here
# (background-shell tools stay forbidden, below).
DEFAULT_CODING_TOOLS: tuple[str, ...] = ("Read", "Write", "Edit", "Bash")

# tool_input keys that carry a filesystem path — every one is confined to the
# workspace by the permission callback.
_FILE_PATH_KEYS = ("file_path", "path", "notebook_path")

# Tools that must never be available even if a caller widens `tools` (belt to the
# `tools` available-set suspenders). Phase 10b-2: Bash is now ALLOWED (sandboxed), but
# the BACKGROUND-shell tools stay forbidden — 10b-2-1 is synchronous bash only.
_FORBIDDEN_TOOLS = ["BashOutput", "KillShell"]

# Per-command bash timeout (Phase 10b-2): the bundled CLI honours these env vars.
# `default` applies when the model gives no timeout; `max` is a hard ceiling it cannot
# exceed — bounds a hung/runaway command (decision D).
DEFAULT_BASH_TIMEOUT_MS = 30_000
MAX_BASH_TIMEOUT_MS = 120_000

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent working strictly inside a single workspace directory. "
    "Use the provided file tools and the shell (Bash) to accomplish the task — you MAY "
    "run commands to build, test, lint, or run code. Your shell runs in a SANDBOX: the "
    "filesystem is restricted to the workspace and network access is denied, so keep "
    "everything inside the workspace and do not depend on the network. Never modify the "
    "repository's .git directory (it is off-limits). Make the smallest change that "
    "satisfies the task. When you are done, reply with a brief plain-text summary of "
    "what you changed and why."
)

_AUTH_HINT = (
    "If this is an auth error (e.g. 'Not logged in'), authenticate the Claude Code CLI "
    "with your subscription: run `claude`, then /login. Do NOT set ANTHROPIC_API_KEY "
    "(that bills the paid API)."
)

# ENV SCRUBBING (Phase 10b-2 escape-test fix). The sandbox governs FILES + NETWORK, not
# the environment — and the SDK spawns the CLI with `{**os.environ, **options.env}`
# (subprocess_cli.py, a MERGE: options.env can ADD but not REMOVE inherited keys). So a
# sandboxed bash would otherwise inherit EVERY worker secret (SECRET_KEY, SMTP_PASSWORD,
# LLM API keys, the DB URL). Those exfiltrate via the model channel (a command's output
# returns as a tool_result) or the diff/output — which network deny cannot stop.
#
# We DENYLIST (not allowlist) the secret-shaped keys: an allowlist starved the CLI's
# subscription auth ("Not logged in" — it needs more of the env than PATH/HOME, e.g. the
# macOS security-session vars), so we keep the CLI's environment INTACT and remove only
# the secrets. Subscription auth (via ~/.claude / keychain) keeps everything it needs.
# A key is a secret if its name contains one of these (case-insensitive) or is named
# explicitly below. `SESSION`/`AUTH` are deliberately NOT patterns — they match the macOS
# auth/session vars the CLI needs. The user re-verifies hands-on that no secret leaks.
_ENV_SECRET_SUBSTRINGS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "PASSPHRASE", "CREDENTIAL")
_ENV_SECRET_NAMES = ("DATABASE_URL",)  # secrets whose name isn't secret-shaped


def _is_secret_env(name: str) -> bool:
    """Whether an env var name looks like a secret that must not reach the sandboxed shell."""
    upper = name.upper()
    return name in _ENV_SECRET_NAMES or any(s in upper for s in _ENV_SECRET_SUBSTRINGS)


@contextlib.contextmanager
def _scrubbed_env():
    """Run the wrapped block with the worker's SECRETS removed from `os.environ`, then
    restore it. Keeps everything else (so the CLI's subscription auth still works) and
    drops only secret-shaped keys (so the sandboxed bash can't read them).

    LOAD-BEARING: the SDK inherits `os.environ` wholesale into the CLI subprocess, and
    `options.env` is only an OVERLAY (it cannot REMOVE inherited keys). So we pop the
    secrets here for the duration of the (blocking, single-threaded) SDK call. Restored
    in `finally` even if the SDK errors.

    CONCURRENCY CONSTRAINT (known, not a leak — a correctness limit): this mutates the
    PROCESS-GLOBAL os.environ for the whole agent-run window. Safe TODAY because the only
    callers run env-isolated: `run-once` is its own process, and the scheduler worker
    executes runs SEQUENTIALLY (BlockingScheduler, max_instances=1, a one-at-a-time drain
    loop — scheduler.py), so no other run reads the scrubbed env during the window. It
    becomes a hazard ONLY IF the worker is made CONCURRENT (thread pool / max_instances>1)
    AND a coding run overlaps an env-reading run (e.g. a digest emailing via
    SMTP_PASSWORD): that run would see the scrubbed env and fail. When coding runs move
    into a shared concurrent worker, fix it then — run coding solo in the worker, or make
    the clean env SUBPROCESS-level (don't touch the shared os.environ)."""
    secrets = {k: os.environ[k] for k in list(os.environ) if _is_secret_env(k)}
    for k in secrets:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        os.environ.update(secrets)


def _bash_env() -> dict[str, str]:
    """The per-command bash timeouts the bundled CLI enforces (passed via options.env)."""
    return {
        "BASH_DEFAULT_TIMEOUT_MS": str(DEFAULT_BASH_TIMEOUT_MS),
        "BASH_MAX_TIMEOUT_MS": str(MAX_BASH_TIMEOUT_MS),
    }


@dataclass(frozen=True)
class CodingResult:
    """What one coding-agent run produced — the seam's return contract.

    `status` is the family's routing signal:
    - "completed"     : the agent finished within all bounds.
    - "stopped_limit" : a turn / tool-call / budget cap (or a confinement denial) cut
                        the run short; `diff` holds whatever partial work exists. U2
                        routes this to human review; U1 marks the run failed.
    - "failed"        : the SDK call itself errored (auth/transport) — no usable result.

    `commands` (Phase 10b-2) are the shell commands the agent ran — shown at review
    alongside the diff, since a command's side effects need not appear in the diff.
    `git_tampered` (Phase 10b-2) lists `.git` paths a command modified; the family
    refuses to finalize such a run (the un-sandboxable hook code-execution vector). It
    is set by the family (worker-side), never by the seam itself.

    `review_*` (Phase 10c) carry the AUTOMATIC reviewer's outcome — the coder↔reviewer
    dialogue. They are set by the family's review node (worker-side), never by the seam:
    `review_verdict` ∈ {None (no auto-review ran), "approved", "not_converged"};
    `review_rounds` = how many reviewer passes happened; `review_issues` = the last
    round's open issues (shown at the human gate so the human sees what the AI reviewer
    flagged).
    """

    summary: str
    diff: str
    changed_files: list[str] = field(default_factory=list)
    status: str = "completed"
    turns: int = 0
    tool_calls: int = 0
    cost_usd: float | None = None
    commands: list[str] = field(default_factory=list)
    git_tampered: list[str] = field(default_factory=list)
    review_verdict: str | None = None
    review_rounds: int = 0
    review_issues: list[dict] = field(default_factory=list)


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
    commands: list[str] = field(default_factory=list)


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
    commands: list[str] = []
    for m in messages:
        if isinstance(m, AssistantMessage):
            turns += 1
            for block in m.content:
                if isinstance(block, TextBlock):
                    text_chunks.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls += 1
                    # Phase 10b-2: capture the shell commands the agent ran, for review.
                    if block.name == "Bash":
                        cmd = (block.input or {}).get("command")
                        if cmd:
                            commands.append(str(cmd))
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
        subtype=subtype, is_error=is_error, denials=denials, commands=commands,
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


def _build_options(
    full_system_prompt: str,
    root: Path,
    *,
    model: str,
    tools: tuple[str, ...],
    max_turns: int,
    max_tool_calls: int,
    max_budget_usd: float | None,
    counter: dict,
) -> ClaudeAgentOptions:
    """Assemble the ClaudeAgentOptions for one bounded, SANDBOXED coding run.

    PURE wiring — no SDK call — so the sandbox / Bash / timeout configuration is
    unit-testable offline without the SDK (the real CONTAINMENT is an OS mechanism,
    verified hands-on). The sandbox (`sandbox=...`) is the system's BORROWED isolation
    (Seatbelt/bubblewrap): filesystem locked to the workspace, network denied
    (no `allowedDomains`), and no command may escape it (`allowUnsandboxedCommands` False,
    `excludedCommands` empty). Per-command timeouts ride env vars the bundled CLI honours.
    """
    return ClaudeAgentOptions(
        system_prompt=full_system_prompt,
        model=model,
        # `tools` is the AVAILABLE set (llm.py lesson); the FILE-tool whitelist + path
        # checks live in can_use_tool; Bash containment is the sandbox. disallowed_tools
        # is belt-and-suspenders against the background-shell tools.
        tools=list(tools),
        disallowed_tools=list(_FORBIDDEN_TOOLS),
        permission_mode="default",  # can_use_tool gates the file tools (requires streaming)
        can_use_tool=_make_permission_cb(root, tools, max_tool_calls, counter),
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        cwd=str(root),  # the agent's working directory IS the workspace
        add_dirs=[],  # nothing outside the workspace is reachable
        setting_sources=[],  # ignore project/user settings for a clean run (mirror llm.py)
        # Phase 10b-2 — borrowed OS sandbox for COMMANDS (Seatbelt/bubblewrap):
        sandbox={
            "enabled": True,
            "allowUnsandboxedCommands": False,  # no command may bypass the sandbox
            "excludedCommands": [],  # nothing runs outside it (not even git/docker)
            # no "allowedDomains" -> network DENIED by default (decision E)
        },
        # Phase 10b-2 — the per-command bash timeouts (decision D). The worker's SECRETS
        # are removed from the inherited env by `_scrubbed_env()` in run_coding_agent (the
        # SDK merges options.env OVER os.environ, so the scrub must happen there).
        env=_bash_env(),
    )


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
    options = _build_options(
        full_system_prompt, root,
        model=model, tools=tools, max_turns=max_turns,
        max_tool_calls=max_tool_calls, max_budget_usd=max_budget_usd, counter=counter,
    )
    try:
        # Remove the worker's secrets from the process env for the duration of the SDK
        # call, so the sandboxed bash never inherits them (the sandbox does not cover env);
        # the CLI keeps the rest of its environment, so subscription auth still works.
        with _scrubbed_env():
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
        commands=tally.commands,
        # git_tampered is computed by the family (worker-side, post-seam); the seam
        # never sets it — see coding_orchestrator._coding_node.
    )
