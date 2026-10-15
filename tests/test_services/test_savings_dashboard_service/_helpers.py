"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_savings_dashboard_service.py``, moved verbatim by ``tools/split``.
"""

from collections import OrderedDict
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.ref import AccountType
from app.services import account_service
from app.services.account_category import account_category
from app.services.pay_calendar import DerivedPeriod
from app.services.savings_dashboard_service._types import AccountProjection

from tests._test_helpers import figure_source_columns, settle_day_columns


def _derived_period(period_id, period_index=None):
    """A REAL :class:`DerivedPeriod` for the pure reducer unit tests.

    The producers in this package moved off ORM ``PayPeriod`` rows onto the
    derived calendar at pay-calendar plan step C2-f2d-3, so a stand-in with an
    ``id`` field would silently stop grading what they read.  Building the
    production value is the whole point: a renamed field fails here.
    """
    index = period_id - 1 if period_index is None else period_index
    return DerivedPeriod(
        period_id=period_id,
        period_index=index,
        start_date=date(2026, 1, 2) + timedelta(days=14 * index),
        end_date=date(2026, 1, 15) + timedelta(days=14 * index),
        end_is_projected=False,
    )


def _projection(account, current_balance, balances=None):
    """Build an :class:`AccountProjection` for the pure reducer unit tests.

    The reducers below read an account, a balance, and -- for the per-period
    ones -- the dense period map, so their fixtures are a stand-in account plus
    those figures.  Constructed through the REAL frozen type rather than a dict
    arranged to look like it (plan step X-t1, finding N-111): a test that builds
    its own shape stays green when production changes the one it builds, which
    is finding B-17's lesson paid on this very package.

    Args:
        account: The stand-in account.  Its ``account_type.category_id`` is what
            the projection's ``category`` is RESOLVED from, exactly as
            production resolves it in ``_project_one_account`` -- so a fixture
            cannot hand a reducer a category its own account contradicts (plan
            step X-z7, ruling R-CT).
        current_balance: The account's balance today.  Non-nullable since
            plan step X-v2 (ruling R-CA).
        balances: The dense ``period_id -> Decimal`` map (plan step X-w, ruling
            R-CG).  Defaults to EMPTY, which is honest for the today-only
            reducers -- the net-worth hero and the group subtotals read
            ``current_balance`` and never a period -- and is supplied for real
            by the per-period reducers' own tests.

    Returns:
        The :class:`AccountProjection` the reducers consume.
    """
    return AccountProjection(
        account=account,
        current_balance=current_balance,
        balances=OrderedDict() if balances is None else OrderedDict(balances),
        projected={},
        needs_setup=False,
        category=account_category(account),
    )


# ── Paid-Off Flag Tests (Commit 5.9-2) ──────────────────────────────


def _create_small_loan(seed_user, db_session, name="Test Loan",
                       principal=Decimal("1000.00"),
                       rate=Decimal("0.05000"), term=24):
    """Create a small loan account with LoanParams for paid-off testing.

    Uses a small principal for fast engine replay and easy verification.
    Origination is Jan 2026 with term=24 so remaining months is
    comfortably positive (~21 from April 2026).  Thin wrapper over the
    shared ``create_loan_account`` builder (DRY -- the four-step
    factory + params + origination-event + rate dance lives in
    ``tests/_test_helpers``, not duplicated per suite).
    """
    from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel
    return create_loan_account(
        seed_user, db_session, name=name,
        principal=principal, rate=rate, term=term,
        origination_date=date(2026, 1, 1), payment_day=1,
    )


# ── Commit 6: canonical entries-aware producer routing ─────────────
#
# Pre-Commit-6 the savings dashboard built its own transaction query
# without ``selectinload(Transaction.entries)`` and called the engine
# directly.  When an envelope expense had cleared debit entries, the
# silent-degrade seam in ``cash_ledger._amounts._entry_aware_amount``
# (removed at the math layer by Commit 5) returned
# ``effective_amount`` unchanged.  Result: the same data shipped
# $160.00 on the grid and $114.29 on /savings -- symptom #1.  Commit 6
# routes the savings dashboard through
# ``balance_resolver.balances_for``, which owns the query and eager-
# loads entries, so the two surfaces produce byte-identical values
# by construction.


def _override_anchor(db_session, account, pay_period, anchor_balance):
    """Replace ``account``'s current anchor with the given balance + period.

    Thin wrapper over the shared :func:`tests._test_helpers.override_anchor`
    (which stamps the assertion inside its own period -- see its docstring for
    why that instant is load-bearing) plus this suite's commit boundary.
    Required because the ``seed_user`` factory writes an origination anchor of
    $1,000 against the seed_periods anchor period; tests reproducing symptom #1
    need $614.29 on a chosen period.

    Args:
        db_session: SQLAlchemy session bound to the test database.
        account: The :class:`~app.models.account.Account` whose anchor
            should be overridden.
        pay_period: The :class:`~app.models.pay_period.PayPeriod` the
            new anchor is anchored against.
        anchor_balance: The new anchor balance as a Decimal.
    """
    from tests._test_helpers import override_anchor  # pylint: disable=import-outside-toplevel

    override_anchor(
        db_session, account, pay_period, anchor_balance,
    )
    db_session.commit()


#: The civil day every purchase :func:`_add_entry` writes is bought and settled
#: on.  Named because the account's BOOKS must precede it (ruling **R-HG**,
#: plan step X-f3c-2b) and two literals that have to agree are two a caller can
#: split -- it was spelled twice inside the helper below and nowhere else.
_PURCHASE_DAY = date(2026, 1, 15)


def _add_entry(
    db_session, *, txn, user_id, amount,
    is_settled=False, is_credit=False, description="Purchase",
):
    """Add a :class:`TransactionEntry` to ``txn``.

    ``is_settled`` stamps the purchase's ``settled_on`` with its own
    purchase day, which the callers' accounts assert on or after, so the
    projection reads it as already inside the declared balance (ruling
    R-DH (d)) -- the state the retired ``is_cleared`` flag named.
    """
    from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel

    db_session.add(TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=user_id,
        amount=amount,
        description=description,
        purchased_on=_PURCHASE_DAY,
        **settle_day_columns(_PURCHASE_DAY if is_settled else None),
        is_credit=is_credit,
    ))
    db_session.flush()


# ── F-21 / Commit 19: Loan period-balance dispatcher ──────────────


def _add_savings_account(seed_user, balance):
    """Create a liquid Savings account.

    Returns:
        The new savings Account.
    """
    savings_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=balance,
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _add_mortgage_account(
    seed_user, balance, origination_date=None, *, tracked_from=None,
):
    """Create a Mortgage (liability) account with a loan schedule.

    Mortgage at 6.5%, 30-year, defaulting to a 2025-01-01 origination so the
    resolver's as-of-today current balance equals the origination principal.
    *origination_date* is overridable: since step C6b the forward liability is a
    FOLD of the loan's payment PLAN (not a schedule walk), so a loan originated in
    the past with NO payment records is delinquent -- its unpaid overdue
    installments never pay it down (finding B-9), and since plan step R16-b-2
    (ruling R-R71) every one of those skipped months is CHARGED too, so the
    balance GROWS until the plan's payments have cleared the arrears.  A test that
    needs a loan to amortize cleanly to ZERO must leave no unrecorded month
    behind the loan's latest assertion: originate it today (pass
    ``date.today()``, the on-schedule case) or state *balance* as the balance
    today at setup (pass ``tracked_from=date.today()``; the mid-life-import
    case, the ``tracking_start`` production's setup door writes -- plan step
    R20).

    Routed through the shared ``create_loan_account`` factory rather than
    re-rolling the account + ``LoanParams`` + rate block: the hand-rolled copy this
    replaces never opened the loan's genesis posting ledger, so every mortgage in
    this suite ran on the no-ledger fallback -- a path production never takes.

    Returns:
        The new mortgage Account.
    """
    # pylint: disable=import-outside-toplevel
    from datetime import date as _date
    from app.enums import AcctTypeEnum
    from app.models.pay_period import PayPeriod
    from tests._test_helpers import create_loan_account

    return create_loan_account(
        seed_user, db.session, name="Home Mortgage",
        principal=balance, rate=Decimal("0.06500"), term=360,
        origination_date=origination_date or _date(2025, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
        tracked_balance=balance if tracked_from is not None else None,
        tracked_from=tracked_from,
    )


def _add_property_account(seed_user, market_value):
    """Create a Property (appreciating physical asset).

    The market value is the user-set anchor balance; no appreciation params
    row is needed for equity (equity reads the anchor value, not the
    forward projection).

    Returns:
        The new Property Account.
    """
    property_type = (
        db.session.query(AccountType).filter_by(name="Property").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=property_type.id,
            name="House",
            anchor_balance=market_value,
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _with_badging_predicate(account_data):
    """Rebuild *account_data* with ``is_retired`` set to the BADGING predicate.

    The shared firing control for the two tests below: it substitutes
    ``is_paid_off`` for ``is_retired`` on every loan projection, so a producer
    that asks the debt-LINE question with the CONGRATULATION predicate answers
    differently and the control fires.

    Every level is rebuilt through ``dataclasses.replace`` on the real frozen
    types -- the projection, its ``LoanDetail``, and the seam's own
    ``LoanFigures`` -- so the control exercises the production shapes rather
    than dicts arranged to look like them (finding B-17's lesson; the
    projection became a frozen value at plan step X-t1, and rewriting it in
    place is no longer possible even if a test wanted to).

    Args:
        account_data: The per-account projections from
            ``compute_dashboard_data``.

    Returns:
        A new list, same order, with the loan projections substituted.
    """
    return [
        ad if ad.loan is None else replace(
            ad,
            loan=replace(
                ad.loan,
                figures=replace(
                    ad.loan.figures, is_retired=ad.loan.figures.is_paid_off,
                ),
            ),
        )
        for ad in account_data
    ]
