"""Dump every figure plan step C2-f2d-1 re-routes, for a HEAD-vs-post diff.

The byte-identity gate for the READ-PASS leaf.  That leaf changes WHERE the
``/retirement`` and ``/savings`` producers get their
:class:`~app.services.balance_at.BalanceContext` -- the object pinning the
owner, the baseline scenario and the day for one render -- and nothing about
what any of them computes.  So every figure here has to come back identical,
and a single moved cent is a defect rather than an expected consequence.

**Why the existing harnesses cannot grade it.**
:mod:`tests.manual.verify_reader_baseline` dumps
``savings_dashboard_service.compute_dashboard_data`` and
``retirement_projection.project_retirement_accounts`` -- but the ``/retirement``
PAGE is neither of those.  It is ``compute_gap_data`` beside
``compute_lever_data``, and the leaf's whole subject is that those two open two
separate read passes; a harness that calls neither would report "nothing moved"
over the surface that actually changed, which is
``docs/plans/verification.md`` standard 3's free-pass shape.
:mod:`tests.manual.verify_balance_baseline` walks the seam, which this leaf does
not touch at all.

**It runs on BOTH sides of the cutover.**  The producers' signatures change --
they take the read pass and drop their ``user_id`` parameter -- so every call
goes through :func:`_call`, which reads the callee's own signature and passes
whichever the tree in front of it declares.  A harness that compiles on only
one side proves nothing (``docs/plans/lessons.md``).

**Usage** (from the repository root, against a production CLONE)::

    DATABASE_URL=postgresql://.../shekel_c2f2d \\
        .venv/bin/python tests/manual/verify_retirement_pass_cutover.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_c2f2d \\
        .venv/bin/python tests/manual/verify_retirement_pass_cutover.py after.json
    diff before.json after.json

**RUN BOTH SIDES ON THE SAME CIVIL DAY.**  Every producer here seeds from the
process clock, so a BEFORE captured yesterday and an AFTER captured today differ
by the calendar rather than by the change.  The clock is deliberately NOT pinned:
pinning it would hide the very thing the leaf is about, because on the HEAD side
each producer reads the clock for itself and a pinned harness would paper over
the second read.  The run STAMPS the day it captured
(:data:`_CAPTURED_ON`) so a diff across midnight is visible in the first line of
the file rather than inferred from surprise.

**It also counts the read passes**, which is the leaf's END STATE rather than
its regression gate: ``/savings`` and ``/retirement`` must each open exactly one.
That count is expected to MOVE (2 -> 1), so it lives under its own
``pass_counts`` key -- a diff that shows the figures identical and the counts
dropping is exactly the result this leaf claims.

Every figure is stringified through :func:`_money` so a ``Decimal`` diff is a
TEXT diff: ``Decimal("1.10")`` and ``Decimal("1.1")`` are equal numerically and
must not be reported as a move, while ``1.10`` -> ``1.11`` must be.
"""

import inspect
import json
import pathlib
import sys
import traceback
from datetime import date
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.user import User
from app.services import (
    retirement_dashboard_service,
    retirement_levers,
    retirement_readiness,
    savings_dashboard_service,
)
from app.services.balance_at import BalanceContext

# The day this capture ran.  Written into the dump so a diff taken across
# midnight names its own cause instead of reading as a defect.
_CAPTURED_ON = date.today()

# The what-if the readiness fragment is exercised at.  A merit horizon is used
# rather than an SWR or a return rate because it moves the SALARY PATH, so the
# override arm recomputes the pension, the income target and the projection --
# the widest of the three what-ifs, and the one whose second read pass this
# leaf collapses.
_MERIT_HORIZON_WHATIF = 7


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
    :mod:`tests.manual.verify_reader_baseline` records: a first draft of that
    harness named the fields it expected with ``getattr(obj, "...", None)`` and
    read ``None`` on both sides over three surfaces it had never captured.
    Walking the structure removes the guess.

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

    A producer that refuses for one owner must not hide every figure for the
    others, and the refusal itself is a fact worth diffing: a reader that starts
    raising where it used to answer has to show up as a MOVE rather than as a
    crashed run.  The clone carries an owner with no pay schedule and no
    baseline scenario, so this arm is live rather than defensive.
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


def _call(producer, user_id, **kwargs):
    """Call *producer* on either side of the read-pass cutover.

    BEFORE the leaf a producer takes ``user_id`` and builds its own read pass;
    AFTER it takes ``balance_ctx`` and builds none.  This reads the callee's
    own signature and supplies whichever it declares, so ONE harness file
    grades both trees -- which is what makes the two captures comparable at
    all.  A signature carrying neither name is a producer this harness was not
    written for and says so rather than guessing.
    """
    parameters = inspect.signature(producer).parameters
    if "balance_ctx" in parameters:
        return producer(BalanceContext.build(user_id), **kwargs)
    if "user_id" in parameters:
        return producer(user_id, **kwargs)
    raise TypeError(
        f"{producer.__module__}.{producer.__qualname__} takes neither "
        f"'user_id' nor 'balance_ctx'; this harness cannot call it",
    )


def _retirement(user_id):
    """Every figure the /retirement page and its what-if fragment publish.

    The page runs ``compute_gap_data`` and shapes the readiness picture from
    it, then runs ``compute_lever_data`` beside it; the fragment runs
    ``compute_readiness_whatif`` and, when a stepper moved, the levers again.
    All four are captured because all four opened a read pass of their own.
    """
    gap = _guard("gap", lambda: _call(
        retirement_dashboard_service.compute_gap_data, user_id,
    ))
    readiness = (
        gap if isinstance(gap, dict) and "RAISED" in gap
        else _guard("readiness", lambda: retirement_readiness
                    .readiness_from_gap_data(gap))
    )
    return {
        "gap_data": _plain(gap),
        "readiness": _plain(readiness),
        "levers": _plain(_guard("levers", lambda: _call(
            retirement_levers.compute_lever_data, user_id,
        ))),
        "whatif_baseline": _plain(_guard("whatif_baseline", lambda: _call(
            retirement_readiness.compute_readiness_whatif, user_id,
        ))),
        "whatif_override": _plain(_guard("whatif_override", lambda: _call(
            retirement_readiness.compute_readiness_whatif, user_id,
            merit_horizon_override=_MERIT_HORIZON_WHATIF,
        ))),
    }


def _savings(user_id):
    """The /savings page and both narrow producers the budget dashboard runs.

    ``compute_dashboard_data`` carries the Horizon range, which is where the
    second read pass on this page came from: its retirement / investment bands
    reuse the /retirement engine and that engine built its own context.  The two
    narrow producers are captured beside it because they share the loader whose
    calendar read this leaf routes through the pass.
    """
    return {
        "dashboard": _plain(_guard("savings_dashboard", lambda: (
            savings_dashboard_service.compute_dashboard_data(user_id)
        ))),
        "goal_progress": _plain(_guard("goal_progress", lambda: (
            savings_dashboard_service.compute_goal_progress(user_id)
        ))),
        "debt_summary": _plain(_guard("debt_summary", lambda: (
            savings_dashboard_service.compute_debt_summary(user_id)
        ))),
    }


def _count_passes(thunk):
    """Run *thunk*, returning how many read passes were opened inside it.

    Patched on the CLASS rather than on a module attribute, so a module holding
    its own imported reference to the name still routes through the counter --
    the very site this leaf deletes (``retirement_projection``) holds one.
    """
    counter = {"n": 0}
    real = BalanceContext.build.__func__

    def counting(cls, user_id, as_of=None):
        counter["n"] += 1
        return real(cls, user_id, as_of)

    BalanceContext.build = classmethod(counting)
    try:
        _guard("pass_count", thunk)
    finally:
        BalanceContext.build = classmethod(real)
    return counter["n"]


def _passes_below_route(user_id, producers):
    """Count the read passes *producers* open when the ROUTE already has one.

    The measurement that states this leaf's end state for ``/retirement``,
    whose route calls TWO independent producers: the honest question there is
    not "how many passes exist" but "how many does the render open BELOW the
    door".  The route's own pass is therefore built OUTSIDE the counter, and
    each producer is offered it -- which is what the route does after this leaf
    and what it cannot do before, because the parameter does not exist yet.

    BEFORE the leaf every producer ignores the offer and builds its own, so
    this counts one per producer.  AFTER it, every producer takes the one it
    was handed and this counts ZERO: the render's only pass is the one the
    route opened, which is visible in ``app/routes/retirement.py`` rather than
    inferred from a number.
    """
    route_pass = BalanceContext.build(user_id)

    def _offer(producer, **kwargs):
        if "balance_ctx" in inspect.signature(producer).parameters:
            return producer(route_pass, **kwargs)
        return producer(user_id, **kwargs)

    return _count_passes(lambda: [
        _offer(producer, **kwargs) for producer, kwargs in producers
    ])


def _pass_counts(user_id):
    """How many read passes each render opens, and where.

    Expected to MOVE with this leaf, which is why it is reported apart from the
    figures: the diff that proves the leaf is every FIGURE identical while
    these drop.

    ``/savings`` is counted whole rather than below-the-route because its route
    calls exactly ONE producer, so that producer is legitimately the render's
    door and opening the pass there is correct.  Its second pass came from the
    Horizon range reusing the ``/retirement`` engine, which built one of its
    own; the expected move is 2 -> 1, not 2 -> 0.
    """
    return {
        "retirement_page_below_route": _passes_below_route(user_id, [
            (retirement_dashboard_service.compute_gap_data, {}),
            (retirement_levers.compute_lever_data, {}),
        ]),
        "readiness_fragment_below_route": _passes_below_route(user_id, [
            (retirement_readiness.compute_readiness_whatif,
             {"merit_horizon_override": _MERIT_HORIZON_WHATIF}),
            (retirement_levers.compute_lever_data, {}),
        ]),
        "savings_page_total": _count_passes(lambda: (
            savings_dashboard_service.compute_dashboard_data(user_id)
        )),
    }


def _dump_user(user_id):
    """Every re-routed figure, and the pass counts, for one owner."""
    return {
        "retirement": _retirement(user_id),
        "savings": _savings(user_id),
        "pass_counts": _pass_counts(user_id),
    }


def main():
    """Dump every user's /retirement and /savings figures to the given path."""
    out_path = pathlib.Path(sys.argv[1])
    app = create_app()
    with app.app_context():
        blob = {"__captured_on__": _CAPTURED_ON.isoformat()}
        for user in db.session.query(User).order_by(User.id).all():
            blob[str(user.id)] = _dump_user(user.id)
    out_path.write_text(
        json.dumps(blob, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(f"wrote {out_path} ({len(blob) - 1} users, {_CAPTURED_ON})")


if __name__ == "__main__":
    main()
