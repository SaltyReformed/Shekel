"""The ONE normalized statement line, and the ONE rule for its identity.

Ruling **R-FP**: *a statement importer is a SOURCE ADAPTER over one normalized
line shape*, so matching, review and fact-writing are source-independent.  This
module is that shape, and the identity rule that makes re-importing a span
harmless.

**Nothing here reads a database, a clock or a request.**  A parser produces
:class:`StatementLine` values, this module orders and keys them, and
:mod:`._record` is the only place that writes.  Services-boundary discipline
(``CLAUDE.md`` Architecture): plain data in, frozen dataclasses out.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class StatementLine:  # pylint: disable=too-many-instance-attributes
    """One line as a source stated it, normalized.

    Pylint: too-many-instance-attributes -- **eight because a statement line
    genuinely states eight things** (8/7), not because the value wants
    splitting.  Ruling **R-FP** makes this THE normalized shape every adapter
    produces and every consumer reads, so the field set is the union of what a
    source can say: two days, a figure, two names, two provenance ids and a
    running balance.  Splitting it would put half a line's facts behind a
    nested value nothing asks for alone, which is rule 13's speculative shape,
    and would make each new adapter fill two objects instead of one.
    ``CandidateRow`` and ``CreatedPurchase`` carry the same disable for the
    same reason.

    Attributes:
        posted_on: The civil day the bank POSTED the line.  This is the fact
            the whole arc exists to obtain -- of 110 movements matched to bank
            lines on exact amount, only 33 carried the day the app had recorded
            (finding **N-173**).
        transaction_on: The civil day the bank STATED the transaction itself
            happened, or ``None`` where the source states none.
            **The NULL is the source saying so, not the app not knowing**
            (plan step ``bank_import:X-f6a-3a``): this field held a COPY of
            :attr:`posted_on` for a source that does not distinguish the two,
            so no reader could tell an observed swipe day from a restatement of
            the clearing day -- and a match writes this day onto a purchase's
            ``purchased_on``, where a clearing day would claim the purchase was
            made on the day it cleared.
            **It is NOT bounded by ``posted_on``**: 2 of 361 lines in the
            developer's own SECU export carry an OFX ``DTUSER`` one day AFTER
            their ``DTPOSTED``, both ACH deposits.
        amount: Signed, positive INTO the account -- the same convention
            ``cash_ledger.settled_cash_leg`` uses, so a later match compares
            two figures that already agree about direction.
        description: What the bank called it, verbatim.
        merchant: What the bank calls the MERCHANT, or ``None`` where the
            source names none.
            **The NULL is the source saying so**, exactly as
            :attr:`transaction_on`'s is, and for a sharper reason: plan step
            ``bank_import:X-f6a-3d`` makes this string the KEY a merchant
            destination policy is stated against, so a source that cannot name
            a merchant must key NOTHING rather than key something wrong.
            Measured on the developer's own 2026-08-16 exports: SECU's CSV
            names one on **361 of 361** lines, and its OFX truncates 326 of
            those same 361 descriptions to exactly 32 characters -- so dozens
            of distinct merchants arrive as the identical string
            ``POINT OF SALE DEBIT L340 DATE 12``.  A reader that fell back to
            the description would key a policy on that and fire it on every
            one of them; ``None`` fires on nothing.
        source_category: The bank's own category string, or ``None``.
            Provenance only: it is the bank's opinion about a merchant, and
            reading it as a Shekel category would be a reference value no
            ``ref`` table governs.
        external_id: The source's own id for the line (an OFX ``FITID``), or
            ``None`` for a source that has none.  CORROBORATION, never
            identity -- see :func:`line_identity`.
        running_balance: The account balance after this line, or ``None`` for a
            source that does not carry one.  What makes an import able to check
            itself (:func:`~._integrity.verify_running_balance`).
    """

    posted_on: date
    transaction_on: "date | None"
    amount: Decimal
    description: str
    merchant: "str | None" = None
    source_category: "str | None" = None
    external_id: "str | None" = None
    running_balance: "Decimal | None" = None


@dataclass(frozen=True)
class KeyedLine:
    """A :class:`StatementLine` with the ordinal that completes its identity.

    Attributes:
        line: The line itself.
        sequence_in_group: Its position among the lines sharing its
            ``(posted_on, amount)``, counted from 0 in the source's own order.
    """

    line: StatementLine
    sequence_in_group: int

    @property
    def identity(self) -> "tuple[date, Decimal, int]":
        """Return the account-relative part of this line's identity."""
        return (self.line.posted_on, self.line.amount, self.sequence_in_group)


def assign_sequences(lines: "list[StatementLine]") -> "list[KeyedLine]":
    """Return *lines* keyed, in the order given.

    **A line's identity is ``(account, posted_on, amount, sequence)`` and the
    sequence is what makes that key TOTAL.**  Two genuinely distinct charges
    can share a day and an amount -- the same coffee twice -- and a key without
    the ordinal would reject the second as a duplicate, which is silent money
    loss on precisely the shape a duplicate guard is supposed to protect.

    **Why not the source's own id.**  ``FITID`` is the obvious key and R-FP
    names it, but only some sources have one: SECU's CSV carries the merchant,
    the bank's category and a running balance and no id at all, while its OFX
    carries the id and truncates every description to 32 characters.  Keying on
    the id would make the identity rule depend on the format, which is one rule
    per adapter -- and the measurement says it buys nothing.  Compared across
    two SECU exports twelve days apart (2026-08-04 and 2026-08-16), this
    positional key reproduced the ``FITID`` key EXACTLY over their 342 shared
    lines: 0 keys present in only one export, 0 lines whose id disagreed.  In
    fact 0 groups needed an ordinal at all -- ``(day, amount)`` alone was unique
    across 361 lines -- so the ordinal is carried for totality rather than
    because today's data needs it.

    An external id is still stored, and
    ``uq_bank_statement_lines_external_id`` still refuses a source that claims
    one twice.  It corroborates; it does not decide.

    Args:
        lines: The source's lines, IN THE SOURCE'S OWN ORDER.  The order is
            this function's only precondition and it is load-bearing: the
            ordinal is positional, so shuffling the input assigns different
            ordinals to the same statement.  Adapters return chronological
            order (:func:`~._adapters.parse_statement`), which is also what
            :func:`~._integrity.verify_running_balance` requires.

    Returns:
        One :class:`KeyedLine` per input line, in the same order.
    """
    seen: "Counter[tuple[date, Decimal]]" = Counter()
    keyed = []
    for line in lines:
        group = (line.posted_on, line.amount)
        keyed.append(KeyedLine(line=line, sequence_in_group=seen[group]))
        seen[group] += 1
    return keyed


def line_identity(
    account_id: int, keyed: KeyedLine,
) -> "tuple[int, date, Decimal, int]":
    """Return the full identity of one keyed line.

    The ONE spelling of the key, so the door that looks a line up and the
    ``uq_bank_statement_lines_identity`` constraint that refuses a duplicate
    cannot come to disagree about what "the same line" means.

    Args:
        account_id: The account the line belongs to.
        keyed: The line and its ordinal.

    Returns:
        ``(account_id, posted_on, amount, sequence_in_group)``.
    """
    return (account_id,) + keyed.identity
