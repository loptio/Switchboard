"""Offline smoke test guarding the pinned Agent SDK contract (no model call).

Phase 1 lesson: an SDK upgrade once broke the `tools=[]` one-shot path (the
agent then burned its single turn on a default tool and errored). This pins the
version and asserts the option the agent relies on still exists and behaves —
without contacting Claude. If you intentionally bump the pin, update this test.
"""

from importlib.metadata import version


def test_sdk_version_is_pinned():
    assert version("claude-agent-sdk") == "0.2.93"


def test_options_accept_empty_tools():
    # The exact surface agent.py depends on must keep importing...
    from claude_agent_sdk import (  # noqa: F401
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    # ...and tools=[] (NO tools available) must still be accepted and preserved.
    options = ClaudeAgentOptions(
        system_prompt="x",
        model="claude-opus-4-8",
        tools=[],
        permission_mode="bypassPermissions",
        max_turns=3,
        setting_sources=[],
    )
    assert options.tools == []
