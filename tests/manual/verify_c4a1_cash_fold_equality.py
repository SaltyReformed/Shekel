"""Dump every cash figure the fold publishes, so C4-a-1 can be diffed.

Pay-calendar plan step **C4-a-1** takes ``balance_at._cash_fold._cash_plan`` off
``txn.pay_period`` and onto the owner's DERIVED pay calendar: a still-projected
row's landing day is now clamped into the span the paydays define rather than
into the ``budget.pay_periods.end_date`` copy stored beside them (finding
**P38**).  Where the two columns agree the fold cannot move, and on every
production-shaped database they do agree -- so the claim this script exists to
support is an EQUALITY, and an equality is only worth what the run that produced
it covered.

**It runs UNCHANGED on both sides of the step**, which is what makes it a
before/after instrument rather than a description of the new code.  C4-a-1
changes two PRIVATE functions; the seam door every figure below comes out of
(``cash_balance_map`` / ``cash_balance_at`` / ``cash_daily_balance_series``, and
the pass they take) is byte-identical on either side.  Run it on the step's
branch and on its merge base against the SAME database and diff the two JSON
documents: ``figures`` and ``coverage`` must match exactly.

Three sections, and they answer different questions:

* **``figures``** -- per owner and account, the seed, every landing day the
  PLANNED tier produced with that day's net, the balance at every saved
  period's end, and the balance at each period's end sampled through the scalar
  door.  This is the part that must be byte-identical across the two runs.
* **``coverage``** -- how many owners, accounts, projected rows and distinct
  landing days the run actually reached.  A byte-identical diff over a run that
  folded nothing is the shape this project has shipped before; the counts are
  what let a reader tell "equal" from "empty".
* **``divergence_probe``** -- for every period, the STORED ``end_date`` beside
  the derivation, and for every still-projected row the day each of the two
  spans clamps it to.  It is computed HERE rather than through the fold, so it
  reads the same on both sides and answers the question the equality cannot:
  **could anything have moved at all?**  ``rows_that_would_move`` is the price
  of this step on this database.

**It never writes.**  Every row is loaded read-only and the run ends in a
rollback regardless.  Run it against a clone all the same.

**The AS-OF is an argument, and that is not a convenience.**  Ruling R-G
clamps a still-projected row to ``as_of + 1``, so the read pass's clock is one
of the inputs every landing day below is a function of.  Defaulting it to the
owner's civil day makes two runs taken either side of midnight differ by a day
on the earliest landing -- which is a real diff, of the CLOCK, printed exactly
like a diff of the code.  It happened on this script's first use.  So the day
is passed in, echoed into the document, and the two runs of a before/after
comparison must be given the SAME one.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel_c4a1 \\
        .venv/bin/python tests/manual/verify_c4a1_cash_fold_equality.py \\
            out.json 2026-08-27

Exit status is ``0`` when the run reached at least one account holding at least
one projected row -- that is, when the document it printed is evidence.  It is
``2`` otherwise, because an empty document diffs clean against another empty
one.  The EQUALITY itself is not asserted here: it is the diff of two runs, and
a script cannot check a file it was not given.

This file is deliberately outside pytest's collection (``pytest.ini`` sets
``python_files = test_*.py``), like its ``verify_*`` siblings: it needs a
populated database chosen by the operator, not the seeded test template.  The
suite's own half of this proof -- the shapes a live database does not supply,
being a stored end that disagrees with the paydays -- is
``tests/test_services/test_cash_fold.py::TestThePlanClampsAgainstTheDerivedSpan``
and its firing control.
"""

import json
import pathlib
import sys
from datetime import date

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so neither ``app`` nor ``tests`` is importable when this is run as
# ``.venv/bin/python tests/manual/verify_c4a1_cash_fold_equality.py``.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.user import User
from app.ref_cache import status_id
from app.services.balance_at import (
    BalanceContext,
    cash_balance_at,
    cash_balance_map,
)
from app.services.balance_at._cash_fold import assembled_fold
from app.services.pay_calendar import DerivedPeriod
from app.utils.dates import display_today


def _clamped(due, start, end):
    """Return the day *due* is budgeted to inside ``[start, end]``.

    The shared clamp, applied here rather than reached through the fold, so the
    probe reads the same on both sides of the step.

    **It builds a ``DerivedPeriod`` to ask** (pay-calendar plan step C4-a-2):
    the clamp is a method on the derived value now and there is no free
    function left that takes two loose dates.  This probe's whole subject is
    comparing a STORED span against a derived one, so it constructs the value
    for whichever span it is asking about; ``period_index`` and
    ``end_is_projected`` are not read by the clamp and are filled with the
    values that make the object legal.

    Args:
        due: The row's ``due_date``, or ``None``.
        start: The span's first day.
        end: The span's last day.

    Returns:
        The clamped :class:`datetime.date`.
    """
    return DerivedPeriod(
        period_id=None,
        period_index=0,
        start_date=start,
        end_date=end,
        end_is_projected=False,
    ).attribution_day(due)


def _probe_owner(calendar, periods, rows):
    """Return the stored-versus-derived probe for one owner.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        periods: Their ``budget.pay_periods`` rows, payday ascending.
        rows: Their still-projected transactions, any order.

    Returns:
        A JSON-safe dict: the per-period end comparison, how many rows each
        span would place differently, and every such row named.
    """
    ends, moved = [], []
    for period in periods:
        derived = calendar.period_by_id(period.id)
        ends.append({
            "period_id": period.id,
            "start_date": period.start_date.isoformat(),
            "stored_end": period.end_date.isoformat(),
            "derived_end": derived.end_date.isoformat(),
            "end_is_projected": derived.end_is_projected,
            "ends_agree": period.end_date == derived.end_date,
        })
    by_id = {period.id: period for period in periods}
    for row in rows:
        stored = by_id[row.pay_period_id]
        derived = calendar.period_by_id(row.pay_period_id)
        stored_day = _clamped(row.due_date, stored.start_date, stored.end_date)
        derived_day = _clamped(
            row.due_date, derived.start_date, derived.end_date,
        )
        if stored_day != derived_day:
            moved.append({
                "transaction_id": row.id,
                "pay_period_id": row.pay_period_id,
                "due_date": None if row.due_date is None
                else row.due_date.isoformat(),
                "stored_landing": stored_day.isoformat(),
                "derived_landing": derived_day.isoformat(),
            })
    return {
        "period_ends": ends,
        "ends_that_disagree": sum(1 for end in ends if not end["ends_agree"]),
        "rows_that_would_move": len(moved),
        "rows_moved": moved,
    }


def _account_figures(account, ctx, window):
    """Return every cash figure the fold publishes for one account.

    Args:
        account: The :class:`~app.models.account.Account` to value.
        ctx: The owner's read pass.
        window: Their whole saved :class:`~app.services.pay_calendar.PeriodWindow`.

    Returns:
        A JSON-safe dict of the seed, the PLANNED tier's landing days, the
        per-period map and the same period ends read through the scalar door.
    """
    folded = assembled_fold(account, ctx)
    per_period = cash_balance_map(account, ctx)
    return {
        "account_id": account.id,
        "name": account.name,
        "seed": str(folded.seed),
        "step_count": len(folded.steps),
        "planned_rows": len(folded.plan.rows),
        "landing_days": {
            day.isoformat(): str(net)
            for day, net in sorted(folded.day_nets.items())
        },
        "period_map": {
            str(period_id): str(balance)
            for period_id, balance in per_period.items()
        },
        "scalar_at_period_ends": {
            period.end_date.isoformat(): str(
                cash_balance_at(account, ctx, period.end_date),
            )
            for period in window
        },
    }


def _owner_report(user_id, as_of):
    """Return one owner's figures and probe, or ``None`` when they hold no paydays.

    Args:
        user_id: The ``auth.users.id`` to report on.
        as_of: The read pass's clock -- ruling R-G's clamp floor, and therefore
            an input to every landing day reported.

    Returns:
        A JSON-safe dict, or ``None`` for an owner with no pay periods -- who
        has no calendar to fold against and nothing to compare.
    """
    periods = (
        db.session.query(PayPeriod)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )
    if not periods:
        return None

    ctx = BalanceContext.build(user_id, as_of=as_of)
    calendar = ctx.calendar()
    window = calendar.saved()
    accounts = (
        db.session.query(Account)
        .filter(Account.user_id == user_id)
        .order_by(Account.id)
        .all()
    )
    rows = (
        db.session.query(Transaction)
        .join(PayPeriod, PayPeriod.id == Transaction.pay_period_id)
        .filter(
            PayPeriod.user_id == user_id,
            Transaction.is_deleted.is_(False),
            Transaction.status_id == status_id(StatusEnum.PROJECTED),
        )
        .order_by(Transaction.id)
        .all()
    )
    return {
        "user_id": user_id,
        "period_count": len(periods),
        "accounts": [
            _account_figures(account, ctx, window) for account in accounts
        ],
        "probe": _probe_owner(calendar, periods, rows),
        "projected_row_count": len(rows),
    }


def main(out_path=None, as_of_text=None):
    """Fold every account of every owner and print the document.

    Args:
        out_path: Optional path to also write the JSON document to.
        as_of_text: The read pass's clock as ``YYYY-MM-DD``.  Defaults to the
            owner's civil day, which is right for a single run and WRONG for a
            before/after pair taken either side of midnight -- see the module
            docstring.

    Returns:
        ``0`` when the run reached at least one projected row, ``2`` otherwise.
    """
    as_of = (
        date.fromisoformat(as_of_text) if as_of_text else display_today()
    )
    app = create_app()
    owners = []
    with app.app_context():
        try:
            for user in db.session.query(User).order_by(User.id).all():
                report = _owner_report(user.id, as_of)
                if report is not None:
                    owners.append(report)
        finally:
            db.session.rollback()

    coverage = {
        "owners": len(owners),
        "accounts": sum(len(owner["accounts"]) for owner in owners),
        "projected_rows": sum(
            owner["projected_row_count"] for owner in owners
        ),
        "landing_days": sum(
            len(account["landing_days"])
            for owner in owners for account in owner["accounts"]
        ),
        "ends_that_disagree": sum(
            owner["probe"]["ends_that_disagree"] for owner in owners
        ),
        "rows_that_would_move": sum(
            owner["probe"]["rows_that_would_move"] for owner in owners
        ),
    }
    document = {
        "as_of": as_of.isoformat(),
        "coverage": coverage,
        "figures": [
            {"user_id": owner["user_id"], "accounts": owner["accounts"]}
            for owner in owners
        ],
        "divergence_probe": [
            {"user_id": owner["user_id"], **owner["probe"]}
            for owner in owners
        ],
    }
    print(json.dumps(document, indent=2, sort_keys=True))
    if out_path is not None:
        pathlib.Path(out_path).write_text(
            json.dumps(document, indent=2, sort_keys=True), encoding="utf-8",
        )
    if coverage["projected_rows"] == 0:
        print(
            "FAIL: no owner held a still-projected row, so the PLANNED tier "
            "this step changes was never driven and an equal diff against "
            "another such run means nothing.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(
        main(
            sys.argv[1] if len(sys.argv) > 1 else None,
            sys.argv[2] if len(sys.argv) > 2 else None,
        )
    )
