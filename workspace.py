"""Workspace utilities for the coding family (Phase 10a) — pure stdlib, no SDK.

Two jobs, both worker-side and dependency-free (so they unit-test offline):

- DIFF: `snapshot` + `compute_diff` give a git-FREE before/after unified diff. The
  10a workspace is a plain directory, not a git clone (blueprint decision F), so a
  real `git diff` is unavailable — we snapshot text files before and after the agent
  runs and diff them with difflib. Binary and oversized files are skipped so the diff
  stays readable and memory/cost stay bounded (hardening #4).

- CONFINEMENT: `confine` is the path-containment SECURITY net the coding seam's
  `can_use_tool` callback uses. An unattended file-editing agent is a real attack
  surface, so every path a tool touches must resolve INSIDE the workspace root.
  Containment is checked on the REALPATH of both root and candidate, so `..`
  traversal, absolute paths, and symlink escapes are all rejected (hardening #1).

No Agent SDK / langgraph import: this is a leaf the seam and its tests both use.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

# Files larger than this are skipped from the snapshot (hardening #4): a coding diff
# over MB-scale files is unreadable and would blow up memory/cost.
MAX_SNAPSHOT_BYTES = 1_000_000

# Directory names never worth snapshotting (VCS / caches / vendored deps): their
# contents are not the agent's work product and would drown the diff in noise.
_SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "node_modules", ".mypy_cache",
                        ".pytest_cache", ".ruff_cache"})


def confine(root: str | os.PathLike, candidate: str | os.PathLike) -> bool:
    """True iff `candidate` resolves to a path inside `root` (root itself included).

    The security primitive (hardening #1). Both paths are resolved with realpath
    BEFORE the containment test, so every escape vector is closed:
    - ``..`` traversal      → collapsed by resolve(), lands outside → rejected
    - an absolute path      → resolved as-is; outside the root → rejected
    - a symlink that points out of the workspace → realpath follows it out → rejected

    A relative `candidate` is taken relative to `root`. Any resolution error (bad
    path, permission) is treated as NOT confined (fail closed).
    """
    try:
        root_r = Path(root).resolve()
        cand = Path(candidate)
        if not cand.is_absolute():
            cand = root_r / cand
        cand_r = cand.resolve()
    except (OSError, RuntimeError, ValueError):
        return False  # fail closed
    # `.parents` containment (not string prefix) so /ws-evil is not seen as under /ws.
    return cand_r == root_r or root_r in cand_r.parents


def _is_text(data: bytes) -> bool:
    """Heuristic: treat a file as text unless it holds a NUL byte (skip binaries)."""
    return b"\x00" not in data


def snapshot(root: str | os.PathLike) -> dict[str, str]:
    """Map each workspace-relative path -> text content, for the before/after diff.

    Includes every readable, non-binary, not-too-large regular file under `root`,
    skipping symlinks (we never follow links out of the workspace) and VCS/cache
    dirs. Returns {} for a missing/empty directory. Deterministic (sorted) so the
    diff order is stable.
    """
    root_r = Path(root).resolve()
    out: dict[str, str] = {}
    if not root_r.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root_r):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            try:
                if p.stat().st_size > MAX_SNAPSHOT_BYTES:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if not _is_text(data):
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            out[str(p.relative_to(root_r))] = text
    return out


def compute_diff(before: dict[str, str], after: dict[str, str]) -> tuple[str, list[str]]:
    """Unified diff of two snapshots + the sorted list of changed paths.

    A path absent on one side is diffed against /dev/null (added / removed file).
    Unchanged files are omitted. Returns ("", []) when nothing changed.
    """
    changed: list[str] = []
    chunks: list[str] = []
    for path in sorted(set(before) | set(after)):
        b = before.get(path)
        a = after.get(path)
        if b == a:
            continue
        changed.append(path)
        diff = difflib.unified_diff(
            (b or "").splitlines(keepends=True),
            (a or "").splitlines(keepends=True),
            fromfile=f"a/{path}" if b is not None else "/dev/null",
            tofile=f"b/{path}" if a is not None else "/dev/null",
        )
        chunk = "".join(diff)
        # difflib omits a trailing newline when the last line has none; keep chunks
        # separated so concatenated multi-file diffs stay readable.
        chunks.append(chunk if chunk.endswith("\n") else chunk + "\n")
    return "".join(chunks), changed
