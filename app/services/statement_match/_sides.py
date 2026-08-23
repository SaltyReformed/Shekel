"""What a match's two halves come to, and the ONE place that derives it.

Plan step ``bank_import:X-f6d-4``.  Four different modules summed the two sides
of a match and subtracted them before this file existed: the accept door for
its refusals, the residual for the figure it mints, the accepted-matches panel
for its ``agrees`` flag, and :class:`~._offers.MatchProposal` for the
correction a near miss states on screen.  They agreed by reading, which is this
arc's own root cause 1 -- and one of them had drifted far enough to cite a
function that had not existed for two steps.

**A LEAF, and that is what makes "one derivation" structural.**  Nothing here
imports another module of this package, so every one of them can import this;
put it beside any of the four and the other three would be choosing whether to.
It is the same reason :class:`~._offers.MatchDays` sits with the values rather
than with the door that writes them, one axis over: that class is the DAY a
match asserts, this is the MONEY.

Services-boundary discipline (``CLAUDE.md`` Architecture): a frozen dataclass,
no Flask import, no query, no clock read.  It computes and never writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.utils.money import round_money


@dataclass(frozen=True)
class MatchSides:
    """What a match's two halves come to, derived ONCE for the whole act.

    :class:`~._offers.MatchDays`' twin on the money axis, and it exists for
    that class's own reason: a door that summed the bank side for its refusal
    and summed it again to decide what to mint would be two answers to one
    question on a money gate, which is this arc's own root cause 1.

    **Both sides are rounded BEFORE they are compared**, so the difference is a
    whole number of cents and the figure the door writes is the figure it
    tested.  Every input descends from ``Numeric(12, 2)``, so the rounding
    changes nothing today; it is stated because a derived price with more
    places is expressible (:data:`~._submission._FIGURE` allows six) and a
    residual of ``0.001`` is not a row anyone can hold.

    Attributes:
        bank: What the statement lines come to, signed, positive INTO the
            account -- the convention ``bank_statement_lines.amount`` uses.
        app: What the owner's own rows come to, on the same convention
            (:attr:`~._offers.CandidateRow.cash_amount`).
        line_count: How many bank lines the bank side is summed over.  Carried
            because the refusals ask it and re-deriving it from a list they do
            not otherwise need would hand them a second answer to "what shape
            is this match" (plan step ``bank_import:X-f6d-4``).
    """

    bank: Decimal
    app: Decimal
    line_count: int

    @classmethod
    def of(cls, lines, rows) -> "MatchSides":
        """Return what *lines* and *rows* come to.

        Args:
            lines: The match's bank lines.  **Structurally typed** over
                :class:`~app.models.statement_import.BankStatementLine` and
                :class:`~._offers.BankLine` alike, both of which expose
                ``amount`` -- :meth:`~._offers.MatchDays.of`'s idiom.
            rows: The match's app rows, already priced.

        Returns:
            Its :class:`MatchSides`.
        """
        lines = list(lines)
        return cls(
            bank=round_money(
                sum((line.amount for line in lines), Decimal("0.00")),
            ),
            app=round_money(
                sum((row.cash_amount for row in rows), Decimal("0.00")),
            ),
            line_count=len(lines),
        )

    @property
    def difference(self) -> Decimal:
        """Return what the bank moved that these rows do not account for.

        Signed on the same convention as both sides: POSITIVE means the bank
        put in more than the rows say, so the missing movement is income;
        negative means it took more, so the missing movement is an expense.
        """
        return self.bank - self.app

    @property
    def oppose(self) -> bool:
        """Return whether the two sides are not even the same DIRECTION.

        Money leaving an account is not the same movement as money entering
        it, whatever the magnitudes do.  It is a fact about the PAIR rather
        than about any one row, so it refuses every shape a match can take --
        including a group, where it was unreachable until this step because
        the group's own refusal short-circuited it.

        **A ZERO side has no direction and therefore opposes nothing.**  A
        bare ``(bank < 0) != (app < 0)`` reads zero as "money in", so rows
        netting to nothing opposed a debit line and not a credit one -- the
        same submission refused or accepted according to the bank's sign.
        Found by adversarial security review 2026-08-23.
        """
        if not self.bank or not self.app:
            return False
        return (self.bank < 0) != (self.app < 0)
