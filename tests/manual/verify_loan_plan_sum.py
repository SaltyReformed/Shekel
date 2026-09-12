"""Dump what the forward loan plan SUMS, and what moves when a definition does.

The regression harness for recurrence plan step **R16-b-2**, which makes the
balance seam's ESTIMATED tier sum EVERY definition paying into a loan on its
own cadence, price every occurrence no row answers (ruling **R-R64**), and
charge the CONTRACT's calendar (ruling **R-R68**) -- every installment after
the loan's LATEST balance assertion, whether or not a payment lands in it
(ruling **R-R71**; both live loans carry an assertion in 2026, so no month
before it is charged).  Run it on a worktree at the base commit and on the
branch, against the same clone, and diff the two outputs from line 2.

**The BASELINE is expected byte-identical, and five doors are PLANTED so the
diff MUST move where the rulings say it moves.**  Both of the developer's live
loan payments are stated-price, monthly on the contractual day, and every
forward slot the schedule reaches is answered by a row, so on unmodified data
the sum and the old one-definition tier name the same occurrences at the same
price -- an all-green diff there says nothing about whether the new code
runs.  Each door constructs a state in which the two tiers differ:

* **DOOR 1 -- D47, a SECOND definition into the Mortgage**: a planted
  ``$500.00`` every-paycheck sweep, the developer's Emergency Fund transfer's
  shape, created into the Mortgage with its id FORCED below the payment's
  (the reachable ordering: the sweep authored first).  The old tier priced
  every uncovered installment from the definition with the lowest id -- the
  sweep -- so the loan read ``None`` against a ``$616.99`` escrow it does not
  cover; the sum adds the sweep's 26 occurrences a year beside the payment's
  and the payoff comes IN.  Planted fresh rather than re-pointing the archived
  sweep itself, whose 51 soft-deleted undated rows claim every saved paycheck
  and would answer its occurrences until the horizon.
* **DOOR 2 -- D46, the reset door's mid-transaction state**: the Van's rows
  in periods that have STARTED are hard-deleted (what ``reset_pay_periods``
  does before it repopulates) and the plan is read in the hole.  The old tier
  priced an occurrence dated before ``as_of`` by neither tier, so the payoff
  read one installment LATE and door-bounded generation would write one
  installment past the loan's life; the sum prices it as the row generation
  would write (ruling **R-R64**) and the payoff does not move.
* **DOOR 3 -- occurrence identity, an ad-hoc EXTRA beside an unanswered
  installment**: a ``$2,000.00`` projected transfer into the Van dated in a
  month past the saved horizon, whose installment no row answers yet.  The
  old month-slot de-dup treated the extra as that month's payment and
  DROPPED the installment; by occurrence the installment is still owed and
  both reach the plan.
* **DOOR 4 -- D53 and R-R64's cancelled clause, a future row the owner
  REMOVED**: the Van's next projected installment is soft-deleted through the
  transfer door.  The old tier could not see a tombstone and re-synthesized
  the installment; the sum reads the tombstone as the owner's answer -- the
  installment is not paid -- and the contract charges the period anyway
  (ruling **R-R71**), so the payoff moves OUT by one installment.
* **DOOR 5 -- D54, a projected extra in a SEEDED month**: a ``$300.00``
  projected transfer into the Van due five days after its most recently
  settled installment, inside that installment's month.  The old calendar charged that month a second time at
  the extra's date; the seed already charged it, so the sum charges it once
  and the extra pays pure principal.

Nothing it prints carries a sequence-assigned id, for the reason
``verify_generation_pass.py`` states: PostgreSQL does not roll a sequence
back, so an id-bearing dump reads as a difference between two runs of
identical code.  Every door opens a nested transaction and
``Session.rollback()`` afterwards discards the whole outer transaction with
it, so the database is unchanged and no door sees another's writes.

**The clone must be STAMPED and it must be production's**: ``occurs_on`` is
what answers an occurrence, and a clone whose loan rows carry NULL there reads
every occurrence as unanswered.  Both loan payments' rows are stamped on
``shekel_r7dc2`` (0 of 58 NULL, 2026-09-11).

Usage::

    PYTHONPATH=. DATABASE_URL=postgresql://.../<clone> DATABASE_URL_APP= \\
        .venv/bin/python tests/manual/verify_loan_plan_sum.py

``PYTHONPATH=.`` is load-bearing: a script run by path puts its OWN directory
on ``sys.path``, not the working directory.
"""

from datetime import date
from decimal import Decimal

from app import create_app
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.services import balance_at, template_amount_service, transfer_service
from app.services.balance_at import BalanceContext
from app.services.balance_at._plan import loan_plan
from app.services.loan_ledger import confirmed_shadows_through
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, author_rule
from app.utils.dates import add_months

USER_ID = 1
#: The read day every door is measured at.
AS_OF = date(2026, 9, 11)
#: The two live loans and their payment definitions (ids are stable on the
#: production clone; names are printed beside them).
MORTGAGE_ACCOUNT_ID = 3
VAN_ACCOUNT_ID = 8
VAN_TEMPLATE_ID = 9
#: How many months of the projected balance to print per loan.
GRID_MONTHS = 36


def _plan_lines(label, account, ctx, *, months=GRID_MONTHS):
    """Print a loan's payoff, its plan's payments and charges, and a balance grid."""
    figures = balance_at.loan_figures(account, ctx)
    print(f"{label}\tPAYOFF\taccount={account.id}\t{figures.payoff_date}")
    plan = loan_plan(account, ctx)
    estimated = [p for p in plan.payments if p.is_estimated]
    planned = [p for p in plan.payments if not p.is_estimated]
    print(
        f"{label}\tCOUNTS\taccount={account.id}"
        f"\tplanned={len(planned)}\testimated={len(estimated)}"
        f"\tcharges={len(plan.charges)}"
    )
    for payment in plan.payments:
        print(
            f"{label}\tPAY\taccount={account.id}\tdue={payment.due_date}"
            f"\tvisible={payment.effective_date}\tcash={payment.cash}"
            f"\testimated={payment.is_estimated}"
        )
    for charge in plan.charges:
        print(
            f"{label}\tCHARGE\taccount={account.id}\ton={charge.on_date}"
            f"\trate={charge.period.annual_rate}\tescrow={charge.escrow}"
        )
    grid = [add_months(AS_OF, n) for n in range(months + 1)]
    owed = balance_at.positions(account, ctx, grid)
    for on_date in sorted(owed):
        print(f"{label}\tOWED\taccount={account.id}\t{on_date}\t{owed[on_date]}")


def _loan(account_id):
    """Return the loan account, freshly loaded."""
    return db.session.get(Account, account_id)


def _last_saved_period():
    """Return the owner's last saved pay period."""
    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=USER_ID)
        .order_by(PayPeriod.start_date.desc())
        .first()
    )


def _period_containing(day):
    """Return the saved pay period covering *day*."""
    ctx = BalanceContext.build(USER_ID, AS_OF)
    period = ctx.calendar().period_containing(day)
    return db.session.get(PayPeriod, period.period_id)


def _adhoc_projected(account_id, period, amount, due):
    """Create a projected ad-hoc transfer of *amount* into *account_id*."""
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
    from app.models.amount_ownership import AmountOwnership  # pylint: disable=import-outside-toplevel
    from app.services.scenario_resolver import get_baseline_scenario  # pylint: disable=import-outside-toplevel

    checking = (
        db.session.query(Account)
        .filter_by(user_id=USER_ID, is_active=True)
        .order_by(Account.id)
        .first()
    )
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=USER_ID,
            from_account_id=checking.id,
            to_account_id=account_id,
            pay_period_id=period.id,
            scenario_id=get_baseline_scenario(USER_ID).id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
            name="Planted extra",
            due_date=due,
        ),
    )


def main():
    """Print the baseline and the five doors."""
    app = create_app()
    with app.app_context():
        print(f"# as_of={AS_OF}")

        # --- BASELINE: unmodified data, expected byte-identical ------------
        ctx = BalanceContext.build(USER_ID, AS_OF)
        for account_id in (MORTGAGE_ACCOUNT_ID, VAN_ACCOUNT_ID):
            _plan_lines("BASE", _loan(account_id), ctx)

        # --- DOOR 1: a $500 every-paycheck sweep into the Mortgage (D47) ---
        db.session.begin_nested()
        mortgage_payment = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=MORTGAGE_ACCOUNT_ID, is_active=True)
            .order_by(TransferTemplate.id)
            .first()
        )
        sweep = TransferTemplate(
            user_id=USER_ID,
            from_account_id=mortgage_payment.from_account_id,
            to_account_id=MORTGAGE_ACCOUNT_ID,
            name="Sweep into the Mortgage (planted)",
            default_amount=Decimal("500.00"),
            is_active=True,
        )
        db.session.add(sweep)
        db.session.flush()
        # The id tie-break is ascending, so a sweep authored FIRST wins the
        # old tier's pick; forced below the payment's id, the reachable order.
        lowest_free = db.session.execute(
            db.text("SELECT min(id) - 1 FROM budget.transfer_templates")
        ).scalar()
        db.session.execute(
            db.text(
                "UPDATE budget.transfer_templates SET id = :low WHERE id = :new"
            ),
            {"low": lowest_free, "new": sweep.id},
        )
        db.session.expire_all()
        sweep = (
            db.session.query(TransferTemplate)
            .filter_by(name="Sweep into the Mortgage (planted)")
            .one()
        )
        template_amount_service.set_amount(
            sweep, Decimal("500.00"), effective_on=date(2026, 1, 1),
        )
        author_rule(
            RecurrenceSpec(
                user_id=USER_ID,
                interval_n=1,
                unit=RecurrenceUnitEnum.PERIOD,
                placement=PeriodPlacementEnum.CONTAINING_DATE,
                starts_on=AS_OF,
                nominal_day=None,
            ),
            calendar_for(USER_ID),
            sweep,
        )
        db.session.flush()
        print(
            f"# DOOR 1 planted sweep id below the payment's: "
            f"{sweep.id} < {mortgage_payment.id}; starts_on="
            f"{sweep.recurrence_rule.starts_on}"
        )
        ctx1 = BalanceContext.build(USER_ID, AS_OF)
        _plan_lines("D1", _loan(MORTGAGE_ACCOUNT_ID), ctx1, months=12)
        db.session.rollback()

        # --- DOOR 2: the reset door's hole (D46) ---------------------------
        # Read three days after the Van's 2026-09-22 installment, which on the
        # clone is a PROJECTED row in a period that has started: the shape
        # ``reset_pay_periods`` wipes before it repopulates.  Hard-deleted
        # (rows CASCADE from the period), so no tombstone answers it.
        db.session.begin_nested()
        as_of_2 = date(2026, 9, 25)
        started = (
            db.session.query(PayPeriod.id)
            .filter(PayPeriod.user_id == USER_ID, PayPeriod.start_date <= as_of_2)
            .all()
        )
        started_ids = [row[0] for row in started]
        wiped = (
            db.session.query(Transfer)
            .filter(
                Transfer.transfer_template_id == VAN_TEMPLATE_ID,
                Transfer.pay_period_id.in_(started_ids),
                Transfer.due_date > date(2026, 9, 1),
                Transfer.due_date <= as_of_2,
            )
            .all()
        )
        print(
            f"# DOOR 2 as_of={as_of_2}; wiping {len(wiped)} Van rows in "
            "started periods: "
            + ", ".join(sorted(str(row.occurs_on) for row in wiped))
        )
        for row in wiped:
            db.session.query(Transaction).filter(
                Transaction.transfer_id == row.id,
            ).delete(synchronize_session=False)
            db.session.delete(row)
        db.session.flush()
        ctx2 = BalanceContext.build(USER_ID, as_of_2)
        _plan_lines("D2", _loan(VAN_ACCOUNT_ID), ctx2, months=12)
        db.session.rollback()

        # --- DOOR 3: an ad-hoc extra past the horizon (occurrence identity)
        db.session.begin_nested()
        last = _last_saved_period()
        extra_due = add_months(date(last.start_date.year, last.start_date.month, 10), 1)
        print(f"# DOOR 3 last saved period starts {last.start_date}; extra due {extra_due}")
        _adhoc_projected(VAN_ACCOUNT_ID, last, Decimal("2000.00"), extra_due)
        db.session.flush()
        ctx3 = BalanceContext.build(USER_ID, AS_OF)
        _plan_lines("D3", _loan(VAN_ACCOUNT_ID), ctx3, months=12)
        db.session.rollback()

        # --- DOOR 4: the next projected installment soft-deleted (D53) ------
        db.session.begin_nested()
        next_row = (
            db.session.query(Transfer)
            .filter(
                Transfer.transfer_template_id == VAN_TEMPLATE_ID,
                Transfer.due_date > AS_OF,
            )
            .order_by(Transfer.due_date)
            .first()
        )
        print(f"# DOOR 4 soft-deleting the Van row due {next_row.due_date}")
        transfer_service.delete_transfer(next_row.id, USER_ID, soft=True)
        db.session.flush()
        ctx4 = BalanceContext.build(USER_ID, AS_OF)
        _plan_lines("D4", _loan(VAN_ACCOUNT_ID), ctx4, months=12)
        db.session.rollback()

        # --- DOOR 5: a projected extra in a seeded month (D54) -------------
        db.session.begin_nested()
        from app.services.scenario_resolver import get_baseline_scenario  # pylint: disable=import-outside-toplevel
        settled = confirmed_shadows_through(
            VAN_ACCOUNT_ID, get_baseline_scenario(USER_ID).id, AS_OF,
        )
        latest = max(settled, key=lambda shadow: shadow.due_date)
        # Five days after the settled installment, inside its own month, so
        # the extra lands in the slot the seed charged.
        extra_due = latest.due_date + (date(2026, 1, 6) - date(2026, 1, 1))
        print(
            f"# DOOR 5 latest settled Van installment due {latest.due_date}; "
            f"extra due {extra_due}"
        )
        _adhoc_projected(
            VAN_ACCOUNT_ID, _period_containing(extra_due), Decimal("300.00"),
            extra_due,
        )
        db.session.flush()
        ctx5 = BalanceContext.build(USER_ID, AS_OF)
        _plan_lines("D5", _loan(VAN_ACCOUNT_ID), ctx5, months=12)
        db.session.rollback()

        print("# rolled back")


if __name__ == "__main__":
    main()
