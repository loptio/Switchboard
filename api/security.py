"""Security primitives: password hashing + CSRF tokens.

Mature libraries only — passlib(bcrypt) for passwords, `secrets` for tokens and
constant-time comparison. No hand-rolled crypto. Deliberately FastAPI-free and
dependency-light so the operator CLI can reuse the same hashing when it creates
users (one canonical CryptContext, no drift between CLI and API).
"""

from __future__ import annotations

import secrets

from passlib.context import CryptContext

# bcrypt is the stored scheme; `deprecated="auto"` lets us add a stronger scheme
# later and have verify() flag old hashes for rehash without breaking logins.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of `plain` (salt embedded). Store only this."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    """Constant-time check of `plain` against a stored bcrypt hash."""
    return _pwd_context.verify(plain, password_hash)


def new_csrf_token() -> str:
    """A fresh, URL-safe random CSRF token (the authoritative copy lives in the
    signed session; this value is mirrored to the client in a readable cookie)."""
    return secrets.token_urlsafe(32)


def csrf_tokens_match(expected: str | None, provided: str | None) -> bool:
    """Constant-time CSRF token comparison; False if either side is missing."""
    if not expected or not provided:
        return False
    return secrets.compare_digest(expected, provided)
