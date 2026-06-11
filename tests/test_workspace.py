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


def test_confine_allows_a_symlinked_workspace_root(tmp_path):
    # The workspace ROOT is itself a symlink (e.g. /tmp -> /private/tmp on macOS, or a
    # repo reached via a symlink). confine resolves BOTH root and candidate, so writes
    # INSIDE the symlinked root are confined=True, while escapes are still rejected.
    # This pins the "softlink root" guarantee the git-aware paths build on.
    real = tmp_path / "real_repo"
    real.mkdir()
    link = tmp_path / "link_root"
    link.symlink_to(real)
    assert workspace.confine(link, link / "a.txt")   # inside the symlinked root
    assert workspace.confine(link, "a.txt")          # relative -> inside
    assert workspace.confine(link, real / "a.txt")   # the resolved real path is inside too
    # adversarial: an escape THROUGH the symlinked root is still rejected
    assert not workspace.confine(link, link / ".." / "escape.txt")
    assert not workspace.confine(link, "/etc/passwd")


def test_in_git_dir_detects_git_internals(tmp_path):
    root = tmp_path / "repo"
    (root / ".git" / "hooks").mkdir(parents=True)
    (root / "src").mkdir()
    assert workspace.in_git_dir(root, ".git/config")
    assert workspace.in_git_dir(root, root / ".git")
    assert workspace.in_git_dir(root, root / ".git" / "hooks" / "x")
    assert not workspace.in_git_dir(root, "src/main.py")
    assert not workspace.in_git_dir(root, root / "README.md")


# --- git-aware diff + restore (Phase 10b-1) --------------------------------

def test_is_git_repo_true_for_a_repo_false_otherwise(tmp_path, git_repo):
    assert workspace.is_git_repo(git_repo) is True
    plain = tmp_path / "plain"
    plain.mkdir()
    assert workspace.is_git_repo(plain) is False           # a non-git dir
    assert workspace.is_git_repo(tmp_path / "missing") is False  # a missing dir


def test_git_is_clean_then_dirty(git_repo):
    assert workspace.git_is_clean(git_repo) is True
    (git_repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    assert workspace.git_is_clean(git_repo) is False


def test_git_diff_includes_new_modified_and_is_git_native(git_repo):
    (git_repo / "new.py").write_text("def f():\n    return 1\n", encoding="utf-8")  # untracked
    (git_repo / "README.md").write_text("# repo\nmore\n", encoding="utf-8")  # modify tracked
    diff, changed = workspace.git_diff(git_repo)
    assert changed == ["README.md", "new.py"]  # sorted; from git status
    assert "diff --git" in diff                # git-native format (not the snapshot fallback)
    assert "+def f" in diff                    # the untracked NEW file shows as an addition
    assert "+more" in diff                     # the tracked modification shows
    # idempotent: the intent-to-add markers are cleaned up, so a second call matches
    assert workspace.git_diff(git_repo) == (diff, changed)
    assert workspace.git_is_clean(git_repo) is False  # git_diff is read-only re: the tree


def test_git_diff_empty_for_a_clean_tree(git_repo):
    assert workspace.git_diff(git_repo) == ("", [])


def test_git_restore_reverts_modifications_and_removes_new_files(git_repo):
    (git_repo / "new.py").write_text("junk\n", encoding="utf-8")             # untracked
    (git_repo / "README.md").write_text("# repo\ntampered\n", encoding="utf-8")  # modified
    assert not workspace.git_is_clean(git_repo)
    workspace.git_restore(git_repo)
    assert workspace.git_is_clean(git_repo)                    # back to committed state
    assert not (git_repo / "new.py").exists()                 # untracked file removed
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "# repo\n"  # reverted


def test_git_security_snapshot_empty_for_non_git(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert workspace.git_security_snapshot(plain) == {}


def test_git_security_diff_detects_injected_hook_and_poisoned_config(git_repo):
    before = workspace.git_security_snapshot(git_repo)
    assert before  # a fresh repo has hooks/*.sample + config + HEAD + refs
    # inject a hook (runs on the user's next git op) + poison config (alias code-exec)
    (git_repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
    cfg = git_repo / ".git" / "config"
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[alias]\n\tx = "!evil"\n', encoding="utf-8")
    changed = workspace.git_security_diff(before, workspace.git_security_snapshot(git_repo))
    assert "hooks/pre-commit" in changed and "config" in changed


def test_git_security_restore_neutralises_tampering(git_repo):
    before = workspace.git_security_snapshot(git_repo)
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
    cfg = git_repo / ".git" / "config"
    cfg.write_text(cfg.read_text(encoding="utf-8") + '\n[alias]\n\tx = "!evil"\n', encoding="utf-8")
    assert workspace.git_security_diff(before, workspace.git_security_snapshot(git_repo))  # tampered

    workspace.git_security_restore(git_repo, before)
    assert not hook.exists()  # the injected hook was removed
    assert "evil" not in cfg.read_text(encoding="utf-8")  # config reverted to prior bytes
    # fully clean again w.r.t. the before-snapshot
    assert workspace.git_security_diff(before, workspace.git_security_snapshot(git_repo)) == []


def test_git_restore_keeps_gitignored_files(git_repo):
    # clean -fd (no -x) must NOT remove .gitignore'd build artifacts.
    (git_repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    import subprocess
    subprocess.run(["git", "-C", str(git_repo), "add", ".gitignore"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-q", "-m", "ignore"], check=True,
                   capture_output=True, text=True)
    (git_repo / "build").mkdir()
    (git_repo / "build" / "artifact.o").write_text("binary-ish\n", encoding="utf-8")
    workspace.git_restore(git_repo)
    assert (git_repo / "build" / "artifact.o").exists()  # gitignored artifact survives


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


# --- Phase 10b-2: git_commit (worker-side auto-commit) ----------------------

def test_git_commit_commits_changes_and_returns_hash(git_repo):
    (git_repo / "new.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    h = workspace.git_commit(git_repo, "add f()\n\nTask: add a function")
    assert h and len(h) >= 7
    # the tree is clean again (the change was committed) and the message stuck
    assert workspace.git_is_clean(git_repo) is True
    import subprocess
    log = subprocess.run(
        ["git", "-C", str(git_repo), "log", "-1", "--pretty=%s"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert log == "add f()"


def test_git_commit_nothing_to_commit_returns_none(git_repo):
    assert workspace.git_commit(git_repo, "noop") is None  # clean tree


def test_git_commit_non_git_returns_none(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert workspace.git_commit(plain, "x") is None
