"""Does the statement agree with ITSELF?

A source carrying a per-line running balance states the same account twice --
once as a sequence of amounts, once as a sequence of balances -- and the two
must agree.  That redundancy is the only self-check the app has over a record it
did not author, and it is worth more than it looks: it catches a missing line, a
mis-ordered parse and an edited file, none of which any later stage could
distinguish from the truth.

**This is also why the CSV was chosen over the OFX** (ruling **R-FP**'s adapter
question, decided by measurement 2026-08-16).  The OFX carries no per-line
balance, and its file-level ``LEDGERBAL`` cannot substitute: on the developer's
own 2026-08-16 export that header read ``$4,747.63``, which is 2026-08-13's
closing balance, while the same file listed two 2026-08-14 lines worth
``-$1,006.72``.  An importer that had trusted the header would have been wrong
by exactly the unposted tail, on every day, and nothing in the file would have
said so.

Services-boundary discipline: plain data in, nothing written, no clock read.
"""

from __future__ import annotations

from decimal import Decimal

from app.exceptions import StatementIntegrityError, StatementParseError


def carries_running_balance(lines: list) -> bool:
    """Return whether *lines* carry a running balance, refusing a mixture.

    Args:
        lines: :class:`~._line.StatementLine` values.

    Returns:
        True when every line carries a running balance, False when none does.

    Raises:
        StatementParseError: When only SOME lines carry one.  A partial column
            is not a source that lacks the fact -- it is a parse that lost it
            on some rows, and silently downgrading to "no self-check available"
            would turn a broken adapter into a missing feature.
    """
    if not lines:
        return False
    with_balance = sum(1 for line in lines if line.running_balance is not None)
    if with_balance == 0:
        return False
    if with_balance == len(lines):
        return True
    raise StatementParseError(
        f"This file carries a running balance on {with_balance} of "
        f"{len(lines)} lines.  A statement either states its balance for "
        f"every line or for none; a partial column means the file was read "
        f"wrongly.  Nothing was imported."
    )


def verify_running_balance(lines: list) -> None:
    """Refuse *lines* unless each balance follows from the one before it.

    The rule is one equation, applied to every consecutive pair:
    ``previous.running_balance + this.amount == this.running_balance``.

    **It is checked over the whole file rather than sampled**, and both ends
    matter: the FIRST line is the only one with no predecessor, so it is
    unchecked by construction and its own opening is derived from it
    (:func:`opening_balance`), while every later line is pinned twice.

    Measured on the developer's own SECU export (2026-07-19 pull, 306 lines):
    305 of 305 consecutive pairs satisfy it.  That is what licenses treating a
    break as a defect rather than as a quirk of the format.

    Args:
        lines: :class:`~._line.StatementLine` values in CHRONOLOGICAL order --
            the order every adapter returns.  Ordering is the precondition and
            it is load-bearing: the chain is a prefix sum, so a file read
            newest-first fails on every pair.

    Raises:
        StatementIntegrityError: When any pair disagrees.  It carries the count
            and the earliest break, because the earliest one is where the
            explanation is; the ones after it are usually the same defect seen
            again.
    """
    if not carries_running_balance(lines):
        return
    breaks = []
    for previous, current in zip(lines, lines[1:]):
        expected = previous.running_balance + current.amount
        if expected != current.running_balance:
            breaks.append(
                f"{current.posted_on} '{current.description[:40]}': the line "
                f"before it left {previous.running_balance}, this line moves "
                f"{current.amount}, so the balance should read {expected} and "
                f"the file says {current.running_balance}"
            )
    if breaks:
        raise StatementIntegrityError(len(breaks), breaks[0])


def opening_balance(lines: list) -> Decimal | None:
    """Return the balance BEFORE the first line, or ``None``.

    Derived from the first line's own two facts rather than read from a header,
    for the reason this module's docstring gives: the header was measured to
    disagree with the lines.

    Args:
        lines: :class:`~._line.StatementLine` values in chronological order.

    Returns:
        The balance the account held before the earliest line, or ``None`` when
        the source carries no running balance.
    """
    if not lines or lines[0].running_balance is None:
        return None
    return lines[0].running_balance - lines[0].amount


def closing_balance(lines: list) -> Decimal | None:
    """Return the balance AFTER the last line, or ``None``.

    Args:
        lines: :class:`~._line.StatementLine` values in chronological order.

    Returns:
        The last line's running balance, or ``None`` when the source carries
        none.
    """
    if not lines:
        return None
    return lines[-1].running_balance
