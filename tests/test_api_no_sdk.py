"""Architectural contract: the web tier must NOT load the Claude Agent SDK — nor
the LangGraph orchestration engine.

The whole point of the handoff (web writes a pending Run; the worker executes it)
is that heavy worker-only machinery never enters the web process: the Claude
Agent SDK (Phase 3) and now the LangGraph engine (Phase 5 Unit 2). An in-process
sys.modules check is unreliable (other tests import `agent`/`orchestrator`), so we
build the app in a fresh subprocess and assert that neither the SDK, LangGraph,
nor the worker modules ever got imported.
"""

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_web_app_does_not_import_the_agent_sdk_or_langgraph():
    code = (
        "import sys\n"
        "from api.settings import APISettings\n"
        "import api.app\n"
        "api.app.create_app(APISettings(secret_key='x'))\n"
        "worker = {'runner', 'agent', 'orchestrator', 'llm', 'scheduler', 'mailer', "
        "'fetch', 'sources', 'brief_agent', 'brief_orchestrator', 'components', 'engine', "
        "'engine_fanout', 'coding_agent', 'coding_orchestrator', "
        "'meta_agent', 'meta_orchestrator'}\n"
        "leaked = sorted(m for m in sys.modules "
        "if 'claude_agent_sdk' in m or 'langgraph' in m or m in worker)\n"
        "assert not leaked, leaked\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout
