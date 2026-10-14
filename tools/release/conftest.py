"""The fixture the release tool's tests share: a throwaway repository whose commits carry
files (``steps.md``, migrations), built with :mod:`tools.ci.scratch`'s isolated git."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.ci.scratch import run
from tools.release._carried import STEPS_PATH

_HEADER = ("| arc | id | also | what this step does | order | commit | starts |\n"
           "| --- | --- | --- | --- | --- | --- | --- |\n")


def steps(*rows: tuple[str, ...]) -> str:
    """A ``steps.md`` holding one row per ``(key, sentence, order, commit)``, or per
    ``(key, sentence, order, commit, also)`` for a row naming the other names it has."""
    lines = []
    for key, sentence, order, commit, *also in rows:
        arc, ident = key.split(":", 1)
        lines.append(f"| {arc} | {ident} | {also[0] if also else '--'} | {sentence} | {order} "
                     f"| {commit} | -- |\n")
    return "# Steps\n\n" + _HEADER + "".join(lines)


def migration(revision: str, down: str | None) -> str:
    """A migration file's text declaring ``revision`` and its ``down_revision``."""
    parent = "None" if down is None else f'"{down}"'
    return f'"""A migration."""\nrevision = "{revision}"\ndown_revision = {parent}\n'


class Repo:
    """A repository whose commits are built from whole file sets, each commit's files its
    first parent's with the changes given (``None`` deletes a file)."""

    def __init__(self, root: Path) -> None:
        """An empty repository at ``root``."""
        self.root = root
        self._files: dict[str, dict[str, str]] = {}

    def commit(self, subject: str, files: dict[str, str | None] | None = None, *,
               ships: tuple[str, ...] = (), trailers: tuple[str, ...] = (),
               parents: tuple[str, ...] = ()) -> str:
        """A commit whose message carries a ``Ships:`` trailer per key in ``ships`` and
        each line of ``trailers``; its sha."""
        held = dict(self._files[parents[0]]) if parents else {}
        for path, text in (files or {}).items():
            if text is None:
                held.pop(path, None)
            else:
                held[path] = text
        for child in self.root.iterdir():
            if child.is_dir() and child != self.root / ".git":
                shutil.rmtree(child)
            elif child.is_file():
                child.unlink()
        for path, text in held.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text, encoding="utf-8")
        run(self.root, "add", "-A")
        tree = run(self.root, "write-tree")
        message = ["-m", subject]
        block = [f"Ships: {key}" for key in ships] + list(trailers)
        if block:
            message += ["-m", "\n".join(block)]
        args = [arg for parent in parents for arg in ("-p", parent)]
        sha = run(self.root, "commit-tree", tree, *args, *message)
        self._files[sha] = held
        return sha

    def merge(self, onto: str, branch: str, subject: str = "Merge pull request") -> str:
        """A merge of ``branch`` into ``onto`` holding ``branch``'s files, as a pull request
        built on ``onto`` merges."""
        changes: dict[str, str | None] = {path: None for path in self._files[onto]}
        changes.update(self._files[branch])
        return self.commit(subject, changes, parents=(onto, branch))

    def plan(self, rows: tuple[tuple[str, ...], ...], *, parents: tuple[str, ...],
             ships: tuple[str, ...] = (), subject: str = "registry") -> str:
        """A commit writing ``steps.md`` as ``rows``."""
        return self.commit(subject, {STEPS_PATH: steps(*rows)}, ships=ships, parents=parents)


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """A :class:`Repo` under tmp_path (never a real checkout)."""
    return Repo(scratch.repository(tmp_path))
