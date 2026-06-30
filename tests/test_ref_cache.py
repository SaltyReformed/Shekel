"""
Shekel Budget App -- Reference Cache and Status Boolean Column Tests

Tests for the ref_cache module (Commit #1) and the boolean columns added
to the Status model.  Verifies that:

  - The cache loads all StatusEnum and TxnTypeEnum members at startup.
  - The cache raises RuntimeError when a database row is missing.
  - The Status boolean columns (is_settled, is_immutable, excludes_from_balance)
    are correct for every status.
  - Transaction.effective_amount respects the boolean columns.
  - The grid shows "Paid" instead of "Done" for the mark-done button.
  - GoalMode and IncomeUnit ref_cache accessors return valid IDs.
  - GoalModeEnum and IncomeUnitEnum match their database rows exactly.
"""

from decimal import Decimal

import pytest
import sqlalchemy.exc

from app.extensions import db
from app import create_app, ref_cache
from app.enums import (
    GoalModeEnum,
    IncomeUnitEnum,
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    LoanAnchorSourceEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.ref import (
    GoalMode,
    IncomeUnit,
    LedgerAccountClass,
    LedgerAccountKind,
    PostingKind,
    PostingSource,
    Status,
    TransactionType,
)
from app.models.transaction import Transaction


class TestRefCacheStatuses:
    """Tests for ref_cache status ID resolution."""

    def test_ref_cache_loads_all_statuses(self, app, db):
        """ref_cache.status_id() returns an integer for every StatusEnum member."""
        with app.app_context():
            for member in StatusEnum:
                result = ref_cache.status_id(member)
                assert isinstance(result, int), (
                    f"status_id({member.name}) returned {type(result)}, expected int"
                )

    def test_ref_cache_loads_all_txn_types(self, app, db):
        """ref_cache.txn_type_id() returns an integer for every TxnTypeEnum member."""
        with app.app_context():
            for member in TxnTypeEnum:
                result = ref_cache.txn_type_id(member)
                assert isinstance(result, int), (
                    f"txn_type_id({member.name}) returned {type(result)}, expected int"
                )

    def test_ref_cache_fails_on_missing_status(self, app, db):
        """ref_cache.init() raises RuntimeError when a status row is missing."""
        with app.app_context():
            # Delete one status row to trigger the failure.
            projected = (
                db.session.query(Status)
                .filter_by(name="Projected")
                .one()
            )
            db.session.delete(projected)
            db.session.flush()

            with pytest.raises(RuntimeError, match="Projected"):
                ref_cache.init(db.session)

            # Roll back so other tests aren't affected.
            db.session.rollback()

            # Re-init cache with all rows present.
            ref_cache.init(db.session)

    def test_init_records_unavailable_table_and_keeps_others_usable(
        self, app, db, monkeypatch
    ):
        """A missing ref table is reported, skipped, and never fatal.

        Simulates the pre-migration bootstrap window by forcing the
        ``loan_anchor_sources`` query to raise ``ProgrammingError`` (as if
        the table did not exist yet).  ``init()`` must: record that one table
        in its returned ``unavailable`` list, still load every other table,
        and leave the unavailable table's accessor raising ``KeyError`` (an
        empty map) rather than returning a wrong value.  ``create_app`` relies
        on a non-empty return here to skip Jinja-globals registration
        (``app/__init__.py``).
        """
        with app.app_context():
            real_query = db.session.query

            def fake_query(model):
                if model.__name__ == "LoanAnchorSource":
                    raise sqlalchemy.exc.ProgrammingError(
                        "SELECT", {}, Exception("relation does not exist")
                    )
                return real_query(model)

            monkeypatch.setattr(db.session, "query", fake_query)
            unavailable = ref_cache.init(db.session)

            # Only the failed table is reported unavailable.
            assert unavailable == ["loan_anchor_sources"]
            # Every other table still loaded and resolves normally.
            assert isinstance(ref_cache.status_id(StatusEnum.PROJECTED), int)
            # The unavailable table's accessor raises KeyError, not a wrong ID.
            with pytest.raises(KeyError):
                ref_cache.loan_anchor_source_id(LoanAnchorSourceEnum.ORIGINATION)

            # Restore a fully-populated cache so later tests are unaffected.
            monkeypatch.undo()
            ref_cache.init(db.session)


class TestCreateAppRefCacheGate:
    """``create_app``'s ``init_ref_cache`` flag gates the eager ref_cache init.

    Locks the deploy-ordering fix: the migration host
    (``scripts/init_database.py``) builds the app with ``init_ref_cache=False``
    so the strict ref_cache row-check does NOT fire on a pre-migration
    database.  ``ref_cache.init`` is fatal on a missing row in an existing ref
    table (a genuine seed/data drift), and a migration that ADDS rows to an
    existing ref table -- like Build-Order Step 3's ``income`` / ``expense``
    posting kinds and ``transaction`` source -- leaves those rows absent until
    the migration the host is about to run actually applies them.  Eager-initing
    there raised and aborted the deploy (rolled back by ``shekel-deploy``);
    skipping it lets the host run the seeding migrations.  Gunicorn, the dev
    server, and the test app keep the eager default (True).
    """

    def test_init_ref_cache_false_skips_ref_cache_init(self, app, monkeypatch):
        """``create_app(init_ref_cache=False)`` never calls ``ref_cache.init``.

        The migration host must build the app without the eager row-check so it
        can run the very migrations that seed the missing rows.  A spy on
        ``ref_cache.init`` must record zero calls.
        """
        calls = []
        monkeypatch.setattr(
            ref_cache, "init", lambda session: calls.append(session) or [],
        )
        create_app("testing", init_ref_cache=False)
        assert calls == []

    def test_init_ref_cache_default_runs_ref_cache_init(self, app, monkeypatch):
        """``create_app()`` (the runtime default) eagerly calls ``ref_cache.init``.

        Gunicorn and the dev/test server rely on the eager init so the cache and
        the ref-id Jinja globals are ready before the first request.
        """
        calls = []
        monkeypatch.setattr(
            ref_cache, "init", lambda session: calls.append(session) or [],
        )
        create_app("testing")
        assert len(calls) == 1


class TestStatusBooleanColumns:
    """Tests for the boolean columns on the Status model."""

    def test_status_boolean_columns_correct(self, app, db):
        """All 6 statuses have the correct boolean column values.

        Expected:
          Projected:  settled=F, immutable=F, excludes=F
          Paid:       settled=T, immutable=T, excludes=F
          Received:   settled=T, immutable=T, excludes=F
          Credit:     settled=F, immutable=T, excludes=T
          Cancelled:  settled=F, immutable=T, excludes=T
          Settled:    settled=T, immutable=T, excludes=F
        """
        with app.app_context():
            expected = {
                "Projected": (False, False, False),
                "Paid": (True, True, False),
                "Received": (True, True, False),
                "Credit": (False, True, True),
                "Cancelled": (False, True, True),
                "Settled": (True, True, False),
            }
            for name, (settled, immutable, excludes) in expected.items():
                status = (
                    db.session.query(Status).filter_by(name=name).one()
                )
                assert status.is_settled == settled, (
                    f"{name}: is_settled={status.is_settled}, expected {settled}"
                )
                assert status.is_immutable == immutable, (
                    f"{name}: is_immutable={status.is_immutable}, expected {immutable}"
                )
                assert status.excludes_from_balance == excludes, (
                    f"{name}: excludes_from_balance={status.excludes_from_balance}, "
                    f"expected {excludes}"
                )


class TestEffectiveAmount:
    """Tests for Transaction.effective_amount with boolean status columns."""

    def test_effective_amount_returns_zero_for_excluded_status(
        self, app, db, seed_user, seed_periods
    ):
        """effective_amount returns Decimal('0') for Credit status
        (excludes_from_balance=True).
        """
        with app.app_context():
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=credit_id,
                name="Credited Expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("250.00"),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.effective_amount == Decimal("0")

    def test_effective_amount_uses_actual_for_settled_status(
        self, app, db, seed_user, seed_periods
    ):
        """effective_amount returns actual_amount for Paid status
        (is_settled=True) when actual_amount is set.
        """
        with app.app_context():
            done_id = ref_cache.status_id(StatusEnum.DONE)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=done_id,
                name="Paid Expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("487.00"),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.effective_amount == Decimal("487.00")

    def test_effective_amount_uses_estimated_for_projected(
        self, app, db, seed_user, seed_periods
    ):
        """effective_amount returns estimated_amount for Projected status."""
        with app.app_context():
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected_id,
                name="Projected Expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.effective_amount == Decimal("500.00")


class TestGridShowsPaidNotDone:
    """Tests that the grid UI shows 'Paid' instead of 'Done'."""

    def test_grid_shows_paid_not_done(self, app, auth_client, seed_user,
                                      seed_periods):
        """The full-edit form for an expense shows 'Paid' button, not 'Done'.

        Verifies the template rename from Commit #1.
        """
        with app.app_context():
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected_id,
                name="Test Expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200

            html = resp.data.decode()
            # The mark-done button should say "Paid", not "Done".
            assert "Paid" in html
            # "Done" should not appear as a button label (it may appear
            # in other contexts like status dropdown options).
            assert "> Done<" not in html


class TestGoalModeRefCache:
    """Tests for ref_cache goal mode ID resolution."""

    def test_goal_mode_ref_cache(self, app, db):
        """ref_cache.goal_mode_id() returns distinct positive integers
        for both GoalModeEnum members (FIXED and INCOME_RELATIVE).
        """
        with app.app_context():
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
            income_relative_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

            assert isinstance(fixed_id, int), (
                f"goal_mode_id(FIXED) returned {type(fixed_id)}, expected int"
            )
            assert isinstance(income_relative_id, int), (
                f"goal_mode_id(INCOME_RELATIVE) returned {type(income_relative_id)}, expected int"
            )
            assert fixed_id > 0, f"FIXED id should be positive, got {fixed_id}"
            assert income_relative_id > 0, (
                f"INCOME_RELATIVE id should be positive, got {income_relative_id}"
            )
            assert fixed_id != income_relative_id, (
                f"FIXED and INCOME_RELATIVE should have different IDs, both are {fixed_id}"
            )

    def test_goal_mode_enum_matches_db(self, app, db):
        """Every GoalModeEnum member has a corresponding database row,
        and every database row has a corresponding enum member.
        No extra rows, no missing rows.
        """
        with app.app_context():
            db_rows = db.session.query(GoalMode).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in GoalModeEnum}

            assert db_names == enum_values, (
                f"GoalMode DB rows {db_names} do not match "
                f"GoalModeEnum values {enum_values}"
            )
            assert len(db_rows) == len(GoalModeEnum), (
                f"GoalMode has {len(db_rows)} rows but GoalModeEnum has "
                f"{len(GoalModeEnum)} members"
            )


class TestIncomeUnitRefCache:
    """Tests for ref_cache income unit ID resolution."""

    def test_income_unit_ref_cache(self, app, db):
        """ref_cache.income_unit_id() returns distinct positive integers
        for both IncomeUnitEnum members (PAYCHECKS and MONTHS).
        """
        with app.app_context():
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

            assert isinstance(paychecks_id, int), (
                f"income_unit_id(PAYCHECKS) returned {type(paychecks_id)}, expected int"
            )
            assert isinstance(months_id, int), (
                f"income_unit_id(MONTHS) returned {type(months_id)}, expected int"
            )
            assert paychecks_id > 0, f"PAYCHECKS id should be positive, got {paychecks_id}"
            assert months_id > 0, f"MONTHS id should be positive, got {months_id}"
            assert paychecks_id != months_id, (
                f"PAYCHECKS and MONTHS should have different IDs, both are {paychecks_id}"
            )

    def test_income_unit_enum_matches_db(self, app, db):
        """Every IncomeUnitEnum member has a corresponding database row,
        and every database row has a corresponding enum member.
        No extra rows, no missing rows.
        """
        with app.app_context():
            db_rows = db.session.query(IncomeUnit).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in IncomeUnitEnum}

            assert db_names == enum_values, (
                f"IncomeUnit DB rows {db_names} do not match "
                f"IncomeUnitEnum values {enum_values}"
            )
            assert len(db_rows) == len(IncomeUnitEnum), (
                f"IncomeUnit has {len(db_rows)} rows but IncomeUnitEnum has "
                f"{len(IncomeUnitEnum)} members"
            )


class TestLedgerAccountClassRefCache:
    """Tests for ref_cache ledger-account-class resolution (posting ledger)."""

    def test_ledger_account_class_ref_cache(self, app, db):
        """ref_cache.ledger_account_class_id() returns a distinct positive int
        for every LedgerAccountClassEnum member.
        """
        with app.app_context():
            ids = {
                member: ref_cache.ledger_account_class_id(member)
                for member in LedgerAccountClassEnum
            }
            for member, class_id in ids.items():
                assert isinstance(class_id, int), (
                    f"ledger_account_class_id({member.name}) returned "
                    f"{type(class_id)}, expected int"
                )
                assert class_id > 0, (
                    f"{member.name} id should be positive, got {class_id}"
                )
            assert len(set(ids.values())) == len(LedgerAccountClassEnum), (
                f"LedgerAccountClassEnum members must have distinct IDs; "
                f"got {ids}"
            )

    def test_ledger_account_class_enum_matches_db(self, app, db):
        """Every LedgerAccountClassEnum member has exactly one DB row and
        every DB row has a member.  No extra rows, no missing rows.
        """
        with app.app_context():
            db_rows = db.session.query(LedgerAccountClass).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in LedgerAccountClassEnum}

            assert db_names == enum_values, (
                f"LedgerAccountClass DB rows {db_names} do not match "
                f"LedgerAccountClassEnum values {enum_values}"
            )
            assert len(db_rows) == len(LedgerAccountClassEnum), (
                f"LedgerAccountClass has {len(db_rows)} rows but "
                f"LedgerAccountClassEnum has {len(LedgerAccountClassEnum)} "
                f"members"
            )

    def test_is_debit_normal_correct_per_class(self, app, db):
        """ledger_class_is_debit_normal() returns the correct natural-balance
        side for every class, read through the cached meta map.

        Fundamental double-entry accounting: a debit increases an Asset or
        Expense balance (debit-normal -> True); a credit increases a
        Liability, Income (revenue), or Equity balance (credit-normal ->
        False).  These five values are the entire reason the
        ``LedgerAccountClass`` table exists, so they are pinned by hand.
        """
        expected = {
            LedgerAccountClassEnum.ASSET: True,
            LedgerAccountClassEnum.LIABILITY: False,
            LedgerAccountClassEnum.INCOME: False,
            LedgerAccountClassEnum.EXPENSE: True,
            LedgerAccountClassEnum.EQUITY: False,
        }
        with app.app_context():
            for member, is_debit_normal in expected.items():
                class_id = ref_cache.ledger_account_class_id(member)
                result = ref_cache.ledger_class_is_debit_normal(class_id)
                assert result is is_debit_normal, (
                    f"{member.name}: ledger_class_is_debit_normal("
                    f"{class_id})={result}, expected {is_debit_normal}"
                )

    def test_ledger_class_is_debit_normal_raises_on_unknown_id(self, app, db):
        """ledger_class_is_debit_normal() raises on an unknown class_id.

        The accessor is logic-bearing (it decides whether a reader negates
        an account's posting sum), so a bogus class_id must fail loud
        rather than silently default to a wrong-but-valid False.
        """
        with app.app_context():
            known = {
                ref_cache.ledger_account_class_id(m)
                for m in LedgerAccountClassEnum
            }
            bogus_id = max(known) + 1000
            with pytest.raises(KeyError):
                ref_cache.ledger_class_is_debit_normal(bogus_id)

    def test_ref_cache_fails_on_missing_ledger_class(self, app, db):
        """ref_cache.init() raises RuntimeError when a ledger-class row is
        missing.

        Proves the enum<->seed parity gate fires for the new posting-ledger
        ref table exactly as it does for the long-standing Status table:
        deleting the 'Asset' row leaves LedgerAccountClassEnum.ASSET
        unresolvable, which must be a fatal startup error, not a silent
        skip.
        """
        with app.app_context():
            asset = (
                db.session.query(LedgerAccountClass)
                .filter_by(name="Asset")
                .one()
            )
            db.session.delete(asset)
            db.session.flush()

            with pytest.raises(RuntimeError, match="Asset"):
                ref_cache.init(db.session)

            # Roll back so other tests aren't affected, then re-init clean.
            db.session.rollback()
            ref_cache.init(db.session)


class TestPostingKindRefCache:
    """Tests for ref_cache posting-kind resolution."""

    def test_posting_kind_ref_cache(self, app, db):
        """ref_cache.posting_kind_id() returns a positive int for TRANSFER."""
        with app.app_context():
            transfer_id = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            assert isinstance(transfer_id, int), (
                f"posting_kind_id(TRANSFER) returned {type(transfer_id)}, "
                f"expected int"
            )
            assert transfer_id > 0, (
                f"TRANSFER id should be positive, got {transfer_id}"
            )

    def test_posting_kind_resolves_income_and_expense(self, app, db):
        """ref_cache resolves the Step-3 INCOME and EXPENSE kinds distinctly.

        The two cash-leg kinds added in Build-Order Step 3 must each
        resolve to a distinct positive ID, distinct from Step 2's TRANSFER.
        This guards that the enum ``.value`` strings match the seeded
        ``ref.posting_kinds.name`` rows and that no two members collapse
        onto one row (a copy-paste value collision).
        """
        with app.app_context():
            transfer_id = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            income_id = ref_cache.posting_kind_id(PostingKindEnum.INCOME)
            expense_id = ref_cache.posting_kind_id(PostingKindEnum.EXPENSE)
            assert income_id > 0 and expense_id > 0, (
                f"income/expense kind ids must be positive, got "
                f"income={income_id}, expense={expense_id}"
            )
            assert len({transfer_id, income_id, expense_id}) == 3, (
                f"posting kind ids collide: transfer={transfer_id}, "
                f"income={income_id}, expense={expense_id}"
            )

    def test_posting_kind_resolves_loan_correction_kinds(self, app, db):
        """ref_cache resolves the Step-4 loan-correction kinds distinctly.

        The four legs of a confirmed loan payment's real-split correction
        (``principal`` / ``interest`` / ``escrow`` / ``refund``) added in
        Build-Order Step 4 must each resolve to a distinct positive ID,
        distinct from one another and from the Step 2/3 kinds.  This guards
        that the enum ``.value`` strings match the seeded
        ``ref.posting_kinds.name`` rows and that no two members collapse onto
        one row (a copy-paste value collision).
        """
        with app.app_context():
            existing = {
                ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
                ref_cache.posting_kind_id(PostingKindEnum.INCOME),
                ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
            }
            loan_kinds = {
                member: ref_cache.posting_kind_id(member)
                for member in (
                    PostingKindEnum.PRINCIPAL,
                    PostingKindEnum.INTEREST,
                    PostingKindEnum.ESCROW,
                    PostingKindEnum.REFUND,
                )
            }
            for member, kind_id in loan_kinds.items():
                assert isinstance(kind_id, int) and kind_id > 0, (
                    f"{member.name} id must be a positive int, got {kind_id}"
                )
            # Four distinct loan kinds, none colliding with the earlier three.
            assert len(set(loan_kinds.values())) == 4, loan_kinds
            assert existing.isdisjoint(set(loan_kinds.values())), (
                f"loan kinds {loan_kinds} collide with earlier kinds {existing}"
            )

    def test_posting_kind_enum_matches_db(self, app, db):
        """Every PostingKindEnum member has a DB row and vice versa.

        Step 2 seeds 'transfer'; Step 3 adds 'income'/'expense'; Step 4 adds
        the four loan-correction kinds.  This guards against a future kind
        added to the enum without a seed row (or vice versa).
        """
        with app.app_context():
            db_rows = db.session.query(PostingKind).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in PostingKindEnum}

            assert db_names == enum_values, (
                f"PostingKind DB rows {db_names} do not match "
                f"PostingKindEnum values {enum_values}"
            )
            assert len(db_rows) == len(PostingKindEnum), (
                f"PostingKind has {len(db_rows)} rows but PostingKindEnum "
                f"has {len(PostingKindEnum)} members"
            )


class TestPostingSourceRefCache:
    """Tests for ref_cache posting-source resolution."""

    def test_posting_source_ref_cache(self, app, db):
        """ref_cache.posting_source_id() returns a positive int for TRANSFER."""
        with app.app_context():
            transfer_id = ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER
            )
            assert isinstance(transfer_id, int), (
                f"posting_source_id(TRANSFER) returned {type(transfer_id)}, "
                f"expected int"
            )
            assert transfer_id > 0, (
                f"TRANSFER id should be positive, got {transfer_id}"
            )

    def test_posting_source_resolves_transaction(self, app, db):
        """ref_cache resolves the Step-3 TRANSACTION source distinctly.

        The ordinary-transaction source added in Build-Order Step 3 must
        resolve to a positive ID distinct from Step 2's TRANSFER, guarding
        that ``PostingSourceEnum.TRANSACTION.value`` matches its seeded
        ``ref.posting_sources.name`` row.
        """
        with app.app_context():
            transfer_id = ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER
            )
            transaction_id = ref_cache.posting_source_id(
                PostingSourceEnum.TRANSACTION
            )
            assert transaction_id > 0, (
                f"transaction source id must be positive, got {transaction_id}"
            )
            assert transaction_id != transfer_id, (
                f"transaction source id {transaction_id} collides with "
                f"transfer {transfer_id}"
            )

    def test_posting_source_resolves_loan_payment(self, app, db):
        """ref_cache resolves the Step-4 LOAN_PAYMENT source distinctly.

        The loan-payment correction source added in Build-Order Step 4 must
        resolve to a positive ID distinct from Step 2's TRANSFER and Step 3's
        TRANSACTION, guarding that
        ``PostingSourceEnum.LOAN_PAYMENT.value`` matches its seeded
        ``ref.posting_sources.name`` row.
        """
        with app.app_context():
            transfer_id = ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER
            )
            transaction_id = ref_cache.posting_source_id(
                PostingSourceEnum.TRANSACTION
            )
            loan_payment_id = ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT
            )
            assert loan_payment_id > 0, (
                f"loan_payment source id must be positive, got {loan_payment_id}"
            )
            assert len({transfer_id, transaction_id, loan_payment_id}) == 3, (
                f"source ids collide: transfer={transfer_id}, "
                f"transaction={transaction_id}, loan_payment={loan_payment_id}"
            )

    def test_posting_source_enum_matches_db(self, app, db):
        """Every PostingSourceEnum member has a DB row and vice versa."""
        with app.app_context():
            db_rows = db.session.query(PostingSource).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in PostingSourceEnum}

            assert db_names == enum_values, (
                f"PostingSource DB rows {db_names} do not match "
                f"PostingSourceEnum values {enum_values}"
            )
            assert len(db_rows) == len(PostingSourceEnum), (
                f"PostingSource has {len(db_rows)} rows but PostingSourceEnum "
                f"has {len(PostingSourceEnum)} members"
            )


class TestLedgerAccountKindRefCache:
    """Tests for ref_cache ledger-account-kind resolution (Step 4 discriminator)."""

    def test_ledger_account_kind_ref_cache(self, app, db):
        """ref_cache.ledger_account_kind_id() returns a distinct positive int
        for every LedgerAccountKindEnum member.

        The explicit row-kind discriminator added in Build-Order Step 4 must
        resolve every member (the four existing chart kinds plus the three
        per-loan kinds) to a distinct positive ID, guarding that the enum
        ``.value`` strings match the seeded ``ref.ledger_account_kinds.name``
        rows and that no two members collapse onto one row.
        """
        with app.app_context():
            ids = {
                member: ref_cache.ledger_account_kind_id(member)
                for member in LedgerAccountKindEnum
            }
            for member, kind_id in ids.items():
                assert isinstance(kind_id, int), (
                    f"ledger_account_kind_id({member.name}) returned "
                    f"{type(kind_id)}, expected int"
                )
                assert kind_id > 0, (
                    f"{member.name} id should be positive, got {kind_id}"
                )
            assert len(set(ids.values())) == len(LedgerAccountKindEnum), (
                f"LedgerAccountKindEnum members must have distinct IDs; "
                f"got {ids}"
            )

    def test_ledger_account_kind_enum_matches_db(self, app, db):
        """Every LedgerAccountKindEnum member has exactly one DB row and
        every DB row has a member.  No extra rows, no missing rows.
        """
        with app.app_context():
            db_rows = db.session.query(LedgerAccountKind).all()
            db_names = {row.name for row in db_rows}
            enum_values = {member.value for member in LedgerAccountKindEnum}

            assert db_names == enum_values, (
                f"LedgerAccountKind DB rows {db_names} do not match "
                f"LedgerAccountKindEnum values {enum_values}"
            )
            assert len(db_rows) == len(LedgerAccountKindEnum), (
                f"LedgerAccountKind has {len(db_rows)} rows but "
                f"LedgerAccountKindEnum has {len(LedgerAccountKindEnum)} "
                f"members"
            )

    def test_ref_cache_fails_on_missing_ledger_account_kind(self, app, db):
        """ref_cache.init() raises RuntimeError when a kind row is missing.

        Proves the enum<->seed parity gate fires for the new discriminator
        ref table exactly as it does for the long-standing Status table:
        deleting the 'linked' row leaves LedgerAccountKindEnum.LINKED
        unresolvable, which must be a fatal startup error, not a silent skip.
        """
        with app.app_context():
            linked = (
                db.session.query(LedgerAccountKind)
                .filter_by(name="linked")
                .one()
            )
            db.session.delete(linked)
            db.session.flush()

            with pytest.raises(RuntimeError, match="linked"):
                ref_cache.init(db.session)

            # Roll back so other tests aren't affected, then re-init clean.
            db.session.rollback()
            ref_cache.init(db.session)
