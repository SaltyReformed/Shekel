"""
Shekel Budget App -- Cash ledger: what ONE row is WORTH to checking.

The per-transaction valuation rules of :mod:`app.services.cash_ledger._amounts`,
tested against that module directly.  The cash analog of the loan side's split
tests: given a single row, "how much of this hits the checking balance right
now?"

**These tests MOVED here at plan step X-c2c2a**
(``docs/audits/balance_architecture/README.md``), from
``test_balance_calculator_entries.py`` and ``test_balance_calculator.py``'s
``TestIncomeOverridesSeam``.  They always tested THIS rule; they reached it
through ``balance_at._calculator.calculate_balances``, a PRODUCER that deletes
at plan step X-c2c4, so every assertion read ``anchor - reservation`` and the
anchor arithmetic was scenery.  **Every hand-computed reservation figure is
preserved verbatim** -- the ``5000.00 -`` wrapper is what dropped, and each test
that had one still carries its original arithmetic comment so the two forms can
be diffed against each other.

What did NOT move, and why the split is not "the whole file":

* the STATUS gates (a settled / cancelled / Credit row contributes nothing) and
  the reductions over a SET of rows are :mod:`app.services.cash_ledger._flows`'
  question, and moved to ``test_cash_flows.py``;
* the two ANCHOR-PERIOD tests discriminate a ``_calculator`` branch -- which of
  its two arms calls the shared reduction -- so they stay with that module and
  die with it at X-c2c4.

The plan's own one-liner said "``test_balance_calculator_entries.py`` (27 tests)
is the three-bucket reservation formula"; tracing measured 18, and the
correction is recorded at the step.

The reservation formula under test::

    cleared_debit   = sum(amount where not is_credit and     is_cleared)
    uncleared_debit = sum(amount where not is_credit and not is_cleared)
    sum_credit      = sum(amount where is_credit)

    impact = max(estimated - cleared_debit - sum_credit, uncleared_debit)
"""

from datetime import date
from decimal import Decimal

from app.services.cash_ledger._amounts import (
    _entry_aware_amount,
    _expense_amount,
    income_amount,
)
from tests._test_helpers import add_entry, add_txn, create_envelope_txn


_ENTRY_DAY = date(2026, 1, 20)


def _envelope(db_session, seed_user, period, estimated, entries=()):
    """Build a Projected envelope expense carrying *entries*, and return it.

    The shared setup for the reservation tests.  Each moved test built ~50
    lines of template + transaction + entry construction inline and then
    re-queried with ``selectinload``; the re-query is not reproduced because
    the rule under test reads ``txn.entries`` through the relationship
    descriptor, which resolves either way -- a property
    :class:`TestTheEntriesRelationshipIsNotASeam` below pins explicitly rather
    than leaving it implied in 18 setups.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        period: The :class:`~app.models.pay_period.PayPeriod` to place it in.
        estimated: The envelope's budgeted amount, as a string.
        entries: An iterable of ``(amount, is_credit, is_cleared)`` triples,
            each a string amount and two bools.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.
    """
    txn = create_envelope_txn(
        seed_user, db_session, period, "Groceries", Decimal(estimated),
    )
    for amount, is_credit, is_cleared in entries:
        add_entry(
            db_session, seed_user, txn, Decimal(amount), _ENTRY_DAY,
            is_credit=is_credit, is_cleared=is_cleared,
        )
    db_session.commit()
    return txn


class TestTheEntryAwareReservation:
    """The three-bucket reservation for a still-Projected envelope expense.

    The six scope-doc scenarios (Section 4.2) plus the boundary shapes.  Every
    figure is the reservation itself; before X-c2c2a each was asserted as
    ``5000.00 - reservation`` through the balance walk.
    """

    def test_no_entries_holds_the_full_estimate(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 1: a tracked expense with no entries holds its estimate.

        est=500, debit=0, credit=0 -> the empty-entries short circuit returns
        ``effective_amount``, which for an unfilled Projected expense is the
        estimate: 500.00.  (Was: 5000 - 500 = 4500.)
        """
        with app.app_context():
            txn = _envelope(db.session, seed_user, seed_periods[1], "500.00")

            assert _entry_aware_amount(txn) == Decimal("500.00")

    def test_debit_under_budget_holds_the_full_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 2: an uncleared debit under budget does not reduce it.

        est=500, uncleared_debit=200, credit=0.
        max(500 - 0 - 0, 200) = max(500, 200) = 500.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("500.00")

    def test_a_credit_entry_reduces_the_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 3: mixed debit + credit under budget -- credit reduces.

        est=500, uncleared_debit=300, credit=100.
        max(500 - 0 - 100, 300) = max(400, 300) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("300.00", False, False), ("100.00", True, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("400.00")

    def test_all_credit_leaves_only_the_uncovered_portion(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 4: all-credit entries -- only the uncovered part hits cash.

        est=500, debit=0, credit=400.
        max(500 - 0 - 400, 0) = max(100, 0) = 100.  (Was: 4900.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("400.00", True, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("100.00")

    def test_debit_overspend_raises_the_reservation_to_the_debits(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 5: overspend -- the uncleared debit total is the floor.

        est=500, uncleared_debit=530, credit=0.
        max(500 - 0 - 0, 530) = max(500, 530) = 530.  (Was: 4470.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("530.00", False, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("530.00")

    def test_mixed_overspend_takes_the_debit_floor_over_the_reduction(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 6: the debit floor beats the credit-reduced reservation.

        est=500, uncleared_debit=400, credit=200.
        max(500 - 0 - 200, 400) = max(300, 400) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("400.00", False, False), ("200.00", True, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("400.00")

    def test_zero_estimate_with_a_debit_reserves_the_debit(
        self, app, db, seed_user, seed_periods,
    ):
        """A zero-budget envelope still holds back what was actually spent.

        est=0, uncleared_debit=50, credit=0.
        max(0 - 0 - 0, 50) = 50.  (Was: 4950.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "0.00",
                [("50.00", False, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("50.00")

    def test_credit_exceeding_the_estimate_floors_at_the_debits(
        self, app, db, seed_user, seed_periods,
    ):
        """Credit beyond the budget cannot drive the reservation negative.

        est=500, uncleared_debit=100, credit=600.
        max(500 - 0 - 600, 100) = max(-100, 100) = 100.  (Was: 4900.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("100.00", False, False), ("600.00", True, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("100.00")

    def test_one_cent_debit_does_not_disturb_the_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """The smallest representable entry: a one-cent uncleared debit.

        est=500, uncleared_debit=0.01, credit=0.
        max(500 - 0 - 0, 0.01) = 500.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("0.01", False, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("500.00")

    def test_values_near_the_column_limit_do_not_overflow(
        self, app, db, seed_user, seed_periods,
    ):
        """Values at the ``Numeric(12,2)`` ceiling survive the ``max()``.

        est=9999999999.99, uncleared_debit=9999999999.99, credit=0.
        max(9999999999.99 - 0 - 0, 9999999999.99) = 9999999999.99.
        (Was: 10000000000.00 - 9999999999.99 = 0.01.)
        """
        with app.app_context():
            large = "9999999999.99"
            txn = _envelope(
                db.session, seed_user, seed_periods[1], large,
                [(large, False, False)],
            )

            assert _entry_aware_amount(txn) == Decimal(large)

    def test_a_row_with_no_template_is_worth_its_effective_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A plain (non-envelope) expense carries no entries, so no reduction.

        The pre-entries behaviour, and still the common case: no template
        means no entries, so the short circuit returns ``effective_amount``
        -- 1200.00.  (Was: 5000 - 1200 = 3800.)
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[1], "Rent", "1200.00",
                category_key="Rent",
            )
            db.session.commit()

            assert _entry_aware_amount(txn) == Decimal("1200.00")


class TestTheClearedFlag:
    """``is_cleared`` moves a debit from the FLOOR into the reduction.

    A cleared debit is already reflected in the account's anchor balance, so it
    is subtracted from the reservation; an uncleared one has hit checking but
    is not yet in the anchor, so it acts as the floor instead.
    """

    def test_the_grocery_bug_after_a_true_up(
        self, app, db, seed_user, seed_periods,
    ):
        """The user-reported defect: three cleared purchases against $500.

        est=500, cleared_debit=106.86+249.71+105.77=462.34, uncleared=0,
        credit=0.  max(500 - 462.34 - 0, 0) = 37.66 -- only the unreconciled
        remainder is still held.  (Was: 5000 - 37.66 = 4962.34.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("106.86", False, True),
                 ("249.71", False, True),
                 ("105.77", False, True)],
            )

            assert _entry_aware_amount(txn) == Decimal("37.66")

    def test_partial_cleared_and_uncleared(
        self, app, db, seed_user, seed_periods,
    ):
        """Cleared reduces, uncleared floors, in the same envelope.

        est=500, cleared_debit=100, uncleared_debit=50, credit=0.
        max(500 - 100 - 0, 50) = max(400, 50) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("100.00", False, True), ("50.00", False, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("400.00")

    def test_cleared_overspend_floors_at_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Cleared debits beyond the budget hold back nothing further.

        est=500, cleared_debit=600, uncleared_debit=0, credit=0.
        max(500 - 600 - 0, 0) = max(-100, 0) = 0 -- the money already left and
        the anchor already knows.  (Was: 5000 - 0 = 5000.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("600.00", False, True)],
            )

            assert _entry_aware_amount(txn) == Decimal("0.00")

    def test_all_uncleared_reduces_to_the_legacy_formula(
        self, app, db, seed_user, seed_periods,
    ):
        """With nothing cleared the three buckets collapse to the old two.

        est=500, uncleared_debit=200, credit=0.
        max(500 - 0 - 0, 200) = 500, which is what the pre-cleared-flag
        formula gave.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("500.00")

    def test_cleared_debit_plus_credit_both_reduce(
        self, app, db, seed_user, seed_periods,
    ):
        """A cleared debit and a credit reduce the same reservation.

        est=500, cleared_debit=200, uncleared_debit=0, credit=100.
        max(500 - 200 - 100, 0) = 200.  (Was: 4800.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, True), ("100.00", True, False)],
            )

            assert _entry_aware_amount(txn) == Decimal("200.00")

    def test_a_new_entry_defaults_to_uncleared(
        self, app, db, seed_user, seed_periods,
    ):
        """An entry written without the flag is UNCLEARED, and that is safe.

        The default matters to money: an entry defaulting to CLEARED would
        subtract a purchase from the reservation before the anchor reflected
        it, double-counting it out of the projection.  est=500,
        uncleared_debit=200 -> max(500 - 0 - 0, 200) = 500.  (Was: 4500.)
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[1], "Groceries",
                Decimal("500.00"),
            )
            add_entry(
                db.session, seed_user, txn, Decimal("200.00"), _ENTRY_DAY,
            )
            db.session.commit()

            assert txn.entries[0].is_cleared is False
            assert _entry_aware_amount(txn) == Decimal("500.00")


class TestTheEntriesRelationshipIsNotASeam:
    """The reduction applies whether or not the caller pre-loaded entries.

    The structural fix for CRIT-01 / F-009 / E-25, and the reason
    :func:`_envelope` above does not re-query with ``selectinload``: symptom #1
    ($160 on the grid against $114.29 on ``/savings`` for one row) was exactly
    this seam, where the rule returned ``effective_amount`` whenever the
    consuming query had not issued the eager load.  The rule now reads
    ``txn.entries`` through the relationship descriptor, which lazy-loads on
    demand, so the value is a function of the DATA and not of the caller's
    query plan.
    """

    def test_an_expired_instance_still_reduces(
        self, app, db, seed_user, seed_periods,
    ):
        """Entries NOT resident on the instance -- the descriptor loads them.

        est=500, uncleared_debit=0, credit=300.
        max(500 - 0 - 300, 0) = 200.  (Was: 5000 - 200 = 4800.)

        The instance is expired first so ``entries`` is genuinely absent from
        its ``__dict__``, which is the state a caller that skipped the eager
        load produces.  Pre-E-25 that state returned 500.00.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("300.00", True, False)],
            )
            db.session.expire(txn)
            assert "entries" not in txn.__dict__

            assert _entry_aware_amount(txn) == Decimal("200.00")


class _FakeRow:  # pylint: disable=too-few-public-methods
    """A non-ORM stand-in carrying only what a valuation rule may read.

    Deliberately missing ``entries`` and ``status_id``: the ordering of
    :func:`_entry_aware_amount`'s two guards is load-bearing, and this shape is
    what proves it (see
    :meth:`TestTheLiveOverride.test_no_entries_short_circuits_before_the_status_read`).
    """

    def __init__(self, txn_id=None, effective_amount="77.00"):
        self.id = txn_id
        self.effective_amount = Decimal(effective_amount)


class TestTheLiveOverride:
    """A live-derived amount replaces the stored figure, on both legs.

    The read-time seam (Workstream B): a projected salary paycheck reflects the
    CURRENT salary profile and a recurring loan-payment shadow the loan's
    current P&I + escrow, rather than a stored amount a later profile,
    calibration or code change may have invalidated without firing a
    regeneration.

    Moved from ``TestIncomeOverridesSeam`` (X-c2c2a) and from
    ``test_balance_resolver.py`` (the expense leg's precedence and the guard
    ordering, which arrived there at plan step X-c2c1).  The fourth
    ``TestIncomeOverridesSeam`` test did NOT move: it pins that the override is
    honoured in the POST-ANCHOR period specifically, which is a ``_calculator``
    branch rather than a valuation rule.
    """

    def test_an_override_replaces_the_income_amount(self):
        """An income row whose id is in the map contributes the override.

        Override $2473.38 wins over the stored $2000.00.  (Was asserted as
        anchor $100.00 + override = $2573.38.)
        """
        row = _FakeRow(txn_id=101, effective_amount="2000.00")

        assert income_amount(row, {101: Decimal("2473.38")}) == Decimal(
            "2473.38",
        )

    def test_no_map_uses_the_stored_amount(self):
        """``amount_overrides=None`` is byte-identical pre-seam behaviour."""
        row = _FakeRow(txn_id=101, effective_amount="2000.00")

        assert income_amount(row, None) == Decimal("2000.00")

    def test_an_unlisted_id_falls_back_to_the_stored_amount(self):
        """A non-empty map overrides only the ids it lists.

        The map keys id 999; row 101 keeps its stored $2000.00.
        """
        row = _FakeRow(txn_id=101, effective_amount="2000.00")

        assert income_amount(row, {999: Decimal("5.00")}) == Decimal("2000.00")

    def test_an_override_wins_over_the_entry_formula(
        self, app, db, seed_user, seed_periods,
    ):
        """On the EXPENSE leg an override short-circuits the reduction.

        A live-derived amount is what the row is worth now and carries no
        entries to reduce, so the override is returned verbatim rather than the
        50.00 the three-bucket reservation would give (est=500, two cleared
        debits of 200.00 and 250.00: max(500 - 450 - 0, 0) = 50.00).
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, True), ("250.00", False, True)],
            )

            assert _expense_amount(txn, None) == Decimal("50.00")
            assert _expense_amount(
                txn, {txn.id: Decimal("123.45")},
            ) == Decimal("123.45")

    def test_no_entries_short_circuits_before_the_status_read(self):
        """The guard ORDER holds: no entries returns before ``is_projected``.

        ``_entry_aware_amount`` checks ``not entries`` FIRST and that is
        load-bearing rather than stylistic -- ``is_projected`` reads
        ``status_id`` through ``ref_cache``, so a non-ORM row with neither
        attribute must still return ``effective_amount`` rather than raising.

        Mutation-verified: swapping the two guards fails this with
        ``AttributeError: '_FakeRow' object has no attribute 'status_id'`` --
        ``is_projected`` reads ``status_id`` BEFORE it consults ``ref_cache``,
        so that attribute, not the cache, is what the ordering protects.
        """
        assert _entry_aware_amount(_FakeRow()) == Decimal("77.00")
