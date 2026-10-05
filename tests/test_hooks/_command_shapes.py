"""The command shapes sessions really send, with made-up text, for both guards' tests.

The first review of plan step L3 replayed this project's recorded Bash calls
through both guards and found one shape misread: a heredoc INSIDE a command
substitution, the way every multi-line commit message and pull-request body is
written -- ``--body "$(cat <<'EOF' ... EOF)"``.  An apostrophe, a lone quote or
an unbalanced ``)`` in the body made the reader close the substitution early,
and the guards then refused or questioned ordinary pull requests into ``dev``
(16 of the 296 recorded ``gh pr create`` calls, measured 2026-10-05).

These are that shape with invented bodies -- no recorded text, no real figure
(ruling ``balance:R-BAL132``) -- each body carrying one of the characters that
broke the first reader.  Both guards' tests run every shape and assert it
passes untouched.
"""
from __future__ import annotations

#: Bodies holding the characters that broke the first reader: an apostrophe, a
#: numbered list's ``)``, double quotes, a lone ``(`` or backtick, and prose
#: quoting gated commands -- which, under a quoted delimiter, run nothing.
BODIES = (
    "It's the fix for the grid's rounding.",
    "Steps:\n1) open the page\n2) press save",
    'The reviewer said "ship it" and left.',
    "A lone ( and a lone ` and a lone ) in prose.",
    "Ships nothing; it's a test-only change.",
    "The hook refuses gh issue close 3 -R saltyreformed-labs/shekel-plan and asks before "
    "gh pr merge 7.",
)


def commit(body: str) -> str:
    """A commit whose message is a quoted heredoc inside a substitution."""
    return f"git commit -m \"$(cat <<'EOF'\n{body}\nEOF\n)\""


def pr_into_dev(body: str) -> str:
    """A pull request into ``dev`` whose body is a quoted heredoc inside a substitution.

    It runs from ``~``, which each guard's tests make a checkout of the code
    repository, as a session's worktree is.
    """
    return (f"cd ~ && gh pr create --base dev --title 'A change' "
            f"--body \"$(cat <<'EOF'\n{body}\nEOF\n)\"")


def merge_with_body(body: str) -> str:
    """A squash merge of pull request 8 with a heredoc commit body."""
    return f"gh pr merge 8 --squash --body \"$(cat <<'EOF'\n{body}\nEOF\n)\""


#: Every shape filled with every body.
SHAPES = tuple(shape(body) for shape in (commit, pr_into_dev, merge_with_body) for body in BODIES)
