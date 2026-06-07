"""Offline tests for the read-only run/output endpoints.

Seed the DB through the data layer, then assert the API returns those real rows
(acceptance: GET /runs and /runs/{id}/output reflect the DB) and that every
endpoint requires login.
"""

from datetime import datetime, timezone

import db
from conftest import login

T0 = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)


def _seed_success_run(workflow="news", content="# Digest\nhello"):
    run = db.create_run(workflow=workflow, trigger="manual", now=T0)
    db.mark_running(run.id, now=T0)
    db.save_output(run.id, content, type="digest", data={"k": "v"}, now=T0)
    return db.mark_success(run.id, now=T0)


def test_runs_require_login(client):
    assert client.get("/runs").status_code == 401
    assert client.get("/runs/whatever").status_code == 401
    assert client.get("/runs/whatever/output").status_code == 401


def test_list_runs_returns_db_rows_newest_first(client, user):
    older = db.create_run(workflow="news", trigger="scheduled", now=T0)
    newer = _seed_success_run()
    login(client)

    rows = client.get("/runs").json()
    assert [r["id"] for r in rows] == [newer.id, older.id]  # newest first
    assert rows[0]["status"] == "success" and rows[0]["trigger"] == "manual"


def test_list_runs_filters_and_limit(client, user):
    _seed_success_run()
    db.create_run(workflow="other", trigger="manual", now=T0)  # pending
    login(client)

    assert len(client.get("/runs", params={"status": "success"}).json()) == 1
    assert len(client.get("/runs", params={"workflow": "other"}).json()) == 1
    assert client.get("/runs", params={"limit": 0}).status_code == 422  # ge=1


def test_get_run_by_id_and_404(client, user):
    run = _seed_success_run()
    login(client)

    assert client.get(f"/runs/{run.id}").json()["id"] == run.id
    assert client.get("/runs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_get_run_output(client, user):
    run = _seed_success_run(content="# Digest\nthe news")
    login(client)

    outs = client.get(f"/runs/{run.id}/output").json()
    assert len(outs) == 1
    assert outs[0]["type"] == "digest"
    assert "the news" in outs[0]["content"]
    assert outs[0]["data"] == {"k": "v"}


def test_get_output_404_for_unknown_run_but_empty_for_bare_run(client, user):
    bare = db.create_run(workflow="news", trigger="manual", now=T0)  # no output
    login(client)

    assert client.get(f"/runs/{bare.id}/output").json() == []  # exists, nothing yet
    assert (
        client.get("/runs/00000000-0000-0000-0000-000000000000/output").status_code
        == 404
    )
