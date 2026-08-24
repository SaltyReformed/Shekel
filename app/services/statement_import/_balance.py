"""What the BANK says an account held at the end of a day -- ONE fold, sampled.

An anchored import states a figure for a day (plan step ``bank_import:X-f6e-1``,
ruling **R-GF**), and the recorded lines say what moved on every day around it.
Together they determine the bank's own balance for any day the lines reach::

    bank_balance(D) = anchor.balance + sum(lines posted in (anchor.day, D])

with the sign falling out of which day comes first, so no branch decides it.

**One derivation, sampled at whatever grain the reader asks for**, which is the
discipline the cash side already pays for and this module exists to keep:
:func:`fold_bank_balances` prefix-sums the account's recorded days ONCE and
reads that running total at every requested day, and :func:`bank_balance_on`
is that same fold sampled at one.  A scalar that re-walked to its date with its
own window is exactly the shape that put ``$15.96`` between the cash scalar and
the cash series on the real Checking account (``balance_at._cash_flow``), and a
statement account will hold a few hundred recorded days, so there is nothing to
buy by writing the second walk.

**Two readers, and they mean different things by EVIDENCE.**
:func:`~._anchor.recorded_opening_before` asks this for the balance before a
file's first line, and what it learns is that TWO STATEMENTS AGREE -- so it
caps what it returns at
:attr:`~app.enums.StatementBalanceEvidenceEnum.CORROBORATED` and applies its own
weakest-link rule on top.  A REPORT asks this to display a figure and learns
nothing new, so it carries the anchor's own strength unchanged.  The cap
therefore lives at the reader that earns it, never here.

**A day is answered only when the recorded lines REACH it** from the anchor.
The walk is exact only if every line between the two days is recorded; a gap
between imports means lines nobody has imported, and summing across one yields
a confident wrong number.  Such a day is ABSENT from the fold's result rather
than present with a guess -- the same direction
:func:`~._anchor.recorded_opening_before` already fails in, and for the same
reason.

**The spans it reads are trustworthy only because deletion releases anchors**
(:func:`~._anchor.release_anchors_from`): an import's span claims days its lines
covered, and ``delete_import`` takes those lines while a later overlapping
import keeps its own span.  Before that release existed this reported "covered"
over a `$150.00` hole.

Services-boundary discipline: no Flask import, no clock read.  It queries, which
is this package's shape (:mod:`._identity`, :mod:`._anchor`) and for the same
reason -- the recorded-history half of one subject is not a different subject.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.enums import StatementBalanceEvidenceEnum
from app.extensions import db
from app.models.statement_import import BankStatementLine, StatementImport

_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class BankAnchor:
    """The recorded fact every derived bank balance is walked from.

    **A value rather than the :class:`~app.models.statement_import.StatementImport`
    row it came from**, because what a derivation needs is the three facts
    below and nothing else -- and handing a reader the ORM row invites it to
    reach for ``period_start`` or ``file_name`` and grow a dependency on which
    import happened to win.

    Attributes:
        day: The day the figure is the balance FOR -- the import's solved
            ``balance_effective_on``, never the day its header names.
        balance: What the bank said the account held at the end of that day.
        evidence: How strongly that figure is held, as the WEAKEST LINK in the
            chain behind it (ruling **R-GF**).
    """

    day: date
    balance: Decimal
    evidence: StatementBalanceEvidenceEnum


@dataclass(frozen=True)
class BankBalances:
    """A fold of the bank's own record, already sampled.

    Attributes:
        anchor: The :class:`BankAnchor` every figure here was walked from.
        balances: ``{day: the bank's balance at the end of that day}``, holding
            an entry only for the requested days the recorded lines REACH.  A
            requested day the lines cannot reach is absent, so ``.get(day)``
            answers ``None`` for it -- absence rather than a sentinel, because
            the caller's question is "what does the bank say" and "nothing it
            has recorded says" is a real answer.
    """

    anchor: BankAnchor
    balances: "dict[date, Decimal]"


def anchor_evidence(anchor: StatementImport) -> StatementBalanceEvidenceEnum:
    """Return one anchored import's own evidence level.

    Args:
        anchor: The import, which carries a non-NULL ``balance_evidence_id``.

    Returns:
        Its :class:`~app.enums.StatementBalanceEvidenceEnum` member.

    **Resolved from the ID and never from the ref row's ``name``**, which is
    the project-wide IDs-for-logic rule at the one place it is easiest to
    break: ``StatementBalanceEvidenceEnum(row.balance_evidence.name)`` reads
    naturally and turns a display string into a dispatch, where
    ``shekel-refname-compare`` cannot see it because it is a constructor rather
    than a comparison.  Found by adversarial review 2026-08-23.
    """
    # Imported inside the call because ``ref_cache`` imports the models this
    # module imports, so a module-scope import would close a cycle at start.
    # Pylint: ``import-outside-toplevel`` (1/0) -- a real import cycle, not a
    # cost dodge; ``app/models/transaction.py`` takes the same shape.
    from app import ref_cache  # pylint: disable=import-outside-toplevel

    return ref_cache.statement_balance_evidence_member(
        anchor.balance_evidence_id,
    )


def usable_anchor(account_id: int) -> "BankAnchor | None":
    """Return the recorded fact this account's balances should be walked from.

    Args:
        account_id: The account whose imports to read.

    Returns:
        The :class:`BankAnchor`, or ``None`` when the account holds no anchored
        import -- which is an ordinary state, not a failure: a file may state no
        balance at all, or state one its own lines cannot reach (a date-range
        export states TODAY's figure), and both record the claim with no anchor.

    **Chosen by EVIDENCE first, with recency only as a tie-break.**  That is
    the correction of a comment claiming any anchor would serve because they
    are "mutually consistent by construction", which was false twice over: a
    ``file_chain`` anchor is solved against the file's own chain and an
    uncorroborated one against nothing, so neither consults a prior anchor and
    two can disagree freely; and ``created_at`` is the IMPORT ACT's time, not
    the statement's, so an unrelated later import would otherwise displace a
    nearer, stronger anchor.  Refuted by adversarial review 2026-08-23.

    **The strength ORDER is read from the enum, never from the ref row's id.**
    Sorting by ``balance_evidence_id`` would work only while the seed happens
    to INSERT the ladder in order -- a second statement of the ladder, in a
    migration, that nothing reconciles against
    :attr:`~app.enums.StatementBalanceEvidenceEnum.strength`.  It was written
    that way first and was measured BACKWARDS: the seed writes
    ``file_chain, corroborated, uncorroborated``, so ``id DESC`` returned the
    WEAKEST anchor.  An account holds a handful of imports, so the ordering
    that matters is done here over the enum and the query orders only the
    tie-break.
    """
    anchored = (
        db.session.query(StatementImport)
        .filter(
            StatementImport.account_id == account_id,
            StatementImport.balance_effective_on.isnot(None),
        )
        # The TIE-BREAK, already applied: ``max`` below returns the FIRST
        # maximal element, so the strongest anchor with the most recent
        # effective day wins without a second sort.
        .order_by(
            StatementImport.balance_effective_on.desc(),
            StatementImport.id.desc(),
        )
        .all()
    )
    if not anchored:
        return None
    chosen = max(anchored, key=lambda row: anchor_evidence(row).strength)
    return BankAnchor(
        day=chosen.balance_effective_on,
        balance=chosen.stated_balance,
        evidence=anchor_evidence(chosen),
    )


def covered_runs(account_id: int) -> "list[tuple[date, date]]":
    """Return this account's recorded spans, merged into contiguous runs.

    Args:
        account_id: The account whose imports to read.

    Returns:
        ``[(first_day, last_day), ...]`` ascending and disjoint, with
        overlapping or ADJACENT spans merged into one run.  Two runs in the
        list are therefore separated by at least one day nobody has imported.

    **Adjacent counts as contiguous** -- a span ending on the 4th and one
    starting on the 5th leave no day unimported between them -- which is what
    makes a run's interior a stretch the recorded lines fully describe.

    **Only an import that still OWNS at least one line contributes its span**,
    and that is a defect an adversarial review reproduced end to end on
    2026-08-24 rather than a precaution.  A span is a claim that every line in
    those days is recorded, and a RE-IMPORT of an identical file records zero
    fresh lines while keeping the full span (``recorded_count 0`` -- the
    developer's own second import is exactly that shape).  Deleting the import
    that actually owned those lines then left the re-import's span still
    claiming them: the walk crossed 28 unimported days and reported ``$1,000.00``
    where the truth was ``$850.00``, and because
    :func:`~._anchor.recorded_opening_before` reads this, the next import solved
    its own effective day against an opening ``$150.00`` wrong -- storing a
    stored day under a *corroborated* badge, which is verbatim the defect ruling
    **R-GF**'s second amendment was written to close.  An import owning no lines
    has nothing left to vouch for; the import that still owns them is what
    carries the claim, and where none does the days are honestly uncovered.
    """
    spans = (
        db.session.query(
            StatementImport.period_start, StatementImport.period_end,
        )
        .filter(
            StatementImport.account_id == account_id,
            db.session.query(BankStatementLine.id)
            .filter(BankStatementLine.import_id == StatementImport.id)
            .exists(),
        )
        .order_by(StatementImport.period_start)
        .all()
    )
    runs: "list[tuple[date, date]]" = []
    for start, end in spans:
        if runs and start <= runs[-1][1] + _ONE_DAY:
            runs[-1] = (runs[-1][0], max(runs[-1][1], end))
        else:
            runs.append((start, end))
    return runs


def reaches_end_of(
    runs: "list[tuple[date, date]]", anchor_day: date, end_day: date,
) -> bool:
    """Return whether the recorded lines carry the walk from one day to another.

    Args:
        runs: This account's :func:`covered_runs`.
        anchor_day: The day the anchor states a balance for.
        end_day: The day whose END balance is being derived.

    Returns:
        True when every day whose lines the walk must cross is inside ONE
        recorded run.  An empty crossing is reached vacuously, which is the
        honest answer rather than a special case: there is nothing between the
        two days to have missed.

    **The days that must be crossed are the half-open span between them**,
    ``(min, max]``, and the asymmetry is the arithmetic rather than a choice:
    walking from the anchor's end-of-day to another end-of-day applies exactly
    the lines posted after the earlier day and up to and including the later
    one, so the earlier day's OWN lines are already inside the figure being
    walked from.
    """
    low, high = min(anchor_day, end_day), max(anchor_day, end_day)
    first_needed = low + _ONE_DAY
    if first_needed > high:
        return True
    return any(
        start <= first_needed and end >= high for start, end in runs
    )


def bank_daily_movements(
    account_id: int,
) -> "list[tuple[date, Decimal]]":
    """Return what the bank's recorded lines moved on each day, ascending.

    Args:
        account_id: The account whose lines to read.

    Returns:
        ``[(day, signed total), ...]`` -- one entry per DISTINCT day the
        account has recorded lines for, ascending.  Empty for an account with
        no recorded line.

    Keyed by DAY rather than by line, because a balance is a fact about a day's
    end: a boundary that split a day between two of its lines would name a
    moment no bank reports.

    **ONE aggregate over ``bank_statement_lines``, and two readers.**
    :func:`_recorded_day_totals` prefix-sums it into the fold's running total,
    and the books-vs-bank report reads the movements themselves -- which it
    needs WITHOUT an anchor, because comparing what moved requires no level.
    Each spelling its own ``SUM(amount) GROUP BY posted_on`` is the duplicate
    an adversarial review measured on 2026-08-24, in a module whose own
    docstring preaches one derivation sampled.
    """
    return (
        db.session.query(
            BankStatementLine.posted_on,
            db.func.sum(BankStatementLine.amount),
        )
        .filter(BankStatementLine.account_id == account_id)
        .group_by(BankStatementLine.posted_on)
        .order_by(BankStatementLine.posted_on)
        .all()
    )


def _recorded_day_totals(
    account_id: int,
) -> "tuple[list[date], list[Decimal]]":
    """Return this account's recorded days and the running total through each.

    Args:
        account_id: The account whose lines to read.

    Returns:
        ``(days, cumulative)`` -- the DISTINCT days the account has recorded
        lines for, ascending, and the sum of every line posted on or before
        each.  Both empty for an account with no recorded line.

    The prefix-sum half of :func:`bank_daily_movements`: a reader asking for
    one day and a reader asking for two hundred consult the same running total.
    """
    days: "list[date]" = []
    cumulative: "list[Decimal]" = []
    running = _ZERO_MONEY
    for day, amount in bank_daily_movements(account_id):
        running += amount
        days.append(day)
        cumulative.append(running)
    return days, cumulative


def _through(
    days: "list[date]", cumulative: "list[Decimal]", day: date,
) -> Decimal:
    """Return the sum of every recorded line posted on or before *day*.

    Args:
        days: The ascending distinct recorded days.
        cumulative: The running total through each of them.
        day: The day to read the running total at.

    Returns:
        The prefix sum, or ``0.00`` for a day before the first recorded line.
    """
    index = bisect_right(days, day)
    return cumulative[index - 1] if index else _ZERO_MONEY


def fold_bank_balances(
    account_id: int, days: "list[date]",
) -> "BankBalances | None":
    """Return what the bank's own record says the account held on each day.

    Args:
        account_id: The account to derive for.
        days: The days to sample, in any order; duplicates are harmless.

    Returns:
        The :class:`BankBalances`, or ``None`` when the account holds no
        anchored import -- so no figure of the bank's is placed on any day and
        there is no level to derive one from.  A requested day the recorded
        lines cannot REACH from the anchor is absent from
        :attr:`BankBalances.balances`.

    **The anchor's own day is always answered**, which is the fold's fixed
    point and worth stating because it is what makes the walk checkable: at
    ``anchor.day`` the crossing is empty, so the figure is the bank's verbatim
    claim and no line has touched it.
    """
    anchor = usable_anchor(account_id)
    if anchor is None:
        return None
    recorded, cumulative = _recorded_day_totals(account_id)
    runs = covered_runs(account_id)
    through_anchor = _through(recorded, cumulative, anchor.day)
    balances = {
        day: anchor.balance + _through(recorded, cumulative, day)
        - through_anchor
        for day in days
        if reaches_end_of(runs, anchor.day, day)
    }
    return BankBalances(anchor=anchor, balances=balances)


def bank_balance_on(account_id: int, day: date) -> "Decimal | None":
    """Return what the bank's own record says the account held at *day*'s end.

    Args:
        account_id: The account to derive for.
        day: The civil day whose END balance to answer.

    Returns:
        The balance, or ``None`` when the account holds no anchored import or
        its recorded lines do not reach *day* from that anchor.

    **A SAMPLE of :func:`fold_bank_balances`, never a second walk.**  The two
    would be the same arithmetic today and the codebase has measured what
    happens next: a scalar and a series stating one quantity drifted apart by
    ``$15.96`` on the real Checking account before the cash seam collapsed them
    onto one fold.  One sample costs one extra prefix-sum pass over a few
    hundred days and removes the possibility.
    """
    folded = fold_bank_balances(account_id, [day])
    return None if folded is None else folded.balances.get(day)
