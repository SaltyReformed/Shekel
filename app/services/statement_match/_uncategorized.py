"""The row a bank line requires and the books do not hold, minted once.

Plan step ``bank_import:X-gf-1``, ruling **bank_import:R-GW**.  **Two doors need this row
and it is written once**: a matched group's leftover difference
(:func:`~._variance.mint`, ruling **R-FN**) and a bank line of money COMING IN
that no app row explains (:func:`~._income.record_income_from_line`).  They are
different acts -- one closes a gap inside a match the owner built, the other IS
the whole match -- but what they write is the same row, and this package's own
root cause is a money rule spelled twice.

**What makes it one row rather than two that look alike.**  Every clause below
is decided by the same fact, which is that a BANK STATEMENT is why the row
exists at all:

* it carries **NO category**, so ``posting_service._settled_target`` books its
  counter leg to the per-(owner, class) Uncategorized fallback -- the app does
  not know what this money was, and saying so is what makes it categorisable
  later rather than misfiled now (**R-FN**);
* it is **born Projected and settled through the app's own verb**, never
  assigned a settled status directly: ``status_seam.apply_status_change`` is
  the one door into the settled band and a row may not be born in one, which
  plan step ``balance:X-aj2`` makes structural;
* its settle day is the bank's posting day on the ``observed`` basis (plan step
  ``balance:X-az``), because a statement SHOWED the money -- it is neither a
  bound nor a day the owner typed;
* it **OWNS its amount** (``amount_source_id`` NULL beside a stored figure,
  which ``ck_transactions_amount_ownership`` pairs): it names no template, no
  transfer and no card spend, so there is no derivation for it to read.  The
  stored figure is the MAGNITUDE and the DIRECTION is the transaction type,
  which is what ``ck_transactions_estimated_amount`` (``>= 0``) requires;
* it is the **BASELINE scenario**, unconditionally: a what-if scenario is a
  hypothesis about money that has not moved, and this row records money the
  bank has already moved.

**The two callers differ in exactly three things** -- what the row is CALLED,
what its figure is, and which event they log -- so those are the parameters and
nothing else is.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import, no clock read.  It MUTATES and does NOT
commit -- the route owns the unit of work.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import transaction_service
from app.services.scenario_resolver import require_baseline_scenario
from app.services.settle_day import SettleDay

from ._candidates import transaction_candidate
from ._offers import CandidateRow
from ._scope import ReviewScope

#: How long ``budget.transactions.name`` is.  A caller composes the name from
#: the bank's own merchant string, so it is cut to fit HERE rather than at each
#: call site -- one bound, because the column has one.
NAME_LIMIT: int = 200


def mint_uncategorized(
    name: str,
    signed_amount: Decimal,
    pay_period_id: int,
    posts_on: date,
    scope: ReviewScope,
) -> CandidateRow:
    """Create and settle the uncategorized row a bank line's money requires.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        name: What to call the row.  Cut to :data:`NAME_LIMIT` here, because
            ``budget.transactions.name`` is NOT NULL and this writer sets it
            directly.
        signed_amount: The money, in the BANK's own direction -- positive for
            money arriving, negative for money leaving.  **The sign is what
            picks the transaction TYPE** and the magnitude is what is stored,
            because the column is non-negative by check constraint.  It must
            not be zero: a row worth nothing is not offerable and
            :func:`~._candidates.transaction_candidate` answers ``None`` for
            one, which this function treats as a broken contract rather than an
            outcome.
        pay_period_id: The paycheck this movement belongs to, resolved by the
            caller through :meth:`~._scope.ReviewScope.period_holding`.
            **Resolved THERE rather than here, and that is a correctness change
            rather than tidying**: that lookup can refuse, and a refusal raised
            here would leave written work behind for the caller that had
            already moved rows -- which :mod:`._accept` explicitly declines to
            lean on its SAVEPOINT for.
        posts_on: The day the bank posted the money, which this row settles on.
        scope: The pass, which is the ONE statement of whose account and whose
            baseline scenario this row belongs to.

    Returns:
        The new row as a :class:`~._offers.CandidateRow`, so the caller can
        record it as a match member exactly like every other one.

    Raises:
        PostingError: From the ledger reconcile, on a broken invariant.
        RuntimeError: When the candidate constructor refuses the row this
            function has just created -- a broken contract rather than anything
            an owner did, so it fails the request loud.

            **This function raises no designed refusal of its OWN, and it is
            not refusal-free**: :func:`~app.services.transaction_service
            .apply_requested_status` below refuses a settle day that has not
            happened yet (**R-EJ**), and it does so AFTER the INSERT.  A first
            version of this paragraph claimed every refusal a caller owes had
            already fired, and an adversarial review 2026-08-27 measured a
            future-dated bank line leaving a written row behind for a
            SAVEPOINT to take back.  **Each caller asks that refusal before it
            calls this** -- :func:`~._income.record_income_from_line` through
            :func:`~app.services.status_seam.reject_future_settle_day`, and
            :func:`~._variance.mint` because a match's day comes from lines the
            offer set already bounded.
    """
    row = Transaction(
        account_id=scope.account_id,
        pay_period_id=pay_period_id,
        scenario_id=require_baseline_scenario(scope.owner_id).id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=name[:NAME_LIMIT],
        category_id=None,
        transaction_type_id=ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if signed_amount > 0 else TxnTypeEnum.EXPENSE,
        ),
        estimated_amount=abs(signed_amount),
        is_envelope=False,
    )
    db.session.add(row)
    # The settle verb reads the row's own type and id, so it must exist first.
    db.session.flush()
    transaction_service.apply_requested_status(
        row,
        transaction_service.settled_status_id(row),
        settle_day=SettleDay(
            day=posts_on, basis=SettledDayBasisEnum.OBSERVED,
        ),
    )
    # **The settle's UPDATE is FLUSHED before the candidate is read**, and the
    # ordering is the one :func:`~._create.create_purchase_from_line` already
    # has: ``version_id`` reaches the instance only when the statement is
    # emitted, and the revision an undo compares against has to be the one this
    # act LEFT.  It was established by an incidental autoflush inside the
    # settle's ledger reconcile until an adversarial review 2026-08-27 measured
    # that the comment claiming it was a caller's later flush was describing
    # something that could not do it -- so a refactor that stopped the settle
    # path querying would have broken every undo on this door, failing closed
    # to *you have edited that row since*.
    db.session.flush()
    candidate = transaction_candidate(row, scope.calendar, signed_amount)
    if candidate is None:  # pragma: no cover - defended, not reachable
        # ``transaction_candidate`` answers ``None`` for a row worth nothing or
        # one whose period this calendar does not carry.  Neither can happen
        # here -- the figure is non-zero by the refusal that let the caller
        # run, and the period was resolved from THIS calendar by that caller.
        #
        # **A RuntimeError rather than a ValidationError, and the difference
        # matters**: this row is already written and settled by now, so a
        # designed refusal would render "Nothing was changed" over money that
        # had moved.  Nothing catches this -- ``_batch._run`` takes only the
        # two designed refusals -- so it fails the whole request loud and rolls
        # back, which is the right answer for a broken contract.
        raise RuntimeError(
            f"transaction_candidate refused the uncategorized row {row.id} "
            f"this door just created and settled; a match cannot record a "
            f"member it cannot describe.",
        )
    return candidate
