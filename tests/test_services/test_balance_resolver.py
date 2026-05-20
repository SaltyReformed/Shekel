"""
Shekel Budget App -- Balance Resolver Producer Tests (Commit 5 / E-25)

Tests for the canonical entries-aware balance producer
``app.services.balance_resolver.balances_for`` and the matching
single-period subtotal ``period_subtotal``.

CRIT-01 / F-009 / symptom #1: pre-Commit-5, the same Projected
envelope expense yielded $160 on the grid (which eager-loaded
entries) and $114.29 on /savings (which did not), because
``balance_calculator._entry_aware_amount`` silently degraded to
``txn.effective_amount`` whenever the consuming query had not issued
``selectinload(Transaction.entries)``.  E-25's correction (this
commit) makes the canonical producer own the query, so the
entries-aware reduction is unconditional and the value cannot
depend on the caller's ORM eager-load habits.

These tests lock the contract:

  * the producer returns the entries-aware value even when the
    caller does NOT pre-load entries (C5-1, the core fix);
  * the value is identical whether the caller pre-loads or not
    (C5-2, the seam-removal proof);
  * with no entries the value equals ``effective_amount``
    semantically (C5-3, regression-safe for no-entries data);
  * credit entries reduce the reservation (C5-4);
  * uncleared debits act as a floor (C5-5);
  * Cancelled and Credit status rows are excluded via the shared
    status predicate (C5-9);
  * an anchor of zero is treated as a value, not "missing", per
    E-12 (C5-10);
  * the seam grep returns empty (C5-8) -- enforced mechanically
    against the file source.

C5-6 / C5-7 (grid + dashboard byte-identical) live in the grid and
dashboard route/service test suites respectively; those callers
already pre-loaded entries before Commit 5, so their pinned values
are unchanged by construction.

Test IDs match the remediation plan's Commit 5 specification (C5-1
through C5-10).
"""

from datetime import date as _date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import selectinload

from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import balance_resolver
from app.services.balance_resolver import (
    BalanceResult,
    PeriodSubtotal,
    balance_as_of_date,
    balances_for,
    period_subtotal,
)


# ── Fixtures local to this test module ─────────────────────────────


def _override_anchor(
    db_session,
    account,
    pay_period,
    anchor_balance: Decimal,
) -> None:
    """Replace ``account``'s current anchor with the given balance + period.

    Appends a fresh :class:`AccountAnchorHistory` row (latest-wins by
    ``created_at``) and updates the ``current_anchor_*`` cache
    columns so the resolver's cache-reconciliation path does NOT
    fire (cache and history agree).  Used by tests that need a
    specific anchor balance distinct from the ``seed_user`` factory
    default of $1,000.

    Args:
        db_session: SQLAlchemy session bound to the test database.
        account: The :class:`~app.models.account.Account` whose
            anchor should be overridden.
        pay_period: The :class:`~app.models.pay_period.PayPeriod`
            the new anchor is anchored against.
        anchor_balance: The new anchor balance.
    """
    history = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=pay_period.id,
        anchor_balance=anchor_balance,
        notes="balance_resolver tests: anchor override",
    )
    db_session.add(history)
    db_session.flush()
    account.current_anchor_balance = anchor_balance
    account.current_anchor_period_id = pay_period.id
    db_session.commit()


def _make_projected_expense(
    db_session,
    *,
    seed_user,
    pay_period,
    estimated: Decimal,
    name: str = "Groceries",
) -> Transaction:
    """Create a Projected envelope expense in ``pay_period``.

    Builds a tracked (``is_envelope=True``) template + transaction
    pair so subsequent :class:`TransactionEntry` rows can attach to
    the parent.  Status: Projected; type: Expense; account/category:
    ``seed_user``'s defaults.

    Returns the newly-created :class:`Transaction`.
    """
    projected = (
        db_session.query(Status).filter_by(name="Projected").one()
    )
    expense_type = (
        db_session.query(TransactionType).filter_by(name="Expense").one()
    )

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        name=name,
        default_amount=estimated,
        is_envelope=True,
    )
    db_session.add(template)
    db_session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=pay_period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=estimated,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _add_entry(
    db_session,
    *,
    txn: Transaction,
    user_id: int,
    amount: Decimal,
    is_cleared: bool = False,
    is_credit: bool = False,
    description: str = "Purchase",
    entry_date: _date | None = None,
) -> None:
    """Add a :class:`TransactionEntry` to ``txn`` with the given flags.

    The ``entry_date`` defaults to ``2026-01-15`` to match the
    pre-existing C5 tests; callers that exercise the E-27
    entry-date filter pass an explicit date in the relevant window.
    """
    db_session.add(TransactionEntry(
        transaction_id=txn.id,
        user_id=user_id,
        amount=amount,
        description=description,
        entry_date=entry_date if entry_date is not None else _date(2026, 1, 15),
        is_credit=is_credit,
        is_cleared=is_cleared,
    ))
    db_session.flush()


# ── Producer correctness ───────────────────────────────────────────


class TestBalancesForEntriesAware:
    """Producer applies the entries-aware reduction unconditionally."""

    # ── C5-1 -----------------------------------------------------------

    def test_producer_loads_entries_itself(
        self, app, db, seed_user, seed_periods,
    ):
        """C5-1: producer returns entries-aware value WITHOUT caller pre-load.

        Setup mirrors symptom #1:
          - anchor 614.29 on seed_periods[0] (overrides seed_user's
            default 1000.00 anchor).
          - one Projected envelope expense est=500.00 on
            seed_periods[0] (the anchor period, so ``_sum_remaining``
            applies).
          - three cleared debit entries 20.00 + 15.71 + 10.00 = 45.71.
          - no uncleared debits, no credits.

        The caller (this test) does NOT pre-load entries -- it just
        passes ``account`` and ``scenario_id`` to ``balances_for``.
        The producer owns its own query, eager-loads entries, and
        applies the formula.

        Hand arithmetic (F-009 worked example reproduced):
          cleared_debit = 20.00 + 15.71 + 10.00 = 45.71
          uncleared_debit = 0
          sum_credit = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          anchor_period_balance = 614.29 + 0 - 454.29 = 160.00

        Pre-Commit-5 the same call with the same data returned
        114.29 because the seam silently degraded ``effective_amount``
        to 500.00 for non-eager-loading callers.
        """
        with app.app_context():
            anchor_period = seed_periods[0]
            _override_anchor(
                db.session,
                seed_user["account"],
                anchor_period,
                Decimal("614.29"),
            )

            txn = _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=anchor_period,
                estimated=Decimal("500.00"),
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    is_cleared=True,
                    is_credit=False,
                )
            db.session.commit()

            # Caller does NOT pre-load entries -- passes account and
            # scenario_id only.
            result = balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods,
            )

            assert isinstance(result, BalanceResult)
            # 614.29 - max(500.00 - 45.71 - 0, 0) = 614.29 - 454.29 = 160.00.
            # Pre-Commit-5 this returned 114.29; F-009 / CRIT-01.
            assert result.balances[anchor_period.id] == Decimal("160.00")

    # ── C5-2 -----------------------------------------------------------

    def test_producer_same_value_regardless_of_caller_preload(
        self, app, db, seed_user, seed_periods,
    ):
        """C5-2: producer value is identical with or without caller eager-load.

        Sanity-check that the seam is structurally gone: the same
        inputs run through ``balances_for`` twice -- once after the
        caller pre-loads ``Transaction.entries`` via selectinload,
        once after the caller deliberately re-fetches without
        pre-load -- must yield byte-identical balances.  Pre-Commit-5
        these two paths produced DIFFERENT numbers
        (entries-aware vs silent-degrade) and that was symptom #1.

        Setup matches C5-1 exactly; the assertion is the equality
        between the two calls plus the C5-1 hand-computed value.
        """
        with app.app_context():
            anchor_period = seed_periods[0]
            _override_anchor(
                db.session,
                seed_user["account"],
                anchor_period,
                Decimal("614.29"),
            )

            txn = _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=anchor_period,
                estimated=Decimal("500.00"),
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    is_cleared=True,
                    is_credit=False,
                )
            db.session.commit()

            # Path A: caller pre-loads entries explicitly.  This is
            # what the pre-Commit-5 grid did.  The producer would
            # still own its own query, but the caller has touched
            # the relationship.
            _preloaded = (
                db.session.query(Transaction)
                .options(selectinload(Transaction.entries))
                .filter(Transaction.id == txn.id)
                .one()
            )
            assert "entries" in _preloaded.__dict__
            result_with_preload = balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods,
            )

            # Path B: caller does NOT pre-load -- expunges the
            # session-cached Transaction so re-load happens fresh
            # without entries in __dict__.
            db.session.expire_all()
            fresh = db.session.get(Transaction, txn.id)
            assert "entries" not in fresh.__dict__
            result_no_preload = balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods,
            )

            # Byte-identical: 614.29 - 454.29 = 160.00 both ways.
            assert (
                result_with_preload.balances[anchor_period.id]
                == result_no_preload.balances[anchor_period.id]
                == Decimal("160.00")
            )

    # ── C5-3 -----------------------------------------------------------

    def test_no_entries_uses_effective_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """C5-3: with no entries at all, the reduction equals effective_amount.

        Setup: anchor 614.29; one Projected envelope expense
        est=500.00 with ZERO entries on the anchor period.  The
        entries-aware formula reduces to
        ``max(500.00 - 0 - 0, 0) = 500.00`` -- identical to
        ``effective_amount`` for a Projected transaction with no
        actual_amount set.  This is the "regression-safe for
        no-entries data" guarantee.

        Hand arithmetic:
          checking_impact = max(500.00 - 0 - 0, 0) = 500.00
          anchor_period_balance = 614.29 - 500.00 = 114.29
        """
        with app.app_context():
            anchor_period = seed_periods[0]
            _override_anchor(
                db.session,
                seed_user["account"],
                anchor_period,
                Decimal("614.29"),
            )

            _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=anchor_period,
                estimated=Decimal("500.00"),
            )
            db.session.commit()

            result = balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods,
            )

            # 614.29 - 500.00 = 114.29 (entries-aware reduces to
            # effective_amount with no entries to subtract).
            assert result.balances[anchor_period.id] == Decimal("114.29")

    # ── C5-4 -----------------------------------------------------------

    def test_credit_entry_reduces_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """C5-4: a credit entry reduces the reservation by its amount.

        Setup: anchor 1000.00; Projected envelope expense est=500.00
        on the anchor period; one credit entry for $500.00 (entire
        budget routed through the CC Payback sibling, so the
        original expense does not hit checking at all).

        Hand arithmetic:
          cleared_debit = 0; uncleared_debit = 0; sum_credit = 500.00
          checking_impact = max(500.00 - 0 - 500.00, 0) = max(0, 0) = 0
          anchor_period_balance = 1000.00 - 0 = 1000.00
        """
        with app.app_context():
            anchor_period = seed_periods[0]
            # seed_user's default anchor is already 1000.00 on
            # seed_periods[0]; no override needed.

            txn = _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=anchor_period,
                estimated=Decimal("500.00"),
            )
            _add_entry(
                db.session,
                txn=txn,
                user_id=seed_user["user"].id,
                amount=Decimal("500.00"),
                is_credit=True,
                is_cleared=False,
            )
            db.session.commit()

            result = balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods,
            )

            # max(500 - 0 - 500, 0) = 0; 1000.00 - 0 = 1000.00.
            assert result.balances[anchor_period.id] == Decimal("1000.00")

    # ── C5-5 -----------------------------------------------------------

    def test_uncleared_floor(
        self, app, db, seed_user, seed_periods,
    ):
        """C5-5: uncleared debits act as a floor on the reservation.

        Setup: anchor 1000.00; Projected envelope expense est=500.00;
        one uncleared debit entry for $600.00 (overspend that has
        already hit checking but is not yet in the anchor).

        Hand arithmetic:
          cleared_debit = 0; uncleared_debit = 600.00; sum_credit = 0
          checking_impact = max(500.00 - 0 - 0, 600.00)
                          = max(500.00, 600.00) = 600.00
          anchor_period_balance = 1000.00 - 600.00 = 400.00
        """
        with app.app_context():
            anchor_period = seed_periods[0]

            txn = _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=anchor_period,
                estimated=Decimal("500.00"),
            )
            _add_entry(
                db.session,
                txn=txn,
                user_id=seed_user["user"].id,
                amount=Decimal("600.00"),
                is_credit=False,
                is_cleared=False,
            )
            db.session.commit()

            result = balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods,
            )

            # max(500 - 0 - 0, 600) = 600; 1000 - 600 = 400.
            assert result.balances[anchor_period.id] == Decimal("400.00")

    # ── C5-8 -----------------------------------------------------------

    def test_seam_removed(self):
        """C5-8: the ``'entries' not in __dict__`` seam is absent from source.

        Mechanically asserts that the silent-degrade short-circuit
        text patterns named by the remediation plan's verification
        gate are not present in either the producer
        (``balance_resolver.py``) or the consumed engine
        (``balance_calculator.py``).  A future regression that
        re-introduces the seam in either file fails this test loud.
        """
        forbidden_patterns = ("not in txn.__dict__", "'entries' not in")
        for module_name in ("balance_resolver.py", "balance_calculator.py"):
            source_path = (
                Path(__file__).resolve().parents[2]
                / "app" / "services" / module_name
            )
            source = source_path.read_text(encoding="utf-8")
            for pattern in forbidden_patterns:
                assert pattern not in source, (
                    f"Forbidden seam pattern {pattern!r} found in "
                    f"{source_path}.  E-25 / CRIT-01 / F-009 "
                    "regression: the producer must not consult the "
                    "instance __dict__ to decide whether the "
                    "entries-aware reduction applies."
                )

    # ── C5-9 -----------------------------------------------------------

    def test_status_gate_is_shared_predicate(
        self, app, db, seed_user, seed_periods,
    ):
        """C5-9: Credit and Cancelled rows excluded via the shared predicate.

        Setup: anchor 1000.00 on the anchor period; three Projected
        expenses on the same period:
          - $100.00 normal Projected (counts as $100 reservation).
          - $200.00 status=Credit (must be excluded -- already
            handled via the CC Payback workflow, not from checking).
          - $300.00 status=Cancelled (must be excluded -- the user
            cancelled the obligation).

        Hand arithmetic:
          Only the $100 normal Projected contributes.
          checking_impact = 100.00.
          anchor_period_balance = 1000.00 - 100.00 = 900.00.

        Pre-Commit-2 the predicate was reproduced inline; post-
        Commit-2 the producer uses ``balance_contributing_clause()``
        at the SQL filter level so Credit/Cancelled rows never enter
        the engine.  This test locks the producer behavior, not the
        implementation -- if the Status table grows a new
        ``excludes_from_balance=True`` row, this assertion still
        holds because the SQL clause is regenerated from the cached
        set on every call.
        """
        with app.app_context():
            anchor_period = seed_periods[0]
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            credit_status = (
                db.session.query(Status).filter_by(name="Credit").one()
            )
            cancelled_status = (
                db.session.query(Status).filter_by(name="Cancelled").one()
            )

            for amount, status in (
                (Decimal("100.00"), projected),
                (Decimal("200.00"), credit_status),
                (Decimal("300.00"), cancelled_status),
            ):
                db.session.add(Transaction(
                    pay_period_id=anchor_period.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=status.id,
                    name=f"Test ${amount}",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=amount,
                ))
            db.session.commit()

            result = balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods,
            )

            # Only the $100 Projected contributes; 1000 - 100 = 900.
            assert result.balances[anchor_period.id] == Decimal("900.00")

    # ── C5-10 ----------------------------------------------------------

    def test_anchor_zero_real_value(
        self, app, db, seed_user, seed_periods,
    ):
        """C5-10: anchor 0.00 is a value, not "missing" (E-12).

        Setup: override the anchor to Decimal("0.00") on
        seed_periods[0]; one Projected income transaction of $100.00
        on the same period.

        Hand arithmetic:
          income = 100.00; expenses = 0.00.
          anchor_period_balance = 0.00 + 100.00 - 0.00 = 100.00.

        Pre-E-12 code that wrote ``account.current_anchor_balance
        or Decimal("0.00")`` would have substituted Decimal("0.00")
        for Decimal("0.00") harmlessly here, but the same idiom
        elsewhere (e.g. truthy short-circuit on a small positive
        balance) is the regression this test guards against by
        proving zero is preserved verbatim through the resolver.
        """
        with app.app_context():
            anchor_period = seed_periods[0]
            _override_anchor(
                db.session,
                seed_user["account"],
                anchor_period,
                Decimal("0.00"),
            )

            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            db.session.add(Transaction(
                pay_period_id=anchor_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Salary",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("100.00"),
            ))
            db.session.commit()

            result = balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods,
            )

            # 0.00 + 100.00 - 0.00 = 100.00; zero anchor honored.
            assert result.balances[anchor_period.id] == Decimal("100.00")


# ── BalanceResult / PeriodSubtotal contract ─────────────────────────


class TestBalanceResultContract:
    """The producer's return type contracts."""

    def test_balance_result_is_frozen(self):
        """BalanceResult is immutable -- writes raise FrozenInstanceError.

        Frozen dataclasses are the project's chosen shape for
        canonical-producer return values: a consumer cannot mutate
        the producer's output and have that mutation silently affect
        a sibling consumer.
        """
        from dataclasses import (  # pylint: disable=import-outside-toplevel
            FrozenInstanceError,
        )
        from collections import OrderedDict  # pylint: disable=import-outside-toplevel
        result = BalanceResult(
            balances=OrderedDict(),
            stale_anchor_warning=False,
        )
        with pytest.raises(FrozenInstanceError):
            result.stale_anchor_warning = True  # type: ignore[misc]

    def test_period_subtotal_is_frozen(self):
        """PeriodSubtotal is immutable -- writes raise FrozenInstanceError."""
        from dataclasses import (  # pylint: disable=import-outside-toplevel
            FrozenInstanceError,
        )
        sub = PeriodSubtotal(
            income=Decimal("0.00"),
            expense=Decimal("0.00"),
            net=Decimal("0.00"),
        )
        with pytest.raises(FrozenInstanceError):
            sub.income = Decimal("999.00")  # type: ignore[misc]


# ── period_subtotal correctness ────────────────────────────────────


class TestPeriodSubtotal:
    """The single-period entries-aware subtotal."""

    def test_period_subtotal_entry_aware(
        self, app, db, seed_user, seed_periods,
    ):
        """``period_subtotal`` applies the entries-aware reduction.

        Setup: Projected envelope expense est=500.00 on anchor
        period; cleared debits 20 + 15.71 + 10 = 45.71.

        Hand arithmetic (mirrors C5-1):
          income = 0
          expense = max(500.00 - 45.71 - 0, 0) = 454.29
          net = 0 - 454.29 = -454.29
        """
        with app.app_context():
            anchor_period = seed_periods[0]
            txn = _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=anchor_period,
                estimated=Decimal("500.00"),
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    is_cleared=True,
                    is_credit=False,
                )
            db.session.commit()

            sub = period_subtotal(
                seed_user["account"],
                seed_user["scenario"].id,
                anchor_period,
            )

            assert isinstance(sub, PeriodSubtotal)
            assert sub.income == Decimal("0.00")
            # 500 - 45.71 - 0 = 454.29; uncleared floor is 0, so 454.29 wins.
            assert sub.expense == Decimal("454.29")
            assert sub.net == Decimal("-454.29")

    def test_period_subtotal_reconciles_balance_delta(
        self, app, db, seed_user, seed_periods,
    ):
        """``period_subtotal.net == balances[p] - balances[p-1]``.

        The same-formula invariant E-25 locks: the subtotal is the
        same entries-aware sum the balance carry-forward uses, so the
        period-to-period balance delta must equal the subtotal's net.

        Setup: Projected $300.00 expense on seed_periods[1] (the
        first post-anchor period).  Anchor is the seed_user default
        $1000 on seed_periods[0].

        Hand arithmetic:
          anchor_period_balance = 1000.00 (no projected items on it).
          period1_expense = 300.00 (no entries -> effective_amount).
          period1_balance = 1000.00 + 0 - 300.00 = 700.00.
          subtotal[period1].net = 0 - 300.00 = -300.00.
          balance_delta = 700.00 - 1000.00 = -300.00.
        """
        with app.app_context():
            anchor_period = seed_periods[0]
            next_period = seed_periods[1]
            _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=next_period,
                estimated=Decimal("300.00"),
            )
            db.session.commit()

            result = balances_for(
                seed_user["account"],
                seed_user["scenario"].id,
                seed_periods,
            )
            sub = period_subtotal(
                seed_user["account"],
                seed_user["scenario"].id,
                next_period,
            )

            anchor_bal = result.balances[anchor_period.id]
            next_bal = result.balances[next_period.id]
            # 700.00 - 1000.00 = -300.00 == sub.net.
            assert next_bal - anchor_bal == sub.net == Decimal("-300.00")


# ── balance_as_of_date (E-27, Commit 9) ────────────────────────────


class TestBalanceAsOfDate:
    """Producer for "balance as of date D" (E-27, HIGH-02 / W-277).

    Locks the contract that replaces the deleted
    ``calendar_service._compute_month_end_balance``:

      * the projection runs through the period containing ``as_of``
        (not the days-stale "last period ending on or before"
        period the pre-Commit-9 calendar selected);
      * entries dated AFTER ``as_of`` are excluded from the
        entries-aware reduction inside the as-of period (a purchase
        that has not happened yet cannot clear the bank as of that
        date and must not reduce the reservation);
      * at a period boundary the result equals the canonical
        ``balances_for`` value for that period (cross-check that
        ``balance_as_of_date`` is a strict generalization, not a
        divergent calculation).
    """

    # ── C9-1 -----------------------------------------------------------

    def test_calendar_month_end_true_date(
        self, app, db, seed_user, seed_periods,
    ):
        """C9-1: month-end mid-period uses true date, not last-period-end.

        ``seed_periods`` runs biweekly from 2026-01-02; period 1 is
        Jan 16 -- Jan 29 (ends BEFORE Jan 31), period 2 is Jan 30 --
        Feb 12 (contains Jan 31).  The pre-Commit-9 calendar selected
        period 1 (the "last period whose end_date <= last_day of
        month") and returned the projected end balance of THAT
        period, missing period 2's contribution.  ``balance_as_of_date``
        must return the projection through period 2, the period
        actually containing Jan 31.

        Setup:
          - anchor 1000.00 on seed_periods[0] (the ``seed_user``
            factory default; no override needed).
          - period 0 (anchor): +2000 income, -500 expense.
          - period 1: +2000 income, -500 expense.
          - period 2 (contains Jan 31): +2000 income, -500 expense.

        Hand arithmetic (E-25 carry-forward, no entries -> formula
        collapses to effective_amount):
          period_0_end = 1000 + 2000 - 500 = 2500
          period_1_end = 2500 + 2000 - 500 = 4000
          period_2_end = 4000 + 2000 - 500 = 5500

        ``balance_as_of_date(2026-01-31)`` -> 5500.00 (period 2 end).
        The pre-Commit-9 path would have stopped at period_1_end
        (4000.00) -- a $1500 stale shortfall (HIGH-02 / W-277).
        """
        with app.app_context():
            account = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            p0, p1, p2 = seed_periods[0], seed_periods[1], seed_periods[2]
            # Sanity-check the period layout the assertion relies on:
            # period 1 ends BEFORE Jan 31 and period 2 contains it.
            assert p1.end_date == _date(2026, 1, 29)
            assert p2.start_date == _date(2026, 1, 30)
            assert p2.end_date == _date(2026, 2, 12)

            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            for period in (p0, p1, p2):
                db.session.add(Transaction(
                    pay_period_id=period.id,
                    scenario_id=scenario_id,
                    account_id=account.id,
                    status_id=projected.id,
                    name="Paycheck",
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("2000.00"),
                ))
                db.session.add(Transaction(
                    pay_period_id=period.id,
                    scenario_id=scenario_id,
                    account_id=account.id,
                    status_id=projected.id,
                    name="Rent",
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("500.00"),
                ))
            db.session.commit()

            result = balance_as_of_date(
                account, scenario_id, _date(2026, 1, 31),
            )
            # Period 0: 1000 + 2000 - 500 = 2500
            # Period 1: 2500 + 2000 - 500 = 4000
            # Period 2: 4000 + 2000 - 500 = 5500  <-- Jan 31 falls here
            assert result == Decimal("5500.00")

    # ── C9-2 -----------------------------------------------------------

    def test_calendar_entry_aware(
        self, app, db, seed_user, seed_periods,
    ):
        """C9-2: entries cleared before ``as_of`` reduce the reservation.

        Setup:
          - anchor 1000.00 on seed_periods[0].
          - period 0 has one Projected envelope expense est=500.00
            with three CLEARED debit entries totalling 462.34, all
            dated 2026-01-08 (well before any conceivable month-end
            ``as_of``).
          - no other transactions.

        Hand arithmetic (E-25 entry-aware reduction, same algebra as
        the F-009 worked example with the symptom-tuple numbers):
          cleared_debit = 200.00 + 162.34 + 100.00 = 462.34
          uncleared_debit = 0
          sum_credit = 0
          checking_impact = max(500.00 - 462.34 - 0, 0) = 37.66
          period_0_end_at_jan31 = 1000.00 - 37.66 = 962.34

        Pre-Commit-9 the calendar would have used the non-entries-
        aware path (no ``selectinload``) and returned
        1000.00 - 500.00 = 500.00 -- the F-009 silent-degrade on the
        calendar surface.  HIGH-02 / W-277.
        """
        with app.app_context():
            account = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            anchor_period = seed_periods[0]
            # Anchor period ends Jan 15; Jan 31 is in a LATER period,
            # but no transactions live there so the projection carries
            # the anchor-period balance forward unchanged.  Asserting
            # on Jan 31 also gives the test the same calendar-flavor
            # as C9-1 above.
            txn = _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=anchor_period,
                estimated=Decimal("500.00"),
            )
            for amt in (
                Decimal("200.00"), Decimal("162.34"), Decimal("100.00"),
            ):
                _add_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    is_cleared=True,
                    entry_date=_date(2026, 1, 8),
                )
            db.session.commit()

            result = balance_as_of_date(
                account, scenario_id, _date(2026, 1, 31),
            )
            # 1000.00 - max(500.00 - 462.34, 0) = 1000.00 - 37.66 = 962.34.
            assert result == Decimal("962.34")

    # ── C9-3 -----------------------------------------------------------

    def test_calendar_equals_resolver_at_period_boundary(
        self, app, db, seed_user, seed_periods,
    ):
        """C9-3: at period.end_date, balance_as_of_date == balances_for.

        When ``as_of`` lands exactly on the end_date of a pay period
        and no entries are dated strictly after that date, the
        entry-date cut is a no-op and the two producers must agree
        -- ``balance_as_of_date`` is a strict generalization of
        ``balances_for`` at the boundary.

        Setup:
          - anchor 1000.00 on seed_periods[0].
          - one Projected envelope expense est=500.00 on period 0
            with two cleared debits totalling 300.00, both dated
            Jan 5 (before the period boundary).
          - period 0 ends Jan 15.

        Hand arithmetic:
          cleared_debit = 200.00 + 100.00 = 300.00
          impact = max(500.00 - 300.00, 0) = 200.00
          period_0_end = 1000.00 - 200.00 = 800.00

        Both producers must return 800.00 for as_of = period 0's
        end_date (Jan 15) and balances_for(...).balances[p0.id].
        """
        with app.app_context():
            account = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            anchor_period = seed_periods[0]
            assert anchor_period.end_date == _date(2026, 1, 15)

            txn = _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=anchor_period,
                estimated=Decimal("500.00"),
            )
            for amt in (Decimal("200.00"), Decimal("100.00")):
                _add_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    is_cleared=True,
                    entry_date=_date(2026, 1, 5),
                )
            db.session.commit()

            as_of = anchor_period.end_date
            via_as_of = balance_as_of_date(account, scenario_id, as_of)

            via_resolver = balances_for(
                account, scenario_id, [anchor_period],
            ).balances[anchor_period.id]

            # 1000.00 - max(500.00 - 300.00, 0) = 1000.00 - 200.00 = 800.00.
            assert via_as_of == Decimal("800.00")
            assert via_as_of == via_resolver

    # ── C9-4 -----------------------------------------------------------

    def test_calendar_entry_after_date_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """C9-4: an entry dated AFTER ``as_of`` is NOT yet reflected.

        Setup:
          - anchor 1000.00 on seed_periods[0].
          - one Projected envelope expense est=500.00 on period 0
            with two cleared debits, dated as follows:
              200.00 cleared on Jan 5 (before as_of)
              250.00 cleared on Jan 20 (AFTER as_of)
          - ``as_of`` = Jan 10.

        Hand arithmetic with the entry-date cut (entry_date <= Jan 10):
          cleared_debit (in-window) = 200.00
          uncleared_debit = 0
          sum_credit = 0
          impact = max(500.00 - 200.00, 0) = 300.00
          balance_at_jan_10 = 1000.00 - 300.00 = 700.00

        WITHOUT the entry-date cut (the wrong behavior the producer
        must not exhibit) cleared_debit would be 450.00 and the
        balance would be 1000.00 - 50.00 = 950.00.  The strict
        inequality between 700.00 (correct) and 950.00 (no-cut)
        proves the date filter is exercised.
        """
        with app.app_context():
            account = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            anchor_period = seed_periods[0]

            txn = _make_projected_expense(
                db.session,
                seed_user=seed_user,
                pay_period=anchor_period,
                estimated=Decimal("500.00"),
            )
            _add_entry(
                db.session,
                txn=txn,
                user_id=seed_user["user"].id,
                amount=Decimal("200.00"),
                is_cleared=True,
                entry_date=_date(2026, 1, 5),
            )
            _add_entry(
                db.session,
                txn=txn,
                user_id=seed_user["user"].id,
                amount=Decimal("250.00"),
                is_cleared=True,
                entry_date=_date(2026, 1, 20),
            )
            db.session.commit()

            result = balance_as_of_date(
                account, scenario_id, _date(2026, 1, 10),
            )
            # cleared_debit (entry_date <= Jan 10) = 200.00.
            # impact = max(500.00 - 200.00, 0) = 300.00.
            # 1000.00 - 300.00 = 700.00.
            assert result == Decimal("700.00")

    # ── as_of before anchor -------------------------------------------

    def test_as_of_before_anchor_returns_anchor_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """``as_of`` strictly before the anchor period returns the anchor.

        The producer does not project BACKWARD from the anchor
        (E-19 / E-27 convention).  Requesting a balance before the
        anchor period returns the anchor balance verbatim
        (rounded to cents).  ``seed_periods[0]`` starts 2026-01-02;
        Dec 1 2025 is before any period.
        """
        with app.app_context():
            account = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            # The seed_user anchor is 1000.00 on seed_periods[0].
            result = balance_as_of_date(
                account, scenario_id, _date(2025, 12, 1),
            )
            assert result == Decimal("1000.00")

    # ── Bad input ----------------------------------------------------

    def test_rejects_non_date_as_of(
        self, app, seed_user, seed_periods,
    ):
        """Passing a non-date raises ``TypeError`` (Decimal discipline).

        ``as_of`` is compared against ``PayPeriod.start_date`` (a
        ``Date``) and a ``datetime`` would silently truncate the
        time portion.  The producer fails loud at the boundary.
        """
        with app.app_context():
            with pytest.raises(TypeError):
                balance_as_of_date(
                    seed_user["account"],
                    seed_user["scenario"].id,
                    "2026-01-31",  # str -- not a date
                )


# ── Module surface ─────────────────────────────────────────────────


def test_balance_resolver_exports_producer():
    """The producer names are importable from the module's public surface."""
    assert hasattr(balance_resolver, "balances_for")
    assert hasattr(balance_resolver, "period_subtotal")
    assert hasattr(balance_resolver, "balance_as_of_date")
    assert hasattr(balance_resolver, "BalanceResult")
    assert hasattr(balance_resolver, "PeriodSubtotal")
