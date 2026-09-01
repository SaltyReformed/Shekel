"""Shekel Budget App -- what ONE calendar DAY is worth, and how it is read.

The analytics calendar draws a cell per day: at most three named flow lines, a
"+N more" residual for the rest, and an (income, expense) fold the month and
year headline totals are summed FROM.  This module holds those three rules and
the two value records they produce.

**Its own module because the ceiling said so, and the ceiling was right**
(plan step ``bank_import:X-gj-2b-3``).  ``calendar_service`` sat at 995 of
pylint's 1,000, and ruling **bank_import:R-II** -- a merchant refund files as a
NEGATIVE purchase -- needed the sign rules here rewritten and their reasoning
written down.  A cap is a forcing function rather than a ceiling to raise
(``docs/plans/conventions.md`` rule 4), which is the argument
:mod:`.calendar_infrequency` makes in as many words for the split that took the
recurrence question out of the same file.

**The subject is the SIGN, which is what makes these three one module.**  A
day's fold, its hidden residual and its display order all read the same
``DayEntry.amount``, and all three read it differently -- two as a FIGURE and
one as a RANKING.  Keeping them apart is how two of them came to disagree:
:func:`fold_income_expense` and :func:`day_overflow` each took ``abs()`` on a
figure that can now be negative, so a day cell and the "+N more" line it hides
rows behind reported a refund as spending, in opposite halves of one cell.

**The amount is SIGNED and the convention is the column's**: positive means
money OUT on the expense leg, and money IN on the income leg.  A refund is a
negative EXPENSE, never an income; an expense that came back is not revenue,
which is ruling **bank_import:R-II**'s own ground and the rule
:mod:`~app.services.balance_at._cash_periods` states one surface over.

Pure: no database, no clock, no Flask.  Every fact arrives as an argument.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction
from app.services.calendar_infrequency import is_infrequent
from app.services.pay_calendar import PayCadence

# Day cells show at most this many named flow lines; any beyond collapse to
# a single "+N more" line whose residual net is computed in the service
# (templates never do money math).  The locked calendar anatomy fixes this
# at three (income first, then expenses by descending magnitude).
MAX_VISIBLE_DAY_FLOWS = 3


@dataclass(frozen=True)
class DayEntry:  # pylint: disable=too-many-instance-attributes
    """A single transaction's representation on a calendar day.

    Pylint: ``too-many-instance-attributes`` (10/7) -- suppressed
    because this is a cohesive value record -- one transaction's row on a
    calendar day -- consumed verbatim by the calendar surface: the CSV
    month export reads the display fields as adjacent columns (folding the
    booleans into single Income/Expense, Status, Large, and Infrequent
    columns), the month-detail table renders name/category/amount and the
    income/paid flags as individual cells, and the route reads
    amount/is_income for day totals.  The two category fields are read as
    independent columns, never as a unit.  Every field is an irreducible
    column of the row; splitting it would fragment one domain concept and
    break every consumer for no design gain.

    Attributes:
        amount: What the row is WORTH, SIGNED.  **It can be negative for an
            expense** since ruling **bank_import:R-II**: a settled envelope is
            worth the sum of its entries, and a merchant refund is a negative
            one.  Every reader here states which way it takes that sign.
    """

    transaction_id: int
    name: str
    amount: Decimal
    is_income: bool
    is_paid: bool
    is_large: bool
    is_infrequent: bool
    category_group: str | None
    category_item: str | None
    due_date: date | None


@dataclass(frozen=True)
class DayOverflow:
    """The collapsed "+N more" residual for a day with more flows than fit.

    A day cell renders at most :data:`MAX_VISIBLE_DAY_FLOWS` named flow lines;
    the remainder collapse to one "+N more" line.  This carries that line's
    two service-computed values so the template does no money math: the
    ``count`` of hidden flows and their signed ``net`` (income positive,
    expense negative).  Only days whose flow count exceeds the cap have one.
    """

    count: int
    net: Decimal


def build_day_entry(
    txn: Transaction,
    amount: Decimal,
    income_type_id: int,
    threshold: Decimal,
    pay_cadence: PayCadence | None,
) -> DayEntry:
    """Create a DayEntry from a transaction.

    Args:
        txn: The transaction to convert.
        amount: What the row is WORTH, from the build's one
            :func:`~app.services.cash_ledger.contributions_by_id` call.
            It replaced ``txn.effective_amount`` at plan step X-au-c2: that
            model property could not answer for a row whose amount is DERIVED,
            because such a row stores no figure and resolving one needs a
            database -- and, for a paycheck, the owner's whole pay-period set.
        income_type_id: Ref ID for the Income transaction type.
        threshold: Amount at or above which a transaction is large.
        pay_cadence: The owner's pay cadence for the infrequent badge, or
            ``None`` when no row in this build repeats
            (:func:`~app.services.calendar_infrequency.badge_cadence`).

    Returns:
        A frozen DayEntry dataclass.
    """
    return DayEntry(
        transaction_id=txn.id,
        name=txn.name,
        amount=amount,
        is_income=txn.transaction_type_id == income_type_id,
        is_paid=bool(txn.status and txn.status.is_settled),
        # **A MAGNITUDE, deliberately**: "large" asks how big the movement is,
        # so a refund of $500 is as large as a charge of $500.  The same
        # reading :func:`order_for_display` takes, and the opposite of the two
        # FIGURES below.
        is_large=abs(amount) >= threshold,
        is_infrequent=is_infrequent(txn, pay_cadence),
        category_group=txn.category.group_name if txn.category else None,
        category_item=txn.category.item_name if txn.category else None,
        due_date=txn.due_date,
    )


def order_for_display(entries: list[DayEntry]) -> None:
    """Sort one day's entries IN PLACE: income first, then by magnitude.

    **The one place the day's display order is stated.**  It was spelled as a
    lambda inside the assembly loop, beside two folds that read the same amount
    the other way; naming it is what makes the difference visible.

    **The magnitude is right HERE and wrong in the two folds**, which is the
    whole reason this module exists.  This is a RANKING -- how big a movement
    is -- so a large refund belongs near the top of its day, exactly as
    ``spending_report_service._surprises`` ranks by ``-abs(delta)``.  A FIGURE
    that took the same ``abs()`` would report money arriving as money spent.

    Args:
        entries: One day's :class:`DayEntry` list, sorted in place.
    """
    entries.sort(key=lambda e: (not e.is_income, -abs(e.amount)))


def fold_income_expense(
    entries: list[DayEntry],
) -> tuple[Decimal, Decimal]:
    """Fold a collection of day entries into an (income, expense) pair.

    The single income/expense sign-fold for the calendar surface: income is the
    signed sum of the ``is_income`` entries; expense is the signed sum over the
    non-income ones, on the column's own convention that positive means money
    OUT.  Both legs seed at ``Decimal("0")`` so an empty or all-one-sign
    collection yields a ``Decimal``, never an int ``0`` -- money is always
    Decimal.  Applied per day to build ``MonthSummary.day_totals``, from which
    the month headline totals are then summed, so the per-day cells the
    calendar renders and the month total derive from one rule and cannot drift.

    **The expense leg took ``abs()`` until plan step
    ``bank_import:X-gj-2b-3``, and that was a MONEY DEFECT.**
    :attr:`DayEntry.amount` is
    :func:`~app.services.cash_ledger.contributions_by_id`'s answer, which for a
    settled envelope is the sum of its entries -- NEGATIVE for one whose
    refunds exceeded its purchases, since ruling **bank_import:R-II** relaxed
    ``ck_transaction_entries_positive_amount`` to ``amount <> 0``.  ``abs()``
    could not change that answer while every purchase was positive and became a
    SIGN FLIP the moment one could be negative: ``-86.67`` reported as
    ``+86.67``, moving ``net`` by ``$173.34`` on a day the account RECEIVED the
    money, on the MONTH and YEAR headline figures.  **The third instance of one
    defect on one producer chain** -- ``_breakdown._totals_by_category`` and
    ``_window._spent_total`` are the other two, both fixed by the same step.

    **A negative expense is the honest figure and this project ruled so one
    surface over**: :mod:`~app.services.balance_at._cash_periods` states that a
    settled expense whose cash leg inverts stays on the EXPENSE row as a
    negative expense, graded by
    ``test_a_row_counts_on_its_TYPE_row_even_when_its_cash_leg_inverts``.
    Booking a refund into the INCOME leg instead grosses up both sides of the
    month, which is the misfiling **R-II** exists to prevent.

    Args:
        entries: The :class:`DayEntry` records for one day (or any collection
            to fold); each carries ``amount`` and ``is_income``.

    Returns:
        ``(income, expense)`` as a pair of ``Decimal`` values.
    """
    income = sum((e.amount for e in entries if e.is_income), Decimal("0"))
    expense = sum(
        (e.amount for e in entries if not e.is_income), Decimal("0"),
    )
    return income, expense


def day_overflow(entries: list[DayEntry]) -> DayOverflow:
    """Summarize the flows past the visible cap into a "+N more" residual.

    The entries are pre-sorted (:func:`order_for_display`), so the hidden tail
    is everything after the first :data:`MAX_VISIBLE_DAY_FLOWS`.  The residual
    ``net`` is signed (income positive, expense negative) and seeded at
    ``Decimal("0")`` so it stays a ``Decimal``.  Called only for days whose
    flow count exceeds the cap.

    **The expense term is a NEGATION and no longer ``-abs()``** (plan step
    ``bank_import:X-gj-2b-3``), for the reason :func:`fold_income_expense`
    states above: a hidden REFUND raises the residual, and ``-abs()`` reported
    it lowering by the same amount.  The two had to be corrected together --
    the "+N more" chip is what the day cell shows INSTEAD of the rows it hides,
    so a disagreement between them is one cell contradicting itself.

    Args:
        entries: One day's ordered :class:`DayEntry` list (length greater than
            :data:`MAX_VISIBLE_DAY_FLOWS`).

    Returns:
        The :class:`DayOverflow` for the hidden tail.
    """
    hidden = entries[MAX_VISIBLE_DAY_FLOWS:]
    net = sum(
        (e.amount if e.is_income else -e.amount for e in hidden),
        Decimal("0"),
    )
    return DayOverflow(count=len(hidden), net=net)
