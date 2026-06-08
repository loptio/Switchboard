"""Runner-level resolution wiring (Phase 8, U1) — offline.

Pins the two new runtime behaviours:
1. The runner resolves the WorkflowDef by id and threads it to the engine — `wf` is
   None for a code default (use the prebuilt module graph) and the resolved def for a
   DB override.
2. Agent assembly binds the resolved (DB-override-or-code) system_prompt into the
   agent callable, resolved ONCE per run, keyed by config_key. A DB AgentDef override
   reaches the model; the code default reproduces the pre-Phase-8 prompt byte-for-byte.
3. build_digest compiled from a (DB-style) WorkflowDef is behaviour-identical to the
   prebuilt module graph (the "generic engine == module graph" proof for overrides).
"""

from pathlib import Path

import agentdefs
import db
import orchestrator
import runner
import workflows
from agent import Critique, Digest, DigestItem
from agentdefs import AGENT_DEFS, render
from config import Config
from fetch import FeedItem

ITEMS = [FeedItem("t1", "l1", "s1", "p1")]
PASS = Critique(passed=True, issues=[])


def _digest(summary: str) -> Digest:
    return Digest([DigestItem("t1", "l1", summary)])


class _FakeSummarizer:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, items, n, model, *, feedback=None, **kw):
        self.calls.append(feedback)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class _FakeVerifier:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def __call__(self, digest, items, model, **kw):
        return self.outcomes.pop(0)


# --- (1) the runner threads wf to the engine -------------------------------

def test_runner_threads_wf_none_for_code_default_and_def_for_override(
    database, monkeypatch, tmp_path
):
    captured: dict = {}
    monkeypatch.setattr(runner, "fetch_feed", lambda url: list(ITEMS))
    monkeypatch.setattr(
        runner, "build_digest",
        lambda items, n, model, **kw: (captured.update(kw), _digest("x"))[1],
    )
    monkeypatch.setattr(runner, "send_digest", lambda d: None)
    monkeypatch.setattr(runner, "write_digest", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "load_config",
        lambda: Config(feed_url="x", count=1, output_dir=tmp_path, model="m"),
    )

    run = runner.run_once(workflow="news")
    assert run.status == "success"
    assert captured["wf"] is None  # code default -> the prebuilt module graph

    captured.clear()
    override = workflows.workflow_def_to_dict(workflows.DIGEST_DEF)
    override["params"] = {"max_redos": 5}
    db.create_workflow_def("news", override)
    runner.run_once(workflow="news")
    assert captured["wf"] is not None and captured["wf"].params["max_redos"] == 5


# --- (2) agent assembly binds the resolved prompt --------------------------

def _capture_system_prompt(fn) -> str:
    captured: dict = {}

    def fake_llm(prompt, *, system_prompt, model):
        captured["sp"] = system_prompt
        return '[{"one_line_summary": "s"}]'

    fn([FeedItem("t", "l", "s", "p")], 1, "m", llm=fake_llm)
    return captured["sp"]


def test_agent_assembly_uses_code_prompt_without_override(database):
    cfg = Config(feed_url="x", count=1, output_dir=Path("."), model="m", output_language="EN")
    fns = runner._agent_fns_for(workflows.DIGEST_DEF, cfg)
    assert _capture_system_prompt(fns["summarize_fn"]) == render(
        AGENT_DEFS["summarize"].system_prompt, language="EN"
    )


def test_agent_assembly_binds_db_override_prompt(database):
    ov = agentdefs.agent_def_to_dict(AGENT_DEFS["summarize"])
    ov["system_prompt"] = "CUSTOM {language}"
    db.create_agent_def("summarize", ov)
    cfg = Config(feed_url="x", count=1, output_dir=Path("."), model="m", output_language="日本語")
    fns = runner._agent_fns_for(workflows.DIGEST_DEF, cfg)
    assert _capture_system_prompt(fns["summarize_fn"]) == "CUSTOM 日本語"


# --- (3) engine-compiled override == module graph --------------------------

def test_build_digest_from_wf_matches_module_graph():
    fresh = workflows.workflow_def_from_dict(
        workflows.workflow_def_to_dict(workflows.DIGEST_DEF)
    )
    out_module = orchestrator.build_digest(
        ITEMS, 1, "m", summarize_fn=_FakeSummarizer(_digest("x")), verify_fn=_FakeVerifier(PASS)
    )
    out_db = orchestrator.build_digest(
        ITEMS, 1, "m",
        summarize_fn=_FakeSummarizer(_digest("x")), verify_fn=_FakeVerifier(PASS),
        wf=fresh,
    )
    assert out_module == out_db == _digest("x")
