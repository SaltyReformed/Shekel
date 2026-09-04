"""
Shekel Budget App -- Investment Projection Input Calculator

Pure function that computes all inputs needed for growth_engine.project_balance()
from raw deduction, contribution, and investment params data.

Used by both the investment detail route and the savings dashboard to avoid
duplicating contribution/employer/YTD calculation logic.

Contributions are derived from shadow income transactions (transfer_id IS NOT
NULL) in the investment/retirement account.  The caller queries these
transactions and passes them in; this module has no database access.

**They arrive PRICED, as :class:`PricedContribution` records rather than ORM
rows** (plan step X-au-c2, a developer ruling of 2026-08-12).  Four readers here
used to ask each row for its ``effective_amount`` and screen it with
``status_contributes_to_balance`` -- a model property that cannot answer for a
row whose amount is DERIVED, since such a row stores no figure and resolving one
needs a database this module deliberately does not have.  Valuing at the
BOUNDARY instead (``projection_inputs.load_shadow_income_contributions_*``)
resolves the whole row set ONCE, drops the rows that contribute nothing, and
retires all four copies of the status screen with them.  What is left here is
arithmetic over plain data, which is what the paragraph above always claimed.

**They arrive DATED too, since plan step C2-f2c**, and for the same reason one
tier down.  A contribution's pay period was carried here as an id, so the three
readers that needed to know WHEN it landed took the owner's whole period list
as an argument and looked the payday up in it -- a join table threaded through
a public signature to answer a question the loader can answer once, where the
session is.  ``calculate_investment_inputs`` and
:func:`build_contribution_timeline` are the readers; neither takes a period id
now, and the period list left the first of them outright.  It also ended a
shape collision this module could not have absorbed otherwise: it is shared by
``/retirement``, which holds ORM rows spelling that key ``id``, and by
``/investment``, which since C2-f2c holds
:class:`~app.services.pay_calendar.DerivedPeriod`\\ s spelling it ``period_id``.

**And the DEDUCTION half arrives priced and dated since plan step
salary:R14-b**, which is the same move a third time and the one that finishes
it (ruling **R-SAL2**).  This module used to be handed the deduction ROWS,
flattened by an ``adapt_deductions`` adapter into ``(amount, calc_method_id,
annual_salary, periods_per_year, annual_cap)``, and work out what each took
from a paycheck itself -- dividing the profile's STORED annual salary by the
paycheck count.  That was one question answered twice, and the second answer
was worse on three independent axes at once: it was blind to every raise
(finding **D45**; ``$1,646.84`` is that row's own figure for a hypothetically
LINKED deduction over the developer's 63 saved paydays, where what this step
moves on his data AS IT STANDS is ``+$452.42`` of employer money through
``balance_at.grid_balance_view`` -- two windows and two feeds, so neither
figure substitutes for the other), blind to a deduction's inflation escalation
(**N-532**, which
``AdaptedDeduction`` could not even carry), and it spelled the calendar-year
cap twice more beside the engine's -- ``_annual_cap_averaged`` evenly and
``_period_capped_total`` front-loaded.  All four spellings are deleted here.
The engine's :class:`~app.services.paycheck_calculator.DeductionLine` already
carries ``target_account_id``, so what one account's payroll puts in on one
payday is a fold of the breakdown the engine already computed, and the
:class:`AccountPayrollFeed` the loader hands over is that fold.

The root cause behind all three divergences was ONE shape: an adapter that
flattens away everything varying PER PERIOD cannot answer a per-period
question, however many of its answers are patched.  That is why the remedy is
a deletion rather than a fourth fix.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.services.growth_engine import ContributionRecord
from app.utils.money import ZERO, round_money


@dataclass(frozen=True)
class PricedContribution:
    """ONE shadow contribution, already valued, screened and DATED.

    The boundary record this module consumes in place of a
    :class:`~app.models.transaction.Transaction` (plan step X-au-c2).  It
    carries exactly the four facts the readers here need, so a row's amount is
    resolved once by the loader that has a session rather than four times by
    functions that do not.

    **Non-contributing rows are ABSENT rather than zero, and that is load
    bearing rather than tidy.**  :func:`_average_transfer_contribution` divides
    by the number of DISTINCT paydays it sees, so a Cancelled contribution
    carried as ``$0.00`` would enlarge that denominator and quietly reduce the
    average -- where the ``status_contributes_to_balance`` screen it replaces
    dropped the row before the count.  The loader applies that screen and omits
    what fails it.

    **It carries the PAYDAY rather than the ``pay_period_id`` since plan step
    C2-f2c**, and that is what let the period LIST leave this module's public
    surface.  Every reader here bucketed on the id and then needed a lookup
    table to find out WHEN that period was: the YTD windows built a set of ids
    filtered by each period's ``start_date``, and the timeline built an
    id-keyed map to stamp each record with one.  So the period list was an
    argument three functions took to answer one question the loader can answer
    once, where the session is -- the same move plan step X-au-c2 made for the
    amount and the status.  It also ended a shape collision: this module is
    shared with ``/retirement``, which holds ORM rows spelling that key ``id``,
    while ``/investment`` now holds
    :class:`~app.services.pay_calendar.DerivedPeriod`\\ s spelling it
    ``period_id``.

    A payday identifies a period as exactly as the id does: paydays are unique
    per owner (``uq_pay_periods_user_start``) and every batch the loader builds
    is scoped to one.

    Attributes:
        account_id: The investment / retirement account the contribution
            landed in.  Read by the cross-account consumers, which load one
            batch and partition it per account.
        payday: The ``start_date`` of the pay period the contribution belongs
            to -- the day every average, YTD sum and timeline record here dates
            it at.  It is a PAYDAY rather than a posting date on purpose: the
            growth engine matches a contribution to a period by that period's
            opening day.
        amount: What the row CONTRIBUTES
            (:func:`app.services.cash_ledger.contributions_by_id`): the entered
            ``actual_amount`` where a human read one off a statement, else the
            row's resolved amount.
        is_confirmed: Whether the contribution actually happened
            (``status.is_settled``), as opposed to being still projected.  The
            growth engine's :class:`~app.services.growth_engine.ContributionRecord`
            takes it verbatim.
    """

    account_id: int
    payday: date
    amount: Decimal
    is_confirmed: bool


@dataclass(frozen=True)
class ShadowContributions:
    """A batch of priced contributions, and WHICH accounts had any at all.

    Two facts that must travel together, because screening the first destroys
    the second and one consumer needs each (plan step X-au-c2).

    **The second field exists because an adversarial review caught the screen
    silently answering a different question.**  ``retirement_projection``'s
    ``none_linked`` is a PRESENCE test -- *is anything linked to fund this
    account?* -- and it read the loader's list length.  Before the screen moved
    to the boundary that list carried Cancelled and Credit rows, so an account
    whose contributions were all cancelled reported ``you $0.00 / employer
    $0.00``; screening them out at the loader would have flipped it to the
    "nothing linked yet" call-to-action, telling the owner to link a
    contribution that already exists.

    Separating them is the correct design rather than a compatibility shim:
    *what does this account receive* and *is anything wired up to it* are
    different questions, and conflating them is exactly what let one change
    answer the second while only meaning to change the first.

    Attributes:
        records: The contributions that COUNT -- screened by
            ``status_contributes_to_balance`` and priced through the amount
            model.  Cancelled and Credit rows are absent (see
            :class:`PricedContribution` on why absent rather than zero).
        linked_account_ids: Every account id that had a contribution shadow in
            the window, WHATEVER its status.  A cancelled contribution is still
            a link.
    """

    records: list[PricedContribution]
    linked_account_ids: frozenset[int]


@dataclass
class InvestmentInputs:
    """All inputs needed for growth_engine.project_balance().

    ``ytd_contributions`` and ``ytd_contributions_seed`` are two YTD views
    of the same contribution stream that differ only on the current period
    (deep-quality-hunt #10):

    * ``ytd_contributions`` -- contributions this calendar year *through*
      the current period (``<=``).  This is the displayed limit-card value.
    * ``ytd_contributions_seed`` -- contributions this calendar year
      *strictly before* the current period (``<``).  This is the
      ``ytd_contributions_start`` handed to the growth engine, whose own
      per-period walk then applies and counts the current period's
      contribution against the limit.  Seeding the through-current value
      instead would charge the current period against the annual limit
      twice.  The two views converge at the engine's current-period row.

    ``periodic_contribution`` is the CURRENT period's employee amount rather
    than a figure held for all time (plan step **salary:R14-b**).  It was
    ``annual_salary / <paycheck count>`` -- one number for every period the
    projection reached, which is finding **D45** -- and the forward walk now
    reads a dated record per period (:func:`build_contribution_timeline`), so
    what is left for this field to answer is the per-period CARD the
    investment and retirement dashboards render.  ``gross_biweekly`` left with
    the same cutover: a gross is a fact about a PAYDAY, so it lives on the
    :class:`AccountPayrollFeed` keyed by one, and no consumer read this copy.
    """
    periodic_contribution: Decimal
    employer_params: Optional[dict]
    annual_contribution_limit: Optional[Decimal]
    ytd_contributions: Decimal
    ytd_contributions_seed: Decimal


@dataclass(frozen=True)
class AccountPayrollFeed:
    """What ONE account's payroll puts in, priced per PAYDAY by the engine.

    The boundary record this module consumes in place of the deduction rows
    and the ``AdaptedDeduction`` adapter that flattened them (plan step
    **salary:R14-b**, ruling **R-SAL2**).  Both series are folds of the
    :class:`~app.services.paycheck_calculator.PaycheckBreakdown`\\ s
    :func:`~app.services.income_service.project_profile` already produces, so
    what a deduction takes from a paycheck is answered ONCE, by the engine
    that computes the paycheck.

    **Both maps are TOTAL over the paydays the calendar reaches**, and that is
    load bearing rather than tidy.  A 24-per-year deduction is not taken on
    its month's third payday, so that payday's entry is an explicit ``$0.00``;
    were it merely absent, :meth:`employee_at` could not tell it from a payday
    beyond the calendar and would hold the previous amount over a skip.  It is
    the same rule :class:`~app.services.growth_engine.ContributionRecord`
    states for its own zero entries.

    **Past the last payday the calendar reaches, both series HOLD** -- the
    interim rule the developer ruled on 2026-09-04, shipped with its successor
    already named.  The engine can only price a paycheck against a salary
    path, and the app has TWO that disagree past the owner's schedule:
    ``salary_raises.apply_raises`` compounds every recurring raise forever,
    while ``pension_calculator.project_salaries_by_year`` stops merit and
    custom raises at ``auth.user_settings.merit_raise_horizon_years`` and
    keeps only recurring COLA.  Unifying them is its own step and its own
    ruling; until it lands, a projection past the schedule states the last
    real paycheck rather than picking one of the two models inside a step that
    was ruled about something else.  The hold is a CLAIM the consumer surfaces
    make, not an arithmetic accident, and it is why the two hold sources
    differ, and each attribute below states its own.

    Attributes:
        employee_by_payday: payday -> what this account received from payroll
            on it.  **Past the saved calendar it HOLDS, and the hold is an
            extrapolation, not a price** -- :meth:`_year_averages` is its one
            producer and states the rule.  In-window it is the sum of every
            :class:`~app.services.paycheck_calculator.DeductionLine` naming
            this account across every profile that funds it, pre- and
            post-tax alike.  Already raise-aware, inflation-escalated,
            cadence-placed and clamped to each line's own calendar-year
            ``annual_cap``, because the engine applied all four before this
            fold saw the line.
        gross_by_payday: payday -> the gross of the paycheck the account's
            FUNDING profile was paid on it.  **HOLDS at the nearest priced
            payday in each direction**: a gross is never skipped, so each
            end's own entry is the last real answer that way.  In-window it is
            the engine's own figure for
            (``budget.investment_params.salary_profile_id``, ruling
            **R-SAL5**).  **EMPTY when no funding profile is known**, which is
            the developer's 2026-09-04 ruling: an employer contribution whose
            funding job is unrecorded models NO money and the surface says so,
            rather than being priced off whichever profile a reader happened
            to resolve.  An ARCHIVED profile is unknown for this purpose too
            -- an employer contribution from a job the owner has left is not
            money they receive -- which is the same test the deduction loader
            already applies to its own side.
        periods_per_year: How many paychecks the owner receives in a year,
            off their :class:`~app.services.pay_calendar.PayCadence`.  Read by
            the employee series' hold rule ALONE, and it is a field rather
            than a count taken off the map because **the two are not the same
            number**: the map holds the paydays inside the SAVED WINDOW, which
            equals the year's real payday count only when the window contains
            that whole year.  An adversarial review measured the difference --
            dividing a ``$1,000``-capped deduction's year by an observed 13
            held it at ``$76.92`` a payday, ``$1,999.92`` a year, **2.00x its
            own cap**.  The deleted ``_annual_cap_averaged`` divided by this
            same structural figure; replacing it with an empirical one is what
            re-opened the defect it closed.  It is also what decides whether a
            calendar year is COMPLETE, the test that picks the hold's branch.
            ``None`` only for a feed that prices nothing.
        is_payroll_linked: Whether an active deduction on an active profile
            NAMES this account, whatever it pays.  A PRESENCE fact, and it
            has to travel beside the amounts because pricing destroys it: a
            deduction of ``$0.00`` still means the owner has wired one up, so
            ``/retirement``'s "nothing linked yet" prompt would tell them to
            create what already exists.  It is the same pair, one tier over,
            that :class:`ShadowContributions` keeps for the recorded feed --
            and that pairing exists because an adversarial review caught
            exactly this question being answered by a screened list.
    """

    employee_by_payday: "Mapping[date, Decimal]"
    gross_by_payday: "Mapping[date, Decimal]"
    is_payroll_linked: bool = False
    periods_per_year: "Decimal | None" = None
    #: The two HELD figures, derived at construction so the hold rule has one
    #: producer and no caller can supply a figure inconsistent with the maps
    #: (the shape ``PayCalendar.periods`` uses for the same reason).  Excluded
    #: from equality: two feeds carrying the same paydays hold the same way.
    #: The held figures, one per DIRECTION.  A payday outside the calendar is
    #: either before its first or after its last, and the two are different
    #: questions: holding the LATEST paycheck backward over a payday that
    #: predates the owner's schedule would answer with a salary they had not
    #: yet been raised to.  No consumer asks the backward question today --
    #: every domain here opens at or after the calendar's first payday -- but
    #: an answer that is wrong only because nobody asks it is one this file
    #: has been burned by before, so the direction is in the value.
    _held_employee: "tuple[Decimal, Decimal]" = field(
        init=False, repr=False, compare=False,
    )
    _held_gross: "tuple[Decimal, Decimal] | None" = field(
        init=False, repr=False, compare=False,
    )
    #: The calendar's FIRST payday, which is the boundary the direction is
    #: decided against.  Derived once rather than re-scanned per lookup: the
    #: 40-year chart asks about ~980 paydays past the calendar, and a ``min()``
    #: over the map inside each of them is a scan the value already knows the
    #: answer to.
    _first_payday: "date | None" = field(
        init=False, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        """Derive the ``(earliest, latest)`` held figure for each series."""
        # The GROSS holds at the first and last paydays priced, full stop: a
        # paycheck's gross is never skipped, so each end's own entry is the
        # last real answer in that direction.
        object.__setattr__(
            self, "_held_gross",
            (self.gross_by_payday[min(self.gross_by_payday)],
             self.gross_by_payday[max(self.gross_by_payday)])
            if self.gross_by_payday else None,
        )
        # The EMPLOYEE amount holds at a whole priced YEAR's average per
        # payday, NOT at any single payday's figure, and the reason is the
        # ANNUAL CAP.
        #
        # **An adversarial review of this step's own fix found the single-day
        # rule wrong by 10.4x.**  A capped deduction's priced year runs
        # ``$600, $400, $0, $0 ... $0`` -- ``cap_period_amount`` clamps it the
        # moment the calendar-year total reaches ``annual_cap``, and the
        # engine resets it each January -- so holding "the last payday that
        # PAID something" picks the ``$400`` and applies it forever with no
        # cap and no year reset.  ``_annual_cap_averaged``, which this step
        # deletes, existed for exactly that and its docstring said so.
        #
        # A year's average reproduces that answer from the ENGINE's own priced
        # amounts rather than from a second formula: the year's total is the
        # cap (or the uncapped run rate), so the average is
        # ``min(amount x ppy, cap) / ppy`` without this module dividing
        # anything.  It also subsumes the cadence case the single-day rule was
        # reaching for -- a 24-per-year deduction's skipped paydays are inside
        # the year being averaged, so a schedule that happens to END on a skip
        # no longer holds ``$0.00`` for the whole projection.
        #
        # Only a COMPLETE year is averaged: the saved window's first and last
        # calendar years are usually partial (the developer's runs 2026-03 to
        # 2028-08), and a partial year's average overstates a capped
        # deduction by the fraction of the year missing.
        object.__setattr__(
            self, "_held_employee", self._year_averages(),
        )
        # Either map's keys serve: both are built over the SAME payday set
        # (the calendar's saved window), and each is empty only when its own
        # half of the feed is absent entirely.
        keys = self.gross_by_payday or self.employee_by_payday
        object.__setattr__(
            self, "_first_payday", min(keys) if keys else None,
        )

    def _year_averages(self) -> "tuple[Decimal, Decimal]":
        """Return the ``(earliest, latest)`` figure the employee series holds at.

        A COMPLETE calendar year's total over the owner's paydays-per-year,
        and the last priced payday where the saved window contains no
        complete year.

        **The calendar year is the span because ``annual_cap`` is**, and the
        engine resets the clamp on it.  A year's total divided by the year's
        paydays IS ``min(amount x ppy, cap) / ppy`` -- what the deleted
        ``_annual_cap_averaged`` returned -- derived from the engine's own
        priced figures rather than from a second formula, and right for a
        capped and an uncapped deduction alike.

        **A year must be COMPLETE, and a window with none falls back to the
        last PRICED payday.**  A sub-year window has thrown the cap away -- a
        ``$500``-a-payday deduction and a ``$1,000``-capped one price
        IDENTICALLY for their first two paydays -- so no rule can be right
        for both from the fold alone, and three tried here were each measured
        wrong on exactly that shape (``docs/plans/lessons.md``, the R14-b
        entry, holds the three and their figures).  The last priced payday is
        exact for an uncapped deduction, which is every live one on the
        developer's data, and reads a capped one's trailing CLAMPED figure,
        which understates; it over-reads only where the window is shorter
        than the cap takes to bind, an owner days into their schedule.

        The exposure is narrow either way: the app's default schedule is 52
        periods, so every owner past their first year has a complete year and
        the exact figure.  What would make it exact for everyone is the
        ENGINE pricing the tail rather than this extrapolating it, which is
        the deferred salary-path step and not this one's to decide.

        Returns:
            ``(earliest, latest)`` -- the per-payday average of the earliest
            and latest COMPLETE calendar years; the first and last priced
            paydays where no year is complete; ``(ZERO, ZERO)`` when nothing
            was priced.  The two coincide for
            any account whose feed is flat, which is every uncapped one.
        """
        if not self.employee_by_payday:
            return (ZERO, ZERO)
        if self.periods_per_year is None:
            raise ValueError(
                "an AccountPayrollFeed that prices paydays must carry the "
                "owner's periods_per_year: the employee hold is a YEAR's "
                "average and the divisor is the year's real payday count, "
                "which the priced map cannot supply for a window that does "
                "not contain a whole year"
            )
        by_year: "dict[int, list[Decimal]]" = {}
        for payday, amount in self.employee_by_payday.items():
            by_year.setdefault(payday.year, []).append(amount)
        complete = sorted(
            year for year, amounts in by_year.items()
            if len(amounts) >= self.periods_per_year
        )
        if not complete:
            # No complete year, so no annual total to divide.  The LAST
            # PRICED payday is the fallback: exact for an uncapped deduction
            # (its rate is the same every payday), and for a capped one it
            # reads the trailing clamped figure, which understates rather
            # than over -- except in a window so short the cap has not bound
            # yet, where it reads the unclamped rate.  That residue is stated
            # on the class and is what the deferred salary-path step removes
            # by pricing the tail instead of extrapolating it.
            last = self.employee_by_payday[max(self.employee_by_payday)]
            first = self.employee_by_payday[min(self.employee_by_payday)]
            return (first, last)
        return (
            round_money(
                sum(by_year[complete[0]], ZERO) / self.periods_per_year),
            round_money(
                sum(by_year[complete[-1]], ZERO) / self.periods_per_year),
        )

    def _held(self, held, payday: date):
        """Return the figure *held* over *payday*, in the right direction.

        Args:
            held: The ``(earliest, latest)`` pair for one series, or ``None``.
            payday: A payday the calendar does not reach.

        Returns:
            The earliest figure for a payday before the calendar's first, the
            latest for one after its last, and ``None`` when the series is
            empty.
        """
        if held is None:
            return None
        if self._first_payday is not None and payday < self._first_payday:
            return held[0]
        return held[1]

    @classmethod
    def absent(cls) -> "AccountPayrollFeed":
        """Return the feed of an account no payroll funds.

        The explicit token for "this account has no payroll feed", so a caller
        that means it says so and no reader has to decide whether two empty
        maps were meant or forgotten.  It models no employee contribution and
        no employer contribution, which are different facts that happen to
        share this value.

        Returns:
            The empty :class:`AccountPayrollFeed`.
        """
        return cls({}, {})

    @property
    def models_employee(self) -> bool:
        """Whether payroll pays this account anything on any priced payday.

        The gate the balance seam's plan asks before modelling an employee
        feed at all (:func:`~app.services.balance_at._asset_contributions
        ._plan_for`), so an account whose deductions all price at ``$0.00``
        models nothing rather than a series of zeros.  The contribution
        TIMELINE asks a different question and gates on
        :attr:`is_payroll_linked`; see that field.

        **It reads the priced MAP, not the held figures, and an adversarial
        review measured why.**  It was ``self._held_employee != (ZERO, ZERO)``
        back when the hold was "the last payday that paid something", for
        which the two agreed closely enough to look equivalent.  The hold is a
        fullest-CALENDAR-YEAR average now, and that is a different predicate:
        a window whose fullest year happens to be all zeros holds ``$0.00`` in
        both directions while an adjacent year paid.  Measured on a 26-payday
        window split 14 / 12 across a year boundary with a ``$1,000``-capped
        deduction paying entirely in the SHORTER year -- ``$1,000.00`` of real
        in-window deductions, and this gate said the account modelled nothing,
        which drops the whole figure out of every balance the seam produces.

        Returns:
            ``True`` when at least one priced payday paid this account.
        """
        return any(
            amount > ZERO for amount in self.employee_by_payday.values()
        )

    @property
    def funds_employer(self) -> bool:
        """Whether a known funding profile can size an employer contribution.

        ``False`` when ``budget.investment_params.salary_profile_id`` is unset
        or names an archived profile -- the state in which the developer's
        2026-09-04 ruling models no employer money and the surface says the
        funding job is not set.  It is a PRESENCE test about the profile link
        and never about the dollars: a profile paid ``$0.00`` still funds.

        Returns:
            ``True`` when a funding profile's paychecks were priced.
        """
        return bool(self.gross_by_payday)

    def prices(self, payday: date) -> bool:
        """Whether *payday* is one the owner's calendar actually reached.

        The boundary between an answer and an extrapolation, asked rather
        than inferred: :meth:`employee_at` and :meth:`gross_at` are TOTAL, so
        a caller cannot tell a priced payday from a held one by their return
        values, and one caller must (see
        :func:`build_contribution_timeline`'s path 1).

        Args:
            payday: The pay period's ``start_date``.

        Returns:
            ``True`` when the engine priced this payday, ``False`` when the
            answer for it is held.
        """
        return (
            payday in self.employee_by_payday
            or payday in self.gross_by_payday
        )

    def employee_at(self, payday: date) -> Decimal:
        """Return what this account received from payroll on *payday*.

        Args:
            payday: The pay period's ``start_date``.

        Returns:
            The engine's own figure for a payday the calendar reaches --
            ``$0.00`` included, where a cadence skipped the deduction -- and
            the held amount past it (see the class docstring).
        """
        amount = self.employee_by_payday.get(payday)
        return self._held(self._held_employee, payday) if amount is None else amount

    def gross_at(self, payday: date) -> "Decimal | None":
        """Return the funding profile's gross for the paycheck on *payday*.

        Args:
            payday: The pay period's ``start_date``.

        Returns:
            The engine's own gross for a payday the calendar reaches, the held
            gross past it, and ``None`` when no funding profile is known --
            the refusal :attr:`funds_employer` names, kept as ``None`` rather
            than ``$0.00`` so a caller cannot spend it as a basis.
        """
        gross = self.gross_by_payday.get(payday)
        return self._held(self._held_gross, payday) if gross is None else gross

    def salary_basis(self, beyond=None):
        """Return the ``period -> gross`` resolver the growth engine takes.

        :func:`~app.services.growth_engine.project_balance`'s ``salary_basis``
        hook, so the employer contribution is sized off each projected
        period's OWN paycheck.  Built here rather than spelled as a lambda at
        each call site, because three sites writing ``lambda p:
        feed.gross_at(p.start_date)`` are three places for the key to move.

        It reads ``start_date`` and nothing else, which is what lets both
        period types serve -- an ORM
        :class:`~app.models.pay_period.PayPeriod` and a
        :class:`~app.services.pay_calendar.DerivedPeriod` -- the same
        discipline :func:`build_contribution_timeline` keeps for its own
        domain.

        Args:
            beyond: An optional ``period -> Decimal`` model for a payday the
                owner's calendar does not reach, which REPLACES the hold rule
                there.  ``/retirement`` supplies one -- the merit-horizon
                salary path ``retirement_projection.build_employer_salary_basis``
                already projected its employer base on -- so that page keeps
                the long-horizon model it had rather than being flattened to
                a held paycheck by a step ruled about something else.  It is
                consulted ONLY where a funding profile is known: an unknown
                one models no employer money at all (developer, 2026-09-04),
                and an outer model must not resurrect what that ruling
                withholds.

        Returns:
            A callable taking a period and returning its gross, or ``None``
            where no funding profile is known.
        """
        def _resolver(period):
            gross = self.gross_by_payday.get(period.start_date)
            if gross is not None:
                return gross
            held = self._held(self._held_gross, period.start_date)
            if held is None or beyond is None:
                return held
            return beyond(period)

        return _resolver




def _average_transfer_contribution(all_contributions):
    """Average per-period contribution from priced shadow contributions.

    ``all_contributions`` are :class:`PricedContribution` records already
    filtered to one account by the caller.  Cancelled / Credit rows never reach
    here: the loader that priced them applied the
    ``status_contributes_to_balance`` screen and omitted what failed it, so this
    module holds no copy of that rule (plan step X-au-c2).  The screen still
    shares ONE definition with the SQL filters in ``year_end_summary_service`` /
    ``savings_dashboard_service`` -- it just lives at the boundary now.

    Contributions are summed on the record's :attr:`~PricedContribution.amount`
    -- the realized actual when a shadow is settled, else what the row's amount
    RESOLVES to -- which is the same figure the per-period timeline reads off
    the same records, so this average and the YTD/limit accounting cannot
    disagree with the engine on a settled transfer whose actual differs from its
    estimate (deep-quality-hunt #11).

    Args:
        all_contributions: List of :class:`PricedContribution` records.

    Returns:
        The per-period average contribution (Decimal), or ZERO when no
        contributions exist.
    """
    if not all_contributions:
        return ZERO

    total_contrib = sum(c.amount for c in all_contributions)
    # DISTINCT PAYDAYS, which is distinct periods: a payday is unique per owner
    # (``uq_pay_periods_user_start``) and one batch is one owner's.  It read
    # ``pay_period_id`` until plan step C2-f2c moved the period key onto the
    # record's own date; the denominator is the same set either way.
    num_periods_with_contrib = len(
        set(c.payday for c in all_contributions)
    )
    if num_periods_with_contrib > 0:
        return round_money(total_contrib / num_periods_with_contrib)
    return ZERO


def employer_contribution_params(investment_params) -> "dict | None":
    """Build the employer-contribution params dict, or None.

    Public because the balance seam's modelled asset fold
    (``balance_at._asset_fold``) sizes the employer amount per pay period
    off the RESOLVED employee total for that period (plan ruling R-R
    consequence (a)), so it needs this dict without the transfer-averaged
    ``periodic_contribution`` :func:`calculate_investment_inputs` bundles
    it with.  It is the only shape
    :func:`~app.services.growth_engine.calculate_employer_contribution`
    accepts, so building it anywhere else would be a second statement of
    the same mapping.

    **It no longer embeds a gross, since plan step salary:R14-b.**  The dict
    carried ``gross_biweekly`` -- ONE figure sizing every period's employer
    contribution for the life of a projection, which on the developer's own
    data froze a 5% match at today's `$3,631.74` while the engine's gross
    walked `$3,525.96` -> `$4,047.97` across the same 63 paydays.  A gross is
    a fact about a PAYDAY (**R-SAL2**), so it is
    :meth:`AccountPayrollFeed.gross_at`'s to answer and every caller supplies
    the period's own; there is no constant left for one to be resolved
    against.

    Args:
        investment_params: Object with ``employer_contribution_type_id``
                           and the ``employer_*_percentage`` fields.

    Returns:
        A dict describing the employer contribution, or None when the
        account has no employer contribution configured.  The dict
        carries the employer-type ref id under ``type_id`` (#38) so the
        growth engine branches on the id, not a string.
    """
    emp_type_id = getattr(investment_params, "employer_contribution_type_id", None)
    none_id = ref_cache.employer_contribution_type_id(
        EmployerContributionTypeEnum.NONE
    )
    if emp_type_id is None or emp_type_id == none_id:
        return None
    return {
        "type_id": emp_type_id,
        "flat_percentage": getattr(
            investment_params, "employer_flat_percentage", None) or ZERO,
        "match_percentage": getattr(
            investment_params, "employer_match_percentage", None) or ZERO,
        "match_cap_percentage": getattr(
            investment_params, "employer_match_cap_percentage", None) or ZERO,
    }


def _ytd_contributions(all_contributions, current_period, *, inclusive):
    """Sum this calendar year's contributions up to the current period.

    ``inclusive`` controls the current period itself: ``True`` keeps it
    (``<=``, the through-current YTD shown on the limit card); ``False``
    drops it (``<``, the strictly-before seed handed to the growth engine,
    whose per-period walk then applies and counts the current period's own
    contribution against the annual limit -- seeding the through-current value
    there would charge that period twice, deep-quality-hunt #10).  ONE
    expression for both keeps them from drifting.

    Both bounds read the current period's own ``start_date``, so the year and
    the boundary are one fact rather than two: a contribution counts when its
    PAYDAY falls in that period's calendar year at or before that period's
    payday.  **It used to build a set of period IDS and match each record's
    ``pay_period_id`` against it**, which needed the owner's whole period list
    as an argument; the record carries its payday since plan step C2-f2c, so
    the list has nothing left to answer (see :class:`PricedContribution`).  The
    two select identically -- every record the loader returns belongs to a
    period in that list, because the list is what scoped the query.

    Every record here has already passed the boundary's
    ``status_contributes_to_balance`` screen, so this sums what it is given.
    :attr:`~PricedContribution.amount` -- the realized actual when a shadow is
    settled, else what its amount resolves to -- is the ONE answer to what a row
    contributes, so this YTD/limit accounting agrees with the per-period
    timeline (:func:`build_contribution_timeline`, reading the same records)
    once a transfer shadow is settled with an actual that differs from its
    estimate (deep-quality-hunt #11).  Summing ``estimated_amount`` here
    previously let the cap/limit math read a different dollar than the engine
    actually applied; the prior "F-027 S18 contract-safe" rationale assumed a
    shadow's ``actual_amount`` is always ``None``, which is untrue once a settle
    sets it (the ``Transfer`` parent has no ``actual_amount`` column, so a
    settled actual lives only on the shadows).

    Args:
        all_contributions: :class:`PricedContribution` records for one account.
        current_period:    The current period object -- anything carrying a
                           ``start_date`` -- or None.
        inclusive:         Keyword-only; include the current period or not.

    Returns:
        The contribution total (Decimal); ZERO when ``current_period`` is None,
        the state in which there is no year and no boundary to ask about.
    """
    if current_period is None:
        return ZERO
    boundary = current_period.start_date
    return sum(
        (
            c.amount for c in all_contributions
            if c.payday.year == boundary.year
            and (c.payday <= boundary if inclusive else c.payday < boundary)
        ),
        ZERO,
    )


def calculate_investment_inputs(
    investment_params,
    feed: AccountPayrollFeed,
    all_contributions,
    current_period,
):
    """Compute projection inputs for an investment account.

    **It stopped taking the owner's period LIST at plan step C2-f2c.**  The
    list served the two YTD windows alone, as a lookup from a contribution's
    ``pay_period_id`` to that period's payday; the loader that prices a
    contribution now dates it too, so there is no lookup left to do and one
    argument fewer for a caller to get wrong.  That also retired this
    function's ``too-many-arguments`` disable rather than re-justifying it.

    **``periodic_contribution`` is the CURRENT period's figure since plan step
    salary:R14-b, and it is no longer the forward walk's input.**  It was one
    raise-blind scalar the whole projection ran on (finding **D45**); the walk
    reads a dated record per period now
    (:func:`build_contribution_timeline`), so what this answers is the
    per-period CARD both dashboards render -- *what does a paycheck put in*,
    asked of the paycheck the owner is actually being paid.  The ``deductions``
    and ``salary_gross_biweekly`` arguments left with it: the feed carries both
    facts, keyed by the payday that makes each one true.

    Args:
        investment_params:     Object with employer fields and
                               ``annual_contribution_limit``.
        feed:                  The account's :class:`AccountPayrollFeed` --
                               what its payroll puts in per payday, priced by
                               the paycheck engine at the boundary.
        all_contributions:     List of :class:`PricedContribution` records
                               for this account -- shadow-income rows already
                               valued, screened and dated at the boundary.
        current_period:        The current period object -- anything carrying a
                               ``start_date``, which both
                               :class:`~app.models.pay_period.PayPeriod` and
                               :class:`~app.services.pay_calendar.DerivedPeriod`
                               do -- or None.  It is the payday the per-period
                               figures above are read at; ``None`` leaves them
                               at ``$0.00``, the same state the two YTD windows
                               already answer zero for.

    Returns:
        InvestmentInputs dataclass.
    """
    periodic_contribution = (
        feed.employee_at(current_period.start_date)
        if current_period is not None else ZERO
    )
    periodic_contribution += _average_transfer_contribution(all_contributions)

    return InvestmentInputs(
        periodic_contribution=periodic_contribution,
        # An employer contribution with no KNOWN funding profile models
        # nothing (developer, 2026-09-04): there is no gross to take a
        # percentage OF, so the params are withheld rather than paired with a
        # basis of zero.  The two states stay distinguishable for the
        # surfaces -- ``employer_params is None`` says no money, and
        # ``feed.funds_employer`` says WHY -- which is the half of that ruling
        # reading "and say so".
        employer_params=(
            employer_contribution_params(investment_params)
            if feed.funds_employer else None
        ),
        annual_contribution_limit=getattr(
            investment_params, "annual_contribution_limit", None),
        ytd_contributions=_ytd_contributions(
            all_contributions, current_period, inclusive=True),
        ytd_contributions_seed=_ytd_contributions(
            all_contributions, current_period, inclusive=False),
    )


def build_contribution_timeline(
    feed: AccountPayrollFeed,
    contribution_transactions,
    periods,
    as_of,
):
    """Build ContributionRecords from the payroll feed and shadow transfers.

    Combines two contribution paths into a unified per-period timeline
    for the growth engine:

    Path 1 -- Paycheck deductions: what the paycheck engine says this
    account's deductions took from each payday
    (:meth:`AccountPayrollFeed.employee_at`) -- raise-aware,
    inflation-escalated, cadence-placed and clamped to each line's own
    calendar-year ``annual_cap`` for every payday the owner's calendar
    REACHES.  Past it the figure is the feed's HOLD, a complete year's
    average, which is none of those four things and is stated as such on
    :attr:`AccountPayrollFeed.employee_by_payday`; on a 40-year chart that is
    most of the periods.  Confirmation is date-based (past period =
    confirmed) because there is no per-period transaction record for
    deductions.

    Path 2 -- Transfer-based contributions: Per-record amounts from the priced
    shadow contributions.  Confirmation is status-based
    (:attr:`PricedContribution.is_confirmed`, resolved from
    ``status.is_settled`` at the boundary) -- factual from the transaction
    workflow.

    The growth engine handles same-date aggregation (summing amounts,
    conservative is_confirmed rule) via its lookup dict.

    **The path-1 gate is PRESENCE, not price** (an adversarial review of this
    step moved it).  It read ``models_employee`` in a first build, which is
    the priced half: a deduction fully consumed by its ``annual_cap`` across
    the whole priced window prices to ``$0.00`` on every payday while being
    genuinely configured.  That fed the engine no records at all, so its
    ``periodic_contribution`` fallback applied the TRANSFER AVERAGE to periods
    that should contribute nothing.  ``is_payroll_linked`` is the question the
    gate means -- *is a deduction wired to this account* -- and the class
    docstring draws exactly that distinction one field over.

    **Path 1 stopped computing anything at plan step salary:R14-b.**  It ran
    each deduction's amount off the profile's stored annual salary and then
    re-applied the calendar-year cap through a private year-state walk
    (``_deduction_contribution_records`` / ``_period_capped_total``) -- a
    second and third answer to a question the paycheck engine answers when it
    prices the paycheck.  A record is still emitted for EVERY period, a fully
    capped ``$0`` included, because a missing record is what makes the growth
    engine fall back to ``periodic_contribution``: it is the difference
    between *this paycheck contributed nothing* and *nobody said*.

    **It reads no clock and needs no period IDENTITY since plan step C2-f2c.**
    The confirmation split took ``date.today()``, so a render that straddled
    midnight could date this timeline one day and the pass around it another;
    it takes the read pass's own ``as_of`` now, which is what every other
    producer on that render already runs on.  And path 2 resolved each
    contribution's date through an id-keyed map of *periods*, which forced this
    function to know how the caller's period type spells its primary key --
    ``id`` on an ORM row, ``period_id`` on a
    :class:`~app.services.pay_calendar.DerivedPeriod`.  A contribution carries
    its own payday now, so the only thing read off a period here is its
    ``start_date`` and both types serve.

    Args:
        feed:                       The account's :class:`AccountPayrollFeed`
                                    -- what its payroll puts in per payday.
        contribution_transactions:  List of :class:`PricedContribution`
                                    records -- shadow-income rows already
                                    valued, screened and dated at the boundary.
        periods:                    The timeline's DOMAIN: period objects with
                                    a ``start_date``, one record emitted per
                                    period for the deduction path and any
                                    contribution outside them dropped.  It may
                                    run PAST the owner's saved schedule -- the
                                    40-year chart's axis does -- which is what
                                    the feed's hold rule answers.
        as_of:                      The read pass's clock; a period opening
                                    strictly before it is confirmed.

    Returns:
        list[ContributionRecord] sorted by contribution_date.  Empty
        list when the account has no payroll feed and no qualifying
        contribution.
    """
    records = []

    # Path 1: Paycheck deductions -- the engine's own figure for each payday,
    # PLUS the transfer average on a payday the calendar does not reach.
    #
    # **That second term is a RESTORE, and leaving it out was a measured
    # regression this step's own adversarial review caught.**  The growth
    # engine's rule is that a dated record REPLACES the periodic fallback, and
    # ``periodic_contribution`` WAS the only carrier of
    # :func:`_average_transfer_contribution` before this step -- the line
    # below is the second, which is the whole of the restore.  Before plan step salary:R14-b
    # this timeline's domain was the owner's SAVED window, so every period
    # past it had no record and fell back to *deduction + average*; widening
    # the domain to the projection axis without carrying the average would
    # have dropped an account's whole recurring-transfer stream out of the
    # forward walk, for the entire horizon, for every account funded by BOTH
    # a deduction and transfers.  ``/retirement`` passed no dated records at
    # all, so there it was every period.
    #
    # **The asymmetry is inherited, not chosen**: the average applies only
    # PAST the saved window and not inside it, where a period without a
    # recorded transfer contributes the deduction alone.  Nobody designed
    # that -- it falls out of the fallback rule meeting the old domain -- and
    # a step ruled about what a DEDUCTION is priced from may not quietly
    # re-rule what a TRANSFER projects to.  It is filed as its own finding.
    #
    # **Reproduced EXACTLY for /investment, and NEWLY INTRODUCED for
    # /retirement**, which an adversarial review of this fix separated and a
    # first draft of this comment ran together.  ``/investment``'s old
    # timeline domain was ``reported_periods()``, which IS
    # ``calendar.saved()`` and so IS ``feed.prices()``'s domain: old and new
    # coincide on both sides of the boundary.  ``/retirement`` passed NO
    # dated records at all, so every period there -- in-window included --
    # took the fallback, and in-window periods now get the deduction alone
    # plus whatever dated transfers exist.  That is a real change to the
    # readiness verdict, its levers and the /savings Horizon band, and it is
    # NOT covered by this step's ``grid_balance_view`` measurement, which
    # reads the balance seam only.
    if feed.is_payroll_linked:
        beyond = _average_transfer_contribution(contribution_transactions)
        records.extend(
            ContributionRecord(
                contribution_date=period.start_date,
                amount=(
                    feed.employee_at(period.start_date)
                    + (ZERO if feed.prices(period.start_date) else beyond)
                ),
                # Past periods are confirmed (the deduction was taken from the
                # paycheck); future periods are projected.
                is_confirmed=period.start_date < as_of,
            )
            for period in sorted(periods, key=lambda p: p.start_date)
        )

    # Path 2: Transfer-based contributions -- per-transaction amounts.
    paydays = {p.start_date for p in periods}
    for contribution in contribution_transactions:
        if contribution.payday not in paydays:
            # Contribution in a period outside this timeline's domain.
            continue
        records.append(ContributionRecord(
            contribution_date=contribution.payday,
            amount=contribution.amount,
            # Transfer-based: determined by the row's settlement status
            # (Paid/Received=True, Projected=False), resolved at the boundary.
            is_confirmed=contribution.is_confirmed,
        ))

    records.sort(key=lambda r: r.contribution_date)
    return records
