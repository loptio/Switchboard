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
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

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


def in_git_dir(root: str | os.PathLike, candidate: str | os.PathLike) -> bool:
    """True iff `candidate` resolves to the workspace's `.git` directory (or inside it).

    The coding agent edits a REAL repo now (Phase 10b-1), and `.git` is inside the
    confined root — so `confine` alone would let a tool scribble in git internals, which
    `git diff` can't see and which can corrupt the repo. The seam's permission gate uses
    this alongside `confine` to DENY any write into `.git`. Fail closed (treat a
    resolution error as forbidden). A non-git workspace has no `.git`, so this is a
    harmless no-op there (and still refuses an attempt to CREATE one)."""
    try:
        root_r = Path(root).resolve()
        cand = Path(candidate)
        if not cand.is_absolute():
            cand = root_r / cand
        cand_r = cand.resolve()
        git_dir = (root_r / ".git").resolve()
    except (OSError, RuntimeError, ValueError):
        return True  # fail closed
    return cand_r == git_dir or git_dir in cand_r.parents


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


# --- git-aware diff + restore (Phase 10b-1) ---------------------------------
# When a coding workspace IS a real git repo, git gives a truer diff than the
# before/after snapshot (it honours .gitignore, sees deletions, and is the natural
# unit of "what changed"). These run git as a LOCAL subprocess — no Agent SDK, no
# network, no spend — so they are exercised offline against temp git repos. The agent
# itself still has only Read/Write/Edit (no shell); this is worker-side git, distinct
# from the agent's toolset. A non-git workspace falls back to snapshot/compute_diff.


def _git(root: str | os.PathLike, *args: str) -> subprocess.CompletedProcess:
    """Run `git -C root <args>`, capturing output. Never raises on a non-zero exit
    (callers inspect returncode); git binary missing surfaces as FileNotFoundError."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False,
    )


def is_git_repo(root: str | os.PathLike) -> bool:
    """True iff `root` is inside a git work tree. False for a non-git / missing dir, or
    if git is unavailable (→ the caller falls back to the snapshot diff)."""
    try:
        r = _git(root, "rev-parse", "--is-inside-work-tree")
    except (OSError, ValueError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def _git_status_entries(root: str | os.PathLike) -> list[str]:
    """The porcelain (NUL-separated, unquoted) status entries for `root`. `-z` avoids
    path-quoting so names with spaces/unicode parse cleanly; `--untracked-files=all`
    expands untracked dirs to individual files (so the agent's new files show up)."""
    r = _git(root, "status", "--porcelain", "--untracked-files=all", "-z")
    return [e for e in r.stdout.split("\0") if e]


def _changed_paths(entries: list[str]) -> list[str]:
    """Workspace-relative changed paths from porcelain entries (status XY + path).
    Renames are not expected — the agent has no git to stage them — so a simple parse
    suffices; an unparseable entry is skipped (fail safe, never crash the diff)."""
    out: list[str] = []
    for e in entries:
        if len(e) < 4:
            continue
        out.append(e[3:])
    return sorted(set(out))


def git_is_clean(root: str | os.PathLike) -> bool:
    """True iff the git work tree has no tracked modifications and no (non-ignored)
    untracked files — the precondition for running a coding agent on a real repo, so
    the blanket restore-on-reject is safe and the diff is fully attributable."""
    return not _git_status_entries(root)


def git_diff(root: str | os.PathLike) -> tuple[str, list[str]]:
    """The git working-tree diff for `root` + the sorted list of changed paths.

    Includes untracked NEW files (which plain `git diff` omits) by marking them
    intent-to-add (`git add -N`) ONLY for those paths, diffing, then unstaging exactly
    those paths so the index/staged state the user had is left untouched. Returns
    ("", []) for a clean tree."""
    entries = _git_status_entries(root)
    changed = _changed_paths(entries)
    untracked = [e[3:] for e in entries if e[:2] == "??" and len(e) >= 4]
    if untracked:
        _git(root, "add", "-N", "--", *untracked)
    try:
        diff = _git(root, "diff").stdout
    finally:
        if untracked:
            _git(root, "reset", "-q", "--", *untracked)  # drop the intent-to-add markers
    return diff, changed


def git_restore(root: str | os.PathLike) -> None:
    """Restore the git work tree to its committed state (Phase 10b-1 reject): revert
    tracked modifications/deletions and remove untracked files the agent created.
    `clean -fd` (no `-x`) keeps .gitignore'd build artifacts. Best-effort — a no-HEAD
    repo can't `checkout`, but `clean` still drops the agent's new files."""
    co = _git(root, "checkout", "--", ".")
    if co.returncode != 0:
        log.info("git_restore: checkout skipped (%s)", (co.stderr or "").strip())
    _git(root, "clean", "-fd")
