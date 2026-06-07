"""Architectural contract: the web tier must NOT load the Claude Agent SDK.

The whole point of the handoff (web writes a pending Run; the worker executes it)
is that the SDK never enters the web process. An in-process sys.modules check is
unreliable (other tests import `agent`), so we build the app in a fresh
subprocess and assert the SDK — and the worker modules — never got imported.
"""

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_web_app_does_not_import_the_agent_sdk():
    code = (
        "import sys\n"
        "from api.settings import APISettings\n"
        "import api.app\n"
        "api.app.create_app(APISettings(secret_key='x'))\n"
        "worker = {'runner', 'agent', 'orchestrator', 'llm', 'scheduler', 'mailer', 'fetch'}\n"
        "leaked = sorted(m for m in sys.modules "
        "if 'claude_agent_sdk' in m or m in worker)\n"
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
