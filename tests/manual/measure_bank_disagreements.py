"""Write the list of every place an account's bank record and its books part.

Plan step **balance:X-bk-1**, ruling **R-BAL232** (the developer's "Tested
report + script"): the input the one-time reconcile of the imported bank
history (plan step X-bk-2) is written from, and the measurement that step
re-runs to show it is done -- when every account's books span RECONCILES.
Everything it prints is :func:`app.services.outstanding_difference
.disagreement_list`'s, which the suite grades; this file only lays it out.

**Run it against a restore, never against a live database**, and the list it
writes carries production figures, so it goes OUTSIDE every repository: ruling
**R-BAL132** refuses real values in anything committed, and ``--out`` refuses a
path inside any git work tree that keeps a ``.git`` entry at its root -- this
checkout, a sibling worktree, the main checkout -- rather than trusting
whoever types it.  The header
names the database and the day it was read, because a list of disagreements
moves every time the owner records a row or imports a statement.

Usage::

    LC_ALL=C.UTF-8 DATABASE_URL=postgresql://.../<a restore> \\
        PYTHONPATH=. python tests/manual/measure_bank_disagreements.py \\
        --out ~/projects/shekel-handoffs/<folder>/bank_disagreements.txt

Reads only.  No writes to the database, no commit, nothing staged.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.user import User
from app.services import balance_at, outstanding_difference
from app.services.balance_at import BalanceContext
from app.services.outstanding_difference import (
    DisagreeingDay,
    SpanAgreement,
    StretchKind,
)


def _out_path(raw: str) -> Path:
    """Return *raw* as the file to write, refusing one inside any git work tree.

    **Any work tree, not only this checkout**: a sibling worktree of the same
    repository is one ``git add -A`` from a commit too.  A folder holding a
    ``.git`` entry -- a directory for a main checkout, a file for a linked
    worktree -- is a work tree's root, so the path and each of its parents is
    asked.

    Args:
        raw: The ``--out`` argument.

    Returns:
        The resolved path.

    Raises:
        SystemExit: When the path lies inside a git work tree, or its folder
            does not exist.
    """
    path = Path(raw).expanduser().resolve()
    for folder in (path, *path.parents):
        if (folder / ".git").exists():
            raise SystemExit(
                f"{path} is inside the git work tree {folder}.  This list "
                "carries production figures, which ruling R-BAL132 keeps out "
                "of anything committed: write it to the handoff folder."
            )
    if not path.parent.is_dir():
        raise SystemExit(f"{path.parent} is not a folder.")
    return path


def _grade(verdict: SpanAgreement) -> str:
    """Return one span's counts as a sentence of plain words.

    Args:
        verdict: A :class:`~app.services.outstanding_difference.SpanAgreement`.

    Returns:
        The days, how many a statement covers, how many were compared and
        how many disagree, each as its own count (they overlap rather than
        partition, so none is a part of another).
    """
    return (
        f"{verdict.day_count} day(s); {verdict.imported} covered by a "
        f"statement, {verdict.compared} compared, "
        f"{verdict.disagreeing} disagree"
    )


def _span(verdict: SpanAgreement) -> str:
    """Return a span's two ends."""
    return f"{verdict.first_day}..{verdict.last_day}"


def _write_day(
    lines: "list[str]", day: DisagreeingDay, kind: StretchKind,
) -> None:
    """Append one disagreeing day: its movements, then every line and row.

    **A day no statement covers is not given a bank side.**  The comparison
    reads its bank movement as ``0.00`` because nothing was imported there,
    which is an absence and not evidence, and "bank lines moved 0.00" beside
    an UNMATCHED row reads as the bank saying the money never moved -- an
    invitation to delete a real expense where the act the day calls for is
    importing the statement.  Found by adversarial review.

    Args:
        lines: The output being built.
        day: A :class:`~app.services.outstanding_difference.DisagreeingDay`.
        kind: The kind of the stretch the day is in.
    """
    comparison = day.comparison
    if kind is StretchKind.NO_STATEMENT:
        lines.append(
            f"      {comparison.day}  app rows moved {comparison.recorded}; "
            "no statement covers this day, so the bank's side is UNKNOWN "
            "(import one before acting on it)"
        )
    else:
        lines.append(
            f"      {comparison.day}  app rows moved {comparison.recorded}, "
            f"bank lines moved {comparison.bank_lines}: the app is "
            f"{comparison.residue} apart (positive = the app has more money "
            "in)"
        )
    for line in day.detail.lines:
        claimed = "matched  " if line.matched else "UNMATCHED"
        lines.append(
            f"        bank {claimed} {line.amount:>12}  {line.description}"
        )
    for row in day.detail.rows:
        claimed = "matched  " if row.matched else "UNMATCHED"
        lines.append(
            f"        app  {claimed} {row.amount:>12}  {row.description}"
        )


def _write_account(
    lines: "list[str]", account: Account, ctx: BalanceContext,
) -> str:
    """Append one account's list, and return its one-line summary.

    Args:
        lines: The output being built.
        account: The account.
        ctx: The read pass's ``BalanceContext``.

    Returns:
        A summary of counts only, for the terminal -- no figure leaves the
        file the list is written to.
    """
    listing = outstanding_difference.disagreement_list(account, ctx)
    lines.append(f"Account {account.id}: {account.name}")
    if listing is None:
        lines.append(
            "  no list: an investment, interest or loan account (ruling "
            "R-FO), no balance ever recorded, or no imported statement."
        )
        lines.append("")
        return f"account {account.id}: no list"
    books = listing.books
    if books.day_count:
        lines.append(
            f"  BOOKS {_span(books)}: {_grade(books)} -- "
            + ("RECONCILES" if books.reconciles else "does NOT reconcile")
        )
    else:
        lines.append(
            f"  BOOKS: no day -- the latest balance is dated "
            f"{books.last_day}, the day the books open, so nothing lies "
            "between them to check (and nothing reconciles)."
        )
    for label, verdict in (
        ("before the books open", listing.before_books),
        ("after the latest balance", listing.after_books),
    ):
        if verdict is not None:
            lines.append(
                f"  set apart, {label}: {_span(verdict)}: {_grade(verdict)}"
            )
    for stretch in listing.stretches:
        lines.append(
            f"    {_span(stretch.verdict)}  {stretch.kind.value.upper()}  "
            f"({_grade(stretch.verdict)})"
        )
        for day in stretch.days:
            _write_day(lines, day, stretch.kind)
    lines.append("")
    by_kind = {
        kind: sum(1 for s in listing.stretches if s.kind is kind)
        for kind in StretchKind
    }
    return (
        f"account {account.id}: books {books.day_count} day(s), "
        f"{books.disagreeing} disagreeing, reconciles={books.reconciles}; "
        + ", ".join(
            f"{count} '{kind.value}' stretch(es)"
            for kind, count in by_kind.items()
        )
    )


def main() -> None:
    """Parse the arguments, list every account of every owner, write the file."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--out", required=True,
        help="the file to write -- outside every git work tree (R-BAL132)",
    )
    out = _out_path(parser.parse_args().out)
    app = create_app()
    with app.app_context():
        lines = [
            "Bank disagreement list -- plan step balance:X-bk-1 "
            "(ruling R-BAL232)",
            f"database: {db.engine.url.database} on "
            f"{db.engine.url.host}:{db.engine.url.port}",
            f"written: {datetime.now(timezone.utc).isoformat()}",
            "",
        ]
        summaries = []
        for user in db.session.query(User).order_by(User.id).all():
            ctx = balance_at.BalanceContext.build(user.id)
            lines.append(f"=== owner {user.id}, read as of {ctx.as_of} ===")
            for account in (
                db.session.query(Account)
                .filter(Account.user_id == user.id)
                .order_by(Account.id)
            ):
                summaries.append(_write_account(lines, account, ctx))
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    for summary in summaries:
        print(f"  {summary}")


if __name__ == "__main__":
    main()
