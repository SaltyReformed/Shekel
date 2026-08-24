"""Which day is a statement's stated balance the balance FOR, and how firmly?

**A bank states its balance as of the EXPORT INSTANT and labels it with the
export's own day, so the day on the header is not the day the figure is for.**
Measured on the developer's own SECU exports: the 2026-08-21 file reads
``Balance as of 08/21/2026,2501.310000`` while its last line is 08-18 and
``2501.31`` is 08-18's closing; the 2026-08-16 file reads ``$4,747.63``, which
is 2026-08-13's closing, over a list containing two 2026-08-14 lines worth
``-$1,006.72``.  Ruling **R-GF**, plan step ``bank_import:X-f6e-1``.

**So the claim and the day it is for are two facts, and the LINES solve the
second.**  Given a known opening -- the balance before the file's first line --
the stated figure is effective at day ``d`` exactly when::

    stated - sum(lines posted on or before d) == opening

over the candidates ``{the day before the first line} + {every day the file
covers}``, bounded above by the day the header itself names.

**A SOLVED day is only as good as the opening it was solved against**, which is
why :class:`~app.enums.StatementBalanceEvidenceEnum` records the WEAKEST LINK
in the chain behind the figure rather than how the day was worked out.  An
anchor solved against an uncorroborated opening is uncorroborated; recording
the minimum makes that true by construction, and is what stops a re-upload of
the same file from laundering an assumption into a corroboration.

**A file may legitimately state a balance NO day of its own explains**, and
that is measured rather than allowed for: a DATE-RANGE export states the
CURRENT balance, not the range's closing.  The developer's own
2026-01-02..2026-03-31 export, pulled 2026-08-23, reads
``Balance as of 08/23/2026,2459.600000`` -- 145 days past its last line and
`$255.41` from the `$2,715.01` its 139 lines imply.  Such a file records its
CLAIM with no anchor, which is the honest absence rather than a guess.

**Only a file that contradicts ITSELF is refused**, and that needs no evidence
from outside it: a per-line running balance states what the account held on
every day the file covers, so a header the chain reaches on no such day is a
file disagreeing with itself.  An earlier draft refused on a mismatch against
RECORDED history too, and an adversarial review reproduced it rejecting an
honest export while blaming the file for the app's own stale anchor.

**An anchor is a conclusion drawn from lines, so the doors that CHANGE lines
release it** (:func:`release_anchors_from`).  Recording a line at or before an
anchor's day means that anchor was solved without it; deleting an import means
the lines it was solved against are gone.  Both were reproduced as silently
wrong openings -- `$150.00` on the delete path -- before the release existed.
Releasing rather than re-solving is deliberate: the next import re-establishes
an anchor from evidence that is present, where a re-solve would be the app
inferring its way around facts that moved underneath it.

**Deriving a balance from a recorded anchor is :mod:`._balance`'s**, not this
module's, and the split is the walk/fold one the cash side already pays for: a
FOLD is a balance, a walk is a fact.  This module decides which DAY a claimed
figure is for; that one turns a settled anchor plus the recorded lines into a
balance for any day, and :func:`recorded_opening_before` is one of its two
readers.  Keeping the arithmetic there is what stops the report added at plan
step ``bank_import:X-f6e-2`` becoming a second statement of it.

Services-boundary discipline: no Flask import, no clock read.  It DOES query,
which is :mod:`._identity`'s shape in this package and for the same reason: the
recorded-history half of one subject is not a different subject.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.enums import StatementBalanceEvidenceEnum
from app.exceptions import StatementBalanceUnexplained
from app.extensions import db
from app.models.statement_import import StatementImport

from ._balance import fold_bank_balances
from ._integrity import opening_balance


@dataclass(frozen=True)
class ImportedBalance:
    """A file's balance CLAIM and what the import made of it, as one value.

    **Its four fields are the four columns, and its nullability is their three
    CHECK constraints** -- ``ck_statement_imports_stated_balance_paired``,
    ``ck_statement_imports_balance_evidence_paired`` and
    ``ck_statement_imports_anchor_needs_a_claim``.  One value rather than four
    parameters threaded through the door, its receipt and the page, because
    every one of those surfaces needs the same four facts together and a
    reader that had to test them separately would be re-deriving what the
    schema already states.

    Attributes:
        stated: The figure the file's header claims, verbatim.
        stated_on: The day that header names -- the EXPORT's day.
        effective_on: The day the figure IS the balance for, solved from the
            lines, or ``None`` where the file's own lines cannot reach the day
            it claims.  NOT a copy of :attr:`stated_on`: on the developer's
            2026-08-16 export the two are three days apart, and on his
            2026-01-02..2026-03-31 one this is ``None`` because that header
            states TODAY's balance, 145 days past the file's last line.
        evidence: How strongly the figure is held, as a
            :class:`~app.enums.StatementBalanceEvidenceEnum` member -- the
            WEAKEST link in the chain behind it.  ``None`` exactly when
            :attr:`effective_on` is.
    """

    stated: Decimal
    stated_on: date
    effective_on: date | None
    evidence: StatementBalanceEvidenceEnum | None

    @property
    def is_anchored(self) -> bool:
        """Return whether this import placed its own figure on a day.

        ONE field is tested rather than two, which the pairing CHECK makes
        exact rather than economical.
        """
        return self.effective_on is not None


@dataclass(frozen=True)
class KnownOpening:
    """A balance before a file's first line, and how firmly it is held.

    **The evidence travels WITH the figure, and that is the whole reason this
    is a value rather than a bare ``Decimal``.**  A solve inherits the weakness
    of whatever it solved against, so an opening arriving without its
    provenance would let the caller record a determination it has not earned --
    the defect an adversarial review reproduced in two clicks on 2026-08-23,
    where re-uploading one file walked back to the app's own assumption, found
    the file agreed with it, and turned the receipt green.

    Attributes:
        amount: The balance before the file's first line.
        evidence: The strength of the chain behind that figure.
    """

    amount: Decimal
    evidence: StatementBalanceEvidenceEnum


def weaker_of(
    first: StatementBalanceEvidenceEnum,
    second: StatementBalanceEvidenceEnum,
) -> StatementBalanceEvidenceEnum:
    """Return whichever of two evidence levels is the weaker.

    The whole of the weakest-link rule, stated once so no caller writes its own
    comparison.  The ORDER it reads is
    :attr:`~app.enums.StatementBalanceEvidenceEnum.strength`, declared on the
    enum itself.

    Args:
        first: One evidence level.
        second: The other.

    Returns:
        The weaker member, or either when they are equal.
    """
    return min(first, second, key=lambda member: member.strength)


def _cumulative_by_day(lines: list) -> "dict[date, Decimal]":
    """Return, per day the file covers, the sum of every line up to and including it.

    Args:
        lines: :class:`~._line.StatementLine` values in chronological order.

    Returns:
        ``{day: sum of lines posted on or before day}``, one entry per DISTINCT
        day.  Keyed by day rather than by line index because the solve's
        candidates are days: a balance is a fact about a day's end, so a
        candidate that split a day between two of its lines would name a moment
        no bank reports.
    """
    running = Decimal("0.00")
    totals: "dict[date, Decimal]" = {}
    for line in lines:
        running += line.amount
        totals[line.posted_on] = running
    return totals


def solve_effective_day(
    lines: list,
    stated_balance: Decimal,
    opening: Decimal,
    not_after: date,
) -> "date | None":
    """Return the day *stated_balance* is the balance for, or ``None``.

    Args:
        lines: :class:`~._line.StatementLine` values in chronological order.
            Must be non-empty.
        stated_balance: What the file claims the account held.
        opening: The balance before the first line, known independently of
            *stated_balance*.
        not_after: The latest day a candidate may be -- the day the header
            itself names.  **A bound rather than a filter applied afterwards**:
            a bank cannot state a balance for a day it has not reached, and
            ``ck_statement_imports_effective_day_within_file`` refuses such a
            row, so an unbounded solve turned a describable file into a 500.
            Found by two independent adversarial reviews, 2026-08-23.

    Returns:
        The LATEST day at or before *not_after* satisfying
        ``stated - sum(lines up to it) == opening``, or ``None`` when no
        candidate day does.

    **Two satisfying days are harmless and that is proven rather than
    assumed.**  If ``d1 < d2`` both satisfy it then the lines in ``(d1, d2]``
    sum to zero, so for any day ``D`` the balance derived from the anchor at
    ``d1`` is ``stated + sum((d1, D])`` and the one derived from ``d2`` is
    ``stated - (sum((d1, d2]) - sum((d1, D]))``, which is the same value.
    Every balance the app later derives is therefore identical either way, and
    taking the latest is a choice about which day to NAME rather than about
    money.  Measured: 0 of the developer's 5 real exports admit two.
    """
    totals = _cumulative_by_day(lines)
    before_first = lines[0].posted_on - timedelta(days=1)
    # The day before the first line is a candidate in its own right: a file
    # whose stated figure IS its opening states a balance no line has moved.
    solved = (
        before_first
        if stated_balance == opening and before_first <= not_after
        else None
    )
    for day in sorted(totals):
        if day <= not_after and stated_balance - totals[day] == opening:
            solved = day
    return solved


def _refuse_self_contradiction(
    lines: list, stated_balance: Decimal, opening: Decimal,
) -> None:
    """Raise because the file's own CHAIN reaches its header figure on no day.

    Args:
        lines: The file's lines, chronological, carrying a running balance.
        stated_balance: What its header claims.
        opening: The balance its own chain states before the first line.

    Raises:
        StatementBalanceUnexplained: Always.  It carries BOTH figures, because
            the pair is what tells the owner the file disagrees with ITSELF
            rather than with anything the app believes -- which is the only
            disagreement a re-export can be expected to fix.
    """
    implied = opening + _cumulative_by_day(lines)[lines[-1].posted_on]
    raise StatementBalanceUnexplained(
        stated_balance,
        implied,
        f"its header says the account held {stated_balance}, while its own "
        f"per-line running balance puts it at {implied} after "
        f"{lines[-1].posted_on} and reaches that header figure on no day it "
        f"covers",
    )


def recorded_opening_before(
    account_id: int, day: date,
) -> "KnownOpening | None":
    """Return what this account's RECORDED statements say it held before *day*.

    **The balance before any line posted on *day* is the balance at the END of
    the day before it**, so this asks :func:`~._balance.fold_bank_balances` for
    that one day rather than restating the walk.  That is not tidiness: this
    function and the report added at plan step ``bank_import:X-f6e-2`` both
    answer "what does the bank's own record say this account held", and two
    spellings of one quantity is the shape that put ``$15.96`` between the cash
    scalar and the cash series on the real Checking account.

    Args:
        account_id: The account to walk.
        day: The day to answer for; the balance returned is the one BEFORE any
            line posted on it.

    Returns:
        The :class:`KnownOpening`, or ``None`` when this account holds no
        anchored import, or when its recorded coverage does not reach from that
        anchor to *day*.

    **The evidence comes back with the figure, and never stronger than
    ``corroborated``.**  Reaching an answer here means two statements agree,
    which is exactly what that level means -- and :func:`weaker_of` caps it at
    the anchor's own strength, so an anchor the app merely assumed yields an
    assumption rather than laundering itself into a determination.  **The cap
    is applied HERE and not in the fold**, because it states what THIS reader
    learned: a report displaying the same figure learns nothing new and carries
    the anchor's own strength unchanged.

    **Coverage is why this can answer ``None`` on an account that HAS an
    anchor**, and the direction it fails in is deliberate.  The walk is only
    exact if every line between the anchor and *day* is recorded; a gap between
    two imports means lines nobody has imported, and summing across one yields
    a confident wrong number.  The fold leaves such a day out of its result, and
    answering ``None`` here sends the caller to ``uncorroborated``, which the
    receipt SAYS -- an unchecked anchor the owner is told about beats a checked
    one that is false.
    """
    folded = fold_bank_balances(account_id, [day - timedelta(days=1)])
    if folded is None:
        return None
    balance = folded.balances.get(day - timedelta(days=1))
    if balance is None:
        return None
    return KnownOpening(
        amount=balance,
        evidence=weaker_of(
            StatementBalanceEvidenceEnum.CORROBORATED,
            folded.anchor.evidence,
        ),
    )


def release_anchors_from(
    account_id: int, day: date, except_import_id: "int | None" = None,
) -> int:
    """Release every anchor a line change on or after *day* has undercut.

    **An anchor is a conclusion drawn from the lines recorded at or before its
    own day, so a write that changes those lines takes the conclusion with
    it.**  Both doors that change them call this:
    :func:`~._record.record_statement` with the earliest day it freshly
    recorded, and :func:`~._undo.delete_import` with the earliest day whose
    lines it is removing.

    Args:
        account_id: The account whose anchors to examine.
        day: The earliest day whose lines changed.
        except_import_id: An import to leave alone -- the one whose write this
            is.  **A parameter rather than a re-statement afterwards**: the
            recording import solved its own anchor against its own COMPLETE
            line list, so those lines never undercut it, and expressing that as
            an exclusion says so once where restoring the row after a blanket
            release would say it twice and could drift.

    Returns:
        How many anchors were released.

    **Reproduced as money before this existed**, by two independent adversarial
    reviews on 2026-08-23.  A later export inserting a line into a day an
    earlier anchor had already priced left that anchor believing a balance the
    bank had since restated, and the next import solved its own day against it
    -- storing a day two days early under a *corroborated* badge, and refusing
    an honest export with a figure the bank's file never asserted.  Deleting an
    import removed the lines an anchor rested on while a later overlapping
    import kept its span, so the coverage test reported "covered" over a
    `$150.00` hole.  One rule closes both: the evidence moved, so the
    conclusion goes.

    **Released rather than re-solved, deliberately.**  Re-solving would be the
    app inferring its way around facts that have changed underneath it; the
    next import re-establishes an anchor from evidence that is actually
    present, and until then the account honestly holds none.
    """
    released = (
        db.session.query(StatementImport)
        .filter(
            StatementImport.account_id == account_id,
            StatementImport.balance_effective_on.isnot(None),
            StatementImport.balance_effective_on >= day,
            StatementImport.id != except_import_id
            if except_import_id is not None else db.true(),
        )
        .all()
    )
    for row in released:
        row.balance_effective_on = None
        row.balance_evidence_id = None
    return len(released)


def resolve_anchor(
    lines: list,
    stated_balance: "Decimal | None",
    stated_balance_on: "date | None",
    recorded: "KnownOpening | None",
) -> "ImportedBalance | None":
    """Return what this file determines about its own stated balance.

    Args:
        lines: :class:`~._line.StatementLine` values in chronological order.
            Must be non-empty; the door refuses an empty file before here.
        stated_balance: What the file's header claims, or ``None`` when it
            states none.
        stated_balance_on: The day that header names.  ``None`` exactly when
            *stated_balance* is -- which the adapter holds and this refuses to
            depend on, because a function whose whole job is to be total over
            its inputs may not rest a guard on another module's invariant.
        recorded: What the account's already-recorded statements say it held
            before this file's first line, with the strength of that claim, or
            ``None`` when they do not say.

    Returns:
        The :class:`ImportedBalance`, or ``None`` when the file states no
        balance at all.  A file that DOES state one but whose own lines cannot
        reach the day it claims gets a value with ``effective_on`` and
        ``evidence`` both ``None`` -- the claim recorded, the anchor
        undetermined, which is the honest absence rather than a guess.

    Raises:
        StatementBalanceUnexplained: When the file carries a per-line running
            balance that reaches its own header figure on no day it covers.
            **Only that**: a mismatch against RECORDED history is not the
            file's fault, and an earlier draft refused honest exports for it.

    **Three arms, and each is named rather than reached by falling out of a
    loop.**  A first draft iterated ``(chain, recorded)`` and returned on the
    first non-``None``, which stated a fallback that did not exist -- a chain
    that failed to solve never tried the recorded opening, correctly, but the
    shape said otherwise.  Found by adversarial review 2026-08-23.
    """
    if stated_balance is None or stated_balance_on is None:
        return None
    claim = {"stated": stated_balance, "stated_on": stated_balance_on}
    chain = opening_balance(lines)
    if chain is not None:
        # The file states the opening itself, so it is answerable from the file
        # alone -- and a failure to solve is the file contradicting itself.
        solved = solve_effective_day(
            lines, stated_balance, chain, stated_balance_on,
        )
        if solved is None:
            _refuse_self_contradiction(lines, stated_balance, chain)
        return ImportedBalance(
            **claim,
            effective_on=solved,
            evidence=StatementBalanceEvidenceEnum.FILE_CHAIN,
        )
    if recorded is not None:
        # What the account already holds decides, and no failure to solve is
        # the file's fault: the movements explaining a date-range export's
        # header are simply not in it.
        solved = solve_effective_day(
            lines, stated_balance, recorded.amount, stated_balance_on,
        )
        return ImportedBalance(
            **claim,
            effective_on=solved,
            evidence=None if solved is None else recorded.evidence,
        )
    # Nothing constrains it, which is what a FIRST import is.  The figure is
    # taken as the balance after the file's last line -- what the bank means
    # when nothing is pending -- bounded by the day the header names, because
    # a balance cannot be effective on a day the bank had not reached.
    return ImportedBalance(
        **claim,
        effective_on=min(lines[-1].posted_on, stated_balance_on),
        evidence=StatementBalanceEvidenceEnum.UNCORROBORATED,
    )
