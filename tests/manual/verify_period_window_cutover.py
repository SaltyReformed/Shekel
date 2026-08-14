"""Dump every figure the RETIRED pay-period readers decided, for a HEAD-vs-post diff.

The regression harness for pay-calendar plan step **C2-f1**, and it exists
because none of the five in ``docs/plans/verification.md`` can see this change.
``verify_balance_baseline`` walks the ``balance_at`` seam and none of these
surfaces is in it; ``verify_savings_producers`` reads the savings package;
``verify_anchor_surfaces`` reads the anchor figures; ``verify_projection_axis``
reads the FORWARD projections, and every figure here is retrospective;
``verify_render_surfaces`` reads status codes and body sizes, so it can tell
that a page still renders and nothing about what it says.  Running any of them
over this leaf would report "nothing moved" while saying nothing about the four
producers it changes, which is the free-pass shape standard 3 asks about.

It answers *did anything move*, never *is the answer right*.  The proof that
each replacement is correct is the suite's hand-computed cases and the three
negative controls this step showed firing; this is the exhaustive regression
check beside them.

**What it covers, and why each is here.**

* ``calendar_service.get_month_detail`` / ``get_year_overview`` -- the two
  surfaces whose PERIOD SELECTION moved from the stored ``end_date`` to the
  derived one.  This is the harness's whole reason: an adversarial review of
  this leaf found that selection had moved while PLACEMENT still read
  ``txn.pay_period``'s stored span, so a row could be clamped into a month
  whose selection had excluded its period -- absent from the day cells AND
  from the month's totals, silently.  The fix threads one value into both, and
  a day-by-day dump is what shows no figure moved on real data.
* ``spending_report_service.compute_spending_report`` -- its window resolution
  moved onto the same calendar, and its trailing series resolves thirteen
  windows, so an off-by-one period at a window edge moves a bar.
* ``dashboard_pulse_service.compute_pulse_section`` -- the hero's
  next-paycheck caption and the still-due panel's next-period range now come
  from ONE value where they were two queries.
* ``companion_service`` + the companion navigation -- the prev/next links.

**BYTE-IDENTITY IS THE GATE HERE**, which is the opposite of
``verify_projection_axis``' standing and worth stating plainly: every
replacement in this leaf is claimed EQUAL to the query it replaces on any
schedule whose stored columns match the derivation, and
``pay_period_write`` has materialised that derivation on every write since
plan step C3-b.  A moved line is therefore either a stored/derived
disagreement on this database -- itself the finding -- or a defect.  Print
``derived_vs_stored`` below before reading the diff: it says whether this
database can express a disagreement at all.

**Usage** (from the repository root, against a production CLONE)::

    DATABASE_URL=postgresql://.../shekel_dev \\
        .venv/bin/python tests/manual/verify_period_window_cutover.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_dev \\
        .venv/bin/python tests/manual/verify_period_window_cutover.py after.json
    diff before.json after.json

For the HEAD side use ``git worktree add`` -- never ``git checkout``, which
reverts the working tree and discards the change under test
(``docs/plans/lessons.md``).

**RUN BOTH SIDES ON THE SAME CIVIL DAY.**  The pulse producer and the
companion view read the clock, so a BEFORE captured yesterday and an AFTER
captured today differ by the calendar rather than by the change.

## What it measured at C2-f1 (2026-08-14)

Against a clone of production migrated to ``dev``'s head -- one owner with 61
paydays at cadence 14, one companion with none; **0 end mismatches and 0 index
mismatches**, so the stored columns and the derivation agree everywhere.  35
months, 4 year overviews and 100 spending windows per side, 0 raises:
**byte-identical**.

**And byte-identity on THAT database proves less than it looks, which is why
the run below exists.**  Where stored and derived agree, a defect that turns on
their disagreement moves nothing -- the shape ``docs/plans/lessons.md`` records
as a baseline that cannot see the window the change lives in.  So the
disagreement was PLANTED (the last period's stored end pushed from 2028-07-26
to 2028-08-20 -- plan finding **P12**'s live shape -- with a ``$1,234.56`` row
due 2028-08-10 inside the extension) and three variants dumped:

======================= =============== =============== ==========
variant                 Jul 2028        Aug 2028        Jul + Aug
======================= =============== =============== ==========
HEAD (stored/stored)    ``$4,206.30``   ``$1,234.56``   ``$5,440.86``
this leaf (derived/     ``$5,440.86``   ``$0.00``       ``$5,440.86``
derived)
the REVIEW's defect     ``$4,206.30``   ``$0.00``       ``$4,206.30``
(derived/stored)
======================= =============== =============== ==========

HEAD renders the row in August and this leaf in July -- the derivation being
believed, which is the arc's whole point, and **the money is conserved**.  The
middle state, which this leaf's first cut shipped (selection moved to the
derived span, placement left on ``txn.pay_period``), renders it in NEITHER:
``$1,234.56`` gone from the day cells and from both months' totals, silently.
That is the firing control for the fix, and it is why placement takes the span
the selection used rather than reading the row's own.
"""

import json
import sys
import traceback
from datetime import date
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.user import User
from app.services import (
    calendar_service,
    companion_service,
    dashboard_pulse_service,
    spending_report_service,
)
from app.services.pay_calendar import calendar_for
from app.services.spending_report_service import SpendingWindow

#: How many months either side of each owner's own schedule to walk.  The
#: window that matters is the one containing the LAST period, whose derived end
#: is the only one that can move (every other end is dictated by the next
#: payday), so the span is anchored on the schedule rather than on today.
_MONTHS_AROUND_SCHEDULE = 3


def _money(value):
    """Stringify a Decimal so the diff is textual and exact."""
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

    A producer that raises for one owner must not hide the rest, and the raise
    is itself a fact worth diffing: this leaf moves three surfaces onto
    ``calendar_for``, which REFUSES an owner whose cadence cannot define a
    calendar (plan finding **P8**) where the retired SQL answered rows.  So
    "raised here, answered there" has to show up as a move rather than as a
    crashed run.
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


def _derived_vs_stored(user_id):
    """Report whether this database can express a stored/derived disagreement.

    **Read this before reading the diff.**  Every equality this leaf claims
    holds on a schedule whose stored ``end_date`` and ``period_index`` equal
    the derivation over the owner's paydays.  Where they disagree the two sides
    legitimately differ, and that disagreement is plan finding **P1** rather
    than a regression -- but a reader cannot tell which without this count.
    """
    calendar = calendar_for(user_id)
    derived = {p.period_id: p for p in calendar.periods}
    rows = (
        db.session.query(PayPeriod)
        .filter(PayPeriod.user_id == user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )
    end_mismatch = [
        {
            "period_id": row.id,
            "stored_end": row.end_date.isoformat(),
            "derived_end": derived[row.id].end_date.isoformat(),
        }
        for row in rows
        if row.id in derived and row.end_date != derived[row.id].end_date
    ]
    index_mismatch = [
        {
            "period_id": row.id,
            "stored_index": row.period_index,
            "derived_index": derived[row.id].period_index,
        }
        for row in rows
        if row.id in derived and row.period_index != derived[row.id].period_index
    ]
    return {
        "saved_paydays": len(rows),
        "cadence_days": calendar.cadence_days,
        "end_mismatches": end_mismatch,
        "index_mismatches": index_mismatch,
    }


def _month_span(user_id):
    """Return the ``(year, month)`` pairs to walk for this owner.

    Anchored on the SCHEDULE rather than on today: the only end that can move
    is the last period's (every other is dictated by its successor's payday),
    so the months around it are where a selection change would show.
    """
    calendar = calendar_for(user_id)
    if not calendar.periods:
        return []
    first = calendar.periods[0].start_date
    last = calendar.periods[-1].end_date
    months = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    # Pad both ends so a period whose selection moves OUT of the schedule's
    # own months still lands in a walked one.
    for _ in range(_MONTHS_AROUND_SCHEDULE):
        y, m = months[0]
        months.insert(0, (y - 1, 12) if m == 1 else (y, m - 1))
        y, m = months[-1]
        months.append((y + 1, 1) if m == 12 else (y, m + 1))
    return months


def _calendar_figures(user_id):
    """Dump every month detail and year overview across the owner's schedule."""
    months = _month_span(user_id)
    years = sorted({year for year, _ in months})
    today = date.today()
    return {
        "month_detail": {
            f"{year:04d}-{month:02d}": _guard(
                f"get_month_detail {year}-{month}",
                lambda year=year, month=month: _plain(
                    calendar_service.get_month_detail(
                        user_id, year, month, today=today,
                    ),
                ),
            )
            for year, month in months
        },
        "year_overview": {
            str(year): _guard(
                f"get_year_overview {year}",
                lambda year=year: _plain(
                    calendar_service.get_year_overview(user_id, year),
                ),
            )
            for year in years
        },
    }


def _spending_figures(user_id):
    """Dump the spending report over every window type the surface offers."""
    months = _month_span(user_id)
    years = sorted({year for year, _ in months})
    periods = calendar_for(user_id).periods
    windows = [
        SpendingWindow(window_type="month", month=month, year=year)
        for year, month in months
    ] + [
        SpendingWindow(window_type="year", year=year) for year in years
    ] + [
        SpendingWindow(window_type="pay_period", period_id=period.period_id)
        for period in periods
    ]
    return {
        f"{w.window_type}:{w.year}:{w.month}:{w.period_id}": _guard(
            f"spending {w}",
            lambda w=w: _plain(
                spending_report_service.compute_spending_report(user_id, w),
            ),
        )
        for w in windows
    }


def _pulse_figures(user_id):
    """Dump the pulse region, whose next-paycheck answers were two queries."""
    return _guard(
        "compute_pulse_section",
        lambda: _plain(dashboard_pulse_service.compute_pulse_section(user_id)),
    )


def _companion_figures(user_id):
    """Dump the companion period set, whose navigation was two ordinal queries."""
    return _guard(
        "get_companion_periods",
        lambda: _plain(companion_service.get_companion_periods(user_id)),
    )


def main(out_path):
    """Write the dump for every user in the database to *out_path*."""
    app = create_app("production")
    with app.app_context():
        users = db.session.query(User).order_by(User.id).all()
        dump = {
            str(user.id): {
                "email": user.email,
                "derived_vs_stored": _guard(
                    "derived_vs_stored",
                    lambda uid=user.id: _derived_vs_stored(uid),
                ),
                "calendar": _guard(
                    "calendar", lambda uid=user.id: _calendar_figures(uid),
                ),
                "spending": _guard(
                    "spending", lambda uid=user.id: _spending_figures(uid),
                ),
                "pulse": _pulse_figures(user.id),
                "companion": _companion_figures(user.id),
            }
            for user in users
        }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(dump, handle, indent=2, sort_keys=True)
    print(f"wrote {out_path} for {len(dump)} user(s)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    main(sys.argv[1])
