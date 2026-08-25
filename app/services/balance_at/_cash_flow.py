"""Balance-at-T seam -- the CASH-FLOW view (no per-kind dispatch).

The single-account cash-flow surfaces -- the budget grid, the dashboard pulse,
the analytics calendar, the cash detail page -- read an account's pure
transaction running-balance, NOT its kind-correct balance (see the package
docstring's "Two views, one seam").  These three entries are the seam's only
way to obtain that view.

**All three are one fold read at three grains** (plan step X-c2b2).  A period
map, a scalar at a date, and a day-by-day series are the SAME running total
(:mod:`app.services.balance_at._cash_fold`) sampled at period ends, at one
date, and at every date -- so they cannot disagree.  Before the cutover they
were three producers: the map carried an anchor forward over still-Projected
rows only, the scalar re-walked to a date with its own entry-date window, and
the daily series distributed the same rows over days -- and on the real
Checking account the scalar and the series stood ``$15.96`` apart on the day
before this commit (``$246.36`` at the worst day of the current period, finding
cash D2), while both dropped every row settled after the last balance assertion
(``$2,108.15`` invisible at that instant, finding cash D1) and answered a
pre-anchor date by fabricating today's balance or omitting the period entirely
(finding cash D3 / B-18).  One total fold subsumes all three.
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.models.account import Account
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.utils.dates import days_in_range

from ._context import BalanceContext

from . import _cash_fold
from ._inputs import _require_scenario

_ONE_DAY = timedelta(days=1)


def _require_civil_date(entry: str, **dates: object) -> None:
    """Refuse anything that is not exactly a civil :class:`datetime.date`.

    **A ``datetime`` is refused, and that is the whole reason this is a
    function.**  ``datetime`` SUBCLASSES ``date``, so the obvious
    ``isinstance(value, date)`` accepts one -- the guard reads like a type
    check and is not one.  The fold's step boundaries are civil dates, so a
    ``datetime`` reaching them dies inside ``bisect_right`` with
    ``'<' not supported between instances of 'datetime.datetime' and
    'datetime.date'``: a real failure, but one whose traceback names a
    bisect rather than the caller's argument, three layers from the mistake.

    It is expressed as "a date that is NOT a datetime" rather than an exact
    type test, because the suite's ``freeze_today`` clock hands the producers
    its own ``date`` SUBCLASS -- a legitimate civil date that an exact test
    would reject, turning a guard against one wrong type into a guard against
    every subclass.

    Saying the type this precisely also states the contract these entries
    hold: a balance is asked for on a DAY.  An instant is the walk's concern
    (the assertion partition), never a valuation date's.

    Args:
        entry: The seam entry's name, for the message.
        **dates: The argument name -> value pairs to check, in order.

    Raises:
        TypeError: On the first value that is not exactly a ``date``.
    """
    for label, value in dates.items():
        if not isinstance(value, date) or isinstance(value, datetime):
            raise TypeError(
                f"{entry} expects a civil datetime.date for {label}, "
                f"got {type(value).__name__} {value!r}"
            )


def cash_balance_map(
    account: Account, ctx: BalanceContext,
) -> "OrderedDict[int, Decimal]":
    """Return one account's cash-flow running balance per pay period.

    The cash-flow view: the account's projected end balance per period as a
    pure transaction running-balance, with NO per-kind dispatch.  This is what
    the single-account cash-flow surfaces show -- the dashboard pulse chart and
    the cash detail page -- where the balance row must reconcile with the
    account's own transaction rows and subtotal row on the same screen.

    **The budget grid was on this list until plan step X-g3b and is not any
    more** (ruling R-W): it reads :func:`~app.services.balance_at.grid_balance_view`,
    which answers a modelled account its MODELLED balance.  A reader that wants
    "what does the grid show" must call that entry -- for a modelled kind the
    two now answer differently by design, and this one would look right while
    proving nothing.

    Contrast with :func:`~app.services.balance_at.balance_map`, the
    KIND-CORRECT view: for an interest-bearing (HYSA), loan, investment, or
    property account ``balance_map`` dispatches to that kind's engine (accruing
    interest, walking an amortization schedule, compounding growth /
    appreciation) -- which is what the net-worth surfaces want, and what a
    cash-flow surface can only carry if the modelled movement is EXPLAINED on
    screen beside it.  The reason this entry once gave -- "accruing interest
    into the grid's balance row while its subtotal row stays transaction-based
    would leave a balance change the rows on screen cannot explain" -- was
    answered rather than overruled: ruling R-K put the explaining rows there
    (the accrual and the contribution), so the grid moved to the modelled
    balance at plan step X-g3b.  The surfaces still on THIS entry have no such
    rows, so they ask for the cash-flow balance of whatever account they are
    pointed at, regardless of its kind.

    **The one kind they are never pointed at is AMORTIZING, and that is a
    gate rather than a coincidence.**  A loan's balance is not a
    transaction sum (finding B-3), so every resolver feeding these entries
    refuses one at the source: ``resolve_grid_account`` since ruling D4 /
    plan step A1 (grid, dashboard, pulse), ``resolve_analytics_account``
    since plan step X-a1 (the calendar -- finding N-38), and the cash
    detail page's own ``_cash_page.cash_detail_wrong_type`` 404.  These producers
    therefore stay TOTAL and kind-blind by design, and no screen can ask
    them a question only ``balance_at.balance_at`` can answer.

    **EVERY requested period is in the result** (plan step X-c2b2).  The
    retired producer projected forward from the anchor and omitted every
    pre-anchor period, so a caller had to treat a missing key as "no balance";
    the fold replays every assertion, so a past period answers with the balance
    in force THEN.  Callers that skipped missing keys are unaffected -- there
    are none left to skip.

    Args:
        account: The account whose cash-flow balance to project.  Its
            ``user_id`` scopes the live salary override; its kind is NOT
            consulted (ruling R-J).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Its ``as_of`` is the reader's NOW -- what decides a still-projected
            row cannot already have happened (ruling R-G) -- NOT a valuation
            date; each period is valued at its own ``end_date``.  **It is also
            where the periods come from** (plan step C2-c): the domain is
            :meth:`~app.services.balance_at.BalanceContext.reported_periods`,
            the owner's whole saved calendar, and it is no longer an argument
            -- every caller passed exactly that, so the only thing the argument
            could express was a mistake.

    Returns:
        ``OrderedDict`` period_id -> cent-quantized ``Decimal``, in payday
        order.  Empty for an owner with no pay periods.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        PayCalendarError: The owner's paydays cannot define a calendar, which
            since plan step C2-c is reachable from every per-period seam entry
            rather than only from the recurrence pages -- see
            :meth:`~app.services.balance_at.BalanceContext.calendar`, where the
            reporting domain is derived, for the one state that produces it and
            the step that removes it.
    """
    _require_scenario(ctx)
    return _cash_fold.cash_period_balances(
        account, ctx.amounts(), ctx.as_of, ctx.reported_periods(),
    )


def cash_balance_at(
    account: Account, ctx: BalanceContext, as_of: date,
) -> Decimal:
    """Return one account's cash-flow balance as of a calendar date *as_of*.

    The scalar cash-flow view -- the date-precise counterpart of
    :func:`cash_balance_map`, and literally the same fold read at one date, so
    ``cash_balance_at(account, ctx, P.end_date)`` equals
    ``cash_balance_map(account, ctx)[P.id]`` whenever *P*'s stored
    ``end_date`` is the one its owner's paydays derive -- which plan step C2-c
    made the qualifier it is.  It read "by construction" until then, and the
    map now samples the DERIVED end while a caller reading ``P.end_date`` off
    an ORM row supplies the stored one; plan step C4 drops that column and
    makes the sentence unconditional again.  Measured 0 of 62 and 0 of 61
    disagreements on the two production-shaped databases.

    Used by the calendar's month-end balance, which must reconcile with the day
    cells it renders for the same month.

    Like :func:`cash_balance_map`, this does NOT dispatch by kind: it is
    the cash-flow balance of whatever account the surface points at (the
    calendar's account can be any kind via an explicit ``account_id``).
    The KIND-CORRECT scalar is :func:`~app.services.balance_at.balance_at`.

    **Two dates, deliberately distinct.**  ``ctx.as_of`` is the reader's NOW --
    the floor ruling R-G clamps a still-projected row's landing day up to --
    while *as_of* is the VALUATION date, which may be long past (a historical
    read) or far future (a projection).  A past valuation date now answers with
    the balance the account really held then, replayed from its assertions,
    rather than with today's balance fabricated backwards (finding B-18).

    Args:
        account: The account to value.  Its kind is NOT consulted.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        as_of: The calendar date to value the account at.

    Returns:
        The cent-quantized ``Decimal`` cash-flow balance at *as_of*.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        TypeError: When ``as_of`` is not a civil :class:`datetime.date` -- a
            ``datetime`` INCLUDED (see :func:`_require_civil_date`).
    """
    _require_scenario(ctx)
    _require_civil_date("cash_balance_at", as_of=as_of)
    return _cash_fold.fold_cash_balances(
        account, ctx.amounts(), ctx.as_of, [as_of],
    )[as_of]


def cash_daily_balance_series(
    account: Account,
    ctx: BalanceContext,
    first_day: date,
    last_day: date,
) -> "OrderedDict[date, Decimal]":
    """Return one account's projected end-of-day cash-flow balance per day.

    The daily-granularity cash-flow view -- the same fold as
    :func:`cash_balance_at`, sampled at every day of the range instead of one,
    which is what makes the calendar's running-balance line reconcile with the
    other CASH-basis surfaces at every period end (the grid left that set at
    plan step X-g3b, and the calendar can be pointed at a modelled account by
    explicit ``account_id`` -- finding N-87 records the divergence)::

        series[P.end_date] == cash_balance_at(account, ctx, P.end_date)

    That identity used to be a claim two producers had to keep true (the series
    distributed a period's still-Projected rows over their attribution days
    while the scalar re-walked to the date through a different entry-date
    window, and they measured ``$15.96`` apart on the real Checking account);
    it is now a property of reading one running total twice.

    Like :func:`cash_balance_at` this does NOT dispatch by kind: it is the
    cash-flow balance of whatever account the surface points at (the
    calendar's account can be any kind via an explicit ``account_id``).  Used
    by the analytics calendar's flow strip and day-cell end-of-day balances.

    Args:
        account: The account to project.  Its kind is NOT consulted; must be
            session-attached.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        first_day: Inclusive first calendar day of the range.
        last_day: Inclusive last calendar day of the range.

    Returns:
        An ``OrderedDict`` mapping each calendar ``date`` in the inclusive
        range (ascending) to its projected end-of-day cash-flow balance,
        quantized to cents.  An inverted range yields an empty map.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        TypeError: When ``first_day`` / ``last_day`` are not civil
            :class:`datetime.date` values -- a ``datetime`` INCLUDED (see
            :func:`_require_civil_date`).
    """
    _require_scenario(ctx)
    _require_civil_date(
        "cash_daily_balance_series", first_day=first_day, last_day=last_day,
    )
    if last_day < first_day:
        return OrderedDict()

    days = days_in_range(first_day, last_day)

    folded = _cash_fold.fold_cash_balances(
        account, ctx.amounts(), ctx.as_of, days,
    )
    return OrderedDict((day, folded[day]) for day in days)


def cash_daily_facts_series(
    account: Account,
    ctx: BalanceContext,
    first_day: date,
    last_day: date,
) -> "_cash_fold.CashDaySeries":
    """Return each day's cash-flow balance beside the three tiers that moved it.

    :func:`cash_daily_balance_series` with the day's MOVEMENT split out, for a
    reader comparing the account against a record kept OUTSIDE the app -- the
    bank's own statement (plan step ``bank_import:X-f6e-2``).  Such a reader
    needs the split because a balance difference alone cannot tell the three
    apart, and they mean different things: money the app's rows say moved, a
    balance the owner ASSERTED, and a plan that has not happened.

    **Measured, which is why it exists rather than a difference column**: on
    the developer's own Checking account, over the 149 days his statement and
    his records both cover, 35 days carry a real disagreement between his rows
    and the bank's lines -- and 11 of those read as EXACT agreement in the
    balance difference, because a same-day assertion cancels the error to the
    cent.  One of the eleven is the ``$943.41`` of card paybacks finding
    **N-337** names.

    ``balance`` here is the same figure :func:`cash_daily_balance_series`
    reports for the same day, off the same running total, so a surface may show
    both without qualifying either.

    Args:
        account: The account to project.  Its kind is NOT consulted (see
            :func:`cash_daily_balance_series`); must be session-attached.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        first_day: Inclusive first calendar day of the range.
        last_day: Inclusive last calendar day of the range.

    Returns:
        The :class:`~app.services.balance_at.CashDaySeries`, whose ``facts`` is
        an ``OrderedDict`` mapping each calendar ``date`` in the inclusive
        range (ascending) to its
        :class:`~app.services.balance_at.CashDayFacts`, and whose
        ``first_event_on`` is the day this account's records begin.  An
        inverted range yields an empty map and that same day.

    Raises:
        BaselineMissingError: When ``ctx`` carries no scenario.
        TypeError: When ``first_day`` / ``last_day`` are not civil
            :class:`datetime.date` values -- a ``datetime`` INCLUDED.
    """
    _require_scenario(ctx)
    _require_civil_date(
        "cash_daily_facts_series", first_day=first_day, last_day=last_day,
    )
    days = days_in_range(first_day, last_day)

    series = _cash_fold.fold_cash_day_facts(
        account, ctx.amounts(), ctx.as_of, days,
    )
    # Re-keyed into range order, which a dict comprehension over an unordered
    # sample list does not promise.  An inverted range yields no days and still
    # reports where the records begin -- the account HAS a first event whether
    # or not the caller asked about a day it falls in.
    return _cash_fold.CashDaySeries(
        facts=OrderedDict((day, series.facts[day]) for day in days),
        first_event_on=series.first_event_on,
    )


def records_balance_at(
    account: Account, ctx: BalanceContext, as_of: date,
) -> "Decimal | None":
    """Return what an account's RECORDS produce at *as_of*, before its own
    assertion for that day overrides them.

    The figure ruling **R-EU** asks the true-up form for (plan step X-f2-a):
    *"Shekel has $X"*, where X is what the recorded transactions add up to --
    NOT what the app currently reports, which after a balance is recorded for a
    day IS that recorded balance.

    **The distinction exists because an assertion RESETS the walk**
    (``cash_ledger._walk``: ``running = anchor.anchor_balance``).  So
    :func:`cash_balance_at` on a day that already carries an assertion answers
    with that assertion, and a difference measured against it is zero by
    construction -- or worse, on a CORRECTION it is the gap between the user's
    two successive guesses, which can carry the opposite sign to the real one.
    Measured on production Checking, 2026-04-15, where three balances were
    recorded in one day with no transaction between them: against the previous
    entry the third reads ``-$45.86``; against the records it reads
    ``-$92.29``.

    **It is the walk's OWN field, not arithmetic over the fold.**
    :attr:`~app.services.cash_ledger.CashAnchorCorrection.balance_before` is
    documented as the running balance *just before this assertion resets it*,
    which is exactly this question, already public and already the figure the
    LOAN side's drift card renders (``loan_posting_service._display``:
    ``computed``).  Subtracting correction deltas out of
    :func:`cash_balance_at` instead would be a second statement of the fold's
    step rule -- and a wrong one, because ``_cash_fold._actual_steps`` moves the
    OPENING correction into the seed and books a compensator for it, so the
    opening's delta is not a step to subtract.

    Two branches, and they are one rule rather than a special case:

    * an assertion is dated *as_of* -- take the FIRST one's ``balance_before``.
      First, not last: a later assertion on the same day carries the earlier
      one's reset inside it, so only the first sees the records alone.
    * no assertion is dated *as_of* -- there is nothing to see past, and
      :func:`cash_balance_at` already IS the records' balance.

    **``None`` is a SCOPE statement, not a failure.**  An account whose balance
    carries a MODELLED tier -- an HYSA accruing interest, a brokerage
    compounding, a Property appreciating -- is not answering "does my bank agree
    with my book".  Its recorded cash is a fraction of what its screens show, so
    a reconciliation against it would caption a model-vs-market difference as
    untracked spend.  Production bears the split out: 57 of the 78 non-loan
    assertions are on Checking, the one account carrying no modelled tier.
    Widening this to the modelled kinds is a different question with different
    copy and wants its own ruling (finding **N-213**).

    Args:
        account: The account to value.  Caller owns the ownership check.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        as_of: The civil day to answer for.

    Returns:
        The cent-quantized records-only ``Decimal``, or ``None`` when the
        account models a return and the question does not apply.

    Raises:
        BaselineMissingError: When ``ctx`` carries no scenario.
        TypeError: When *as_of* is not a civil :class:`datetime.date`.
    """
    _require_scenario(ctx)
    _require_civil_date("records_balance_at", as_of=as_of)
    if classify_account(account) is not AccountProjectionKind.PLAIN:
        # The app's OWN name for "no modelled tier rides on this balance"
        # (``account_projection``, whose ladder every seam producer already
        # dispatches on).  Asked through the classifier rather than by
        # re-testing ``has_interest`` / ``has_appreciation`` / an
        # ``InvestmentParams`` row, which would be a fourth statement of a
        # precedence this codebase has already been bitten by stating twice
        # (S6-03).
        return None
    walk = _cash_fold.assemble(account, ctx.amounts(), ctx.as_of).walk
    for correction in walk.anchor_corrections:
        if correction.observed_on == as_of:
            return correction.balance_before
    return cash_balance_at(account, ctx, as_of)


@dataclass(frozen=True)
class CashAnchorRow:
    """One recorded balance, beside what the ledger held just before it.

    The display row of :func:`cash_anchor_history`, and the cash twin of
    :class:`app.services.loan_posting_service.LoanAnchorDrift`.

    **:attr:`ledger` and :attr:`correction` are OPTIONAL, and their absence is
    a statement rather than a gap.**  Both are ``None`` exactly where the pair
    has no agreed meaning, and the two cases are one rule:

    * **the OPENING row.**  Its ``balance_before`` is the sum of settled rows
      dated BEFORE the account's first assertion, replayed from a zero seed --
      which :func:`app.services.cash_ledger.dated_deltas` documents as "not a
      balance the account ever had", and what a reader should answer there is
      OPEN finding **N-37**.  On the real Checking account it reads
      ``$2,057.42`` against an opening of ``$2,746.58``, and the ``$689.16``
      between them is the account's opening EQUITY (the figure plan step X-f5
      books), not a correction -- so publishing it would say "the records were
      off by $689.16 the day the account opened", which is false.  The loan
      twin renders the same two cells ``--`` for the same reason, one tier
      down: a loan opens from nothing.
    * **an account whose balance carries a MODELLED tier.**  The walk sees
      recorded CASH only; an HYSA's accrued interest and a brokerage's growth
      never were transactions, so the figure would name a model-vs-market gap
      as untracked spend.  Measured on production: the HYSA's one row would
      read ``$4,863.56`` against a ``$5,363.56`` balance (91 percent of it),
      and the Money Market's two true-ups ``$15.01`` / ``$15.24``, which are
      interest.  This is the scope :func:`records_balance_at` already states
      for the X-f2-a preview, and widening it is finding **N-213**.

    Attributes:
        observed_on: The civil day this balance was declared TRUE for
            (``AccountAnchorHistory.observed_on``) -- the day the whole walk
            partitions on.
        recorded_at: The RECORDING instant, aware-UTC
            (:attr:`~app.services.cash_ledger.CashAnchorFact.asserted_at`
            unchanged).  It is what a reader RANKS by, and
            :attr:`recorded_on` is what a reader SHOWS -- two jobs a single
            field cannot do, which a first build of this row got wrong.  A day
            has no resolution to rank with: every assertion typed in one
            sitting shares one recording DAY, so "the most recently recorded
            twelve" degenerated to "the twelve newest ``observed_on``" and
            buried the back-dated row the pair exists to surface.  That is not
            a rare tie -- it is what a bookkeeping session looks like.
        recorded_on: The civil day this assertion was ENTERED, in the user's
            zone.  **A stored fact carried through from
            :attr:`~app.services.cash_ledger.CashAnchorFact.recorded_on`, not
            derived from :attr:`recorded_at`** (finding **N-299**, developer
            ruling 2026-08-25).

            It exists so a BACK-DATED row identifies itself.  Sorted by
            :attr:`observed_on` a balance recorded today for a past day lands
            at that past day's position -- 40 rows down on the real Checking
            account -- so ordering alone does not give a back-dated write the
            retrieval path finding **N-205** is about.  It equals
            :attr:`observed_on` for the ordinary same-day true-up, which is why
            the card shows the caption only when the two differ.

            **Splitting rank from show was right and stopped one step short.**
            Both halves were still computed from ``created_at``, which
            PostgreSQL stamps (``server_default=db.func.now()``), while
            :attr:`observed_on` comes from the application's
            ``display_today()``.  So the caption gated on
            ``recorded_on != observed_on`` compared two clocks: red on every
            calendar-sweep matrix date since 2026-08-10, because
            ``time_machine`` fakes one of them and cannot fake the other, and
            wrong in production for a true-up submitted in a civil day's last
            second.  The shown value now has its own application-written
            column and :attr:`recorded_at` keeps the ranking job alone.
        recorded: The balance the user asserted, cent-quantized.  A fact for
            every account kind, which is why it is never ``None``.
        ledger: What the running balance held immediately before this assertion
            RESET it
            (:attr:`~app.services.cash_ledger.CashAnchorCorrection.balance_before`),
            or ``None`` in the two cases above.
        correction: ``recorded - ledger``
            (:attr:`~app.services.cash_ledger.CashAnchorCorrection.delta`) --
            the jump THIS assertion booked, or ``None`` alongside
            :attr:`ledger`.

            **It is deliberately NOT the difference the true-up form previews,
            and the name is what keeps them apart.**  On a day carrying more
            than one assertion the two are different numbers for one row:
            :func:`records_balance_at` answers what the RECORDS produced for
            the day, taking the FIRST assertion's ``balance_before``, while
            this is measured against whatever the running balance held just
            before -- which for a second or third entry is the previous ENTRY.
            Measured on production Checking 2026-04-15, where three balances
            were recorded with no transaction between them: the third's
            correction is ``-$45.86`` and the preview's difference for the same
            row is ``-$92.29``.  Both are right about their own question, and
            one word covering both is the defect: a review of this step found
            the card and the form publishing those two figures under the label
            "Difference" on one screen.

            This is the LEDGER's arithmetic and the row shows its whole
            working: ``recorded - ledger`` reconciles on the row, and a day's
            corrections telescope to the day's total (``-7.46 + -38.97 +
            -45.86 = -92.29``, the gap the preview names).
        is_opening: ``True`` for the account's first assertion.
    """

    observed_on: date
    recorded_at: datetime
    recorded_on: date
    recorded: Decimal
    ledger: "Decimal | None"
    correction: "Decimal | None"
    is_opening: bool


@dataclass(frozen=True)
class CashAnchorHistory:
    """An account's balance-assertion log, as the history card renders it.

    Attributes:
        rows: One :class:`CashAnchorRow` per assertion the account carries,
            NEWEST first.  The producer's order is the reverse of the walk's,
            which is the order the card reads in; it is not the order the
            figures were computed in, and it cannot change them (each row's
            ``ledger`` is the running total of the events BEFORE it).
        reconcilable: ``True`` when this account's balance is recorded cash
            alone, so the :attr:`CashAnchorRow.ledger` /
            :attr:`~CashAnchorRow.correction` pair means something and the card
            shows those columns.

            **It cannot be derived from the rows, and that is a trap worth
            naming.**  ``any(row.correction is not None)`` looks equivalent and
            is wrong for a PLAIN account carrying exactly ONE assertion: its
            only row is the opening, whose pair is ``None`` for the ruled
            reason above, and the derived flag would then hide the columns on
            an account that reconciles perfectly well.  It is read from
            :func:`~app.services.account_projection.classify_account`, stated
            once, here.
    """

    rows: "list[CashAnchorRow]"
    reconcilable: bool


def cash_anchor_history(
    account: Account, ctx: BalanceContext,
) -> CashAnchorHistory:
    """Return every balance an account has been told it held, newest first.

    Ruling **R-EV** (plan step X-f2-b): the DURABLE record of a balance
    assertion, which the app did not have.  Until this entry the only evidence
    that a back-dated assertion landed was an 8-second toast, and an AST pass
    over :class:`~app.models.account.AccountAnchorHistory` found no read path
    into any template except the GOVERNING assertion's "as of" caption -- which
    by definition is not the back-dated row (finding **N-205**).

    **It needs no new producer and adds no new rule.**  Every column is a field
    the walk already publishes: :class:`~app.services.cash_ledger.CashAnchorCorrection`
    carries ``observed_on``, its anchor's ``anchor_balance`` and ``asserted_at``,
    ``balance_before`` and ``delta``.  Re-deriving any of them here would be a
    second statement of the walk's own arithmetic, which is the shape plan step
    X-f2-a corrected in the difference preview.

    **It takes no valuation date, and that is exact rather than an omission.**
    The loan twin
    (:func:`app.services.loan_posting_service.loan_balance_anchor_history`)
    filters its anchors to ``anchor_date <= as_of`` because a loan anchor can
    be dated ahead of a display date.  A cash assertion cannot:
    :func:`app.services.anchor_service.resolve_observation_day` refuses a day
    in the future at BOTH write doors, so every assertion has already happened
    and a bound would be a no-op that a later reader would have to re-justify.
    ``ctx`` is still required -- it scopes the settled rows the ``ledger``
    column folds -- but its ``as_of`` decides nothing here.

    Reads only -- no writes, no commit.

    Args:
        account: The account whose assertion log to read.  Caller owns the
            ownership check.  Its KIND is consulted once, for
            :attr:`CashAnchorHistory.reconcilable`.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Its scenario scopes the settled rows the walk folds; assertions
            themselves are per-ACCOUNT and replay in every scenario.

    Returns:
        The :class:`CashAnchorHistory`.  Its ``rows`` are empty only for an
        account carrying no assertion at all -- production-unreachable
        (``account_service.create_account`` and migration ``cfb15e782f86``
        guarantee an opening row), and answered rather than raised because a
        log of nothing is honestly empty.

    Raises:
        BaselineMissingError: When ``ctx`` carries no scenario.
    """
    _require_scenario(ctx)
    reconcilable = classify_account(account) is AccountProjectionKind.PLAIN
    # Through ``assemble`` rather than ``walk_cash_ledger`` directly, which is
    # the spelling :func:`records_balance_at` one function up already uses.
    # Two spellings of one dependency inside one module is how a later
    # request-scoped memo on ``assemble`` would collapse ONE of the cash
    # detail page's two walks and quietly leave the other -- and that memo is
    # plan step X-i1, the step this page's double walk is waiting on.
    walk = _cash_fold.assemble(account, ctx.amounts(), ctx.as_of).walk
    # ``booked`` rather than ``correction``: the walk's record and the row's
    # field would otherwise share a name inside one expression, and
    # ``correction=correction.delta`` reads as a self-reference.
    rows = [
        CashAnchorRow(
            observed_on=booked.observed_on,
            recorded_at=booked.anchor.asserted_at,
            recorded_on=booked.anchor.recorded_on,
            recorded=booked.anchor.anchor_balance,
            # One condition, both cases: the pair is published only where it
            # has an agreed meaning (see :class:`CashAnchorRow`).
            ledger=(
                booked.balance_before
                if reconcilable and not booked.anchor.is_opening
                else None
            ),
            correction=(
                booked.delta
                if reconcilable and not booked.anchor.is_opening
                else None
            ),
            is_opening=booked.anchor.is_opening,
        )
        for booked in reversed(walk.anchor_corrections)
    ]
    return CashAnchorHistory(rows=rows, reconcilable=reconcilable)
