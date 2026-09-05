"""R7d-d: prove the Recurring surface renders the same characters it did.

Dumps what the ``/templates`` producer publishes for every recurring definition
on the database this is pointed at -- the cadence phrase, the stop line, the
next date and the monthly equivalent -- plus, for a definition paying into a
configured loan, the stored column beside the resolver's own answer.  Run it on
``origin/dev`` and again on the branch and diff the two files.  An empty diff
is the claim that no rendered character moved, measured rather than argued.
Where it is NOT empty, the moved rows must be loan payments whose stored bound
is NULL or LATER than the loan's closing date -- the only rows the composed
value changes, since the stored copy still binds where it is the earlier date
until plan step R7d-g deletes it -- and each such row is the step working; a
moved row of any other kind is a regression.

Usage (from a checkout, against a production clone)::

    DATABASE_URL=... python tests/manual/verify_recurring_surface_stops.py OUT.txt

**It must COMPILE AND RUN ON BOTH SIDES**, which is the whole point of a
before/after harness.  ``build_view`` takes ``(calendar, as_of)`` before this
step and a ``BalanceContext`` after, and ``loan_payment_window`` gains the
resolved recurrence as a parameter, so a script written against either
signature measures one side and crashes on the other -- and a harness that only
runs on the branch proves nothing about what changed.  Each adaptation is a
single :func:`inspect.signature` read (:func:`_build`, :func:`_stop`), which
states why the call shape is computed rather than written out twice.

It reads only: it opens no transaction and writes nothing.
"""
import inspect
import sys
from datetime import date

from app import create_app
from app.extensions import db
from app.enums import TxnTypeEnum
from app import ref_cache
from app.models.account import Account
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import recurring_view
from app.services.balance_at import BalanceContext
from app.services.loan_recurrence_sync import loan_payment_window
from app.services.obligations_aggregator import template_rule
from app.services.recurrence import resolved_recurrence

#: Pinned so two runs on two checkouts measure the same day.  The surface's
#: next date and its expired-rule filter are measured from the pass's as-of, so
#: an unpinned clock would make the two sides differ for a reason unrelated to
#: the change under test.
AS_OF = date(2026, 9, 2)


def _stop(template, ctx):
    """Ask the resolver about *template* through whichever signature it has.

    ``loan_payment_window`` takes ``(template, ctx)`` before plan step R7d-d
    and ``(template, resolved, ctx)`` after it -- the door resolves the rule
    once and hands the value down -- so, like :func:`_build`, the call is one
    call through a computed shape rather than two literal calls of which one
    cannot compile on the checked-out side.

    **The keyword set is COMPUTED, for the reason :func:`_build`'s is**: a
    literal call in either shape is an arity error against the other side's
    signature, and ``pylint tests/manual/`` refuses it there -- including a
    star-args list, whose length it infers.

    Args:
        template: A transfer template.
        ctx: The read pass.

    Returns:
        The resolver's answer, or ``None`` when the definition does not repeat
        or the owner has no pay periods (the door's own two ``None`` states,
        answered before the resolver is asked on the after side).
    """
    kwargs = {"template": template, "ctx": ctx}
    if "resolved" in inspect.signature(loan_payment_window).parameters:
        rule = template_rule(template)
        if rule is None:
            return None
        resolved = resolved_recurrence(rule, ctx.calendar())
        if resolved is None:
            return None
        kwargs["resolved"] = resolved
    return loan_payment_window(**kwargs)


def _build(templates_by_kind, ctx):
    """Call the surface producer through whichever signature it declares.

    **ONE call through a COMPUTED keyword set, deliberately.**  Writing the two
    signatures as two literal calls is what a first draft did, and
    ``pylint tests/manual/`` refused it -- correctly, and on BOTH sides: the
    branch that does not match the checked-out signature is a call with a
    keyword the function does not take, which is exactly the rot that gate
    exists to catch.  There is no spelling of a both-sides harness that names
    both keyword sets literally and passes, so the set is genuinely computed
    and the call is genuinely one.

    Args:
        templates_by_kind: ``(income, expense, transfer)`` template lists.
        ctx: The read pass.  Its ``calendar()`` and ``as_of`` are what the
            pre-R7d-d signature wanted, so one value answers for both.

    Returns:
        The ``RecurringView``.
    """
    income, expense, transfer = templates_by_kind
    kwargs = {
        "income_templates": income,
        "expense_templates": expense,
        "transfer_templates": transfer,
    }
    takes = inspect.signature(recurring_view.build_view).parameters
    if "ctx" in takes:
        kwargs["ctx"] = ctx
    else:
        kwargs["calendar"] = ctx.calendar()
        kwargs["as_of"] = ctx.as_of
    return recurring_view.build_view(**kwargs)


def _rows(view):
    """Yield ``(kind, row)`` for every row the view publishes.

    Args:
        view: The ``RecurringView``.

    Yields:
        ``(kind, RecurringRow)`` pairs.
    """
    for kind, section in (
        ("income", view.income),
        ("expense", view.expenses),
        ("transfer", view.transfers),
    ):
        for row in section.rows:
            yield kind, row


def _dump_user(user_id, income_id):
    """Return every line the surface publishes for one owner, as of ``AS_OF``.

    One owner per call so :func:`main` stays a loop over owners rather than a
    function holding every per-owner local at once.

    Args:
        user_id: The owner.
        income_id: The ``ref`` id of the INCOME transaction type, resolved once
            by the caller.

    Returns:
        The lines for this owner, in a stable order.
    """
    lines = [f"### user {user_id}"]
    ctx = BalanceContext.build(user_id, AS_OF)
    txns = (
        db.session.query(TransactionTemplate)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(TransactionTemplate.id).all()
    )
    xfers = (
        db.session.query(TransferTemplate)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(TransferTemplate.id).all()
    )
    view = _build(
        (
            [t for t in txns if t.transaction_type_id == income_id],
            [t for t in txns if t.transaction_type_id != income_id],
            xfers,
        ),
        ctx,
    )
    for kind, row in _rows(view):
        described = row.recurrence
        lines.append(
            f"{kind} tmpl={row.template.id} {row.template.name!r} "
            f"cadence={None if described is None else described.cadence!r} "
            f"stops={None if described is None else described.stops!r} "
            f"next={row.next_date} monthly={row.equivalent.monthly}"
        )
    lines.append(
        f"band net={view.band.net.monthly} "
        f"income={view.band.income.monthly} "
        f"expenses={view.band.expenses.monthly} "
        f"transfers={view.band.transfers_out.monthly}"
    )
    for template in xfers:
        stop = _stop(template, ctx)
        if stop is None:
            continue
        rule = template_rule(template)
        lines.append(
            f"STOP tmpl={template.id} {template.name!r} "
            f"stored_end={rule.end_date} derived={stop!r}"
        )
    return lines


def main(out_path):
    """Write every Recurring-surface stop line on this database to *out_path*.

    Args:
        out_path: The file to write.
    """
    app = create_app()
    lines = []
    with app.app_context():
        income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        user_ids = [
            row[0] for row in db.session.query(Account.user_id).distinct().all()
        ]
        for user_id in sorted(user_ids):
            lines.extend(_dump_user(user_id, income_id))
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"{len(lines)} lines -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1])
