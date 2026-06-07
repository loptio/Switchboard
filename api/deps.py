"""Shared FastAPI dependencies: who's logged in, and CSRF protection.

Auth is cookie/session based (Starlette SessionMiddleware signs the cookie). The
authoritative CSRF token lives in that signed session; clients echo it back in a
header on unsafe methods. See api.routers.auth and the README "Phase 3" section.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

import db
from api.security import csrf_tokens_match

# Methods that don't change state never need a CSRF token.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def get_current_user(request: Request) -> db.User:
    """Resolve the logged-in user from the signed session, or 401.

    Looks the user up so a deleted/stale session is rejected (and cleared).
    """
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    user = db.get_user(uid)
    if user is None:
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    return user


def require_csrf(request: Request) -> None:
    """Enforce the CSRF token on state-changing requests (no-op on safe methods).

    Compares the `X-CSRF-Token` header against the authoritative token in the
    signed session (constant-time). SameSite=Lax already blocks the cookie on
    most cross-site writes; this token is the defense-in-depth layer and the
    explicit contract the SPA codes against. Safe to attach at router level —
    GETs pass straight through.
    """
    if request.method in _SAFE_METHODS:
        return
    expected = request.session.get("csrf")
    provided = request.headers.get(request.app.state.settings.csrf_header)
    if not csrf_tokens_match(expected, provided):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid"
        )
