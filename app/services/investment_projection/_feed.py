"""
Shekel Budget App -- the account payroll feed (plan step salary:R14-b).

One account's side of what the paycheck engine already priced: what payroll
put into it on each payday of the owner's saved calendar, and the gross of
the paycheck that did it.  A VALUE, built once by
:func:`app.services.projection_inputs.load_payroll_feeds` and read by every
consumer; it computes no deduction of its own, which is the whole point of
the step that introduced it (ruling **R-SAL2**).

It lives in its own module because it is the half of
:mod:`app.services.investment_projection` with its own invariants -- the two
held figures derived at construction, the completeness question both halves
of the hold ask -- and because the two halves together broke the 1000-line
ceiling (ledger row **N-539**; the split falls to the breaker, per
``balance:R-IR``).  Nothing outside the package imports this module: the
package re-exports the class under its original path, so every reference
written before the split still resolves.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.utils.money import ZERO, round_money


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

        A COMPLETE calendar year's total over THAT YEAR'S OWN payday count,
        and the last priced payday where the saved window contains no
        complete year.

        **The calendar year is the span because ``annual_cap`` is**, and the
        engine resets the clamp on it.  A complete year's total over that
        year's own paydays reproduces what the deleted
        ``_annual_cap_averaged`` returned, ``min(amount x ppy, cap) / ppy``,
        from the engine's own priced figures rather than a second formula --
        and is right where that function was not, on the 27-payday biweekly
        year: 27 payments of ``$500`` are ``$500`` a payday, not the
        ``$519.23`` that ``13,500 / 26`` gives.  Which years qualify, and why
        the question is asked of the WINDOW and never of the cadence, is
        :meth:`_complete_years`.

        **A window with no complete year falls back to the last PRICED
        payday.**  A sub-year window has thrown the cap away -- a
        ``$500``-a-payday deduction and a ``$1,000``-capped one price
        IDENTICALLY for their first two paydays -- so no rule can be right
        for both from the fold alone, and three tried here were each measured
        wrong on exactly that shape (``docs/plans/lessons.md``, the R14-b
        entry, holds the three and their figures).  The fallback is exact for
        an uncapped deduction, which is every live one on the developer's
        data, and reads a capped one's trailing CLAMPED figure, which
        understates -- except on a window shorter than the cap takes to bind,
        where it reads the unclamped rate at up to 13.00x the cap.

        The residue is the SHORT window, not a cadence: any owner whose saved
        schedule covers one whole calendar year gets the exact figure,
        biweekly or weekly.  What would make it exact for EVERYONE is the
        ENGINE pricing the tail rather than this extrapolating it -- the
        salary-path step the developer ruled on 2026-09-04 to follow this
        one, which deletes this method and its fallback entirely.

        Returns:
            ``(earliest, latest)`` -- the per-payday average of the earliest
            and latest COMPLETE calendar years; the first and last priced
            paydays where no year is complete; ``(ZERO, ZERO)`` when nothing
            was priced.  The two coincide for
            any account whose feed is flat, which is every uncapped one.
        """
        if not self.employee_by_payday:
            return (ZERO, ZERO)
        by_year: "dict[int, list[Decimal]]" = {}
        for payday, amount in self.employee_by_payday.items():
            by_year.setdefault(payday.year, []).append(amount)
        complete = sorted(self._complete_years())
        if not complete:
            # No complete year, so no annual total to divide.  The LAST
            # PRICED payday is the fallback: exact for an uncapped deduction
            # (its rate is the same every payday), and for a capped one it
            # reads the trailing clamped figure, which understates rather
            # than over -- except in a window so short the cap has not bound
            # yet, where it reads the unclamped rate at up to 13.00x the cap
            # (measured: a $1,000-capped deduction on a one- or two-payday
            # window holds $500, which annualises to $13,000).  That residue
            # is what the deferred salary-path step removes by pricing the
            # tail instead of extrapolating it.
            last = self.employee_by_payday[max(self.employee_by_payday)]
            first = self.employee_by_payday[min(self.employee_by_payday)]
            return (first, last)
        return (
            round_money(sum(by_year[complete[0]], ZERO)
                        / Decimal(len(by_year[complete[0]]))),
            round_money(sum(by_year[complete[-1]], ZERO)
                        / Decimal(len(by_year[complete[-1]]))),
        )

    def _complete_years(self) -> "set[int]":
        """Calendar years the priced window holds EVERY payday of.

        **Both halves of the hold ask this one question**, and two
        adversarial passes measured it wrong when it was asked of the CADENCE
        instead.  ``periods_per_year`` is a constant; the number wanted is
        how many paydays THIS calendar year holds, and a biweekly year holds
        27 about one year in eleven (26 x 14 is 364 days, so the extra day
        accumulates).  Dividing such a year by the cadence overstates the
        whole tail, and a ``>=`` count test grades it COMPLETE on 26 of its
        27 observed, understating a front-loaded capped deduction.  Both
        figures are asserted by the cases named for them in
        ``TestAccountPayrollFeed``, which is where they cannot decay.

        The window is a contiguous run of one owner's paydays, so a year is
        covered exactly when the run reaches past both its edges.  A payday
        observed in an adjacent year proves that; at the run's own ends, the
        payday one interval further out would have to fall in an adjacent
        year, which is what stepping the boundary out tests.  The interval is
        the SMALLEST observed gap, so an irregular cadence (semi-monthly gaps
        run 13-16 days) grades conservatively: a year it cannot prove covered
        is simply not averaged.

        Returns:
            The set of calendar years every one of whose paydays is priced
            in this feed.  Empty for a window of fewer than two paydays,
            which cannot establish its own interval.
        """
        days = sorted(self.employee_by_payday)
        if len(days) < 2:
            return set()
        step = min(later - earlier for earlier, later in zip(days, days[1:]))
        below = (days[0] - step).year
        above = (days[-1] + step).year
        return {year for year in {day.year for day in days}
                if below < year < above}

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
        COMPLETE-calendar-year average now, and that is a different predicate:
        a window with no complete year at all, or one whose complete year
        happens to be all zeros, holds ``$0.00`` in both directions while an
        adjacent partial year paid.  Measured on a 26-payday
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
