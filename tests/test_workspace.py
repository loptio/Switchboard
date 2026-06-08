"""Offline tests for the coding workspace helpers (Phase 10a) — pure, no SDK.

Two surfaces:
- the git-free diff pipeline (snapshot + compute_diff), incl. binary/oversize skipping;
- `confine`, the SECURITY net (hardening #1): adversarial path-escape attempts —
  `..` traversal, absolute paths, and symlink escapes — must all be rejected.
"""

import workspace


# --- confine: the path-confinement security net (adversarial) --------------

def test_confine_allows_paths_inside_the_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert workspace.confine(ws, ws / "a.txt")
    assert workspace.confine(ws, "a.txt")  # relative -> under root
    assert workspace.confine(ws, ws / "sub" / "deep" / "b.txt")
    assert workspace.confine(ws, ws)  # the root itself
    # a `..` that stays inside after resolving is fine
    assert workspace.confine(ws, ws / "sub" / ".." / "a.txt")


def test_confine_rejects_parent_traversal(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert not workspace.confine(ws, "../escape.txt")
    assert not workspace.confine(ws, ws / ".." / "escape.txt")
    assert not workspace.confine(ws, "../../etc/passwd")


def test_confine_rejects_absolute_paths_outside(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert not workspace.confine(ws, "/etc/passwd")
    assert not workspace.confine(ws, str(tmp_path / "sibling.txt"))


def test_confine_rejects_sibling_with_shared_prefix(tmp_path):
    # /tmp/ws-evil must NOT be seen as inside /tmp/ws (string-prefix bug guard).
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "ws-evil").mkdir()
    assert not workspace.confine(ws, tmp_path / "ws-evil" / "x.txt")


def test_confine_rejects_symlink_escape(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret", encoding="utf-8")
    # a symlink INSIDE the workspace pointing OUT of it
    (ws / "link").symlink_to(outside)
    assert not workspace.confine(ws, ws / "link" / "secret.txt")
    # a symlink to an outside file directly
    (ws / "escape").symlink_to(outside / "secret.txt")
    assert not workspace.confine(ws, ws / "escape")


# --- snapshot + compute_diff: the git-free diff pipeline --------------------

def test_snapshot_reads_text_files(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("hello\n", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "b.py").write_text("print(1)\n", encoding="utf-8")
    snap = workspace.snapshot(ws)
    assert snap == {"a.txt": "hello\n", "sub/b.py": "print(1)\n"}


def test_snapshot_missing_dir_is_empty(tmp_path):
    assert workspace.snapshot(tmp_path / "nope") == {}


def test_snapshot_skips_binary_and_oversize(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "text.txt").write_text("ok\n", encoding="utf-8")
    (ws / "bin.dat").write_bytes(b"\x00\x01\x02binary")  # NUL -> binary, skipped
    monkeypatch.setattr(workspace, "MAX_SNAPSHOT_BYTES", 8)
    (ws / "big.txt").write_text("x" * 100, encoding="utf-8")  # over the cap, skipped
    snap = workspace.snapshot(ws)
    assert set(snap) == {"text.txt"}


def test_snapshot_skips_vcs_and_cache_dirs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "keep.txt").write_text("k\n", encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref: x\n", encoding="utf-8")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "m.pyc").write_text("junk\n", encoding="utf-8")
    assert set(workspace.snapshot(ws)) == {"keep.txt"}


def test_compute_diff_add_modify_delete():
    before = {"keep.txt": "same\n", "mod.txt": "old\n", "gone.txt": "bye\n"}
    after = {"keep.txt": "same\n", "mod.txt": "new\n", "added.txt": "hi\n"}
    diff, changed = workspace.compute_diff(before, after)
    assert changed == ["added.txt", "gone.txt", "mod.txt"]  # sorted; keep.txt omitted
    assert "added.txt" in diff and "+hi" in diff
    assert "gone.txt" in diff and "-bye" in diff
    assert "-old" in diff and "+new" in diff


def test_compute_diff_no_change_is_empty():
    snap = {"a.txt": "x\n"}
    assert workspace.compute_diff(snap, dict(snap)) == ("", [])


def test_snapshot_then_diff_round_trip(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("one\n", encoding="utf-8")
    before = workspace.snapshot(ws)
    (ws / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (ws / "new.txt").write_text("fresh\n", encoding="utf-8")
    after = workspace.snapshot(ws)
    diff, changed = workspace.compute_diff(before, after)
    assert changed == ["a.txt", "new.txt"]
    assert "+two" in diff and "+fresh" in diff
