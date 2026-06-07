"""Offline tests for schedule CRUD.

Covers create (with cron validation + primed next_run_at), list, PATCH
(enable/disable and cron/tz edits), DELETE, CSRF enforcement, and the acceptance
item that a running scheduler picks up API changes without a restart (it reads
db.list_due_schedules every tick — no in-memory state to invalidate).
"""

from datetime import datetime, timezone

import db
from conftest import csrf_headers, login

FUTURE = datetime(2030, 1, 1, tzinfo=timezone.utc)


def _login(client):
    login(client)
    return csrf_headers(client)


def test_schedule_endpoints_require_login(client):
    assert client.get("/schedules").status_code == 401
    assert client.post("/schedules", json={"cron": "0 6 * * *"}).status_code == 401


def test_create_schedule_primes_next_run(client, user):
    h = _login(client)
    r = client.post("/schedules", json={"cron": "0 6 * * *", "tz": "UTC"}, headers=h)
    assert r.status_code == 201
    body = r.json()
    assert body["cron"] == "0 6 * * *" and body["enabled"] is True
    assert body["next_run_at"] is not None  # primed -> no immediate catch-up run
    assert len(db.list_schedules()) == 1  # reflected in the DB


def test_create_requires_csrf(client, user):
    login(client)  # authenticated, but send no CSRF header
    assert client.post("/schedules", json={"cron": "0 6 * * *"}).status_code == 403


def test_create_rejects_bad_cron(client, user):
    h = _login(client)
    r = client.post("/schedules", json={"cron": "not a cron"}, headers=h)
    assert r.status_code == 400


def test_list_schedules(client, user):
    h = _login(client)
    client.post("/schedules", json={"cron": "0 6 * * *"}, headers=h)
    assert len(client.get("/schedules").json()) == 1


def test_patch_toggle_enabled(client, user):
    h = _login(client)
    sid = client.post("/schedules", json={"cron": "0 6 * * *"}, headers=h).json()["id"]

    r = client.patch(f"/schedules/{sid}", json={"enabled": False}, headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert db.get_schedule(sid).enabled is False


def test_patch_cron_recomputes_next_run(client, user):
    h = _login(client)
    created = client.post("/schedules", json={"cron": "0 6 * * *"}, headers=h).json()

    r = client.patch(f"/schedules/{created['id']}", json={"cron": "30 7 * * *"}, headers=h)
    assert r.status_code == 200
    assert r.json()["cron"] == "30 7 * * *"
    assert r.json()["next_run_at"] != created["next_run_at"]  # re-primed


def test_patch_bad_cron_400_and_unknown_404(client, user):
    h = _login(client)
    sid = client.post("/schedules", json={"cron": "0 6 * * *"}, headers=h).json()["id"]

    assert client.patch(f"/schedules/{sid}", json={"cron": "bad"}, headers=h).status_code == 400
    missing = "/schedules/00000000-0000-0000-0000-000000000000"
    assert client.patch(missing, json={"enabled": False}, headers=h).status_code == 404


def test_delete_schedule(client, user):
    h = _login(client)
    sid = client.post("/schedules", json={"cron": "0 6 * * *"}, headers=h).json()["id"]

    assert client.delete(f"/schedules/{sid}", headers=h).status_code == 204
    assert db.get_schedule(sid) is None
    assert client.delete(f"/schedules/{sid}", headers=h).status_code == 404  # gone


def test_api_changes_visible_to_worker_without_restart(client, user):
    h = _login(client)
    sid = client.post("/schedules", json={"cron": "0 6 * * *"}, headers=h).json()["id"]

    # The worker's view (list_due_schedules) sees it immediately...
    assert sid in [s.id for s in db.list_due_schedules(FUTURE)]
    # ...and disabling via the API removes it from that view — no restart needed.
    client.patch(f"/schedules/{sid}", json={"enabled": False}, headers=h)
    assert sid not in [s.id for s in db.list_due_schedules(FUTURE)]
