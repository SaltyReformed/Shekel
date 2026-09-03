"""R7d-d: prove the Recurring surface renders the same characters it did.

Dumps what the ``/templates`` producer publishes for every recurring definition
on the database this is pointed at -- the cadence phrase, the stop line, the
next date and the monthly equivalent -- plus, for a definition paying into a
configured loan, the stored column beside the resolver's own answer.  Run it on
``origin/dev`` and again on the branch and diff the two files: an empty diff is
the equality claim, measured rather than argued.

Usage (from a checkout, against a production clone)::

    DATABASE_URL=... python tests/manual/verify_recurring_surface_stops.py OUT.txt

**It must COMPILE AND RUN ON BOTH SIDES**, which is the whole point of a
before/after harness.  ``build_view`` takes ``(calendar, as_of)`` before this
step and a ``BalanceContext`` after, so a script written against either
signature measures one side and crashes on the other -- and a harness that only
runs on the branch proves nothing about what changed.  The adaptation is a
single :func:`inspect.signature` read in :func:`_build`, which states why the
keyword set is computed rather than written out twice.

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

#: Pinned so two runs on two checkouts measure the same day.  A retired loan's
#: derived stop tracks the read pass's own now (ruling **R-R50**), so an
#: unpinned clock would make the two sides differ for a reason unrelated to the
#: change under test.
AS_OF = date(2026, 9, 2)


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
            lines.append(f"### user {user_id}")
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
                stop = loan_payment_window(template, ctx)
                if stop is None:
                    continue
                rule = template_rule(template)
                lines.append(
                    f"STOP tmpl={template.id} {template.name!r} "
                    f"stored_end={rule.end_date} derived={stop!r}"
                )
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"{len(lines)} lines -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1])
