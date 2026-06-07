"""Pydantic request/response models — the typed surface of the OpenAPI contract.

These map the data layer's frozen records (db.records) onto the JSON shapes the
frontend (Unit 2) depends on. Output models expose `from_record` classmethods so
routers translate records in one place.
"""

from __future__ import annotations

from pydantic import BaseModel


# --- auth ------------------------------------------------------------------

class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str
