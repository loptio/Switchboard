"""Definition resolution order (Phase 8, U1) — DB override else code default.

The no-regression safety net in miniature: with no DB row, resolution returns the
CODE default (by identity, so the runner knows to use the prebuilt module graph); a
DB row overrides it (a fresh object). Also pins the is_configured() gate: with no
engine configured, resolution never touches the DB.
"""

import agentdefs
import db
import defs_resolve
import workflows


def test_resolve_workflow_falls_back_to_code_by_identity(database):
    # empty DB -> the code default object itself (identity => "use the module graph")
    assert defs_resolve.resolve_workflow_def("news") is workflows.WORKFLOWS["news"]
    assert defs_resolve.resolve_workflow_def("brief") is workflows.WORKFLOWS["brief"]


def test_resolve_workflow_db_override(database):
    override = workflows.workflow_def_to_dict(workflows.DIGEST_DEF)
    override["params"] = {"max_redos": 5}
    db.create_workflow_def("news", override)
    wf = defs_resolve.resolve_workflow_def("news")
    assert wf is not workflows.WORKFLOWS["news"]  # a fresh DB-resolved object
    assert wf.params["max_redos"] == 5


def test_resolve_agent_falls_back_to_code(database):
    assert defs_resolve.resolve_agent_def("summarize") == agentdefs.AGENT_DEFS["summarize"]


def test_resolve_agent_db_override(database):
    ov = agentdefs.agent_def_to_dict(agentdefs.AGENT_DEFS["summarize"])
    ov["system_prompt"] = "OVERRIDDEN {language}"
    db.create_agent_def("summarize", ov)
    assert defs_resolve.resolve_agent_def("summarize").system_prompt == "OVERRIDDEN {language}"


def test_resolution_skips_db_when_not_configured(monkeypatch):
    # The is_configured() gate (public accessor): with no engine configured, resolution
    # never calls the DB layer — it falls straight to the code default.
    monkeypatch.setattr(db, "is_configured", lambda: False)
    touched: list = []
    monkeypatch.setattr(db, "get_workflow_def", lambda i: touched.append(("wf", i)))
    monkeypatch.setattr(db, "get_agent_def", lambda i: touched.append(("ag", i)))
    assert defs_resolve.resolve_workflow_def("news") is workflows.WORKFLOWS["news"]
    assert defs_resolve.resolve_agent_def("verify") == agentdefs.AGENT_DEFS["verify"]
    assert touched == []  # the DB was never queried


def test_resolution_degrades_to_code_on_db_error(database, monkeypatch):
    # A DB error (e.g. table missing / unreachable) degrades to the code default
    # rather than crashing a run.
    def boom(_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "get_workflow_def", boom)
    assert defs_resolve.resolve_workflow_def("news") is workflows.WORKFLOWS["news"]
