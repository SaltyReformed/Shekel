"""Dump every figure the BUDGET DASHBOARD publishes, for a HEAD-vs-post diff.

The regression harness for pay-calendar plan step **C2-f2e**, which moves ``/``
and its two HTMX fragments onto the route's one read pass and off
``pay_period_service``'s two surviving readers.

It answers *did anything move*, never *is the answer right*.  The proof that
each replacement is correct is the suite's hand-computed cases and the gates
this step showed firing on the merge base; this is the exhaustive regression
check beside them.

**Why none of the existing harnesses covers this.**
``verify_period_window_cutover`` dumps ``compute_pulse_section`` and nothing
else on this page -- not the tracks tier, not the hero fragment -- and it was
written for C2-f1's next-paycheck question.  ``verify_savings_producers`` reads
the ``/savings`` package (which the tracks tier delegates to, but through a
reshaping this file is the only one that sees).  ``verify_anchor_surfaces``
reads the anchor figures on both dashboard producers, so it overlaps the hero's
``last_updated_date`` and nothing else.  ``verify_render_surfaces`` reads status
codes and body sizes, so it can tell that ``/`` still renders and nothing about
what it says.

**What it covers, and why each is here.**

* ``compute_pulse_section`` -- the whole region dict, WHOLE.  Every one of its
  eight keys changes basis at this step: the hero's period dates and the
  street's day span now come from a DERIVED period rather than the stored
  ``start_date`` / ``end_date`` span; the chart, the trough and the peak read
  the pass's ``reported_periods()`` rather than ``get_all_periods``; the
  still-due totals bucket by ``DerivedPeriod.period_id``; and the street
  marker, each bill's ``days_until_due`` and the hero's staleness flag all read
  the pass's pinned day instead of three separate ``date.today()`` calls.
* ``compute_tracks_section`` -- the position tier.  Its producers did not
  change, but the READ PASS it hands them did: it opened one of its own and now
  takes the route's, so a loan resolved for the pulse region and the same loan
  in the debt track are one resolution rather than two that agreed.
* ``compute_balance_section`` -- the anchor editor's revert fragment.  Its
  period moved from ``get_current_period`` (SQL over the stored span, with its
  own clock) to the pass's calendar, so this is the surface where a
  stored/derived disagreement would show as a moved DOLLAR figure rather than a
  moved date.

**BYTE-IDENTITY IS THE GATE HERE.**  Every replacement in this leaf is claimed
EQUAL to the query it replaces on any schedule whose stored columns match the
derivation, and ``pay_period_write`` has materialised that derivation on every
write since plan step C3-b.  A moved line is therefore either a stored/derived
disagreement on this database -- itself the finding -- or a defect.  Print
``derived_vs_stored`` below before reading the diff: it says whether this
database can express a disagreement at all.  Measured on the dev clone
2026-08-18: one owner, 62 paydays at cadence 14, **0 end mismatches and 0 index
mismatches**, so byte-identity is the honest expectation and any diff is a
defect.

**It COMPILES AND RUNS ON BOTH SIDES**, which is what
``docs/plans/lessons.md`` asks of a before/after harness and what the three
producers' changed signatures would otherwise have prevented: before this step
they took a ``user_id`` and lived in ``dashboard_pulse_service`` /
``dashboard_service``; after it they take a read pass or a resolved section and
live in the ``dashboard_service`` package.  :func:`_producers` resolves both
spellings at import, so ONE file dumps both trees.

**Usage** (from the repository root, against a production CLONE)::

    DATABASE_URL=postgresql://.../shekel_dev \\
        .venv/bin/python tests/manual/verify_dashboard_cutover.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_dev \\
        .venv/bin/python tests/manual/verify_dashboard_cutover.py after.json
    diff before.json after.json

For the HEAD side use ``git worktree add`` -- never ``git checkout``, which
reverts the working tree and discards the change under test
(``docs/plans/lessons.md``).

**RUN BOTH SIDES ON THE SAME CIVIL DAY.**  Every producer here places "today":
the pulse region resolves the period containing it, the street marks it, and
each bill counts days to its due date from it.  A BEFORE captured yesterday and
an AFTER captured today differ by the calendar rather than by the change.

## What it measured at C2-f2e (2026-08-18)

Against a clone of production migrated to ``dev``'s head -- one owner with 62
paydays at cadence 14 and one companion with none; **0 end mismatches and 0
index mismatches** for both, so the database cannot express a stored/derived
disagreement and byte-identity is the honest expectation.

**The dumps are BYTE-IDENTICAL**: ``diff before.json after.json`` is empty
across both owners, all three producers, and all eight keys of the pulse
region.  The owner's hero reads ``$752.62`` at the end of the period
2026-08-13 .. 2026-08-26 with the next paycheck on 2026-08-27, the revert
fragment reads the same ``$752.62``, and the tracks tier carries one goal --
before and after alike.  The companion, who has no pay periods and no
resolvable account, gets ``None`` from the pulse region and ``{"hero": None}``
from the fragment on both sides, so the degraded arm is covered rather than
merely unexercised.

The BEFORE side was captured from a ``git worktree`` at ``5ab457b7``, on the
same civil day, with ``PYTHONPATH=.`` (these harnesses are run from the
repository root, which the shell does not add for a script under
``tests/manual/``).
"""

import importlib
import json
import sys
from datetime import date
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.user import User
from app.services.balance_at import BalanceContext


def _producers():
    """Resolve the three dashboard producers on EITHER side of C2-f2e.

    Before the step the pulse and tracks producers lived in
    ``dashboard_pulse_service`` and took a ``user_id``; the hero fragment lived
    in ``dashboard_service`` and took one too.  After it all three live in the
    ``dashboard_service`` package, the tracks tier takes a
    :class:`~app.services.balance_at.BalanceContext`, and the other two take a
    resolved ``DashboardSection``.

    Discriminated on ``resolve_section`` -- the function the step ADDS -- rather
    than on a module name or an argument count: a name is what the step also
    moved, and an argument count would not tell a section from a context.

    Returns:
        A ``{name: callable}`` dict of three one-argument callables, each
        taking a ``user_id`` and returning the producer's output.
    """
    # Pylint: ``import-outside-toplevel`` -- deliberate: WHICH module carries
    # these producers is exactly what this function is deciding, and deciding
    # it at module scope would make the file fail to import on one side of the
    # step it exists to measure.
    from app.services import dashboard_service  # pylint: disable=import-outside-toplevel

    if hasattr(dashboard_service, "resolve_section"):
        def pulse(user_id):
            return dashboard_service.compute_pulse_section(
                dashboard_service.resolve_section(
                    BalanceContext.build(user_id),
                ),
            )

        def tracks(user_id):
            return dashboard_service.compute_tracks_section(
                BalanceContext.build(user_id),
            )

        def balance(user_id):
            return dashboard_service.compute_balance_section(
                dashboard_service.resolve_section(
                    BalanceContext.build(user_id),
                ),
            )

        return {"pulse": pulse, "tracks": tracks, "balance": balance}

    # Resolved by NAME rather than imported: the pre-step module does not
    # exist on the post-step tree, and a static import of it would make this
    # file unimportable there -- which is the same failure, in the other
    # direction, that the branch above avoids.
    pre_step = importlib.import_module("app.services.dashboard_pulse_service")
    return {
        "pulse": pre_step.compute_pulse_section,
        "tracks": pre_step.compute_tracks_section,
        "balance": dashboard_service.compute_balance_section,
    }


def _plain(value):
    """Render *value* as JSON-comparable plain data, losslessly for money.

    ``Decimal`` becomes its own string (never a float -- a float round-trip is
    exactly the precision loss this project's money rules exist to prevent),
    ``date`` its ISO form, and any dataclass or ORM-ish object its public
    attribute map, so a producer that returns a value object dumps its fields
    rather than a repr carrying a memory address.

    Args:
        value: Anything a producer returned.

    Returns:
        The plain-data equivalent.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=str)}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {
            name: _plain(getattr(value, name))
            for name in sorted(vars(value))
            if not name.startswith("_")
        }
    return value


def _guard(label, thunk):
    """Run *thunk*, recording a raise as data rather than aborting the dump.

    A producer that RAISES for one owner is a finding, and a harness that dies
    on it reports nothing about the other owners.  The exception's type and
    message are dumped, so a raise that appears or disappears across the change
    shows up in the diff like any other moved line.

    Args:
        label: What is being measured, for the error line.
        thunk: A zero-argument callable.

    Returns:
        The thunk's plain-data result, or ``{"error": "..."}``.
    """
    try:
        return thunk()
    # Pylint: ``broad-exception-caught`` -- deliberate, and rule 4 is satisfied
    # by REPORTING rather than swallowing: the raise lands in the dump, where a
    # raise that appears or disappears across the change shows in the diff.  A
    # narrower clause would need this harness to predict which exception each
    # producer can throw, which is the thing under measurement.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {
            "RAISED": type(exc).__name__,
            "message": str(exc)[:400],
            "where": label,
        }


def _derived_vs_stored(user_id):
    """Count where this owner's stored period columns disagree with the paydays.

    The premise the byte-identity gate rests on, asserted rather than assumed
    (``docs/plans/lessons.md``): if every stored ``end_date`` equals the day
    before the next payday and every stored ``period_index`` equals the payday
    ordinal, then the retired readers and the calendar answer the same period
    for every day, and any moved line below is a defect.  A non-zero count here
    is itself the finding and the diff must be read against it.

    Args:
        user_id: The owner to count for.

    Returns:
        A dict with ``periods``, ``end_mismatch`` and ``index_mismatch``.
    """
    rows = (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )
    end_mismatch = sum(
        1 for earlier, later in zip(rows, rows[1:])
        if (later.start_date - earlier.end_date).days != 1
    )
    index_mismatch = sum(
        1 for ordinal, row in enumerate(rows) if row.period_index != ordinal
    )
    return {
        "periods": len(rows),
        "end_mismatch": end_mismatch,
        "index_mismatch": index_mismatch,
    }


def main(out_path):
    """Write the dump for every user in the database to *out_path*.

    Args:
        out_path: Where to write the JSON dump.
    """
    app = create_app("production")
    with app.app_context():
        producers = _producers()
        users = db.session.query(User).order_by(User.id).all()
        dump = {
            str(user.id): {
                "email": user.email,
                "derived_vs_stored": _guard(
                    "derived_vs_stored",
                    lambda uid=user.id: _derived_vs_stored(uid),
                ),
                **{
                    name: _guard(
                        f"{name} for user {user.id}",
                        lambda fn=fn, uid=user.id: _plain(fn(uid)),
                    )
                    for name, fn in producers.items()
                },
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
