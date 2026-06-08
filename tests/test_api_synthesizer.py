"""Synthesizer API — workflow/agent CRUD + components (Phase 8, U2).

Pins: auth + CSRF on every endpoint; built-in defs are read-only (clone to edit);
save-time validation rejects a broken def with 400; CRUD round-trips; "run now"
reuses the pending-run handoff; the components manifest is served as pure data.
"""

import copy

from conftest import csrf_headers, login

# A minimal VALID digest-family workflow (reachable END, registered refs).
MINI = {
    "id": "mini",
    "entry": "summarize",
    "params": {"max_redos": 1},
    "source_ref": "hn_feed",
    "output_ref": "digest",
    "nodes": [
        {
            "id": "summarize", "kind": "step",
            "handler_ref": "digest_summarize", "agent_ref": "summarize",
            "config_key": "summarize_fn", "next": "finalize_gate",
        },
        {"id": "finalize_gate", "kind": "step", "handler_ref": "digest_finalize_gate", "next": "__end__"},
    ],
}


def _create(client, definition=None, name="Mini"):
    return client.post(
        "/workflows",
        json={"definition": definition or MINI, "name": name},
        headers=csrf_headers(client),
    )


# --- auth + CSRF -----------------------------------------------------------

def test_workflows_requires_login(client):
    assert client.get("/workflows").status_code == 401


def test_components_requires_login(client):
    assert client.get("/components").status_code == 401


def test_create_requires_csrf(client, user):
    login(client)
    assert client.post("/workflows", json={"definition": MINI}).status_code == 403


# --- list / get: built-ins are present + read-only -------------------------

def test_list_includes_builtins(client, user):
    login(client)
    r = client.get("/workflows", headers=csrf_headers(client))
    assert r.status_code == 200
    by_id = {d["def_id"]: d for d in r.json()}
    assert by_id["news"]["builtin"] is True
    assert by_id["brief"]["builtin"] is True


def test_get_builtin_workflow(client, user):
    login(client)
    r = client.get("/workflows/news", headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.json()["builtin"] is True
    assert r.json()["definition"]["id"] == "news"


# --- create + save-time validation -----------------------------------------

def test_create_and_get_roundtrip(client, user):
    login(client)
    assert _create(client).status_code == 201
    g = client.get("/workflows/mini", headers=csrf_headers(client))
    assert g.status_code == 200
    assert g.json()["builtin"] is False
    assert g.json()["definition"]["entry"] == "summarize"


def test_create_rejects_invalid_def_400(client, user):
    login(client)
    bad = copy.deepcopy(MINI)
    bad["nodes"][0]["handler_ref"] = "ghost"
    r = _create(client, bad)
    assert r.status_code == 400 and "ghost" in r.json()["detail"]


def test_create_rejects_builtin_id_409(client, user):
    login(client)
    d = copy.deepcopy(MINI)
    d["id"] = "news"
    assert _create(client, d).status_code == 409


def test_create_duplicate_409(client, user):
    login(client)
    assert _create(client).status_code == 201
    assert _create(client).status_code == 409


# --- patch -----------------------------------------------------------------

def test_patch_db_def(client, user):
    login(client)
    _create(client)
    d = copy.deepcopy(MINI)
    d["params"] = {"max_redos": 3}
    r = client.patch(
        "/workflows/mini", json={"definition": d, "name": "Renamed"}, headers=csrf_headers(client)
    )
    assert r.status_code == 200
    assert r.json()["definition"]["params"]["max_redos"] == 3
    assert r.json()["name"] == "Renamed"


def test_patch_builtin_409(client, user):
    login(client)
    assert client.patch(
        "/workflows/news", json={"name": "x"}, headers=csrf_headers(client)
    ).status_code == 409


def test_patch_missing_404(client, user):
    login(client)
    assert client.patch(
        "/workflows/nope", json={"name": "x"}, headers=csrf_headers(client)
    ).status_code == 404


def test_patch_invalid_def_400(client, user):
    login(client)
    _create(client)
    bad = copy.deepcopy(MINI)
    bad["nodes"][1]["next"] = "ghost"  # dangling edge
    assert client.patch(
        "/workflows/mini", json={"definition": bad}, headers=csrf_headers(client)
    ).status_code == 400


# --- clone (decision E) ----------------------------------------------------

def test_clone_builtin_creates_editable(client, user):
    login(client)
    r = client.post(
        "/workflows/news/clone", json={"new_id": "my-news", "name": "Mine"}, headers=csrf_headers(client)
    )
    assert r.status_code == 201
    assert r.json()["def_id"] == "my-news"
    assert r.json()["builtin"] is False
    assert r.json()["definition"]["id"] == "my-news"
    g = client.get("/workflows/my-news", headers=csrf_headers(client))
    assert g.json()["builtin"] is False


# --- delete ----------------------------------------------------------------

def test_delete_db_def(client, user):
    login(client)
    _create(client)
    assert client.delete("/workflows/mini", headers=csrf_headers(client)).status_code == 204
    assert client.get("/workflows/mini", headers=csrf_headers(client)).status_code == 404


def test_delete_builtin_409(client, user):
    login(client)
    assert client.delete("/workflows/news", headers=csrf_headers(client)).status_code == 409


# --- run now reuses the pending-run handoff --------------------------------

def test_run_now_enqueues_pending(client, user):
    login(client)
    _create(client)
    r = client.post("/runs", json={"workflow": "mini"}, headers=csrf_headers(client))
    assert r.status_code == 202
    assert r.json()["workflow"] == "mini" and r.json()["status"] == "pending"


# --- components manifest ---------------------------------------------------

def test_components_manifest(client, user):
    login(client)
    r = client.get("/components", headers=csrf_headers(client))
    assert r.status_code == 200
    m = r.json()
    assert "digest_summarize" in m["node_handlers"]
    assert "summarize" in m["agents"]
    assert {f["id"] for f in m["families"]} == {"digest", "brief"}


# --- agents (symmetric) ----------------------------------------------------

def test_agents_list_and_clone(client, user):
    login(client)
    ids = {a["agent_id"] for a in client.get("/agents", headers=csrf_headers(client)).json()}
    assert {"summarize", "verify", "filter", "summarize_item", "perspective"} <= ids
    c = client.post(
        "/agents/summarize/clone", json={"new_id": "my-sum"}, headers=csrf_headers(client)
    )
    assert c.status_code == 201 and c.json()["definition"]["id"] == "my-sum"


def test_agents_create_validates(client, user):
    login(client)
    bad = {"definition": {"id": "x", "system_prompt": "hi", "prompt_builder_ref": "nope", "parser_ref": "nope"}}
    assert client.post("/agents", json=bad, headers=csrf_headers(client)).status_code == 400


def test_agent_edit_then_clone_builtin_readonly(client, user):
    login(client)
    assert client.patch(
        "/agents/summarize", json={"name": "x"}, headers=csrf_headers(client)
    ).status_code == 409
