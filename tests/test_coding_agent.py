"""Offline tests for the coding seam's pure surfaces (Phase 10a) — NO SDK call.

The seam (run_coding_agent) is the only Agent SDK caller and runs only in a metered
E2E. But its safety-critical pieces are pure and tested here offline:
- `_classify` — the BOUNDED-LOOP decision (over turns/tool-calls/budget/denials → stop).
- `_make_permission_cb` — the CONFINEMENT net (tool whitelist + path-escape deny + cap),
  the security companion to workspace.confine (hardening #1).
- `_tally` — folding the SDK message stream into bound-relevant facts (built from real
  SDK message objects, constructed offline — no SDK call).

Constructing SDK message dataclasses is offline (no network); only `query()` would hit
the model, and it is never called here.
"""

import os

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

import coding_agent as C


def _tally(**kw) -> C._Tally:
    return C._Tally(**kw)


# --- _classify: the bounded-loop decision ----------------------------------

def test_classify_completed_within_bounds():
    t = _tally(turns=3, tool_calls=5, cost_usd=0.1, subtype="success", is_error=False)
    assert C._classify(t, max_turns=12, max_tool_calls=40, max_budget_usd=1.0) == "completed"


def test_classify_stopped_on_max_turns_subtype():
    t = _tally(turns=12, subtype="error_max_turns", is_error=True)
    assert C._classify(t, max_turns=12, max_tool_calls=40, max_budget_usd=1.0) == "stopped_limit"


def test_classify_stopped_when_turns_exceed_cap():
    t = _tally(turns=13, subtype="success")
    assert C._classify(t, max_turns=12, max_tool_calls=40, max_budget_usd=1.0) == "stopped_limit"


def test_classify_stopped_when_tool_calls_exceed_cap():
    t = _tally(tool_calls=41, subtype="success")
    assert C._classify(t, max_turns=12, max_tool_calls=40, max_budget_usd=1.0) == "stopped_limit"


def test_classify_stopped_on_permission_denial():
    # a confinement/cap denial means the run was cut short -> route to review (U2).
    t = _tally(denials=1, subtype="success")
    assert C._classify(t, max_turns=12, max_tool_calls=40, max_budget_usd=1.0) == "stopped_limit"


def test_classify_stopped_when_over_budget():
    t = _tally(cost_usd=1.0, subtype="success")
    assert C._classify(t, max_turns=12, max_tool_calls=40, max_budget_usd=1.0) == "stopped_limit"
    # budget None disables the budget check
    assert C._classify(t, max_turns=12, max_tool_calls=40, max_budget_usd=None) == "completed"


def test_classify_failed_on_plain_error():
    t = _tally(is_error=True, subtype="error_during_execution")
    assert C._classify(t, max_turns=12, max_tool_calls=40, max_budget_usd=1.0) == "failed"


# --- _make_permission_cb: confinement + whitelist + cap --------------------

def _decide(cb, name, tool_input):
    return anyio.run(cb, name, tool_input, None)


def test_permission_allows_whitelisted_in_workspace(tmp_path):
    cb = C._make_permission_cb(tmp_path, ("Read", "Write", "Edit"), 40, {"n": 0})
    res = _decide(cb, "Write", {"file_path": str(tmp_path / "a.txt")})
    assert isinstance(res, PermissionResultAllow)


def test_permission_denies_non_whitelisted_tool(tmp_path):
    cb = C._make_permission_cb(tmp_path, ("Read", "Write", "Edit"), 40, {"n": 0})
    res = _decide(cb, "Bash", {"command": "rm -rf /"})
    assert isinstance(res, PermissionResultDeny)


def test_permission_denies_path_escape(tmp_path):
    cb = C._make_permission_cb(tmp_path, ("Read", "Write", "Edit"), 40, {"n": 0})
    for bad in ("../escape.txt", "/etc/passwd", str(tmp_path.parent / "sibling.txt")):
        res = _decide(cb, "Write", {"file_path": bad})
        assert isinstance(res, PermissionResultDeny), bad
        assert res.interrupt is True  # a path escape hard-stops the loop


def test_permission_denies_writes_into_dot_git(tmp_path):
    # Phase 10b-1: the agent edits a REAL repo; git internals are off-limits.
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    cb = C._make_permission_cb(tmp_path, ("Read", "Write", "Edit"), 40, {"n": 0})
    for bad in (".git/config", str(tmp_path / ".git" / "hooks" / "evil"), ".git"):
        res = _decide(cb, "Write", {"file_path": bad})
        assert isinstance(res, PermissionResultDeny), bad
        assert res.interrupt is True  # a .git write hard-stops the loop
    # a normal in-workspace file is still allowed
    assert isinstance(
        _decide(cb, "Write", {"file_path": str(tmp_path / "src.py")}), PermissionResultAllow
    )


def test_permission_path_escape_does_not_consume_the_call_budget(tmp_path):
    # order matters: a denied-for-escape call must not eat the tool-call budget.
    counter = {"n": 0}
    cb = C._make_permission_cb(tmp_path, ("Write",), 1, counter)
    _decide(cb, "Write", {"file_path": "/etc/passwd"})  # denied (escape)
    assert counter["n"] == 0
    assert isinstance(_decide(cb, "Write", {"file_path": str(tmp_path / "ok.txt")}), PermissionResultAllow)
    assert counter["n"] == 1


def test_permission_denies_once_over_call_cap(tmp_path):
    counter = {"n": 0}
    cb = C._make_permission_cb(tmp_path, ("Write",), 2, counter)
    inp = {"file_path": str(tmp_path / "a.txt")}
    assert isinstance(_decide(cb, "Write", inp), PermissionResultAllow)  # 1
    assert isinstance(_decide(cb, "Write", inp), PermissionResultAllow)  # 2
    over = _decide(cb, "Write", inp)  # 3 -> over cap
    assert isinstance(over, PermissionResultDeny) and over.interrupt is True


# --- _tally: fold the SDK message stream -----------------------------------

def test_tally_counts_turns_tools_cost_and_summary():
    messages = [
        AssistantMessage(
            content=[TextBlock(text="working"), ToolUseBlock(id="1", name="Write", input={})],
            model="m",
        ),
        AssistantMessage(content=[ToolUseBlock(id="2", name="Edit", input={})], model="m"),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=2, session_id="s", total_cost_usd=0.25, result="done: edited 1 file",
        ),
    ]
    t = C._tally(messages)
    assert t.turns == 2 and t.tool_calls == 2
    assert t.cost_usd == 0.25 and t.summary == "done: edited 1 file"
    assert t.is_error is False and t.subtype == "success" and t.denials == 0


def test_tally_falls_back_to_text_when_no_result():
    messages = [AssistantMessage(content=[TextBlock(text="just text")], model="m")]
    assert C._tally(messages).summary == "just text"


def test_tally_captures_bash_commands_in_order(tmp_path):
    # Phase 10b-2: the shell commands the agent ran are folded out for review.
    messages = [
        AssistantMessage(
            content=[
                ToolUseBlock(id="1", name="Bash", input={"command": "pytest -q"}),
                ToolUseBlock(id="2", name="Write", input={"file_path": "a.py"}),
                ToolUseBlock(id="3", name="Bash", input={"command": "ls -la"}),
            ],
            model="m",
        ),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.1, result="done",
        ),
    ]
    t = C._tally(messages)
    assert t.commands == ["pytest -q", "ls -la"]  # only Bash, in order
    assert t.tool_calls == 3  # every tool use still counted toward the bound


# --- Phase 10b-2: the sandbox / Bash / timeout WIRING (offline; no SDK call) ----

def test_default_toolset_adds_bash_and_forbids_only_background_shells():
    assert "Bash" in C.DEFAULT_CODING_TOOLS  # the agent can run commands now
    assert "Bash" not in C._FORBIDDEN_TOOLS
    assert set(C._FORBIDDEN_TOOLS) == {"BashOutput", "KillShell"}  # background shells off


def _opts(tmp_path):
    return C._build_options(
        "sys", tmp_path, model="m", tools=C.DEFAULT_CODING_TOOLS,
        max_turns=12, max_tool_calls=40, max_budget_usd=1.0, counter={"n": 0},
    )


def test_build_options_enables_sandbox_and_denies_network(tmp_path):
    opts = _opts(tmp_path)
    assert opts.sandbox["enabled"] is True
    assert opts.sandbox["allowUnsandboxedCommands"] is False  # no command may bypass it
    assert opts.sandbox["excludedCommands"] == []  # nothing runs outside the sandbox
    assert "network" not in opts.sandbox  # no allowlist -> network denied by default
    # workspace is the writable root; nothing outside it is reachable
    assert opts.cwd == str(tmp_path) and opts.add_dirs == []


def test_build_options_curated_network_allowlist(tmp_path):
    # an OPERATOR allowlist opens exactly those domains; the rest of the sandbox is intact
    opts = C._build_options(
        "sys", tmp_path, model="m", tools=C.DEFAULT_CODING_TOOLS,
        max_turns=12, max_tool_calls=40, max_budget_usd=1.0, counter={"n": 0},
        allowed_domains=("pypi.org", "files.pythonhosted.org"),
    )
    assert opts.sandbox["network"] == {"allowedDomains": ["pypi.org", "files.pythonhosted.org"]}
    assert opts.sandbox["enabled"] is True and opts.sandbox["allowUnsandboxedCommands"] is False


def test_build_options_empty_allowlist_omits_network_byte_for_byte(tmp_path):
    # the default (no allowlist) must produce the SAME sandbox dict as before the feature
    default = _opts(tmp_path).sandbox
    explicit_empty = C._build_options(
        "sys", tmp_path, model="m", tools=C.DEFAULT_CODING_TOOLS,
        max_turns=12, max_tool_calls=40, max_budget_usd=1.0, counter={"n": 0},
        allowed_domains=(),
    ).sandbox
    assert default == explicit_empty == {
        "enabled": True, "allowUnsandboxedCommands": False, "excludedCommands": [],
    }


def test_build_options_makes_bash_available_and_bounds_command_timeout(tmp_path):
    opts = _opts(tmp_path)
    assert "Bash" in opts.tools  # available to the model
    assert "Bash" not in opts.disallowed_tools
    assert opts.disallowed_tools == ["BashOutput", "KillShell"]
    assert opts.env["BASH_DEFAULT_TIMEOUT_MS"] == str(C.DEFAULT_BASH_TIMEOUT_MS)
    assert opts.env["BASH_MAX_TIMEOUT_MS"] == str(C.MAX_BASH_TIMEOUT_MS)


# --- Phase 10b-2 escape fix: the sandboxed shell must NOT inherit worker secrets ----
# Denylist (not allowlist): an allowlist starved the CLI's subscription auth ("Not logged
# in"), so we keep the CLI's environment and drop only the secret-shaped keys.

def test_is_secret_env_flags_secrets_but_keeps_auth_infra():
    for secret in ("SECRET_KEY", "SMTP_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                   "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "GITHUB_TOKEN", "DATABASE_URL"):
        assert C._is_secret_env(secret), secret
    # CLI/shell infra — incl. the macOS auth/session vars the allowlist wrongly dropped
    for keep in ("PATH", "HOME", "LANG", "LC_ALL", "SHELL", "USER", "TMPDIR",
                 "SECURITYSESSIONID", "SSH_AUTH_SOCK", "XPC_SERVICE_NAME"):
        assert not C._is_secret_env(keep), keep


def test_secret_overlay_neutralises_secrets_without_mutating_environ(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "sk-leak")
    monkeypatch.setenv("SMTP_PASSWORD", "pw-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("SECURITYSESSIONID", "auth-infra")  # CLI auth needs this -> keep

    overlay = C._secret_overlay()

    # every secret is overridden to "" (so the SDK's {**os.environ, **options.env} merge
    # hands the sandboxed bash an EMPTY value, never the real one)
    for secret in ("SECRET_KEY", "SMTP_PASSWORD", "ANTHROPIC_API_KEY", "DATABASE_URL"):
        assert overlay[secret] == ""
    # auth/shell infra the CLI needs is NOT in the overlay (passes through untouched)
    assert "SECURITYSESSIONID" not in overlay
    assert "PATH" not in overlay
    # THE POINT: os.environ is NEVER mutated — a concurrent env-reading run still sees the
    # real secret in its own process env (the old pop-and-restore made this unsafe).
    assert os.environ["SECRET_KEY"] == "sk-leak"
    assert os.environ["DATABASE_URL"] == "postgresql://u:p@h/db"
    # simulate the SDK merge: the overlay wins, the subprocess sees empty, the process real
    merged = {**os.environ, **overlay}
    assert merged["SMTP_PASSWORD"] == "" and os.environ["SMTP_PASSWORD"] == "pw-leak"


def test_build_options_env_neutralises_secrets_and_keeps_timeouts(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "pw-leak")
    opts = _opts(tmp_path)
    # options.env now CARRIES the secret as "" (the subprocess-level scrub) — not the value
    assert opts.env["SMTP_PASSWORD"] == ""
    assert "pw-leak" not in opts.env.values()
    assert opts.env["BASH_MAX_TIMEOUT_MS"] == str(C.MAX_BASH_TIMEOUT_MS)
    # building options must not have mutated the process env
    assert os.environ["SMTP_PASSWORD"] == "pw-leak"
