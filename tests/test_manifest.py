"""The pure-data manifest must match the live registries (Phase 8, U1).

The web tier imports `manifest` (pure data) instead of `components` (pulls the SDK),
so the two could silently drift. This test — worker-side, so it MAY import the
execution modules — pins the manifest's declared names equal to the live registries.
If a component is added/removed in code without updating manifest.py, this fails.
"""

import brief_orchestrator  # noqa: F401 — registers brief glue at import
import orchestrator  # noqa: F401 — registers digest glue at import

import components
import manifest as M


def test_manifest_matches_live_registries():
    assert set(M.NODE_HANDLERS) == set(components.NODE_HANDLERS)
    assert set(M.PREDICATES) == set(components.PREDICATES)
    assert set(M.COMPOSERS) == set(components.COMPOSERS)
    assert set(M.AGENTS) == set(components.AGENTS)
    assert set(M.PROMPT_BUILDERS) == set(components.PROMPT_BUILDERS)
    assert set(M.PARSERS) == set(components.PARSERS)
    assert set(M.SOURCES) == set(components.SOURCES)
    assert set(M.RENDERERS) == set(components.RENDERERS)


def test_build_manifest_shape():
    m = M.build_manifest()
    assert set(m["node_kinds"]) == {"step", "human_review", "fan_out", "gather"}
    assert m["end"] == "__end__"
    assert {f["id"] for f in m["families"]} == {"digest", "brief"}
    # families name registered intake/delivery so a runner harness exists
    for fam in m["families"]:
        assert fam["source"] in m["sources"]
        assert fam["output"] in m["renderers"]
