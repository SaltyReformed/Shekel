"""
Shekel Budget App -- Pay-Period Balance Projection Helpers

Pure helpers for picking projected account balances at fixed future
horizons.  No Flask, no SQLAlchemy: they operate on the DERIVED pay calendar
(:class:`~app.services.pay_calendar.DerivedPeriod` values) and a
``{period_id: balance}`` map, so they import cleanly into any route or service.

**They took ORM ``PayPeriod`` rows until pay-calendar plan step C2-f2d-3.**
Both of the two things read off a period here -- its ordinal and its id -- are
answered by the derived value, and the ordinal is one of the two columns plan
step **C4-c** dropped, so a helper keyed on the stored one would have had to move
anyway.

**The horizons are named in MONTHS and resolved in PAY PERIODS, and until
recurrence plan step R-F17 the second half of that sentence was hardcoded.**
``HORIZON_OFFSETS`` was ``(("3 months", 6), ("6 months", 13), ("1 year", 26))``
-- ``months x 26 / 12``, which is the truth for an owner paid every fourteen
days and for nobody else.  The counts are derived per owner now, from the
cadence they stated, by :meth:`app.services.pay_calendar.PayCadence.paychecks_within`
(ruling **R-R31**; ledger row **F-17**).  At ``cadence_days = 14`` that
derivation returns the same 6 / 13 / 26, so no displayed figure moved when it
landed.

The ``PayCadence`` dependency is a TYPE_CHECKING import alone: this module
calls a method on a value its caller supplies and constructs nothing, so
``app.utils`` gains no runtime edge to ``app.services``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.pay_calendar import PayCadence

#: The forward spans this application offers, in MONTHS, each named ONCE.
#:
#: SIX surfaces render a subset of these -- the ACCOUNT DETAIL page's balance
#: chips and the ``/savings`` cockpit's (3 / 6 / 12, resolved separately through
#: :func:`horizon_offsets`), the account page's "Interest, next 12 mo" chip
#: (12), the grid's date-range buttons (6 / 12 / 24), the mobile Plan tab (6)
#: and the dashboard pulse chart (6) -- and each names its own labels, because a
#: chip says "6 months" where a button says "6M" with that as its tooltip.
#: (It said FOUR, then FIVE; an adversarial review of plan step R10-b found the
#: two chip renderers counted as one.  ``pay_calendar._cadence``'s module
#: docstring states the census as membership and says why the COUNT keeps going
#: stale.)  What they may
#: NOT do is name their own NUMBER: two literal sixes in two modules is the
#: agreement-by-sentence that ledger row **F-17** is, one level up from the
#: period counts it was about.  ``accounts/detail.py`` held a separate
#: ``_ONE_YEAR_PERIODS = 26`` whose comment asserted it matched the "1 year"
#: chip beside it, and that is the shape these constants exist to refuse.
THREE_MONTHS = 3
SIX_MONTHS = 6
ONE_YEAR_MONTHS = 12
TWO_YEARS_MONTHS = 24

#: The forward balance horizons, as ``(span in months, display label)`` pairs.
#:
#: The MONTHS are the fixed thing and the pay-period offset is derived from
#: them -- the direction ruling **R-R31** settled.  The other direction (fix
#: the offsets, derive the label) was rejected: it makes the chips read "1.4
#: months" for a weekly owner, and the labels stop being comparable between
#: two people on different cadences.
HORIZON_MONTHS: tuple[tuple[int, str], ...] = (
    (THREE_MONTHS, "3 months"),
    (SIX_MONTHS, "6 months"),
    (ONE_YEAR_MONTHS, "1 year"),
)


def offered_spans(cadence: PayCadence, spans):
    """Resolve each labelled span into a paycheck count, dropping the unreachable.

    **The ONE implementation of ruling R-R31's second half.**  Every surface
    that offers a set of month-named windows asks the same two questions of
    each -- how many of this owner's paychecks does it reach, and is that zero
    -- and the account chips and the grid range buttons each answered them with
    their own copy of this loop until an adversarial design review of plan step
    **R-F17** pointed out that pylint's ``duplicate-code`` cannot see the
    duplication, because the two carry different label shapes.  That is the
    R0801-invisible semantic duplication ``docs/audits/pylint-cleanup/
    deep-quality-hunt.md`` row 35 already caught once on this very module.

    **A horizon no paycheck reaches is dropped rather than clamped**: the pay
    period is this application's finest forward resolution, so a window
    resolving to zero has no column to show, and the alternatives -- the
    current period's own end balance, or a fabricated ``$0.00`` -- are both
    figures the app did not compute.

    Args:
        cadence: The owner's :class:`~app.services.pay_calendar.PayCadence`.
            *This said "resolve it only where a current pay period exists: a
            calendar with no paydays carries no cadence and refuses, and a
            current period is what proves it has some".  Plan step
            ``pay_calendar:C4-d`` (ruling R-PC45) made that false in both
            halves -- a calendar carries a cadence or is not built, and
            ``PayCalendar.cadence`` is total -- and this module is the LEAF
            both of that step's updated callers reach, so the instruction
            outlived the two copies of it.*  Where a caller resolves it is now
            that caller's own question: ``routes/accounts/detail`` still guards
            on a current period because ``_interest_next_year`` dereferences
            ``period_index``, and the savings cockpit's guard is ledger row
            **N-490**.
        spans: ``(months, *label_fields)`` tuples.  The label fields are
            CARRIED, never read, so each surface passes whatever shape it
            renders -- one label for a chip, a button label plus a tooltip for
            the grid.

    Returns:
        ``(count, *label_fields)`` tuples in input order, omitting every span
        this owner's cadence reaches no paycheck inside.
    """
    resolved = []
    for months, *label_fields in spans:
        count = cadence.paychecks_within(months)
        if count:
            resolved.append((count, *label_fields))
    return resolved


def horizon_offsets(cadence: PayCadence) -> tuple[tuple[str, int], ...]:
    """Return the ``(label, pay-period offset)`` pairs for one owner's cadence.

    Resolve it ONCE per read pass and thread it: ``/savings`` projects every
    account through :func:`project_balance_horizons` in a loop, and deriving
    the same three offsets per account would be the redundant-producer shape
    this package's :class:`~app.services.savings_dashboard_service._types._ProjectionContext`
    exists to prevent.

    The offset is read as the LAST PAYCHECK WITHIN the span -- period
    ``current + offset`` -- where the grid reads the same number as a COUNT of
    columns starting at the current period.  The two differ by one pay period;
    see :meth:`~app.services.pay_calendar.PayCadence.paychecks_within`.

    Args:
        cadence: The owner's :class:`~app.services.pay_calendar.PayCadence`.
            Always readable since plan step ``pay_calendar:C4-d`` -- see
            :func:`horizon_offsets` for the precondition this used to state and
            why it no longer holds.

    Returns:
        The reachable horizons in :data:`HORIZON_MONTHS` order, each paired
        with how many pay periods ahead of the current one it lands.  A horizon
        the owner's cadence reaches no paycheck inside is absent (ruling
        **R-R31**); the twelve-month one is never absent, so the tuple is never
        empty for a real cadence.
    """
    return tuple(
        (label, offset)
        for offset, label in offered_spans(cadence, HORIZON_MONTHS)
    )


def project_balance_horizons(current_period, all_periods, balance_map, offsets):
    """Pick the projected balance at each of the owner's forward horizons.

    For each horizon offset, finds the pay period whose ``period_index``
    is ``current_period.period_index + offset`` and, when a balance
    exists for it in ``balance_map``, records it under the horizon label.

    Shared by the interest/checking account-detail pages
    (:mod:`app.routes.accounts.detail`) and the savings dashboard's
    plain-account projection branch
    (:mod:`app.services.savings_dashboard_service`).

    Args:
        current_period: The
            :class:`~app.services.pay_calendar.DerivedPeriod` covering the read
            pass's clock, or ``None`` (no current period yields an empty
            result).
        all_periods: The owner's saved schedule -- a
            :class:`~app.services.pay_calendar.PeriodWindow` -- searched by
            ``period_index``.
        balance_map: Mapping of ``budget.pay_periods.id`` to the projected
            balance at that period.  Both callers get it from the
            :mod:`app.services.balance_at` seam, which reports over the same
            pass's ``reported_periods()``, so a period taken from that
            window and this map name one calendar.
        offsets: The owner's ``(label, offset)`` pairs from
            :func:`horizon_offsets`.  Passed in rather than derived here so a
            per-account loop resolves them once; an empty tuple is the honest
            answer for an owner with no current period and yields no rows.

    Returns:
        Dict of horizon label ("3 months" / "6 months" / "1 year") to
        the projected balance at that horizon.  Labels with no matching
        period (or no balance for it) are omitted.
    """
    projected = {}
    if current_period is None:
        return projected
    for label, offset in offsets:
        target_idx = current_period.period_index + offset
        for period in all_periods:
            if (period.period_index == target_idx
                    and period.period_id in balance_map):
                projected[label] = balance_map[period.period_id]
                break
    return projected
