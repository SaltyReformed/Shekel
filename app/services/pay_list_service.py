"""
Shekel Budget App -- a salary's PAY LIST doors (plan step salary:X-av-3a).

What one paycheck pays, from a dated payday on, is the salary's stored fact
(:class:`~app.models.salary_pay_entry.SalaryPayEntry`, rulings **R-SAL59** and
**R-SAL61**), and it is written through here and nothing else: the create
form's FIRST entry (:func:`start_pay_list`) and the salary page's Fix
(:func:`fix_entry`).  Record a pay change and Remove are plan step
salary:X-av-3b's (ruling **R-SAL83**), and Remove never takes the last entry
(**R-SAL68**).

**What the doors refuse, and whose rule each refusal is:**

* a day that is not a payday the app holds or projects a paycheck for --
  the stub door's own rule and message, asked through
  :func:`~app.services.pay_stub_service.not_a_payday` so "is this a payday"
  has one home (R-SAL50's shape); asked of a Fix only when it CHANGES the
  payday, as the stub door asks it (**R-SAL53**), so an entry whose payday
  later left the pay record stays fixable in place;
* a Fix moving an entry onto a payday another of the profile's entries
  holds: one entry per payday (``uq_pay_entries_profile_payday``), and a Fix
  never overwrites an entry unseen.

The amount's bounds are the schema's (``SalaryPayEntryFixSchema``,
``SalaryProfileCreateSchema``).  Every write re-prices the paychecks the
entry covers; the route follows it with
:func:`~app.services.salary_regeneration.regenerate_salary_transactions`, the
walk every salary write is followed by.

Flask-free: ORM rows and plain values in, the row out.  Flushes so a caller
sees assigned ids; never commits -- the route owns the unit of work.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.exceptions import ValidationError
from app.extensions import db
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.services.pay_calendar import PayCalendar, cadence_on
from app.services.pay_stub_service import not_a_payday
from app.services.payroll_basis import PayrollBasis


@dataclass(frozen=True)
class PayRow:
    """One pay entry as the salary page lists it (ruling **R-SAL61**).

    Attributes:
        entry: The :class:`~app.models.salary_pay_entry.SalaryPayEntry`, for
            its id, payday, amount and version counter.
        paychecks_a_year: The paychecks a year of the rhythm in force on its
            payday (ruling **R-SAL66**).
        yearly: The entry's yearly figure: its amount times that count,
            shown and never stored (ruling **R-SAL59**).
    """

    entry: SalaryPayEntry
    paychecks_a_year: int
    yearly: Decimal


def pay_rows(basis: PayrollBasis) -> list[PayRow]:
    """Return the profile's pay list as the salary page shows it, payday ascending.

    Args:
        basis: The profile's :class:`~app.services.payroll_basis.PayrollBasis`
            -- read for its profile's entries and its calendar, whose rhythm
            on each entry's payday gives that entry's yearly figure.

    Returns:
        One :class:`PayRow` per entry.
    """
    rows = []
    for entry in sorted(basis.profile.pay_entries, key=lambda e: e.payday):
        count = cadence_on(basis.calendar, entry.payday).periods_per_year
        rows.append(PayRow(entry, int(count), entry.amount * count))
    return rows


def _refuse_non_payday(calendar: PayCalendar, payday: date) -> None:
    """Raise when *payday* is not a payday the calendar holds or projects.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        payday: The day an entry would take effect on.

    Raises:
        ValidationError: With :func:`~app.services.pay_stub_service
            .not_a_payday`'s message.
    """
    refusal = not_a_payday(calendar, payday)
    if refusal is not None:
        raise ValidationError(refusal)


def start_pay_list(
    profile: SalaryProfile, calendar: PayCalendar, amount: Decimal, payday: date,
) -> SalaryPayEntry:
    """Write a new profile's FIRST pay entry: *amount* a paycheck from *payday* on.

    Every paycheck before *payday* is priced from this entry too (ruling
    **R-SAL59**: "Paychecks before the first entry use it"), and a forecast
    raise landing on or before it is taken to be in *amount* already.

    Args:
        profile: The new, flushed profile.
        calendar: Its owner's calendar.
        amount: What one paycheck pays, above zero (the schema's bound).
        payday: The payday it pays it from.

    Returns:
        The entry, flushed.

    Raises:
        ValidationError: *payday* is not a payday.
    """
    _refuse_non_payday(calendar, payday)
    entry = SalaryPayEntry(payday=payday, amount=amount)
    # Through the relationship, so the profile's loaded pay list holds the
    # entry before the create door prices it in the same unit of work.
    profile.pay_entries.append(entry)
    db.session.flush()
    return entry


def fix_entry(
    entry: SalaryPayEntry, calendar: PayCalendar, amount: Decimal, payday: date,
) -> SalaryPayEntry:
    """Correct one pay entry's amount, its payday, or both (ruling **R-SAL61**, "Fix").

    A correction moves only the paychecks the entry covers -- from its payday
    to the next entry's, and before it when it is the first -- which is the
    difference between a fix and a raise the dated list exists to keep.

    Args:
        entry: The entry, its ownership and version already checked by the
            route.
        calendar: Its owner's calendar.
        amount: The corrected amount, above zero.
        payday: The corrected payday.

    Returns:
        The entry, flushed.

    Raises:
        ValidationError: A CHANGED payday that is not a payday, or that
            another of the profile's entries holds.
    """
    if payday != entry.payday:
        _refuse_non_payday(calendar, payday)
        held = db.session.query(SalaryPayEntry.id).filter(
            SalaryPayEntry.salary_profile_id == entry.salary_profile_id,
            SalaryPayEntry.payday == payday,
            SalaryPayEntry.id != entry.id,
        ).first()
        if held is not None:
            raise ValidationError(
                f"Your pay from {payday.isoformat()} is already recorded; "
                f"fix that entry instead."
            )
    entry.amount = amount
    entry.payday = payday
    db.session.flush()
    return entry


__all__ = ["PayRow", "fix_entry", "pay_rows", "start_pay_list"]
