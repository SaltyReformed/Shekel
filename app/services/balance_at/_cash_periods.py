"""Balance-at-T seam -- the CASH fold regrouped into per-period COLUMNS.

Ruling R-K's half of :mod:`._cash_fold`: the same assembled fold, read a second
way.  The fold answers "what is the balance on date D" off one running total;
this module answers "what does one pay-period COLUMN say" by grouping the very
same valued rows on the BUDGET clock beside it, so the grid's balance row and
its subtotal rows reconcile by construction rather than by a test holding two
producers in step.

**Why it is its own module (plan step S1-c).**  It lived inside ``_cash_fold``
until ruling R-DH (f) split the remainder in two, at which point that module
passed the 1,000-line ceiling.  Growing past a gate is a signal, not a nuisance:
assembling a running total and regrouping it into columns are two jobs, they
share exactly one input (the :class:`~._cash_fold.AssembledCashFold`), and the
dependency runs one way -- this module imports the fold and the fold imports
nothing here.  Splitting on that seam is what the ceiling was measuring.

The identity every column satisfies, in terms of
:class:`CashPeriodFigures`::

    balance(p.end) - balance(p.start - 1 day)
        == net + period_timing + book_vs_bank

(the boundary form, so the FIRST period is covered too -- it has no predecessor
column to subtract).  A modelled account adds two more terms above this layer;
see :mod:`._grid`.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from bisect import bisect_right
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services.cash_ledger import CashLedgerWalk, sum_projected
from app.utils.money import round_money

from ._cash_fold import (
    AssembledCashFold,
    _CashPlan,
    _period_balances,
    assemble,
)

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class CashPeriodFigures:
    """One pay period's cash column: the balance, the subtotals, the remainders.

    The per-period output of :func:`cash_period_view`, and the grid rows ruling
    R-K makes ONE row set grouped two ways.  For every period and every account
    kind, in terms of :attr:`balance` below::

        balance(p.end) - balance(p.start - 1 day)
            == net + period_timing + book_vs_bank

    (the boundary form, so the FIRST period is covered too -- it has no
    predecessor column to subtract).  That identity is a property of the
    construction, not a coincidence: :attr:`net` sums the rows attributed to the
    period on the BUDGET clock, :attr:`period_timing` is what the same rows
    contribute on the CASH clock MINUS that budget sum, :attr:`book_vs_bank` is
    what the user's own balance assertions booked inside the period, and the
    balances are the fold of those very steps.  Verified on the prod-shape
    clone 2026-07-25 over 360 (account, period) pairs -- 6 non-loan accounts x
    60 periods -- with zero breaks.

    An INTEREST account's modelled accrual is NOT in :attr:`balance` (this is
    the kind-blind CASH-FLOW view), so a grid layering it back on adds R-K's
    remaining terms and reads ``balance[p] - balance[p-1] == net[p] +
    period_timing[p] + book_vs_bank[p] + contribution[p] + accrual[p]``.

    **The remainder is TWO figures, and that is ruling R-DH (f).**  It was one
    -- rendered "Timing & true-ups" -- and on the developer's own data it read
    ``-$4,588.69`` in a single column, of which ``-$4,161.47`` was a plug the
    engine had booked against its own double count.  Even correct it summed two
    unrelated facts: money landing in a different column from the one it was
    budgeted to, and the gap between what the app had recorded and what the
    bank actually held.  They have different causes and different fixes, so a
    user cannot act on their sum.  Split, each is a diagnostic:
    :attr:`period_timing` should read ``$0.00`` whenever every bill's money
    moves inside the period it was budgeted to, so a persistently non-zero
    value means a bill is budgeted to the wrong period or dates are being
    recorded late; :attr:`book_vs_bank` is untracked spend and should be small.

    Attributes:
        balance: The fold's balance at the period's ``end_date``, cent-quantized
            -- assertions replayed, settled cash counted from the day it moved,
            and the still-projected plan clamped forward (ruling R-G).
        income: Every row ATTRIBUTED to this period whose type is income --
            settled at its confirmed cash leg, still-projected at its live or
            entries-aware amount.  A magnitude, cent-quantized.
        expense: The same, for expense rows.  A magnitude (positive), so the
            column reads ``income`` minus ``expense``.
        net: ``round_money(income - expense)`` -- rounded ONCE at the boundary
            rather than as the difference of two separately-rounded legs,
            because it is the figure the balance roll-forward has to reconcile
            with.
        period_timing: What the two subtotal rows cannot explain about the
            balance change from the ROWS alone: money BUDGETED here that moved
            in another period (or has not moved yet), and money that MOVED here
            but is budgeted elsewhere.  ``$0.00`` for a period where every row
            settled in its own column -- which is every FUTURE period, so the
            row is conditional on screen.
        book_vs_bank: What the user's balance ASSERTIONS booked inside the
            period -- the difference between what the app had recorded and what
            the bank actually showed, each time they looked.  Excludes the
            OPENING assertion (see :func:`_assertion_sums`).  ``$0.00`` for a
            period with no true-up, which is every future period.
    """

    balance: Decimal
    income: Decimal
    expense: Decimal
    net: Decimal
    period_timing: Decimal
    book_vs_bank: Decimal


@dataclass(frozen=True)
class CashPeriodView:
    """A whole account's cash columns, and the income basis they were valued on.

    The output of :func:`cash_period_view`.  The override map rides on the
    result rather than being the caller's ARGUMENT (ruling R-Q): it is what the
    projection was actually computed with, so a consumer that renders the
    individual rows beside the columns -- the budget grid -- prices each row off
    the same map its balance row folded, BY CONSTRUCTION.  Passing it in was the
    only way for the two to disagree, and an argument a caller can get wrong is a
    defect rather than a contract (plan Section 8).

    Attributes:
        columns: ``OrderedDict`` period id -> :class:`CashPeriodFigures`, in the
            order the caller's *periods* were given.  Every input period is
            present.
        amount_overrides: The live ``{transaction_id: Decimal}`` map the
            still-Projected rows were valued through -- recomputed salary income
            and derived loan debits.  ``{}`` for an account with no plan.
    """

    columns: "OrderedDict[int, CashPeriodFigures]"
    amount_overrides: "dict[int, Decimal]"


def cash_period_view(
    account: Account,
    scenario_id: int,
    as_of: date,
    periods: "list[PayPeriod]",
) -> CashPeriodView:
    """Return the account's cash column for each of *periods* -- ruling R-K.

    ONE valued row set, grouped on TWO clocks, plus the assertion steps.  The
    same walk and the same plan :func:`~._cash_fold.fold_cash_balances` folds
    are grouped here a second way -- by the pay period each row was BUDGETED to
    -- so the grid's balance row and its subtotal rows stop being two producers
    that a test has to keep in step and become two readings of one set.

    **Why the subtotals had to change basis, measured.**  Today's subtotal counts
    only rows that are still UNPAID (``cash_ledger.sum_projected`` filters through
    ``is_projected``), while a balance folded from the facts counts money that
    MOVED.  On the real Checking account that identity breaks on 8 of 59 period
    pairs -- worst ``$2,505.17`` -- and every past column reads ``$0.00`` income
    and ``$0.00`` expenses while thousands of dollars moved through it
    (finding N-41).  So :attr:`~CashPeriodFigures.income` /
    :attr:`~CashPeriodFigures.expense` count EVERY row attributed to the period,
    which is budget-vs-actual and fixes the ``$0.00`` past columns as a side
    effect.  Verified on the prod-shape clone 2026-07-25: the eight past columns
    go from ``$0.00`` to real figures, the current column from ``-$140.63`` to
    ``$3,153.22``, and every FUTURE column is unchanged to the cent (nothing has
    settled there, so the two bases coincide).

    **What the remainders hold, and why they are TWO rows** (ruling R-DH (f)).
    Three things the subtotal rows structurally cannot say, falling into two
    causes.  :attr:`~CashPeriodFigures.period_timing` carries the two that are
    about WHEN money moved: a row that settled outside its own pay period (19 of
    the real account's 130 settled rows; nets to ``$0.00`` across history and
    swings to ``-$2,007.46`` inside one period), and a still-projected row
    ruling R-G clamped forward out of its column.
    :attr:`~CashPeriodFigures.book_vs_bank` carries the balance ASSERTIONS (51
    after the opening on the real account, ``-$2,906.31`` net), which are about
    what the app did not know.  Rejected at the ruling: leaving the subtotals
    unpaid-only, which turns the remainder into a garbage bucket holding all
    real past activity; shipping no remainder at all, which leaves a visible
    contradiction on the screen; and summing the two, which is what shipped
    until 2026-08-01 and produced a figure with no action attached to it.

    **The OPENING assertion is in neither** (ruling R-I): the fold moves that one
    correction into its seed -- back-projecting the first assertion over the
    records it already contains -- so it moves no balance inside any period and
    must not appear in a period's remainder either.

    Kind-blind, exactly as the fold is (ruling R-J): this is the CASH-FLOW view,
    whose balance has to reconcile with the transaction rows rendered beside it.
    An INTEREST account's accrual is layered on ABOVE this by the grid view,
    which is the ``+ accrual[p]`` term of R-K's identity.

    Args:
        account: The account to project.  Must be attached to ``db.session``;
            its kind is not consulted.
        scenario_id: The budget scenario whose rows to group.
        as_of: The reader's NOW (ruling R-G's clamp floor) -- NOT a valuation
            date; each period is valued at its own ``end_date``.
        periods: The pay periods to report, in the caller's display order.  They
            need not be contiguous and need not start at the account's anchor:
            each period's figures are read off its OWN span, so a window is a
            window rather than a re-based projection.

    Returns:
        The :class:`CashPeriodView`: one :class:`CashPeriodFigures` per input
        period (a period with no rows and no assertions reports zeros against
        its folded balance), plus the live override map the projection was
        computed with.
    """
    return period_view_of(assemble(account, scenario_id, as_of), periods)


def period_view_of(
    folded: AssembledCashFold, periods: "list[PayPeriod]",
) -> CashPeriodView:
    """Regroup an ALREADY-assembled fold into its per-period columns.

    :func:`cash_period_view`'s body, split from its assembly so a reader that
    needs the cash columns AND something else off the same account pays for ONE
    walk, ONE plan load and ONE valuation rather than two (plan step X-g2a).
    The grid is that reader: from plan step X-g2b it renders the modelled
    balance -- :func:`app.services.balance_at._asset_fold.resolve` over this very
    :class:`~._cash_fold.AssembledCashFold` -- beside the budget-clock subtotals
    this returns, and calling both entry points would have assembled the account
    twice.

    Taking the assembled record rather than the account is what makes the
    sharing STRUCTURAL: the columns and whatever the caller resolves beside them
    are readings of one valued row set by construction, not two producers that a
    test keeps in step.

    Args:
        folded: The account's :class:`~._cash_fold.AssembledCashFold`
            (:func:`~._cash_fold.assemble`).
        periods: The pay periods to report, in the caller's display order.  See
            :func:`cash_period_view` for the windowing contract.

    Returns:
        The :class:`CashPeriodView`.
    """
    spans = _PeriodSpans.of(periods)
    return CashPeriodView(
        columns=_assemble_figures(
            periods,
            _period_balances(folded, periods),
            _budget_legs(folded.walk, folded.plan, spans),
            _cash_sums(folded.walk, folded.day_nets, spans),
            _assertion_sums(folded.walk, spans),
        ),
        amount_overrides=folded.plan.basis.amount_overrides,
    )


@dataclass(frozen=True)
class _PeriodSpans:
    """The reported periods, indexed for "which column does this day fall in".

    The CASH clock's grouping key.  A day is answered by the period whose
    ``[start_date, end_date]`` span CONTAINS it, and by nothing otherwise -- no
    nearest-period fallback, deliberately.  The seam's
    :func:`~app.services.loan_ledger.find_period_containing_date` does fall back
    to the latest period that ENDED before the target, which is right for the
    question it answers (an anchor correction needs a home period, and
    ``journal_entries.pay_period_id`` is NOT NULL) and wrong for this one: the
    identity this index serves reads a period's balance change as the steps
    inside its own span, so a step in a gap or past the horizon belongs to NO
    column and must not be pulled into the previous one.

    Pay periods do not overlap -- the generator rejects a batch whose earliest
    start falls on or before the latest existing ``end_date``, because two
    periods covering one day also make ``get_current_period`` nondeterministic --
    so the latest period STARTING on or before a day is the only candidate that
    can contain it, and one bisect answers.

    Attributes:
        starts: The periods' ``start_date`` values, ascending -- the bisect key.
        periods: The same periods in that ascending order.
    """

    starts: "list[date]"
    periods: "list[PayPeriod]"

    @classmethod
    def of(cls, periods: "list[PayPeriod]") -> "_PeriodSpans":
        """Index *periods* by start date (the caller's order is not assumed).

        Args:
            periods: The pay periods to report, in any order.

        Returns:
            The :class:`_PeriodSpans` index.
        """
        ordered = sorted(periods, key=lambda period: period.start_date)
        return cls(
            starts=[period.start_date for period in ordered], periods=ordered,
        )

    def containing(self, day: date) -> "int | None":
        """Return the id of the period whose span contains *day*, else ``None``.

        Args:
            day: The calendar day to place.

        Returns:
            The containing period's id, or ``None`` when *day* falls in a gap,
            before the first reported period, or after the last one's end.
        """
        index = bisect_right(self.starts, day) - 1
        if index < 0:
            return None
        period = self.periods[index]
        return period.id if day <= period.end_date else None

    def zeroed(self) -> "dict[int, Decimal]":
        """Return a ``{period_id: 0.00}`` accumulator over every reported period.

        Returns:
            One zero per reported period, so a component's dict is TOTAL over
            the window and a period with nothing in it reads ``0.00`` rather
            than being missing.
        """
        return {period.id: _ZERO_MONEY for period in self.periods}


def _budget_legs(
    walk: CashLedgerWalk,
    plan: _CashPlan,
    spans: _PeriodSpans,
) -> "dict[int, tuple[Decimal, Decimal]]":
    """Return ``{period_id: (income, expense)}`` on the BUDGET clock.

    Every row ATTRIBUTED to a reported period, whatever day its money moved:
    settled rows at the confirmed cash leg the walk already valued them at, and
    still-projected rows at the shared ``sum_projected`` reduction -- the same
    engine :func:`~._cash_fold._planned_day_nets` reduces the same rows through
    on the other clock, through the same
    :class:`~app.services.cash_ledger.ProjectedBasis`, which is why the two
    groupings reconcile to the cent.

    The income / expense split follows the transaction TYPE
    (:attr:`~app.services.cash_ledger.CashSourceFact.is_income`), never the sign
    of the row's value, and the difference is observable: a settled expense whose
    ``actual_amount`` was corrected BELOW its credit-card entries has a POSITIVE
    cash leg (it nets money back into checking) and still belongs on the expense
    row, as a negative expense.  An expense that came back is not income.  The
    net and the balance agree either way, which is exactly why the shape is
    pinned -- it is the only one that can tell the two rules apart, and without
    it this classification would be an untested claim
    (``test_cash_period_view.py``:
    ``test_a_row_counts_on_its_TYPE_row_even_when_its_cash_leg_inverts``).

    Args:
        walk: The account's walk -- its settled facts carry both clocks.
        plan: The account's :class:`~._cash_fold._CashPlan`.
        spans: The reported periods.

    Returns:
        ``{period_id: (income, expense)}`` -- both magnitudes, UNROUNDED (the
        caller rounds once at the boundary), and total over the window.
    """
    income = spans.zeroed()
    expense = spans.zeroed()
    for fact in walk.source_facts:
        if fact.pay_period_id not in income:
            continue
        if fact.is_income:
            income[fact.pay_period_id] += fact.delta
        else:
            # A settled expense's leg is NEGATIVE (money left), and the expense
            # row on screen is a magnitude.
            expense[fact.pay_period_id] -= fact.delta
    by_period: "dict[int, list[Transaction]]" = defaultdict(list)
    for txn in plan.rows:
        if txn.pay_period_id in income:
            by_period[txn.pay_period_id].append(txn)
    for period_id, txns in by_period.items():
        projected_income, projected_expense = sum_projected(txns, plan.basis)
        income[period_id] += projected_income
        expense[period_id] += projected_expense
    return {
        period_id: (income[period_id], expense[period_id])
        for period_id in income
    }


def _cash_sums(
    walk: CashLedgerWalk,
    day_nets: "dict[date, Decimal]",
    spans: _PeriodSpans,
) -> "dict[int, Decimal]":
    """Return ``{period_id: net}`` on the CASH clock -- what MOVED in the period.

    The same rows :func:`_budget_legs` groups by budget column, grouped instead
    by the day each one's money moves: a settled row on its ``settled_on``
    (``paid_at``'s display-timezone civil day, resolved once on the fact --
    ruling R-DH), a planned row on the day ruling R-G lands it.
    Assertions are NOT here -- they are :func:`_assertion_sums`, because an
    assertion is not a row.

    Args:
        walk: The account's walk.
        day_nets: The plan's per-day nets
            (:func:`~._cash_fold._planned_day_nets`).
        spans: The reported periods.

    Returns:
        ``{period_id: net}`` -- signed, UNROUNDED, total over the window.
    """
    moved = spans.zeroed()
    for fact in walk.source_facts:
        period_id = spans.containing(fact.settled_on.civil_day)
        if period_id is not None:
            moved[period_id] += fact.delta
    for day, net in day_nets.items():
        period_id = spans.containing(day)
        if period_id is not None:
            moved[period_id] += net
    return moved


def _assertion_sums(
    walk: CashLedgerWalk, spans: _PeriodSpans,
) -> "dict[int, Decimal]":
    """Return ``{period_id: correction}`` -- what the user's true-ups booked.

    Every assertion correction EXCEPT the opening's, on the civil day it was
    asserted.  The opening is excluded because ruling R-I moves it into the
    fold's seed (:func:`~._cash_fold._actual_steps`), where it back-projects
    over the records it already contains rather than stepping the balance on its
    own day; counting it here would put a jump in a column the balance never
    took.

    The slice is the exact COMPLEMENT of ``_actual_steps``' ``[0]``, and
    that is why it is a slice rather than a second ``is_opening`` test: two
    independent predicates could come to disagree about which correction the
    seed swallowed, while ``[0]`` and ``[1:]`` partition the list by
    construction.

    Args:
        walk: The account's walk.  Its ``anchor_corrections`` are chronological
            and the FIRST is the opening (the leaf's own contract).
        spans: The reported periods.

    Returns:
        ``{period_id: correction}`` -- signed, UNROUNDED, total over the window.
    """
    asserted = spans.zeroed()
    for correction in walk.anchor_corrections[1:]:
        period_id = spans.containing(correction.observed_on.civil_day)
        if period_id is not None:
            asserted[period_id] += correction.delta
    return asserted


def _assemble_figures(
    periods: "list[PayPeriod]",
    balances: "OrderedDict[int, Decimal]",
    legs: "dict[int, tuple[Decimal, Decimal]]",
    moved: "dict[int, Decimal]",
    asserted: "dict[int, Decimal]",
) -> "OrderedDict[int, CashPeriodFigures]":
    """Combine the three groupings into one :class:`CashPeriodFigures` per period.

    The remainders are computed HERE and directly -- ``what moved in the column``
    minus ``what was budgeted to it``, and the assertions -- rather than as the
    leftover of the fold's balance change.  That distinction is the step's whole
    verification standard: a remainder defined as ``balance_delta - net`` makes
    the identity arithmetically true and therefore untestable, and it would
    silently ABSORB a row the budget grouping got wrong.  Computed from the row
    set, the identity is a claim the fold can falsify (Section 7.2).

    **The two remainders are rounded separately, and their sum is still exact**
    (ruling R-DH (f)).  Every leg reaching this point is already cent-quantized
    (stored ``Numeric(12,2)`` columns, and both live-override seams return
    ``round_money``), so each ``round_money`` is a no-op on real data and
    ``round(a) + round(b) == round(a + b)`` holds rather than being assumed --
    which is what lets the identity hold on the DISPLAYED figures, not merely on
    the raw ones, now that the row a user reads is two rows.

    Args:
        periods: The pay periods to report, in display order.
        balances: The fold sampled at every period ``end_date``, keyed by
            period id (:func:`~._cash_fold._period_balances`).
        legs: The budget-clock ``(income, expense)`` per period.
        moved: The cash-clock net per period.
        asserted: The assertion corrections per period.

    Returns:
        ``OrderedDict`` period id -> :class:`CashPeriodFigures`.
    """
    figures: "OrderedDict[int, CashPeriodFigures]" = OrderedDict()
    for period in periods:
        income, expense = legs[period.id]
        net = income - expense
        figures[period.id] = CashPeriodFigures(
            balance=balances[period.id],
            income=round_money(income),
            expense=round_money(expense),
            net=round_money(net),
            period_timing=round_money(moved[period.id] - net),
            book_vs_bank=round_money(asserted[period.id]),
        )
    return figures
