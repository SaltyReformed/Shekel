"""Tests for the semantic ``is_settled`` archive predicates (CRIT-05 / E-22).

The audit's CRIT-05 finding ("Irreversible silent hard-delete of
RECEIVED settled-income history") proved that the pre-fix predicates
``template_has_paid_history`` and ``transfer_template_has_paid_history``
enumerated ``[DONE, SETTLED]`` by ID and silently omitted ``RECEIVED``
-- the status assigned to every income paycheck on mark-done.  The
guard in ``hard_delete_template`` then permanently destroyed real
received-income history while telling the user it was "permanently
deleted." These tests pin the post-fix behavior: both predicates
return True for any status carrying ``Status.is_settled = True``
(Paid, Received, Settled per ``ref_seeds.py``) and False otherwise.

Test IDs C21-1..C21-7 trace to ``remediation_plan.md`` Section 9
"Commit 21" subsection E.  The companion route-level tests live in
``tests/test_routes/test_templates.py``; this file is the unit-level
pin on the predicate boolean output and on the absence of the prior
ID enumeration in the production source.
"""

from decimal import Decimal
from pathlib import Path

from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.utils.archive_helpers import (
    template_has_paid_history,
    transfer_template_has_paid_history,
)


def _make_template_with_status_txn(app, db_, seed_user, period, status_name):
    """Create an expense template plus one transaction with the given status.

    Returns the (template, transaction) pair.  All boilerplate lives
    here so each test pins exactly one status and asserts the
    predicate's boolean output without restating account/category/
    scenario plumbing.  Resolves Status and TransactionType by name
    (test scaffolding only -- production code uses cached IDs per
    CLAUDE.md rule 4 / E-15).
    """
    expense_type = db_.session.query(TransactionType).filter_by(name="Expense").one()
    status = db_.session.query(Status).filter_by(name=status_name).one()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense_type.id,
        name=f"Template-{status_name}",
        default_amount=Decimal("500.00"),
    )
    db_.session.add(template)
    db_.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense_type.id,
        name=f"Txn-{status_name}",
        estimated_amount=Decimal("500.00"),
        status_id=status.id,
    )
    db_.session.add(txn)
    db_.session.commit()
    return template, txn


def _make_income_template_with_status_txn(app, db_, seed_user, period, status_name):
    """Create an income template plus one transaction with the given status.

    Income templates are the specific case CRIT-05 documents: mark-done
    on an income transaction assigns ``RECEIVED``, the pre-fix
    predicate omitted RECEIVED, and the guard fell through to the
    unconditional bulk delete.
    """
    income_type = db_.session.query(TransactionType).filter_by(name="Income").one()
    status = db_.session.query(Status).filter_by(name=status_name).one()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Salary"].id,
        transaction_type_id=income_type.id,
        name=f"IncomeTemplate-{status_name}",
        default_amount=Decimal("2000.00"),
    )
    db_.session.add(template)
    db_.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Salary"].id,
        transaction_type_id=income_type.id,
        name=f"Paycheck-{status_name}",
        estimated_amount=Decimal("2000.00"),
        status_id=status.id,
    )
    db_.session.add(txn)
    db_.session.commit()
    return template, txn


def _make_transfer_template_with_status(app, db_, seed_user, period, status_name):
    """Create a transfer template plus one transfer with the given status.

    The shadow-transaction pair the transfer service normally
    materialises is not needed here -- the predicate queries
    ``budget.transfers`` directly via ``transfer_template_id``.
    """
    status = db_.session.query(Status).filter_by(name=status_name).one()
    savings_type = db_.session.query(AccountType).filter_by(name="Savings").one()

    savings_account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name=f"Savings-{status_name}",
            anchor_balance=Decimal("0.00"),
        ),
    )
    db_.session.flush()

    xfer_template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_account.id,
        name=f"XferTemplate-{status_name}",
        default_amount=Decimal("100.00"),
    )
    db_.session.add(xfer_template)
    db_.session.flush()

    xfer = Transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=status.id,
        transfer_template_id=xfer_template.id,
        name=f"Transfer-{status_name}",
        amount=Decimal("100.00"),
    )
    db_.session.add(xfer)
    db_.session.commit()
    return xfer_template, xfer


class TestTemplateHasPaidHistorySemanticIsSettled:
    """``template_has_paid_history`` uses ``Status.is_settled`` (CRIT-05).

    The pre-fix predicate enumerated ``[DONE, SETTLED]`` by ID and
    silently omitted ``RECEIVED``.  Post-fix, the predicate filters
    on the semantic ``Status.is_settled`` boolean so every settled
    status (current: Paid, Received, Settled per ``ref_seeds.py``;
    future: any new settled row) is covered without enumeration.
    """

    def test_received_income_returns_true(self, app, db, seed_user, seed_periods_today):
        """C21-1 (predicate half): RECEIVED on an income template -> True.

        This is the bug CRIT-05 documents end-to-end: a recurring
        income template whose paycheck was marked RECEIVED previously
        returned False from this predicate and let the route's bulk
        delete physically destroy the paycheck.  ``RECEIVED`` has
        ``is_settled=True, is_immutable=True`` in ``ref_seeds.py``,
        identical protection to ``DONE`` / ``SETTLED``; the post-fix
        predicate must say so.
        """
        with app.app_context():
            template, _ = _make_income_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Received",
            )
            assert template_has_paid_history(template.id) is True

    def test_paid_expense_returns_true(self, app, db, seed_user, seed_periods_today):
        """C21-3 (predicate half): PAID on an expense template -> True.

        Regression keep for the pre-fix behavior that was already
        correct.  ``Paid`` carries ``is_settled=True``; the predicate
        must continue to return True after the enumeration -> boolean
        swap.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Paid",
            )
            assert template_has_paid_history(template.id) is True

    def test_settled_returns_true(self, app, db, seed_user, seed_periods_today):
        """C21-2 (predicate half): SETTLED -> True.

        Regression keep.  ``Settled`` carries ``is_settled=True``.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Settled",
            )
            assert template_has_paid_history(template.id) is True

    def test_projected_returns_false(self, app, db, seed_user, seed_periods_today):
        """C21-4 (predicate half): PROJECTED only -> False.

        ``Projected`` carries ``is_settled=False``, so the predicate
        must let the template be permanently deleted.  The intended
        behavior the route relies on for the no-history path.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Projected",
            )
            assert template_has_paid_history(template.id) is False

    def test_cancelled_returns_false(self, app, db, seed_user, seed_periods_today):
        """``CANCELLED`` is not settled, so the predicate returns False.

        ``Cancelled`` has ``is_settled=False, is_immutable=True,
        excludes_from_balance=True`` -- the user explicitly voided
        the transaction, no money actually moved, no financial
        history exists to protect.  Pins that the predicate honors
        the ``is_settled`` semantics rather than over-broad
        immutability protection.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Cancelled",
            )
            assert template_has_paid_history(template.id) is False

    def test_credit_returns_false(self, app, db, seed_user, seed_periods_today):
        """``CREDIT`` is not settled, so the predicate returns False.

        ``Credit`` has ``is_settled=False`` (the payback transaction
        is the settled half of the credit-card workflow).  Same
        rationale as the Cancelled pin: the predicate keys on
        ``is_settled``, not on ``is_immutable``.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Credit",
            )
            assert template_has_paid_history(template.id) is False

    def test_soft_deleted_settled_ignored(self, app, db, seed_user, seed_periods_today):
        """Soft-deleted settled rows do not block deletion.

        The predicate filters by ``is_deleted=False``, mirroring the
        pre-fix behavior at ``archive_helpers.py``.  Pins that the
        is_settled refactor preserves this gate -- a row the user
        already discarded cannot block subsequent template cleanup.
        """
        with app.app_context():
            template, txn = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Paid",
            )
            txn.is_deleted = True
            db.session.commit()
            assert template_has_paid_history(template.id) is False


class TestTransferTemplateHasPaidHistorySemanticIsSettled:
    """``transfer_template_has_paid_history`` uses ``Status.is_settled``.

    Mirror of the transaction-template tests above; the transfer
    template's bulk-delete path has the same data-loss exposure that
    CRIT-05 documents for transactions.  Pin equivalent behavior so
    a future caller cannot regress one predicate without the other.
    """

    def test_received_returns_true(self, app, db, seed_user, seed_periods_today):
        """C21-6 (predicate half): RECEIVED transfer shadow -> True.

        Even though transfers are structurally always expense/income
        pairs, the predicate must protect any settled-status row --
        ``RECEIVED`` included.
        """
        with app.app_context():
            xfer_template, _ = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Received",
            )
            assert transfer_template_has_paid_history(xfer_template.id) is True

    def test_paid_returns_true(self, app, db, seed_user, seed_periods_today):
        """PAID transfer -> True (regression keep)."""
        with app.app_context():
            xfer_template, _ = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Paid",
            )
            assert transfer_template_has_paid_history(xfer_template.id) is True

    def test_settled_returns_true(self, app, db, seed_user, seed_periods_today):
        """SETTLED transfer -> True (regression keep)."""
        with app.app_context():
            xfer_template, _ = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Settled",
            )
            assert transfer_template_has_paid_history(xfer_template.id) is True

    def test_projected_returns_false(self, app, db, seed_user, seed_periods_today):
        """PROJECTED transfer only -> False (intended permanent-delete path)."""
        with app.app_context():
            xfer_template, _ = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Projected",
            )
            assert transfer_template_has_paid_history(xfer_template.id) is False

    def test_soft_deleted_settled_ignored(self, app, db, seed_user, seed_periods_today):
        """Soft-deleted settled transfers do not block deletion."""
        with app.app_context():
            xfer_template, xfer = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Paid",
            )
            xfer.is_deleted = True
            db.session.commit()
            assert transfer_template_has_paid_history(xfer_template.id) is False


class TestPredicateSourceUsesIsSettledNotIds:
    """Mechanical guard: the production source never enumerates status IDs.

    CLAUDE.md rule 4 / E-15 require ``IDs for logic, strings for
    display``; CRIT-05's specific failure mode was an enumeration
    (``[paid_id, settled_id]``) that silently omitted RECEIVED.  A
    future regression that re-introduces an enumeration would re-open
    the irreversible data-loss path, so the guard against it is a
    source-level mechanical check rather than another behavioral
    test -- behavior tests cannot catch the regression where someone
    re-adds an enum but happens to include all current settled IDs
    while still missing a future one.
    """

    def test_predicate_source_has_no_paid_settled_enumeration(self):
        """C21-7: ``archive_helpers.py`` source contains no ``[paid_id, settled_id]``.

        The pre-fix predicates carried the literal token
        ``[paid_id, settled_id]`` (and the equivalent
        ``[DONE_ID, SETTLED_ID]``).  The post-fix predicates filter
        by ``Status.is_settled.is_(True)``.  This grep-equivalent
        assertion is the load-bearing source-level lock: it would
        fire on any future refactor that drops the JOIN and goes
        back to enumerating status IDs.
        """
        source = Path("app/utils/archive_helpers.py").read_text(encoding="utf-8")
        assert "[paid_id, settled_id]" not in source
        assert "[DONE_ID, SETTLED_ID]" not in source
        assert "[StatusEnum.DONE, StatusEnum.SETTLED]" not in source
        # And the post-fix expression is actually present in both
        # predicates -- guards against a future "fix" that drops the
        # boolean filter entirely.
        assert source.count("Status.is_settled.is_(True)") == 2
