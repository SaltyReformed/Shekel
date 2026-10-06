"""Throwaway git repositories for the tests of ``tools/``: the ONE builder they share.

Every command but :func:`_prove_bound`'s one control goes through
:func:`tools.ci.gitcmd.git`, so it runs in the directory named and in no other
repository, whatever the calling process is bound to (a pre-commit hook binds
it to the repository being committed: ``gitcmd._binding_variables``).  Never
point it at a real checkout.

:func:`bound_sentinel` stands up the other half of that claim's control: a
repository the calling process IS bound to, which no call may touch, proven
bound by the one git call here that does not go through ``gitcmd``
(:func:`_prove_bound`).  Moved here from what are now quill's tests
(``tools/quill``) by step X-cx's L4, so the card-trailer rules and the tracker
tool test against one builder; the repository the tests start from
(:func:`repository`), the ``dev`` they read (:func:`point_dev`) and the
sentinel's controls joined it for BAL-609, each once spelled in both suites.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from tools.ci.gitcmd import git
from tools.ci.trailers import DEV

#: Settings no developer's own git configuration may change under a test.
ISOLATED = ("-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "-c", "user.name=Plan Test", "-c", "user.email=plan-test@example.invalid")

#: The two ways a process gets bound to another repository (:func:`bound_sentinel`).
BINDINGS = ("hook in a linked worktree", "git-dir and work-tree")


def run(root: Path, *args: str) -> str:
    """One git command in ``root``, isolated from the developer's configuration; its output."""
    return git(root, *ISOLATED, *args).strip()


def commit(root: Path, *paragraphs: str, parents: tuple[str, ...] | list[str] = ()) -> str:
    """A commit of the empty tree whose message is ``paragraphs``; its sha."""
    tree = run(root, "hash-object", "-t", "tree", "/dev/null")
    args = [arg for parent in parents for arg in ("-p", parent)]
    args += [arg for paragraph in paragraphs for arg in ("-m", paragraph)]
    return run(root, "commit-tree", tree, *args)


def repository(parent: Path, name: str = "code") -> Path:
    """An empty repository ``parent / name`` whose first branch is ``dev``, never a real
    checkout; its path."""
    root = parent / name
    root.mkdir()
    run(root, "init", "--quiet", "--initial-branch=dev")
    return root


def point_dev(root: Path, sha: str) -> None:
    """Point the remote-tracking branch every shipped card is read from
    (:data:`tools.ci.trailers.DEV`) at ``sha``."""
    run(root, "update-ref", f"refs/remotes/{DEV}", sha)


def leaf_on_dev(parent: Path) -> tuple[Path, str]:
    """A repository ``parent / "work"`` whose branch ``dev`` holds one commit, a leaf
    carrying ``Ships: plan#3``: the repository and the leaf's sha."""
    work = repository(parent, "work")
    leaf = commit(work, "leaf", "Ships: plan#3")
    run(work, "update-ref", "refs/heads/dev", leaf)
    return work, leaf


def sentinel_state(sentinel: Path) -> dict:
    """What the 2026-10-04 incident changed, or could have: every ref, the config, each HEAD
    and index of the main checkout and of its linked worktree ``lane``."""
    gitdir = sentinel / ".git"
    files = (gitdir / "config", gitdir / "HEAD", gitdir / "index",
             gitdir / "worktrees" / "lane" / "HEAD", gitdir / "worktrees" / "lane" / "index")
    return {"refs": run(sentinel, "for-each-ref"),
            **{f.name if f.parent == gitdir else f"lane/{f.name}":
               f.read_bytes() if f.exists() else None for f in files}}


def bound_sentinel(tmp_path: Path, binding: str,
                   setenv: Callable[[str, str], None]) -> tuple[Path, dict]:
    """A SENTINEL repository with a linked worktree, standing in for the developer's shared
    one, and this process's environment bound to it; the sentinel and its state.

    ``binding`` is one of :data:`BINDINGS`.  The first is what git hands a
    pre-commit hook run from a linked worktree (measured 2026-10-04, git 2.56,
    by a hook that dumped its environment: an absolute ``GIT_DIR`` at the
    worktree's gitdir, its ``GIT_INDEX_FILE``, an empty ``GIT_PREFIX``); the
    second is the main checkout bound by ``--git-dir`` and ``--work-tree``.
    ``setenv`` is pytest's ``monkeypatch.setenv``, so the binding ends with the
    test.  Returned only once :func:`_prove_bound` shows the test it serves can
    fail: a sentinel the binding does not reach, or whose rewrite would not
    show, would prove nothing.
    """
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    run(sentinel, "init", "--quiet", "--initial-branch=main")
    (sentinel / "work.txt").write_text("the developer's file\n", encoding="utf-8")
    run(sentinel, "add", "work.txt")
    run(sentinel, "commit", "--quiet", "-m", "the developer's commit")
    run(sentinel, "worktree", "add", "--quiet", "-b", "lane", str(tmp_path / "lane"))
    gitdir = sentinel / ".git"
    if binding == BINDINGS[0]:
        planted = {"GIT_DIR": gitdir / "worktrees" / "lane",
                   "GIT_INDEX_FILE": gitdir / "worktrees" / "lane" / "index", "GIT_PREFIX": ""}
    elif binding == BINDINGS[1]:
        planted = {"GIT_DIR": gitdir, "GIT_WORK_TREE": sentinel,
                   "GIT_INDEX_FILE": gitdir / "index"}
    else:
        raise ValueError(f"no binding {binding!r}; one of {BINDINGS}")
    state = sentinel_state(sentinel)
    for name, value in planted.items():
        setenv(name, str(value))
    _prove_bound(tmp_path, sentinel, state)
    return sentinel, state


def _prove_bound(outside: Path, sentinel: Path, state: dict) -> None:
    """The sentinel's two controls, each raising when it fails: every index it holds is not
    empty, so an index a leaked ``GIT_INDEX_FILE`` rewrote with a test's empty tree differs
    from it (review cp3 M-1: with an empty index, the rewrite was byte-identical and
    passed); and a PLAIN git call from ``outside`` -- a directory that is no repository,
    started as ``subprocess`` starts it, not through ``gitcmd`` -- answers the sentinel's
    git directory, so the planted environment binds it."""
    if not (b"work.txt" in state["index"] and b"work.txt" in state["lane/index"]):
        raise RuntimeError("control: an index the sentinel holds is empty, so a rewrite "
                           "of it would not show")
    plain = subprocess.run(["git", "-C", str(outside), "rev-parse", "--absolute-git-dir"],
                           capture_output=True, text=True, check=False)
    if plain.returncode or not Path(plain.stdout.strip()).resolve().is_relative_to(
            (sentinel / ".git").resolve()):
        raise RuntimeError("control: the planted environment does not bind a plain git call "
                           f"to the sentinel: {(plain.stdout or plain.stderr).strip()!r}")
