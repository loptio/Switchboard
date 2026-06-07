"""API settings — from env vars, never hardcoded. Injectable for offline tests.

Mirrors the project rule (config.py / db.settings): the one secret is SECRET_KEY
(signs the session cookie) and it comes from the environment only. `load_settings`
reads the process env; tests construct APISettings directly with a fixed secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Bounded login lifetime by default — a session expires; it is never permanent.
# SessionMiddleware re-issues the cookie on each response, so this acts as an
# idle (sliding) timeout: 12 hours of inactivity ends the session.
DEFAULT_SESSION_MAX_AGE = 12 * 60 * 60  # 12 hours, in seconds

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class APISettings:
    secret_key: str
    cookie_secure: bool = False  # add Secure flag (only over HTTPS)
    cors_allow_origins: tuple[str, ...] = ()
    session_max_age: int = DEFAULT_SESSION_MAX_AGE
    session_cookie: str = "session"
    csrf_cookie: str = "csrftoken"  # readable by JS; mirrors the session's CSRF token
    csrf_header: str = "X-CSRF-Token"  # where clients echo the token on writes


def _positive_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


def load_settings() -> APISettings:
    """Build settings from the environment. Raises if SECRET_KEY is missing."""
    secret = (os.getenv("SECRET_KEY") or "").strip()
    if not secret:
        raise RuntimeError(
            "SECRET_KEY is not set — it signs the session cookie. Generate one with\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
            "and put it in your .env (gitignored). See .env.example."
        )
    origins = tuple(
        o.strip()
        for o in (os.getenv("CORS_ALLOW_ORIGINS") or "").split(",")
        if o.strip()
    )
    return APISettings(
        secret_key=secret,
        cookie_secure=(os.getenv("COOKIE_SECURE") or "").strip().lower() in _TRUE,
        cors_allow_origins=origins,
        session_max_age=_positive_int("SESSION_MAX_AGE", DEFAULT_SESSION_MAX_AGE),
    )
