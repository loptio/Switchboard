"""Offline tests for the users data-access functions (Phase 3, Unit 1).

These exercise the DB contract the auth layer builds on — storage of the bcrypt
hash, unique-username enforcement, and lookups — using the same in-memory SQLite
fixture as the rest of the suite. No hashing happens here (that's the API layer);
we store opaque hash strings.
"""

import pytest

import db


def test_create_and_fetch_user(database):
    user = db.create_user("admin", "hash-abc")

    assert user.username == "admin"
    assert user.password_hash == "hash-abc"
    assert user.id and user.created_at is not None

    by_name = db.get_user_by_username("admin")
    assert by_name == user
    by_id = db.get_user(user.id)
    assert by_id == user


def test_get_user_missing_returns_none(database):
    assert db.get_user_by_username("nobody") is None
    assert db.get_user("not-a-uuid") is None  # malformed id -> not found, no error
    assert db.get_user("00000000-0000-0000-0000-000000000000") is None


def test_duplicate_username_rejected(database):
    db.create_user("admin", "hash-1")
    with pytest.raises(ValueError, match="already exists"):
        db.create_user("admin", "hash-2")


def test_set_user_password(database):
    db.create_user("admin", "old-hash")

    updated = db.set_user_password("admin", "new-hash")
    assert updated.password_hash == "new-hash"
    assert db.get_user_by_username("admin").password_hash == "new-hash"


def test_set_password_missing_user_raises(database):
    with pytest.raises(LookupError):
        db.set_user_password("ghost", "whatever")
