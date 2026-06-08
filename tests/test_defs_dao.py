"""DAO CRUD for workflow_defs / agent_defs (Phase 8, U1) — offline SQLite.

Mirrors the schedule-CRUD conventions: dup natural key -> ValueError, missing ->
LookupError, partial update via keyword, JSON definition round-trips as a dict.
"""

import pytest

import db


def test_workflow_def_crud(database):
    wf = db.create_workflow_def("flow", {"id": "flow"}, name="F", description="d")
    assert wf.def_id == "flow" and wf.name == "F" and wf.updated_at is None
    assert db.get_workflow_def("flow").definition == {"id": "flow"}
    assert [w.def_id for w in db.list_workflow_defs()] == ["flow"]

    up = db.update_workflow_def("flow", definition={"id": "flow", "x": 1}, name="F2")
    assert up.definition == {"id": "flow", "x": 1}
    assert up.name == "F2" and up.description == "d"  # description untouched
    assert up.updated_at is not None

    db.delete_workflow_def("flow")
    assert db.get_workflow_def("flow") is None


def test_workflow_def_duplicate_raises(database):
    db.create_workflow_def("flow", {"id": "flow"})
    with pytest.raises(ValueError):
        db.create_workflow_def("flow", {"id": "flow"})


def test_workflow_def_missing_raises(database):
    with pytest.raises(LookupError):
        db.update_workflow_def("nope", name="x")
    with pytest.raises(LookupError):
        db.delete_workflow_def("nope")


def test_agent_def_crud(database):
    ad = db.create_agent_def("a", {"id": "a", "system_prompt": "hi"}, name="A")
    assert ad.agent_id == "a" and ad.name == "A"
    assert db.get_agent_def("a").definition["system_prompt"] == "hi"
    assert [a.agent_id for a in db.list_agent_defs()] == ["a"]

    db.update_agent_def("a", definition={"id": "a", "system_prompt": "yo"})
    assert db.get_agent_def("a").definition["system_prompt"] == "yo"

    db.delete_agent_def("a")
    assert db.get_agent_def("a") is None


def test_agent_def_duplicate_raises(database):
    db.create_agent_def("a", {"id": "a"})
    with pytest.raises(ValueError):
        db.create_agent_def("a", {"id": "a"})


def test_get_missing_returns_none(database):
    assert db.get_workflow_def("nope") is None
    assert db.get_agent_def("nope") is None
