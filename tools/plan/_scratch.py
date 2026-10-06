"""Throwaway git repositories for this package's tests: the ONE builder they share.

Every command goes through :func:`_git.git`, so it runs in the directory
named and in no other repository, whatever the calling process is bound to
(a pre-commit hook binds it to the repository being committed:
``_git._binding_variables``).  Never point it at a real checkout.
"""
from __future__ import annotations

from pathlib import Path

import _git

#: Settings no developer's own git configuration may change under a test.
ISOLATED = ("-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "-c", "user.name=Plan Test", "-c", "user.email=plan-test@example.invalid")


def run(root: Path, *args: str) -> str:
    """One git command in ``root``, isolated from the developer's configuration; its output."""
    return _git.git(root, *ISOLATED, *args).strip()
