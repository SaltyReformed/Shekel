"""Throwaway git repositories for the tests of ``tools/``: the ONE builder they share.

Every command goes through :func:`tools.ci.gitcmd.git`, so it runs in the
directory named and in no other repository, whatever the calling process is
bound to (a pre-commit hook binds it to the repository being committed:
``gitcmd._binding_variables``).  Never point it at a real checkout.

:func:`bound_sentinel` stands up the other half of that claim's control: a
repository the calling process IS bound to, which no call may touch.  Moved
here from ``tools/plan`` by step X-cx's L4, so the card-trailer rules and the
tracker tool test against one builder.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tools.ci.gitcmd import git

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
    test.
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
    return sentinel, state
