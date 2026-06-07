"""Offline auth tests — FastAPI TestClient over the in-memory test DB.

Covers the acceptance items: login succeeds and sets the session cookie; wrong
password and unknown user are rejected; protected endpoints require login; and
CSRF is enforced on state-changing requests (the cookie alone is not enough —
the token must be echoed in the header).
"""

from conftest import csrf_headers, login


def test_login_success_sets_session_and_csrf(client, user):
    r = login(client)
    assert r.status_code == 200
    assert r.json() == {"username": "admin"}
    # The signed session cookie AND the readable CSRF cookie are both set.
    assert client.cookies.get("session")
    assert client.cookies.get("csrftoken")


def test_login_wrong_password_rejected(client, user):
    r = login(client, password="not-the-password")
    assert r.status_code == 401
    assert not client.cookies.get("session")  # no session handed out


def test_login_unknown_user_rejected(client, user):
    assert login(client, username="ghost").status_code == 401


def test_me_requires_login(client):
    assert client.get("/auth/me").status_code == 401


def test_me_after_login(client, user):
    login(client)
    r = client.get("/auth/me")
    assert r.status_code == 200
    assert r.json() == {"username": "admin"}


def test_logout_clears_session(client, user):
    login(client)
    r = client.post("/auth/logout", headers=csrf_headers(client))
    assert r.status_code == 204
    assert client.get("/auth/me").status_code == 401  # session is gone


def test_logout_requires_csrf(client, user):
    login(client)
    # Authenticated, but no CSRF header -> rejected (the cookie is auto-sent, the
    # header is not — exactly what stops cross-site writes).
    assert client.post("/auth/logout").status_code == 403
    assert client.get("/auth/me").status_code == 200  # still logged in


def test_logout_requires_login(client):
    assert client.post("/auth/logout").status_code == 401


def test_healthz_is_open(client):
    assert client.get("/healthz").json() == {"status": "ok"}
