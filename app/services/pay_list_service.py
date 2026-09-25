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

* a day that is not a payday the app holds or projects a paycheck for, or
  one later than the owner's next payday -- the stub door's own rule and
  message (**R-SAL49**, **R-SAL48**), asked through
  :func:`~app.services.pay_stub_service.payday_refusal` so the rule has one
  home (ruling **R-SAL90**, "Up to next payday": an entry is pay
  received, and a mistyped year would otherwise replace every forecast raise
  before it); asked of a Fix only when it CHANGES the payday, as the stub door asks
  it (**R-SAL53**), so an entry whose payday later left the pay record stays
  fixable in place;
* a Fix moving an entry onto a payday another of the profile's entries
  holds: one entry per payday (``uq_pay_entries_profile_payday``), and a Fix
  never overwrites an entry unseen.

The amount's bounds are the schema's (``SalaryPayEntryFixSchema``,
``SalaryProfileCreateSchema``).  Every write re-prices the paychecks the
entry covers; the route follows it with
:func:`~app.services.salary_regeneration.regenerate_salary_transactions`, the
walk every salary write is followed by.

Flask-free: ORM rows and plain values in, the row out.  Never commits -- the
route owns the unit of work.  :func:`start_pay_list` flushes, so its caller
prices the new entry in the same unit of work; :func:`fix_entry` only STAGES
its change, so the flush that can lose a version race or meet
``uq_pay_entries_profile_payday`` runs inside the route's guard
(:func:`~app.routes._commit_helpers.regenerate_commit_or_report`) and is
reported there, never as a 500.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.exceptions import ValidationError
from app.extensions import db
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.services.pay_stub_service import payday_refusal
from app.services.payroll_basis import PayrollBasis

if TYPE_CHECKING:
    from app.services.balance_at import BalanceContext


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

    Each entry's yearly figure is :meth:`~app.services.payroll_basis
    .PayrollBasis.base_pay_on` its own payday -- the ONE producer of a
    yearly figure (``BasePay.annual``, rule 14), which on an entry's own
    payday walks nothing past the entry: no raise lands in ``(payday,
    payday]`` and the rhythm walked from is the rhythm arrived at, so it is
    the entry's amount times the paychecks a year in force that day.

    Args:
        basis: The profile's :class:`~app.services.payroll_basis.PayrollBasis`.

    Returns:
        One :class:`PayRow` per entry.
    """
    rows = []
    for entry in sorted(basis.profile.pay_entries, key=lambda e: e.payday):
        base = basis.base_pay_on(entry.payday)
        rows.append(PayRow(entry, int(base.periods_per_year), base.annual))
    return rows


def _refuse_payday(ctx: "BalanceContext", payday: date, today: date) -> None:
    """Raise when *payday* is not a payday, or is later than the owner's next one.

    Args:
        ctx: The route's pass, read for the owner's calendar.
        payday: The day an entry would take effect on.
        today: The owner's civil today, as the stub door is handed it.

    Raises:
        ValidationError: With :func:`~app.services.pay_stub_service
            .payday_refusal`'s message.
    """
    refusal = payday_refusal(ctx, payday, today)
    if refusal is not None:
        raise ValidationError(refusal)


def start_pay_list(
    profile: SalaryProfile, ctx: "BalanceContext", amount: Decimal,
    payday: date, today: date,
) -> SalaryPayEntry:
    """Write a new profile's FIRST pay entry: *amount* a paycheck from *payday* on.

    Every paycheck before *payday* is priced from this entry too (ruling
    **R-SAL59**: "Paychecks before the first entry use it"), and a forecast
    raise landing on or before it is taken to be in *amount* already.

    Args:
        profile: The new, flushed profile.
        ctx: The route's pass (its owner's calendar).
        amount: What one paycheck pays, above zero (the schema's bound).
        payday: The payday it pays it from.
        today: The owner's civil today.

    Returns:
        The entry, flushed.

    Raises:
        ValidationError: *payday* is not a payday, or is later than the
            owner's next one.
    """
    _refuse_payday(ctx, payday, today)
    entry = SalaryPayEntry(payday=payday, amount=amount)
    # Through the relationship, so the profile's loaded pay list holds the
    # entry before the create door prices it in the same unit of work.
    profile.pay_entries.append(entry)
    db.session.flush()
    return entry


def fix_entry(
    entry: SalaryPayEntry, ctx: "BalanceContext", amount: Decimal,
    payday: date, today: date,
) -> SalaryPayEntry:
    """Correct one pay entry's amount, its payday, or both (ruling **R-SAL61**, "Fix").

    A correction moves only the paychecks the entry covers -- from its payday
    to the next entry's, and before it when it is the first -- which is the
    difference between a fix and a raise the dated list exists to keep.

    Args:
        entry: The entry, its ownership and version already checked by the
            route.
        ctx: The route's pass (its owner's calendar).
        amount: The corrected amount, above zero.
        payday: The corrected payday.
        today: The owner's civil today.

    Returns:
        The entry, its change staged and NOT flushed: the version-pinned
        UPDATE runs at the caller's guarded flush (module docstring), where a
        concurrent Fix surfaces as ``StaleDataError`` and a concurrent move
        onto the same payday as the unique key's ``IntegrityError``.

    Raises:
        ValidationError: A CHANGED payday that is not a payday, is later
            than the owner's next one, or another of the profile's entries
            holds.
    """
    if payday != entry.payday:
        _refuse_payday(ctx, payday, today)
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
    return entry


__all__ = ["PayRow", "fix_entry", "pay_rows", "start_pay_list"]
