"""Dump every figure the PROJECTION AXIS decides, for a HEAD-vs-post diff.

The regression harness for pay-calendar plan step **C2-e**, and it exists
because none of the three in ``docs/plans/verification.md`` can see this
change.  ``verify_balance_baseline`` walks the ``balance_at`` seam, and the
forward projections are all ABOVE it; ``verify_savings_producers`` reads the
savings package but not the ``Horizon`` band's engine reuse, /retirement, the
levers or the /investment growth chart; ``verify_anchor_surfaces`` reads the
retirement SEEDS rather than the projections they seed.  Running any of them
over this leaf would report "nothing moved" while saying nothing about the six
surfaces it changes, which is the free-pass shape standard 3 asks about.

It answers *did anything move*, never *is the answer right*.  The proof that
the new axis is correct is the suite's hand-computed figures and the per-rule
controls; this is the exhaustive regression check beside them.

**Byte-identity is NOT the gate here, and saying so is the point.**  The axis
this step replaces opened at ``date.today()`` and stepped every 14 days
regardless of the owner's cadence; the axis that replaces it opens on the
PAYDAY covering the read pass's ``as_of`` and steps at the owner's own
``budget.pay_schedule.cadence_days``.  On a database whose cadence is 14 and
whose read day IS a payday the two coincide exactly and this must diff clean.
On any other read day the head moves by up to a cadence and every figure
downstream of it moves with it -- which is the change, not a regression.  So
read the diff against the two facts this dump prints in its header: the
owner's cadence, and whether the read day is one of their paydays.

**Usage** (from the repository root, against a production CLONE)::

    DATABASE_URL=postgresql://.../shekel_dev \\
        .venv/bin/python tests/manual/verify_projection_axis.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_dev \\
        .venv/bin/python tests/manual/verify_projection_axis.py after.json
    diff before.json after.json

For the HEAD side use ``git worktree add`` -- never ``git checkout``, which
reverts the working tree and discards the change under test
(``docs/plans/lessons.md``).

**RUN BOTH SIDES ON THE SAME CIVIL DAY.**  Every producer here is
clock-dependent: the axis opens at the read pass's ``as_of``, the seeds are
read the day before it, and the ``Horizon`` domain is sized off today.  A
BEFORE captured yesterday and an AFTER captured today differ by the calendar
rather than by the change, and this harness cannot tell the two apart.  Plan
step X-au-c2a measured that mistake at 2,277 spurious moved lines.
"""

import json
import sys
import traceback
from datetime import date
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.user import User
from app.services import (
    home_equity_service,
    property_equity_chart,
    retirement_plan,
    retirement_levers,
    retirement_readiness,
    savings_dashboard_service,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.investment_dashboard_service import compute_growth_chart_data
from app.services.pay_calendar import calendar_for

#: The horizon slider positions the /investment growth chart is asked for.
#: Three rather than one because the axis LENGTH is what the slider moves, and
#: a single position cannot show a per-period defect growing with the horizon.
_HORIZON_YEARS = [1, 10, 40]


def _money(value):
    """Stringify a Decimal so the diff is textual and exact.

    ``None`` passes through as ``None`` so an absent figure stays
    distinguishable from a zero one.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return f"{value:.6f}"
    return str(value)


def _plain(value, depth=0):
    """Serialise ANY producer result to comparable plain data.

    Generic rather than field-by-field, for the reason
    :mod:`tests.manual.verify_reader_baseline` records: a draft that named the
    fields it expected reported "identical" over three surfaces it had never
    captured.  Walking the structure removes the guess.

    ORM instances collapse to ``ClassName#id``: their identity is stable across
    the two runs and their attribute graph is unbounded.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return _money(value)
    if isinstance(value, float):
        return f"FLOAT:{value!r}"
    if isinstance(value, date):
        return value.isoformat()
    if depth > 8:
        return "DEPTH"
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in sorted(
            value.items(), key=lambda kv: str(kv[0]),
        )}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v, depth + 1) for v in value]
    if hasattr(value, "_sa_instance_state"):
        return f"{type(value).__name__}#{getattr(value, 'id', None)}"
    if hasattr(value, "__dict__"):
        return {
            "__type__": type(value).__name__,
            **{
                k: _plain(v, depth + 1)
                for k, v in sorted(vars(value).items())
                if not k.startswith("_")
            },
        }
    return str(value)


def _guard(label, thunk):
    """Run *thunk*, recording a RAISE rather than aborting the dump.

    A producer that raises on one account must not hide the rest, and the raise
    itself is a fact worth diffing: this leaf makes the calendar value REFUSE a
    range it used to answer short (ledger row P23), so "raised here, answered
    there" has to show up as a move rather than as a crashed run.
    """
    try:
        return thunk()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {
            "RAISED": type(exc).__name__,
            "message": str(exc)[:400],
            "where": label,
            "tb": traceback.format_exc(limit=3)[-400:],
        }


def _clock_facts(user_id):
    """Record what decides whether the two axes coincide at all.

    Printed into the dump rather than left to the reader's memory, because it
    is the only thing that tells a legitimate move from a regression: the two
    axes agree exactly when the cadence is 14 AND the read day opens a period,
    and differ by up to a cadence otherwise.
    """
    ctx = BalanceContext.build(user_id)
    calendar = calendar_for(user_id)
    covering = calendar.span_containing(ctx.as_of)
    return {
        "as_of": ctx.as_of.isoformat(),
        "cadence_days": calendar.cadence_days,
        "saved_paydays": len(calendar.periods),
        "opening_bound": _plain(calendar.opening_bound()),
        "horizon": _plain(calendar.horizon()),
        "as_of_opens_a_period": (
            covering is not None and covering.start_date == ctx.as_of
        ),
        "period_covering_as_of": _plain(covering),
    }


def _gap(user_id):
    """The /retirement gap picture and the axis it ran over.

    **Written to run on BOTH sides of this step**, which is what makes the diff
    meaningful.  The per-row identity is deliberately NOT captured: on the HEAD
    side ``ProjectedBalance.period_id`` is an ordinal fabricated by the deleted
    producer and on this side there is no such field at all, so the two are
    incomparable and a naive dump of them would report every row as moved while
    saying nothing about the money.  Rows are keyed POSITIONALLY -- what has to
    be explained is a moved figure at a given step of the walk.

    The axis itself is dumped where it exists (this side publishes it), because
    it is the value under test and a diff showing only dollars would leave the
    reader inferring the cause.
    """
    def build():
        picture = retirement_plan.picture_at(
            retirement_plan.load_retirement_inputs(
                BalanceContext.build(user_id),
            ),
            retirement_plan.STORED_PLAN,
        )
        axis = picture.axis
        return {
            # Absent on the HEAD side -- the key is new here.  Its absence in
            # the BEFORE file is the marker for which side is which.
            "axis": None if axis is None else {
                "len": len(axis),
                "head": _plain(list(axis)[:3]),
                "tail": _plain(list(axis)[-3:]),
            },
            "as_of": _plain(picture.as_of),
            # The NET-frame analysis.  The gross-frame one this dumped went
            # with ``compute_gap_data`` at plan step C2-f2d-2: it was computed
            # at the possibly-unset stored tax rate and reached no screen.
            "gap_analysis": _plain(picture.net),
            "planned_retirement_date": _plain(picture.retirement_date),
            "projections": [
                {
                    "account": _plain(proj["account"]),
                    "current_balance": _money(proj["current_balance"]),
                    "projected_balance": _money(proj["projected_balance"]),
                    "employee_per_period": _money(
                        proj["employee_per_period"],
                    ),
                    "employer_per_period": _money(
                        proj["employer_per_period"],
                    ),
                    "rows": len(proj["projection_rows"]),
                    # Every tenth row, so a diff localises WHERE the two
                    # curves part rather than only that they did.
                    "row_sample": [
                        {
                            "step": step * 10,
                            "end_balance": _money(row.end_balance),
                            "contribution": _money(row.contribution),
                            "employer": _money(row.employer_contribution),
                            "ytd": _money(row.ytd_contributions),
                        }
                        for step, row in enumerate(
                            proj["projection_rows"][::10],
                        )
                    ],
                }
                for proj in picture.projections
            ],
        }
    return _guard("gap", build)


def _readiness(user_id):
    """The readiness verdict, its two chart series and its countdown."""
    return _plain(_guard(
        "readiness",
        lambda: retirement_readiness.readiness_from_picture(
            retirement_plan.picture_at(
                retirement_plan.load_retirement_inputs(
                    BalanceContext.build(user_id),
                ),
                retirement_plan.STORED_PLAN,
            ),
        ),
    ))


def _levers(user_id):
    """Both lever solvers -- the contribution annuity and the retire-later bisect."""
    return _plain(_guard(
        "levers", lambda: retirement_levers.compute_lever_data(
            retirement_plan.load_retirement_inputs(
                BalanceContext.build(user_id),
            ),
        ),
    ))


def _horizon(user_id):
    """The /savings cockpit's long-horizon band composition and milestones."""
    def build():
        net_worth = savings_dashboard_service.compute_dashboard_data(
            BalanceContext.build(user_id),
        )["net_worth"]
        return _plain(net_worth.horizon)
    return _guard("horizon", build)


def _growth_charts(user_id, accounts):
    """Every investment account's growth chart at three slider positions."""
    charts = {}
    for account in accounts:
        for years in _HORIZON_YEARS:
            charts[f"{account.id}:{years}y"] = _plain(_guard(
                f"chart:{account.id}:{years}",
                lambda a=account, y=years: compute_growth_chart_data(
                    user_id, a, y, None,
                ),
            ))
    return charts


def _property_charts(user_id, accounts):
    """Every Property's equity chart -- the second consumer of the rate formula.

    Not an axis consumer, and captured anyway: plan step C2-e replaced the
    duck-typed period parameter this chart fabricated an ``_AppreciationSpan``
    to satisfy, so its value line is the one place a signature change could
    move a figure with no axis involved.  It must diff clean on every database.
    """
    def build(account):
        ctx = BalanceContext.build(user_id)
        equity = home_equity_service.resolve_home_equity(account, ctx)
        return property_equity_chart.build_property_equity_chart(
            balance_at.secured_loan_series(account, ctx),
            equity.market_value,
            account.asset_appreciation_params.annual_appreciation_rate,
            ctx.as_of,
        )
    return {
        str(account.id): _plain(_guard(
            f"property:{account.id}", lambda a=account: build(a),
        ))
        for account in accounts
    }


def _dump_user(user_id):
    """Every projection figure for one owner."""
    accounts = (
        db.session.query(Account)
        .filter(Account.user_id == user_id, Account.is_active.is_(True))
        .order_by(Account.id)
        .all()
    )
    investments = [
        account for account in accounts
        if classify_account(account) is AccountProjectionKind.INVESTMENT
    ]
    properties = [
        account for account in accounts
        if classify_account(account) is AccountProjectionKind.APPRECIATING
    ]
    return {
        "clock": _guard("clock", lambda: _clock_facts(user_id)),
        "gap": _gap(user_id),
        "readiness": _readiness(user_id),
        "levers": _levers(user_id),
        "horizon": _horizon(user_id),
        "growth_charts": _growth_charts(user_id, investments),
        "property_charts": _property_charts(user_id, properties),
    }


def main():
    """Dump every projection figure for every user to the named JSON file."""
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    app = create_app()
    with app.app_context():
        payload = {
            str(user.id): _dump_user(user.id)
            for user in db.session.query(User).order_by(User.id).all()
        }
    with open(sys.argv[1], "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    print(f"wrote {sys.argv[1]}")


if __name__ == "__main__":
    main()
