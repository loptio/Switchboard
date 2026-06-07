"""Auth endpoints: login / logout / me (session-cookie based).

On login we store the user id in the signed session and mint a CSRF token,
mirrored to a JS-readable `csrftoken` cookie so the SPA can echo it on writes.
Login is exempt from CSRF (no session yet — credentials are the proof); logout
is protected. See api.deps for the dependencies and api.security for the crypto.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

import db
from api.deps import get_current_user, require_csrf
from api.schemas import LoginIn, UserOut
from api.security import new_csrf_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_csrf_cookie(request: Request, response: Response, token: str) -> None:
    """Mirror the session's CSRF token into a readable cookie for the SPA."""
    settings = request.app.state.settings
    response.set_cookie(
        settings.csrf_cookie,
        token,
        max_age=settings.session_max_age,
        httponly=False,  # the SPA must read this to echo it in the CSRF header
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


@router.post("/login", response_model=UserOut)
def login(creds: LoginIn, request: Request, response: Response) -> UserOut:
    user = db.get_user_by_username(creds.username)
    # Verify even on unknown user would be ideal for timing; bcrypt.verify on a
    # missing hash isn't available, so we accept the tiny timing signal here
    # (single-user, local). The error is identical either way (no user enumeration).
    if user is None or not verify_password(creds.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid username or password"
        )
    token = new_csrf_token()  # fresh token each login (rotate on auth)
    request.session["uid"] = user.id
    request.session["csrf"] = token
    _set_csrf_cookie(request, response, token)
    return UserOut(username=user.username)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)
def logout(request: Request) -> Response:
    request.session.clear()  # empties the session -> middleware expires the cookie
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(request.app.state.settings.csrf_cookie, path="/")
    return resp


@router.get("/me", response_model=UserOut)
def me(
    request: Request,
    response: Response,
    user: db.User = Depends(get_current_user),
) -> UserOut:
    # Re-issue the CSRF cookie so a freshly loaded SPA (session still valid) can
    # always recover the token to make writes.
    token = request.session.get("csrf") or new_csrf_token()
    request.session["csrf"] = token
    _set_csrf_cookie(request, response, token)
    return UserOut(username=user.username)
