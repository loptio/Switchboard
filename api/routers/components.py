"""Component palette endpoint (Phase 8) — the synthesizer's building blocks.

GET /components returns the pure-data manifest (node kinds + the registered
handler/predicate/composer/agent/parser/prompt-builder/source/renderer names +
families) so the structured-form builder can populate its dropdowns. Pure data;
the web tier never imports the real registries (decision D).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

import manifest as _manifest
from api.deps import get_current_user, require_csrf

router = APIRouter(
    prefix="/components",
    tags=["components"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)


@router.get("")
def get_components() -> dict:
    return _manifest.build_manifest()
