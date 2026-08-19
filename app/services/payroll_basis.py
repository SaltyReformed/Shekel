"""The paycheck engine's owner-level input: a salary profile and its cadence.

Plan step **R-F16** (``docs/plans/implementation_plan_recurrence_redesign.md``,
"Carried steps").  Its own module rather than a type inside
:mod:`app.services.paycheck_calculator`, which sits at the 1000-line ceiling
this step's own additions pushed it past -- and because nine modules outside
that one CONSTRUCT this value while only it computes a paycheck, so the input
has more consumers than the producer does.  Every one of the nine imports it
from HERE; an earlier draft of this sentence made the consumer argument while
all nine still reached the type through the engine, which is a claim its own
import graph refuted.

Pure: no Flask, no ORM import, no clock, no database.  The profile is typed
loosely on purpose (see the class).
"""
from dataclasses import dataclass
from decimal import Decimal

from app.services.pay_calendar import PayCadence


@dataclass(frozen=True)
class PayrollBasis:
    """One owner's salary contract bound to the rhythm their paychecks arrive on.

    **The pair, as one value, is plan step R-F16's fix for finding F-16.**  The
    engine needs two facts to price a paycheck -- what the job pays a year, and
    how many paychecks that year holds -- and until this step they travelled
    separately: the salary profile carried its own ``pay_periods_per_year``
    column (a 12 / 24 / 26 / 52 dropdown) while
    ``budget.pay_schedule.cadence_days`` carried the payday rhythm, and no door
    validated one against the other.  Measured with the real engine on a
    ``$91,675`` salary, a profile saying 26 beside a 7-day cadence modelled
    ``$15,279.20`` of monthly gross against a true ``$7,639.60`` -- the year's
    paychecks summing to 200% of salary.  Only 5 of the 365 legal cadences had
    a dropdown value that could agree with them at all, so validating the pair
    was not an available remedy: the count had to become a derivation.

    Binding them makes the mismatched pair unrepresentable rather than merely
    discouraged -- the same argument the read-pass ruling makes for
    ``BalanceContext`` -- and there is now ONE derivation of the count,
    :attr:`~app.services.pay_calendar.PayCadence.periods_per_year`, which is
    also what every monthly-equivalent conversion in the application reads.
    A salary profile's paycheck recurs every pay period BY DEFINITION (it is
    what ``routes.salary.profiles._paycheck_template`` authors), so there was
    never a per-profile count for the dropped column to hold.

    Attributes:
        profile: The ``SalaryProfile`` -- read for the annual salary, the
            raises, the deductions and the W-4 inputs.  Typed loosely because
            every consumer reads attributes rather than the ORM class, and
            because this module must not import a model (the engine below it is
            pure).  The test suite prices duck-typed profiles through the same
            door for that reason.
        cadence: How often this owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`), derived from
            ``budget.pay_schedule.cadence_days``.
    """

    profile: object
    cadence: PayCadence

    @property
    def periods_per_year(self) -> Decimal:
        """Return how many paychecks this owner receives in a year.

        Forwarded rather than re-derived so the engine's arithmetic reads as
        one name instead of a two-hop attribute chain, and so there is a single
        place to look when asking where its denominator comes from.

        Returns:
            The paycheck count as an integral ``Decimal``.
        """
        return self.cadence.periods_per_year


__all__ = ["PayrollBasis"]
