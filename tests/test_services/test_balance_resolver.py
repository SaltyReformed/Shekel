"""
Shekel Budget App -- Balance Resolver Producer Tests (Commit 5 / E-25)

Tests for the entries-aware anchor-forward balance producer
``app.services.balance_at._cash_engine.balances_for``, the one cash producer
outside the fold, kept until plan step X-c2c windows the investment and
appreciation bases onto the fold too.

**Three suites left with their subjects at plan step X-c2b3**, and what each
was pinning is now pinned against the seam instead:

  * ``TestBalanceAsOfDate`` (9 tests) went with ``balance_as_of_date``.  Its
    contract was "the projection runs through the period CONTAINING the date,
    not the last period that ended before it" -- a correction to a period-FLAT
    producer.  The fold is date-precise by construction (a day's balance is the
    running total through that day), so the property has no shape left to
    assert; what replaces it is ``tests/test_services/test_cash_fold.py``'s
    ``TestThePlannedTier`` (which day each row lands on) and
    ``TestEveryAssertionIsReplayed`` (a past date reads its own assertion), plus
    ``test_cash_fold_parallel.py``, which walks EVERY day of each shape and
    pins the scalar, the map and the daily series as one running total.
  * ``TestPeriodSubtotal`` / ``TestPeriodSubtotalsBatch`` (5 tests) went with
    ``cash_ledger.period_subtotal`` / ``period_subtotals``.  They pinned
    ``balances[p] - balances[p-1] == subtotal[p].net``; ruling R-K's successor
    identity ``balance[p] - balance[p-1] == net + reconciliation (+ interest)``
    is pinned on the shipped basis by ``test_cash_period_view.py``'s
    ``TestTheIdentityHoldsOnEveryPeriod`` and, as RENDERED ``GridColumn`` rows,
    by ``test_balance_at.py``'s ``_assert_grid_view_reconciles`` (four call
    sites, including ``TestTheRemainderIsWhatTheRowsCannotExplain``).
  * ``TestBalanceResultContract`` (2 tests) went with ``BalanceResult``, whose
    second field was the stale-anchor flag the fold makes unrepresentable
    (finding N-50).

CRIT-01 / F-009 / symptom #1: pre-Commit-5, the same Projected
envelope expense yielded $160 on the grid (which eager-loaded
entries) and $114.29 on /savings (which did not), because
``cash_ledger._amounts._entry_aware_amount`` silently degraded to
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

from collections import OrderedDict
from datetime import date as _date
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import selectinload

from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import cash_ledger
from app.services.balance_at import _calculator as balance_calculator, _cash_engine as balance_resolver
from app.services.balance_at._cash_engine import balances_for
from app.services.cash_ledger import (
    load_balance_transactions,
    sum_projected,
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
    from tests._test_helpers import override_anchor  # pylint: disable=import-outside-toplevel

    override_anchor(
        db_session, account, pay_period, anchor_balance,
        notes="balance_resolver tests: anchor override",
    )
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
            seed_periods[0] (the anchor period, so ``sum_projected``
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

            # The producer returns the period map itself since plan step
            # X-c2b3: its ``BalanceResult`` wrapper existed to carry the
            # stale-anchor flag beside the map, and the fold made staleness
            # unrepresentable (finding N-50).
            assert isinstance(result, OrderedDict)
            # 614.29 - max(500.00 - 45.71 - 0, 0) = 614.29 - 454.29 = 160.00.
            # Pre-Commit-5 this returned 114.29; F-009 / CRIT-01.
            assert result[anchor_period.id] == Decimal("160.00")

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
                result_with_preload[anchor_period.id]
                == result_no_preload[anchor_period.id]
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
            assert result[anchor_period.id] == Decimal("114.29")

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
            assert result[anchor_period.id] == Decimal("1000.00")

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
            assert result[anchor_period.id] == Decimal("400.00")

    # ── C5-8 -----------------------------------------------------------

    def test_seam_removed(self):
        """C5-8: the ``'entries' not in __dict__`` seam is absent from source.

        Mechanically asserts that the silent-degrade short-circuit
        text patterns named by the remediation plan's verification
        gate are not present in the producer
        (``balance_at/_cash_engine.py``), the consumed engine
        (``balance_at/_calculator.py``), or ANY module of the
        ``cash_ledger`` package.  A future regression that
        re-introduces the seam in any of them fails this test loud.

        ``cash_ledger`` is scanned WHOLE, by enumerating the package rather
        than naming a file, and that is load-bearing rather than tidy.  Plan
        step D1a moved ``load_balance_transactions`` out of the producer --
        it carries the ``selectinload(Transaction.entries)`` guarantee this
        whole guard rests on -- and D1c moved ``_entry_aware_amount``, which
        is the function the forbidden patterns would actually appear IN, into
        ``cash_ledger/_amounts.py``.  A scan keyed on a file NAME would have
        gone quiet at exactly the moment the code it guards moved, which is
        the "creating a module is how you escape a module-keyed gate" shape
        (finding N-28).  Globbing the package means a new submodule is
        covered the day it is written.
        """
        services = Path(__file__).resolve().parents[2] / "app" / "services"
        package = sorted((services / "cash_ledger").rglob("*.py"))
        # The cash producers moved INTO the seam at plan step D1d
        # (``balance_resolver`` -> ``balance_at._cash_engine``,
        # ``balance_calculator`` -> ``balance_at._calculator``).
        scanned = [
            services / "balance_at" / "_cash_engine.py",
            services / "balance_at" / "_calculator.py",
            *package,
        ]
        # The package must really have been walked, and the walk must have
        # reached the module the forbidden patterns would actually appear in.
        # Asserting a total instead would be a magic count that breaks when the
        # package legitimately gains or sheds a module; ``rglob`` (not
        # ``glob``) so a future nested subpackage cannot escape the scan --
        # the same shape this guard exists to close.
        assert package, "the cash_ledger package enumerated to nothing"
        assert any(p.name == "_amounts.py" for p in package), (
            f"the entry-aware rule's module was not scanned: "
            f"{[p.name for p in package]}"
        )
        forbidden_patterns = ("not in txn.__dict__", "'entries' not in")
        for source_path in scanned:
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
            assert result[anchor_period.id] == Decimal("900.00")

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
            assert result[anchor_period.id] == Decimal("100.00")


# ── One projected sum, and it reads no clock (D1c, then X-c2c1) ────


class TestTheProjectedSumValuesAnExpenseRow:
    """How ``sum_projected`` prices ONE still-Projected expense, and no date.

    ``balance_resolver`` once carried a private ``_sum_period_as_of`` plus a
    private ``_entry_aware_amount_dated``, whose own docstrings said they
    "mirror" the engine and were "otherwise identical to the engine helper".
    Two copies of the checking-reservation rule, kept in step by hand, is the
    agreeing-by-coincidence shape this arc exists to end -- and pylint's
    cross-file ``duplicate-code`` reported it the moment D1c made both copies
    call the same ``income_amount``.  D1c unified them into ONE rule carrying
    an optional ``as_of`` bound over entry inclusion.

    **Plan step X-c2c1 deleted that bound**, so what is left here is the
    reduction's clock-free half: every loaded entry counts, an override wins
    over the formula, and the no-entries short-circuit precedes the status
    read.  Ruling R-M closed the bound's fork at the write door instead (plan
    step X-c0 refuses ``entry_date > display_today()``), after which it could
    drop nothing; the two tests that existed only to prove a bound CUTS went
    with it, and the teeth moved to where a date still exists --
    ``test_cash_fold.py``'s
    ``test_the_reservation_reads_no_clock_whatever_the_readers_as_of``, which
    fails if a window is ever re-introduced.
    """

    @staticmethod
    def _expense_with_two_cleared_debits(db_session, seed_user, period):
        """Create est=500.00 with 200.00 cleared Jan 5 and 250.00 cleared Jan 20."""
        txn = _make_projected_expense(
            db_session,
            seed_user=seed_user,
            pay_period=period,
            estimated=Decimal("500.00"),
        )
        for amount, day in ((Decimal("200.00"), 5), (Decimal("250.00"), 20)):
            _add_entry(
                db_session,
                txn=txn,
                user_id=seed_user["user"].id,
                amount=amount,
                is_cleared=True,
                entry_date=_date(2026, 1, day),
            )
        db_session.commit()
        return txn

    def test_every_loaded_entry_counts(self, app, db, seed_user, seed_periods):
        """The reduction sees every loaded entry, whatever date each carries.

        cleared_debit = 200.00 + 250.00 = 450.00; uncleared = 0; credit = 0.
        impact = max(500.00 - 450.00 - 0, 0) = 50.00.
        """
        with app.app_context():
            self._expense_with_two_cleared_debits(
                db.session, seed_user, seed_periods[0],
            )
            txns = load_balance_transactions(
                seed_user["account"], seed_user["scenario"].id,
                [seed_periods[0].id],
            )

            income, expense = sum_projected(txns)

            assert income == Decimal("0.00")
            assert expense == Decimal("50.00")

# ── Module surface ─────────────────────────────────────────────────


def test_balance_resolver_exports_only_the_producers():
    """The module's public surface is ONE balance producer, and ONLY that.

    Pins plan step D1a's split, in both directions.  ``balance_resolver`` is
    the cash BALANCE producer; the facts it folds over and the per-row FLOW
    sum live in the ``cash_ledger`` leaf, because only a balance producer
    belongs inside the ``balance_at`` seam at D1d -- a name that answers no
    balance-at-T question would otherwise have to be re-exported through the
    seam's public surface to keep its consumers working.

    The set is ONE name since plan step X-c2b3, and this assertion is where the
    module's shrinking is visible: ``balance_as_of_date`` (the date-precise
    scalar) and ``BalanceResult`` (the wrapper carrying the stale-anchor flag
    beside the map) both deleted with the surfaces that read them -- the seam
    reads the fold at a date, and the fold makes staleness unrepresentable.
    What is left is the anchor-forward roll-up the investment and appreciation
    bases seed off until plan step X-c2c.

    The "only" is enforced as a SET EQUALITY over what the module DEFINES, and
    that is the load-bearing half.  An earlier version of this test asserted
    ``not hasattr`` for the relocated names, which does not enforce "only" at
    all: adding a fresh non-producer back to ``balance_resolver`` passed it
    green (measured in D1a's adversarial review).  Equality catches that,
    because a new public definition here changes the set.

    It keys on ``__module__`` -- where each name is DEFINED -- rather than on
    ``hasattr``.  ``balance_resolver`` imports the ``cash_ledger`` names it
    folds over, and an imported name IS a module attribute, so
    ``hasattr(balance_resolver, "resolve_anchor")`` is True and always will be.
    Worth stating rather than working around: this split relocates OWNERSHIP,
    and Python offers no way to stop a caller reaching a re-exported name. What
    will forbid reaching it is the D-gate package boundary, now that these
    producers have moved inside the seam at D1d -- structure, not this test. The
    deterministic guard against a NEW unclassified producer here is W9909, not
    this either.

    The ``cash_ledger`` half asserts the SUBMODULE, not just the package, which
    is what pins D1c's cohesion split rather than merely its relocation: the
    FACTS, what one row is WORTH, and what a set of rows SUMS TO are three
    answers to three different questions, and collapsing them back into one
    module would pass a package-level assertion unchanged.
    """
    defined_here = {
        name for name in dir(balance_resolver)
        if not name.startswith("_")
        and getattr(
            getattr(balance_resolver, name), "__module__", None,
        ) == "app.services.balance_at._cash_engine"
    }
    assert defined_here == {"balances_for"}, (
        "the cash engine defines the anchor-forward balance producer and ONLY "
        f"that; unexpected surface: {sorted(defined_here)}"
    )

    owners = {
        # The FACTS (plan step D1a).
        "resolve_anchor": "_facts",
        "AnchorPoint": "_facts",
        "load_balance_transactions": "_facts",
        # What ONE row is WORTH (plan step D1c: the cash analog of
        # ``loan_ledger._split``).
        "live_amount_overrides": "_amounts",
        "income_amount": "_amounts",
        # What a SET of rows SUMS TO (plan steps D1a + D1c).  ``sum_projected``
        # is the only name left here: its per-period ``period_subtotal`` /
        # ``period_subtotals`` / ``PeriodSubtotal`` siblings deleted at plan
        # step X-c2b3, ruling R-K having changed what a subtotal COUNTS.
        "sum_projected": "_flows",
    }
    for moved, submodule in owners.items():
        assert getattr(cash_ledger, moved).__module__ == (
            f"app.services.cash_ledger.{submodule}"
        ), f"{moved} is owned by cash_ledger.{submodule}"


def test_balance_calculator_defines_only_the_balance_walk():
    """``balance_calculator`` is a PRODUCER module and nothing else (D1c).

    The other half of the same split, and the reason D1c exists.  This module
    held five explicitly-ruled NON-producers -- the projected-sum reduction and
    the per-row checking-valuation rules -- for as long as the fence has
    existed, which is what made it unmovable into the seam: ``cash_ledger``
    (outside the seam, correctly) calls ``sum_projected``, so moving the module
    wholesale would have put a seam-private import in an out-of-seam consumer
    (finding N-30).

    Set equality, not ``not hasattr``, for the reason the sibling test above
    records: the module still IMPORTS ``sum_projected``, so ``hasattr`` is True
    and always will be.  What this pins is that nothing NEW that fails to answer
    "what is the balance at T" gets defined here again -- which is what would
    silently re-create the blocker.

    The set is ONE name since plan step X-c2b2: the interest composition
    ``calculate_balances_with_interest`` was "roll the anchor forward, then
    layer", and when the base became the cash fold its two halves separated --
    the layering moved to ``balance_at._interest`` beside the accrual window it
    needs, and the wrapper had no caller left.  What survives here is the pure
    carry-forward walk, still feeding ``_cash_engine.balances_for`` for the
    investment and appreciation bases until plan step X-c2c windows those onto
    the fold too.

    Scope caveat, stated so this is not read as stronger than it is: the
    ``__module__`` filter sees FUNCTIONS and CLASSES defined here, not a
    module-level constant (a bare ``Decimal`` reports ``__module__``
    ``'decimal'``, not this module).  That is the same structural gap W9909
    names in its own header, and the deterministic guard against a new
    unclassified PRODUCER here is W9909, not this test; this pins the
    higher-level "only the two producers, by name" property that a reader
    checks at a glance.
    """
    defined_here = {
        name for name in dir(balance_calculator)
        if not name.startswith("_")
        and getattr(
            getattr(balance_calculator, name), "__module__", None,
        ) == "app.services.balance_at._calculator"
    }
    assert defined_here == {"calculate_balances"}, (
        "the calculator defines the balance walk and ONLY that; a "
        "non-producer defined here is what stranded five of them before D1c. "
        f"Unexpected surface: {sorted(defined_here)}"
    )
