"""
Shekel Budget App -- Transaction Service Tests

Tests for ``app.services.transaction_service`` -- the cross-cutting
helpers used by both the manual ``mark_done`` route and the
carry-forward envelope branch (Phase 4 of
``docs/carry-forward-aftermath-implementation-plan.md``).

Each test verifies exact Decimal values and explicit arithmetic so a
reviewer can recompute the expected values by hand.  Tests are
deliberately kept small and single-purpose so a regression at any
contract boundary surfaces with a precise failure message.
"""

from datetime import date, datetime, timedelta

from app.utils.dates import display_today
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettlementBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.journal_entry import JournalEntry
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import posting_service, status_seam, transaction_service
# The leaf, imported for ONE control that spies on a private name the package
# does not re-export -- see
# ``test_the_live_figure_is_resolved_BEFORE_the_status_flip`` for why patching
# the package attribute would grade nothing.
from app.services.transaction_service import _settle
from app.services.row_valuation import owned_contribution, settled_figure
from tests._test_helpers import (
    make_every_period_rule,
    net_posted_by_day,
    settlement_basis_id,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_entry(txn_id, user_id, amount, description, *,
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    The transaction service operates on already-loaded entries, so
    these tests construct entries with raw ORM access to keep the
    setup focused on the helper's contract.
    """
    entry = TransactionEntry(
        transaction_id=txn_id,
        # The parent's account, resolved from the id this helper takes: an
        # entry's account IS its parent's, and the schema refuses any other
        # value (``fk_transaction_entries_parent_account``).
        account_id=db.session.get(Transaction, txn_id).account_id,
        user_id=user_id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _make_envelope_template(seed_user, *, txn_type_name="Expense",
                            default_amount="500.00"):
    """Create an envelope-tracked template of the requested type.

    Mirrors the seed_entry_template fixture but parameterizes the
    transaction type so the income-side branch can be exercised.
    """
    txn_type = (
        db.session.query(TransactionType)
        .filter_by(name=txn_type_name).one()
    )

    rule = make_every_period_rule(db.session, seed_user["user"].id)

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=txn_type.id,
        name=f"Tracked {txn_type_name}",
        default_amount=Decimal(default_amount),
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()
    return template


def _make_projected_txn(seed_user, period, *, template,
                        estimated_amount="500.00"):
    """Create a Projected transaction tied to the supplied template."""
    projected_status = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected_status.id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        estimated_amount=Decimal(estimated_amount),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


# ── Happy-Path Tests ─────────────────────────────────────────────────


class TestSettleFromEntriesExpense:
    """Settling expense transactions produces DONE status and entry-sum actual."""

    def test_expense_with_entries_sets_done_and_sum(
        self, app, db, seed_user, seed_periods,
    ):
        """Expense + multiple entries: status=DONE, actual=sum, day recorded.

        Setup: $150 + $250 = $400 of debit entries against a $500 envelope.
        Expected: status_id == DONE, actual_amount == 400.00, day recorded.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "150.00", "Kroger")
            _make_entry(txn.id, user_id, "250.00", "Target")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 150 + 250 = 400
            assert settled_figure(txn) == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on is not None

    def test_expense_includes_credit_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """Both debit and credit entries contribute to actual_amount.

        Per ``purchases_total`` semantics, credit entries
        count toward total spending for analytics; the credit/checking
        impact split is handled separately by the CC payback workflow.

        Setup: $300 debit + $100 credit = $400.
        Expected: actual_amount == 400.00.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "300.00", "Kroger")
            _make_entry(
                txn.id, user_id, "100.00", "Amazon", is_credit=True,
            )
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 300 + 100 = 400
            assert settled_figure(txn) == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_expense_zero_entries_settles_at_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Empty entry list settles at actual=0 (carry-forward rollover case).

        The carry-forward envelope branch invokes the helper on
        envelope rows that may have no entries; the contract is
        ``actual_amount == Decimal("0")``, NOT a fallback to
        ``estimated_amount``.  This frees the full estimated amount
        to roll into the next period's canonical row.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="100.00",
            )

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            assert settled_figure(txn) == Decimal("0.00")
            assert txn.estimated_amount == Decimal("100.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on is not None

    def test_expense_overspend_records_full_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Overspending records the full entry sum without clamping.

        If the wife spends $120 against a $100 envelope, the actual
        reflects the truth ($120), not the cap ($100).  The
        carry-forward helper uses this to compute leftover =
        max(0, estimated - actual) elsewhere; clamping here would
        hide the overspend signal from analytics.

        Setup: $80 + $40 = $120 against a $100 estimate.
        Expected: actual_amount == 120.00, estimated stays 100.00.
        """
        with app.app_context():
            template = _make_envelope_template(
                seed_user, default_amount="100.00",
            )
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="100.00",
            )
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "80.00", "Kroger")
            _make_entry(txn.id, user_id, "40.00", "Target")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 80 + 40 = 120 -- exceeds the 100 estimate, intentionally.
            assert settled_figure(txn) == Decimal("120.00")
            assert txn.estimated_amount == Decimal("100.00")


class TestSettleFromEntriesIncome:
    """Settling income transactions produces RECEIVED status.

    Phase 2 of the carry-forward aftermath plan (committed in
    ``feat(template): rename ... reject envelope semantics on income``)
    rejects ``is_envelope=True`` on income templates at the schema
    layer, so this branch is not reachable via the normal template
    create/update flow.  The branch must remain correct because
    direct DB writes can still produce the combination, and the
    helper is the lowest-level mutation point -- its behavior
    documents the contract regardless of how the row got into this
    state.
    """

    def test_income_with_entries_sets_received(
        self, app, db, seed_user, seed_periods,
    ):
        """Income + entries: status=RECEIVED, actual=sum, day recorded."""
        with app.app_context():
            template = _make_envelope_template(
                seed_user, txn_type_name="Income", default_amount="2500.00",
            )
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="2500.00",
            )
            user_id = seed_user["user"].id

            _make_entry(txn.id, user_id, "1000.00", "Direct deposit 1")
            _make_entry(txn.id, user_id, "1500.00", "Direct deposit 2")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            # 1000 + 1500 = 2500
            assert settled_figure(txn) == Decimal("2500.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.RECEIVED)
            assert txn.settled_on is not None


# ── Settle-day handling ──────────────────────────────────────────────


class TestSettleFromEntriesSettleDay:
    """The helper records the settle day through the seam, and takes no knob.

    **It accepted an explicit ``paid_at`` until plan step X-f1, and the
    parameter was DEAD** (ruling R-EC).  Its docstring named the carry-forward
    envelope branch as the caller that supplied one;
    ``carry_forward_service._execute`` calls it with no such argument and always
    has, and an AST sweep of ``app/`` found ZERO call sites passing it.  The
    test that pinned it is deleted with it rather than renamed: a test for a
    knob nothing turns is a test for speculative flexibility, which the coding
    standards forbid outright.  A caller that genuinely means "this settled on
    another day" corrects it afterwards, through the edit door ruling R-ED
    builds.
    """

    def test_the_settle_day_is_the_users_today(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling from entries records the user's civil day, via the seam.

        A ``date``, not an instant, and not the process's UTC day: the helper
        does no dating of its own -- it hands the status change to
        ``status_seam.apply_status_change``, whose stamp is ``display_today()``.
        The zone rule itself is pinned at the seam
        (``test_status_seam.py``); this pins that the envelope path really goes
        through it rather than reaching for a clock of its own, which it did
        (``db.func.now()``, a fourth database-clock reach) before the seam
        absorbed it.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "10.00", "Test")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            db.session.refresh(txn)
            assert txn.settled_on == display_today()
            assert not isinstance(txn.settled_on, datetime)


# ── Precondition Tests ───────────────────────────────────────────────


class TestSettleFromEntriesPreconditions:
    """Each documented precondition raises ValidationError on violation.

    The helper is the lowest-level "settle a tracked row" mutation,
    used by both manual mark-done and the carry-forward batch.  A
    permissive helper would silently corrupt state when fed a
    soft-deleted row, a transfer shadow, an immutable status, or a
    non-envelope template; defensive validation keeps the bug
    surface narrow.  Each test below confirms one rule.
    """

    def test_rejects_soft_deleted_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Soft-deleted transactions cannot be resurrected via settle."""
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            txn.is_deleted = True
            db.session.commit()
            txn_id = txn.id

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "soft-deleted" in str(exc_info.value)

            # No state change should have leaked through -- the helper
            # raised before any mutation, so a rollback is just hygiene.
            db.session.rollback()
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.settled_amount is None
            assert reloaded.status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )
            assert reloaded.is_deleted is True

    def test_rejects_template_less_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Transactions without a template are not envelope-tracked.

        Ad-hoc transactions (created without a recurrence template)
        have no envelope semantics; mark_done's manual-actual branch
        handles them.  The helper is for tracked rows only.
        """
        with app.app_context():
            projected_status = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected_status.id,
                name="Ad-hoc expense",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "envelope-tracked" in str(exc_info.value)

    def test_rejects_non_envelope_template(
        self, app, db, seed_user, seed_periods,
    ):
        """Templates with is_envelope=False are not entry-tracked."""
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            rule = make_every_period_rule(db.session, seed_user["user"].id)
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                recurrence_rule_id=rule.id,
                transaction_type_id=expense_type.id,
                name="Rent",
                default_amount=Decimal("1200.00"),
                is_envelope=False,
            )
            db.session.add(template)
            db.session.flush()

            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="1200.00",
            )

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "envelope-tracked" in str(exc_info.value)

    def test_rejects_transfer_shadow(self, app, db, seed_user, seed_periods):
        """Transfer shadows must settle through transfer_service.

        The invariants in CLAUDE.md require shadow legs and the
        parent transfer to mutate together; settling one shadow in
        isolation would break the invariant ``shadow amounts and
        statuses always equal the parent transfer's``.

        Uses ``no_autoflush`` because the test stamps a synthetic
        ``transfer_id`` in memory that would violate the FK if
        SQLAlchemy autoflushed before the helper's guard fires.  The
        helper itself only reads the attribute -- the FK never enters
        the picture in production callers.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            db.session.commit()

            with db.session.no_autoflush:
                # The helper inspects ``transfer_id`` directly without
                # a DB query, so an in-memory non-None value is enough
                # to exercise the guard -- avoiding a real
                # transfer/category setup that the rule does not
                # depend on.
                txn.transfer_id = 999_999

                with pytest.raises(ValidationError) as exc_info:
                    transaction_service.settle_from_entries(txn)
                assert "transfer" in str(exc_info.value).lower()

            db.session.rollback()

    def test_rejects_already_settled_status(
        self, app, db, seed_user, seed_periods,
    ):
        """A second call on a Paid transaction raises (no idempotent re-settle).

        The Paid status is immutable per the ``is_immutable`` flag on
        ``ref.statuses``.  Attempting to re-settle would update
        the settle day and possibly ``actual_amount`` on a finalised
        row, which is meaningless and indicates a caller bug.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "100.00", "Kroger")
            db.session.flush()

            transaction_service.settle_from_entries(txn)
            db.session.commit()

            # Second call should refuse.
            db.session.refresh(txn)
            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "immutable" in str(exc_info.value).lower()

    def test_rejects_cancelled_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Cancelled transactions stay cancelled.

        ``CANCELLED`` is immutable so the user's deliberate
        cancellation is preserved.  Settling would silently override
        the cancel decision -- a financial bug masquerading as a
        no-op.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            cancelled_status = (
                db.session.query(Status).filter_by(name="Cancelled").one()
            )
            txn.status_id = cancelled_status.id
            db.session.flush()

            with pytest.raises(ValidationError) as exc_info:
                transaction_service.settle_from_entries(txn)
            assert "immutable" in str(exc_info.value).lower()


# ── Caller-Owned Session Lifecycle ───────────────────────────────────


class TestSettleFromEntriesSessionContract:
    """Verify the helper does not flush or commit on its own.

    The carry-forward batch needs the helper to participate in a
    single atomic transaction; if the helper auto-committed, a
    failure later in the batch would leave a half-applied state.
    """

    def test_does_not_commit_on_success(
        self, app, db, seed_user, seed_periods,
    ):
        """Mutations are visible only after the caller commits."""
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            user_id = seed_user["user"].id
            _make_entry(txn.id, user_id, "75.00", "Pharmacy")
            # Persist setup so a rollback after the helper call only
            # reverts the helper's own mutations, not the test fixtures.
            db.session.commit()
            txn_id = txn.id

            transaction_service.settle_from_entries(txn)

            # Attribute mutations are visible on the in-memory object.
            assert settled_figure(txn) == Decimal("75.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            # A rollback should reverse the helper's writes -- proving
            # the helper itself never committed or flushed.
            db.session.rollback()

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded is not None
            assert reloaded.settled_amount is None
            assert reloaded.status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )


class TestSettleTransactionTheVerb:
    """``settle_transaction`` -- ruling **R-FA**'s verb, graded as a SERVICE.

    **These exist because an adversarial review found the verb had no
    service-tier control at all.**  Every assertion about it reached it through
    ``POST /transactions/<id>/mark-done``, so its contract was graded only in
    the shape one HTTP door happens to call it in -- and the whole reason plan
    step X-f2-c2 extracted it is that a SECOND, non-HTTP caller arrives next
    leaf (the reconcile panel's tick) whose shape nothing pinned.
    """

    def test_a_transfer_shadow_is_refused(self, app, seed_user, seed_periods):
        """A shadow cannot settle through this verb (transfer invariants 3-4).

        **The control for a defect the first draft of this verb shipped.**  Its
        docstring claimed ``settle_from_entries`` refused a shadow by
        precondition.  That guard is real but UNREACHABLE for a shadow: a
        shadow carries no ``template_id`` and no ``is_envelope``, so
        ``tracks_purchases`` is False and it always takes the MANUAL branch,
        where nothing looked at ``transfer_id``.  A review ran it and settled
        one leg of a pair -- expense shadow Paid, income shadow Projected,
        parent transfer Projected -- which is ``CLAUDE.md`` transfer invariants
        3 and 4 broken in one call, silently, because the posting reconcile
        returns ``[]`` for a shadow so the ledger stays flat under a grid that
        shows one leg settled.

        Shown to FIRE: deleting the ``transfer_id`` guard settles the row.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            # Make it a shadow the cheap way -- the verb reads exactly one
            # field to decide, and a real transfer pair would grade the
            # transfer service rather than this guard.
            txn.transfer_id = 1
            status_before = txn.status_id

            with pytest.raises(ValidationError) as exc:
                transaction_service.settle_transaction(txn)

            assert "transfer shadow" in str(exc.value)
            assert "transfer_service.update_transfer" in str(exc.value)
            # A refused call leaves the row untouched.
            assert txn.status_id == status_before
            assert txn.settled_on is None
            assert txn.settled_amount is None

    def test_a_soft_deleted_row_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """A settle cannot resurrect a deleted row -- on EITHER branch.

        **Finding N-233.**  The envelope branch refused this from the
        beginning, with its own docstring giving the reason, and the MANUAL
        branch never did -- so the refusal was a property of which branch a row
        happened to take rather than of the verb.  It was REACHABLE:
        ``get_accessible_transaction`` does not filter ``is_deleted``, so
        ``POST /transactions/<id>/mark-done`` on a soft-deleted non-envelope row
        flipped it into the settled band and stamped a settle day, while
        ``effective_amount`` valued it at ``Decimal("0")`` -- a row that reads
        Paid on every surface and is worth nothing on all of them.  Production
        carries 102 soft-deleted rows, every one of them Projected.

        Graded on the MANUAL branch specifically, because that is the half that
        had no guard.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            txn.is_deleted = True
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transaction_service.settle_transaction(txn)

            assert "soft-deleted" in str(exc.value)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None

    def test_settle_amount_refuses_a_soft_deleted_row(
        self, app, seed_user, seed_periods,
    ):
        """The read refuses what the write refuses, for the same reason.

        A deleted row values at ``Decimal("0")`` through ``effective_amount``,
        so answering here would publish a figure ``settle_transaction`` will not
        book -- the exact argument the transfer-shadow half of
        ``reject_unsettleable`` already made, applied to the rule that was
        missing beside it.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            txn.is_deleted = True
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transaction_service.settle_amount(txn)

            assert "soft-deleted" in str(exc.value)

    def test_an_envelope_with_entries_ignores_a_supplied_actual(
        self, app, seed_user, seed_periods,
    ):
        """sum(entries) wins over a caller-supplied actual, and posts.

        The precedence half of act 1: an envelope's entries ARE the record of
        what it cost, so a caller offering a different figure is offering an
        opinion about a derived value.  $40 + $50 against a $500 estimate and a
        supplied $999.99 settles at $90.00.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            db.session.flush()
            _make_entry(txn.id, seed_user["user"].id,
                        Decimal("40.00"), "Walmart")
            _make_entry(txn.id, seed_user["user"].id,
                        Decimal("50.00"), "Sam's")
            db.session.flush()

            transaction_service.settle_transaction(
                txn, submitted=Decimal("999.99"),
            )

            assert settled_figure(txn) == Decimal("90.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on == display_today()

    def test_a_plain_row_honours_a_supplied_actual(
        self, app, seed_user, seed_periods,
    ):
        """A row with no entries books what the caller supplied.

        The other half of act 1, and the channel ruling **R-FB** gives a first
        real caller: a bill's tick may correct its amount.  $250.00 supplied
        against a $500.00 estimate books $250.00.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            db.session.flush()

            transaction_service.settle_transaction(
                txn, submitted=Decimal("250.00"),
            )

            assert txn.settled_amount == Decimal("250.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_a_plain_row_with_no_correction_records_what_it_booked(
        self, app, seed_user, seed_periods,
    ):
        """Nothing typed still RECORDS what moved -- the one-click path.

        **This asserted the opposite until plan step X-au-c3**: the figure
        column was left NULL, because it doubled as the "a human typed this"
        signal and writing the estimate into it would have manufactured a
        correction nobody made.  The two questions have two columns now, so a
        one-click settle records the figure it booked and says the basis is
        ``derived``.  The row's PLAN is untouched -- no settle writes a plan
        column -- and the figure it books is the same ``$500.00`` the old
        fall-back answered, so no balance moves.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            db.session.flush()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is a column of its own now, so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert settled_figure(txn) == Decimal("500.00")
            assert txn.estimated_amount == Decimal("500.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_an_illegal_transition_raises_and_leaves_the_row_alone(
        self, app, seed_user, seed_periods,
    ):
        """Settling an already-Cancelled row is refused by the state machine.

        The verb's ``ValidationError`` surface, which the route renders as a
        designed 400.  Graded here rather than only through HTTP so the next
        caller knows what it must catch.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
            db.session.flush()

            with pytest.raises(ValidationError):
                transaction_service.settle_transaction(txn)

            assert txn.settled_on is None
            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)

    def test_income_takes_received_and_expense_takes_paid(
        self, app, seed_user, seed_periods,
    ):
        """``settled_status_id`` is the ONE spelling of the income/expense pick.

        It was written twice before plan step X-f2-c2 -- once in the mark-done
        route and once in ``settle_from_entries``, whose comment said it
        "mirrors" the route.  Both ends are graded here so a future third
        spelling has something to fail against.
        """
        with app.app_context():
            expense_tpl = _make_envelope_template(seed_user)
            expense_tpl.is_envelope = False
            expense = _make_projected_txn(seed_user, seed_periods[0],
                                          template=expense_tpl)
            income_tpl = _make_envelope_template(
                seed_user, txn_type_name="Income",
            )
            income_tpl.is_envelope = False
            income = _make_projected_txn(seed_user, seed_periods[0],
                                         template=income_tpl)
            db.session.flush()

            assert transaction_service.settled_status_id(expense) == (
                ref_cache.status_id(StatusEnum.DONE)
            )
            assert transaction_service.settled_status_id(income) == (
                ref_cache.status_id(StatusEnum.RECEIVED)
            )

            transaction_service.settle_transaction(expense)
            transaction_service.settle_transaction(income)
            assert expense.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert income.status_id == ref_cache.status_id(StatusEnum.RECEIVED)


class TestASettleBooksTheFreshestFigure:
    """Plan step **X-aq**, ruling **R-FE**, finding **N-224**.

    **The defect these grade is two answers to one question.**
    ``transactions.estimated_amount`` is a CACHE of a derivation, and
    ``income_service.live_projected_net`` recomputes a salary-linked paycheck at
    READ time without writing back -- so every balance surface showed the live
    figure while every settle door booked the stored one, and settling moved the
    projected end balance by the difference.  That is the exact invariant ruling
    R-DH (c) states and plan step X-f3 is ship-gated on.

    The setup is the sibling suite's, so the expected net is not invented here:
    a ``$104,000`` profile over 26 periods with no tax configs seeded nets
    ``$4,000.00`` a period, which
    ``test_income_service.TestLiveProjectedNet.test_recomputes_live_ignoring_stored_amount``
    pins independently.  Every row below stores ``$1.00`` against it, so a test
    that passed by reading the cache would have to report ``$1.00``.
    """

    @staticmethod
    def _salary_row(seed_user, period, *, estimated="1.00", is_override=False):
        """Return a Projected income row whose template IS a salary profile."""
        # pylint: disable=import-outside-toplevel  -- the salary models are not
        # part of this module's subject and importing them at the top would put
        # the paycheck stack on every transaction-service test's load path.
        from app.models.ref import FilingStatus
        from app.models.salary_profile import SalaryProfile

        filing = db.session.query(FilingStatus).first()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="X-aq Salary",
            annual_salary=Decimal("104000.00"),
            state_code="NC",
            pay_periods_per_year=26,
            is_active=True,
        )
        db.session.add(profile)
        db.session.flush()

        template = _make_envelope_template(seed_user, txn_type_name="Income")
        template.is_envelope = False
        profile.template_id = template.id
        db.session.flush()

        txn = _make_projected_txn(
            seed_user, period, template=template, estimated_amount=estimated,
        )
        txn.is_override = is_override
        db.session.flush()
        return txn

    def test_a_stale_estimate_settles_at_the_live_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The row books ``$4,000.00``, not the ``$1.00`` in its column.

        The headline of X-aq.  Before it, this settle booked ``$1.00`` while
        the grid cell beside it read ``$4,000.00`` -- ``$3,999.00`` of income
        deleted from the projection by pressing Mark Paid.

        **The refresh lands in ``estimated_amount`` and ``actual_amount`` stays
        NULL**, which is the developer's ruling of 2026-08-11 amending R-FE.
        The stale column IS the cache, so reconciling it is what the settle
        does; ``actual_amount`` means "a human entered this fact" and three
        subsystems read its NULL-ness that way -- ``income_service`` (a settled
        income row's actual is never a recomputable projection),
        ``spending_analysis`` (only an explicitly entered actual is a surprise)
        and the grid cell (which strikes through the estimate whenever the two
        differ).  A machine write there is indistinguishable from a correction
        afterwards, and permanent: the row leaves ``live_projected_net``'s
        Projected-only candidate set at this very flip.
        """
        with app.app_context():
            txn = self._salary_row(seed_user, seed_periods[0])
            db.session.commit()

            transaction_service.settle_transaction(txn)

            assert txn.estimated_amount == Decimal("4000.00")
            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is a column of its own now, so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert settled_figure(txn) == Decimal("4000.00")
            assert owned_contribution(txn) == Decimal("4000.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.RECEIVED)

    def test_a_supplied_actual_still_wins_over_the_live_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """A figure a human typed beats every derivation.

        The precedence half of act 1 and the reason X-f2-c2's correctable box
        (ruling **R-FB**) is safe: the panel's prefilled amount is what the
        statement says, and a live recompute must not overwrite it.

        **It is also the control for the two columns being SEPARABLE**, which
        is what the developer's 2026-08-11 amendment to R-FE bought.  Both
        facts survive one settle and neither is readable off the other: the
        machine's recompute lands in ``estimated_amount`` (``$4,000.00``, what
        the projection was holding) and the human's in ``actual_amount``
        (``$3,912.44``, what the bank really paid).  Under the shipped-then-
        withdrawn single-column version the recompute was invisible here, so
        nothing could tell a stale projection from an accurate one after the
        fact -- and plan step X-ar's reconciler could not have cleaned it.
        """
        with app.app_context():
            txn = self._salary_row(seed_user, seed_periods[0])
            db.session.commit()

            transaction_service.settle_transaction(
                txn, submitted=Decimal("3912.44"),
            )

            assert txn.settled_amount == Decimal("3912.44")
            assert txn.estimated_amount == Decimal("4000.00")
            assert owned_contribution(txn) == Decimal("3912.44")

    def test_an_overridden_row_is_not_re_derived(
        self, app, db, seed_user, seed_periods,
    ):
        """``is_override`` means the user set this amount, so nothing recomputes.

        The rule is the projection's own -- ``live_projected_net`` drops an
        overridden row -- and this verb inherits it by ASKING that producer
        rather than restating which rows carry a live value.  Stored
        ``$1,234.56`` settles at ``$1,234.56`` with the column left NULL.
        """
        with app.app_context():
            txn = self._salary_row(
                seed_user, seed_periods[0],
                estimated="1234.56", is_override=True,
            )
            db.session.commit()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is a column of its own now, so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert settled_figure(txn) == Decimal("1234.56")
            assert owned_contribution(txn) == Decimal("1234.56")

    def test_an_agreeing_live_figure_leaves_the_column_null(
        self, app, db, seed_user, seed_periods,
    ):
        """Nothing is written when the two answers already agree.

        **Not an optimisation -- a signal.**  ``actual_amount`` is NULL on every
        uncorrected row, which is what makes "a human typed a figure here"
        readable at all; ruling R-FB's own production measurement ("11 of 93
        settled bills carry a hand-typed correction") is made of exactly that
        NULL.  Storing ``$4,000.00`` into the actual because the derivation
        agreed would erase it.
        """
        with app.app_context():
            txn = self._salary_row(
                seed_user, seed_periods[0], estimated="4000.00",
            )
            db.session.commit()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is a column of its own now, so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert settled_figure(txn) == Decimal("4000.00")
            assert owned_contribution(txn) == Decimal("4000.00")

    def test_a_row_with_no_live_seam_is_untouched(
        self, app, db, seed_user, seed_periods,
    ):
        """An ordinary bill is not a candidate, so X-aq writes nothing.

        The blast-radius control.  ``live_projected_net`` wants an income row
        linked to an active profile and ``live_loan_transfer_amounts`` wants a
        transfer shadow, so an expense on a plain template matches neither and
        the settle is byte-identical to its pre-X-aq behaviour.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            db.session.commit()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is a column of its own now, so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert settled_figure(txn) == Decimal("500.00")
            assert owned_contribution(txn) == Decimal("500.00")

    def test_an_envelope_with_entries_still_settles_at_its_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """The envelope branch is untouched: entries beat every derivation.

        Act 1's ordering, re-graded from the X-aq side.  A row that is BOTH
        envelope-tracked with entries AND income keeps ``sum(entries)`` -- the
        freshest answer for an envelope is its own record of what it cost, not
        a recompute of what it was expected to be.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user,
                                               txn_type_name="Income")
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            db.session.flush()
            _make_entry(txn.id, seed_user["user"].id,
                        Decimal("12.34"), "Refund")
            db.session.commit()

            transaction_service.settle_transaction(txn)

            assert settled_figure(txn) == Decimal("12.34")

    # ``test_a_hand_typed_actual_is_NEVER_overwritten`` lived here until plan
    # step X-au-c3, and its state is unconstructible now rather than merely
    # untested.  It built a PROJECTED salary row carrying
    # ``actual_amount = 3880.15`` and proved ``_freshest_amount``'s fourth
    # guard kept the settle from booking ``$4,000.00`` over it.  A figure
    # RECORDS a settle now, so ``ck_transactions_settled_amount_needs_basis``
    # refuses one on a row whose money has not moved -- and the door that
    # produced the state is gone with it: the full-edit form's Actual box was
    # deleted, so the only way a human's pre-settle figure reaches a row is
    # ``estimated_amount``, which ``routes/transactions/mutations`` DOES flag
    # ``is_override`` for.  That flag is what excludes the row from the
    # projection's override map, and
    # ``test_an_overridden_row_is_not_re_derived`` above is the control on it.
    # The guard was deleted rather than translated to ``settled_basis_id is not
    # None``: every caller of ``_freshest_amount`` is pre-settle and both live
    # producers are Projected-only, so no single-line mutation of the
    # translated guard could fail a test, and a guard whose only possible test
    # cannot fail is not a guard (finding **N-184**'s rule).

    def test_a_supplied_figure_equal_to_the_booked_one_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The echo rule applies to a CALLER's figure too, not just a derived one.

        It was two rules before an adversarial review: the verb suppressed an
        echo of the LIVE figure and the reconcile writer separately suppressed
        an echo of the SUBMITTED one, so the same column meant different things
        depending on which door wrote it.  Plan step X-ap routes a form here
        that submits ``actual_amount`` on EVERY save, so the caller-supplied
        half is the half that matters next.

        `$500.00` supplied against a `$500.00` estimate leaves the column NULL.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            db.session.commit()

            transaction_service.settle_transaction(
                txn, submitted=Decimal("500.00"),
            )

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is a column of its own now, so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert settled_figure(txn) == Decimal("500.00")
            assert owned_contribution(txn) == Decimal("500.00")

    def test_settle_amount_refuses_a_transfer_shadow(
        self, app, db, seed_user, seed_periods,
    ):
        """The valuation refuses what the verb refuses to book.

        ``settle_amount`` is PUBLIC and the reconcile panel reads it, so a
        version that priced a shadow off the loan-payment seam would publish a
        figure ``settle_transaction`` then refuses -- which is what plan step
        X-f2-c3 would have walked into with the transfer arm.  One rule
        (``reject_unsettleable``), three doors.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            txn.transfer_id = 1

            with pytest.raises(ValidationError) as exc:
                transaction_service.settle_amount(txn)

            assert "transfer shadow" in str(exc.value)

    def test_the_live_figure_is_resolved_BEFORE_the_status_flip(
        self, app, db, seed_user, seed_periods,
    ):
        """Order is load-bearing, and this is the control that says so.

        ``live_projected_net`` filters to Projected rows, so a verb that asked
        AFTER ``apply_status_change`` would always be handed an empty map and
        would silently book the cache -- passing every other test in this class
        except this one.  Grading it as an ORDER rather than as an outcome:
        the row is Projected at the moment the resolver is called.

        Shown to FIRE: moving the :func:`_reconcile_cached_amount` call below
        ``apply_status_change`` books ``$1.00``.

        **The LIST is the control on the one-resolution rule too** (developer
        ruling, 2026-08-17).  It is asserted as a whole rather than by
        membership, so a settle that resolved the derivation twice -- which is
        what ``booked``, the echo comparison and the cache refresh each doing
        their own would produce -- fails here with ``[2, 2] != [2]``.  That is
        the measurement, not a claim about it.

        **The spy is installed on the LEAF that owns the name**, not on the
        package that re-exports the verb, and the difference is not cosmetic:
        ``_reconcile_cached_amount`` resolves ``_freshest_amount`` as a global
        of its own module, so patching a package attribute would intercept
        nothing and this control would pass while grading nothing.  It moved
        with the code at plan step X-f2-c3, which made ``transaction_service``
        a package; the assertion is unchanged.
        """
        with app.app_context():
            txn = self._salary_row(seed_user, seed_periods[0])
            db.session.commit()

            seen_status = []
            real = _settle._freshest_amount  # noqa: SLF001

            def _spy(row, basis):
                seen_status.append(row.status_id)
                return real(row, basis)

            _settle._freshest_amount = _spy  # noqa: SLF001
            try:
                transaction_service.settle_transaction(txn)
            finally:
                _settle._freshest_amount = real  # noqa: SLF001

            assert seen_status == [
                ref_cache.status_id(StatusEnum.PROJECTED),
            ]
            assert txn.estimated_amount == Decimal("4000.00")
            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is a column of its own now, so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert settled_figure(txn) == Decimal("4000.00")


class TestApplyRequestedStatusTheDoorVerb:
    """``apply_requested_status`` -- the route layer's ONE status entry point.

    **It exists because a ROUTE was deciding what a status change means.**  The
    transaction PATCH handler and the cancel handler each called
    ``status_seam.apply_status_change`` -- the MECHANICS primitive, which
    assigns the column and posts nothing -- so the PATCH door could flip a row
    into the settled band without ever asking what the row was worth (finding
    **N-219**).  These grade the verb's two acts: the status change, and the
    ledger reconcile that every status change owes.
    """

    @staticmethod
    def _plain_row(seed_user, period, *, estimated_amount="100.00"):
        """Return a Projected, NON-envelope row: the verb's manual branch."""
        template = _make_envelope_template(seed_user)
        template.is_envelope = False
        txn = _make_projected_txn(
            seed_user, period, template=template,
            estimated_amount=estimated_amount,
        )
        db.session.flush()
        return txn

    def test_a_status_change_reconciles_the_ledger(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled status posts the row's effect, in the same call.

        Act 2 is the point: the seam alone assigns a column and posts nothing,
        so a door that called it settled a row while the double-entry ledger
        stayed flat.  A $100.00 expense settles and exactly ONE journal entry
        appears against it, booking $100.00 out of the cash account.

        Shown to FIRE: deleting the reconcile leaves zero entries.
        """
        with app.app_context():
            txn = self._plain_row(seed_user, seed_periods[0])

            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on == display_today()
            entries = (
                db.session.query(JournalEntry)
                .filter_by(transaction_id=txn.id).all()
            )
            assert len(entries) == 1
            # effective_amount == estimated_amount == 100.00, nothing credited,
            # so the cash account's settled effect is a 100.00 outflow.
            assert posting_service.settled_transaction_effect(
                seed_user["account"].id, seed_user["scenario"].id,
            ) == Decimal("-100.00")

    def test_a_cancel_leaves_the_ledger_flat(
        self, app, db, seed_user, seed_periods,
    ):
        """Cancelling a Projected row posts nothing, and that IS the reconcile.

        The non-settled arm.  A Projected row has never posted, so reconciling
        to Cancelled's empty target is an idempotent no-op -- the property the
        cancel handler used to spell out for itself and now inherits.  The
        settle day is cleared by the seam, because a cancelled row's money
        never moved.
        """
        with app.app_context():
            txn = self._plain_row(seed_user, seed_periods[0])

            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert txn.settled_on is None
            assert (
                db.session.query(JournalEntry)
                .filter_by(transaction_id=txn.id).count() == 0
            )

    def test_an_illegal_transition_raises_and_posts_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A refused transition leaves both the row and the ledger untouched.

        Cancelled -> Paid is not in the transaction workflow: a cancelled row
        must be reprojected first, so the audit trail records both moves.  The
        verb must refuse BEFORE it reconciles -- a reconcile run against a
        status the state machine rejected would post an effect the row is not
        entitled to.
        """
        with app.app_context():
            txn = self._plain_row(seed_user, seed_periods[0])
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )

            with pytest.raises(ValidationError) as exc:
                transaction_service.apply_requested_status(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                )

            assert "transition" in str(exc.value)
            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert (
                db.session.query(JournalEntry)
                .filter_by(transaction_id=txn.id).count() == 0
            )


class TestARevertKeepsWhatMovedAndReleasesTheAssertion:
    """The retention round trips, end to end through the real verbs.

    **The whole of plan step X-au-c3's third design**, and the arm the suite
    could not see before: a row carries THREE facts with THREE lifetimes -- the
    PLAN, WHAT MOVED, and the ASSERTION -- and no column belongs to two.  A
    revert releases the assertion (``settled_on``, ``reconciled_by_id``) and
    keeps what moved, so the revert / edit / re-settle round trip the full-edit
    popover INSTRUCTS the user to perform is lossless.

    Every case below drives ``status_seam.apply_status_change`` and
    ``transaction_service.settle_transaction`` -- the doors -- rather than
    assigning columns, because the claim under test is what those doors do.
    """

    @staticmethod
    def _settle(txn, *, submitted=None):
        """Settle *txn* through the verb, returning whether it booked a human's figure."""
        return transaction_service.settle_transaction(txn, submitted=submitted)

    @staticmethod
    def _revert(txn):
        """Put *txn* back to Projected through the ONE status door."""
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.PROJECTED),
        )

    def test_a_revert_keeps_the_figure_and_drops_the_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The asymmetry, stated as an assertion instead of a comment.

        A first version of this step released all four columns together, under
        a CHECK that paired the day with the basis -- and it cost the user real
        data, because the popover tells them to revert in order to edit.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="500.00",
            )
            self._settle(txn, submitted=Decimal("245.32"))
            db.session.flush()
            assert txn.settled_on is not None
            assert txn.settled_amount == Decimal("245.32")
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.CORRECTED)
            # A statement is recorded as having SHOWN this money, so the
            # release below has something to release.  Asserting
            # ``reconciled_by_id is None`` after a revert without this is
            # vacuous -- it passes on a build that never clears the link, which
            # is half of what "a revert releases the ASSERTION" claims (found
            # by adversarial review, 2026-08-17).
            anchor = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=txn.account_id)
                .order_by(AccountAnchorHistory.id.desc())
                .first()
            )
            assert anchor is not None, "the fixture account carries no anchor"
            txn.reconciled_by_id = anchor.id
            db.session.flush()
            assert txn.reconciled_by_id is not None

            self._revert(txn)
            db.session.flush()

            # The ASSERTION is withdrawn -- BOTH of its columns ...
            assert txn.settled_on is None
            assert txn.reconciled_by_id is None
            # ... and WHAT MOVED survives it.
            assert txn.settled_amount == Decimal("245.32")
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.CORRECTED)
            # But nothing counts it: the STATUS decides, not the columns.
            assert settled_figure(txn) is None

    def test_revert_edit_the_plan_re_settle_books_the_HUMANS_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The round trip the popover instructs, with an edit in the middle.

        The user reverts to change the PLAN, changes it, and marks the row paid
        again without typing an amount.  The re-settle must book the figure they
        read off their statement -- not the plan they just edited, and not a
        fresh derivation of it.  This is the case a ``derived``-only retention
        rule would get wrong in the direction that loses the user's number.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="500.00",
            )
            self._settle(txn, submitted=Decimal("245.32"))
            db.session.flush()

            self._revert(txn)
            txn.estimated_amount = Decimal("610.00")
            db.session.flush()

            booked_a_human_figure = self._settle(txn)
            db.session.flush()

            assert txn.settled_amount == Decimal("245.32")
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.CORRECTED)
            assert settled_figure(txn) == Decimal("245.32")
            # The plan edit stands, and is a different fact from what moved.
            assert txn.estimated_amount == Decimal("610.00")
            # Nobody typed anything at THIS tick, so the reconcile writer's
            # correction count does not count it (finding N-231).
            assert booked_a_human_figure is False

    def test_the_panel_OFFERS_exactly_what_the_re_settle_BOOKS(
        self, app, db, seed_user, seed_periods,
    ):
        """Finding C1, pinned: the offer and the booking are one expression.

        Measured before ``honoured_correction`` answered both: the panel offered
        a reverted row at its ``$500.00`` plan and the tick booked ``$245.32``.
        Worse than the drift, no input meant "book the plan" -- a submitted
        figure counts as a correction only when it DIFFERS from the offer, so
        typing ``$500.00`` was read as an echo of a figure never shown.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="500.00",
            )
            self._settle(txn, submitted=Decimal("245.32"))
            db.session.flush()
            self._revert(txn)
            db.session.flush()

            offered = transaction_service.settle_amount(txn)
            assert offered == Decimal("245.32")

            self._settle(txn)
            db.session.flush()
            assert settled_figure(txn) == offered

    def test_a_retained_DERIVED_record_is_RE_DERIVED_not_reused(
        self, app, db, seed_user, seed_periods,
    ):
        """Only a HUMAN's figure is honoured; the app's own inference is redone.

        A ``derived`` record is what the app resolved at a moment that has
        passed, and re-resolving is strictly better -- the plan may legitimately
        have been re-priced meanwhile.  **This is the case a mutant dropping
        ``retained.basis is CORRECTED`` from ``Settlement.from_settle``
        survives**: with a corrected record the two arms agree, so only a
        retained DERIVED record whose plan has since MOVED can tell them apart.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="500.00",
            )
            self._settle(txn)
            db.session.flush()
            assert txn.settled_amount == Decimal("500.00")
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)

            self._revert(txn)
            txn.estimated_amount = Decimal("610.00")
            db.session.flush()
            # Nothing is OFFERED from the retained record either -- the offer
            # and the booking are one expression, so both re-derive.
            assert transaction_service.settle_amount(txn) == Decimal("610.00")

            self._settle(txn)
            db.session.flush()

            assert settled_figure(txn) == Decimal("610.00")
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.DERIVED)

    def test_a_reverted_PURCHASES_row_re_sums_its_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """An envelope retains a record that stores NO figure, and re-derives.

        ``purchases`` is the basis with nothing to retain: the row's own
        children state the figure, so a revert leaves the record naming a basis
        and no amount, and a re-settle re-sums whatever the entries now say.
        The re-sum is the point -- an envelope reverted in order to CHANGE its
        purchases must close at what it now holds.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="500.00",
            )
            _make_entry(txn.id, seed_user["user"].id, "40.00", "Store A")
            db.session.flush()

            self._settle(txn)
            db.session.flush()
            assert txn.settled_amount is None
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.PURCHASES)
            assert settled_figure(txn) == Decimal("40.00")

            self._revert(txn)
            db.session.flush()
            # The record survives and stores nothing, which is legal:
            # ``ck_transactions_settled_amount_needs_basis`` binds a FIGURE to a
            # basis, never a basis to a figure.
            assert txn.settled_amount is None
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.PURCHASES)
            assert settled_figure(txn) is None

            _make_entry(txn.id, seed_user["user"].id, "12.50", "Store B")
            db.session.flush()
            # ``_make_entry`` inserts by parent ID rather than through the
            # relationship, so ``txn.entries`` is still the collection the
            # FIRST settle loaded.  Expiring it is what a second REQUEST does
            # for free, and the re-sum is the thing under test.
            db.session.expire(txn, ["entries"])
            self._settle(txn)
            db.session.flush()

            assert settled_figure(txn) == Decimal("52.50")
            assert txn.settled_basis_id == settlement_basis_id(SettlementBasisEnum.PURCHASES)

    def test_a_reverted_row_that_is_then_CANCELLED_counts_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The retained figure never leaks back through a non-settled status.

        Cancelled is ``excludes_from_balance``, so the row is worth ``0`` -- and
        the arm that says so sits ABOVE the settlement read in
        ``row_valuation.fixed_contribution``, which is what makes the order of
        those two arms load-bearing rather than incidental.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="500.00",
            )
            self._settle(txn, submitted=Decimal("245.32"))
            db.session.flush()
            self._revert(txn)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.flush()

            assert txn.settled_amount == Decimal("245.32")
            assert settled_figure(txn) is None
            assert owned_contribution(txn) == Decimal("0")


class TestAReplayedSettleIsANoOp:
    """``is_identity_move``'s firing control -- the guard the suite could not see.

    **Measured 2026-08-17: the FULL suite (9,611 tests) passed with this guard
    disabled.**  A guard whose removal no test can see is not a guard, and this
    class is what makes it one.

    The defect the guard was written for is now closed twice over --
    ``Settlement.from_settle`` honours a retained ``corrected`` record, so a
    replayed manual settle no longer destroys provenance by re-deriving.  What
    it still UNIQUELY protects is the ENVELOPE replay: ``settle_from_entries``
    has preconditions a settled row fails, so without the early return a
    double-clicked Mark Paid on an already-Paid envelope answers 400 where the
    user has done nothing wrong and nothing needs doing.
    """

    def test_a_replayed_close_on_an_ENVELOPE_is_a_no_op_not_a_400(
        self, app, db, seed_user, seed_periods,
    ):
        """The identity move this guard exists for, and the only one that needs it.

        Shown to FIRE: disabling the early return in ``settle_transaction``
        raises ``ValidationError`` here instead of answering ``False``.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            _make_entry(txn.id, seed_user["user"].id, "40.00", "Store A")
            db.session.flush()

            transaction_service.settle_transaction(txn)
            db.session.flush()
            recorded = settled_figure(txn)
            day = txn.settled_on

            # The double click / stale tab / re-POST.
            assert transaction_service.settle_transaction(txn) is False
            db.session.flush()

            assert settled_figure(txn) == recorded
            assert txn.settled_on == day

    def test_it_is_NARROWER_than_the_settled_band(
        self, app, db, seed_user, seed_periods,
    ):
        """``Settled -> Paid`` still owes the state machine an answer.

        A first version of this guard asked ``status_id in settled_status_ids()``
        and thereby swallowed an ILLEGAL transition, turning a designed 400 into
        a silent 200.  Only the IDENTITY move is nothing to do.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
            )
            _make_entry(txn.id, seed_user["user"].id, "40.00", "Store A")
            db.session.flush()
            transaction_service.settle_transaction(txn)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.SETTLED),
            )
            db.session.flush()

            # The row is ARCHIVED; a settle asks for Paid, which is not the
            # status it holds -- so this is a transition, not a re-submit.
            with pytest.raises(ValidationError):
                transaction_service.settle_transaction(txn)


class TestTheRetainedMapAnswersOnlyWhereTheGapIsREAL:
    """``retained_settle_amounts_by_id``'s status gate, which nothing graded.

    The map's contract is narrow and its whole value is the narrowness: a
    non-``None`` entry means "this row will book something other than what you
    can SEE", so a template draws a marker without deciding anything.  A settled
    row is already showing its recorded figure
    (``row_valuation.settled_amounts_by_id``), so a marker there would tell the
    user a settled row is about to book something -- which it is not.

    **Measured 2026-08-17 by an adversarial mutation pass**: dropping the
    settled-status gate left the whole suite green, so the badge would have
    appeared on every settled envelope and every settled corrected row with
    nothing to say so.
    """

    def test_a_SETTLED_row_is_not_in_the_map(
        self, app, db, seed_user, seed_periods,
    ):
        """The gate, from both sides in one case.

        The same row is asked twice -- once settled, once reverted -- so the
        test cannot pass by answering ``None`` for everything, which is the
        shape a one-sided assertion would admit.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="500.00",
            )
            transaction_service.settle_transaction(
                txn, submitted=Decimal("245.32"),
            )
            db.session.flush()

            settled_map = transaction_service.retained_settle_amounts_by_id(
                [txn],
            )
            assert settled_map[txn.id] is None, (
                "a settled row shows its recorded figure already; a 'will "
                "book' marker on one is a second answer to a settled question"
            )

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()

            reverted_map = transaction_service.retained_settle_amounts_by_id(
                [txn],
            )
            assert reverted_map[txn.id] == Decimal("245.32")


class TestTheDoorAppliesTheStatusANDTheCorrection:
    """A status change and a figure correction are INDEPENDENT facts.

    **The door treated them as alternatives, and that dropped status changes on
    the floor** -- measured on 2026-08-18, not reasoned.  ``apply_requested_status``
    recorded a submitted figure and RETURNED, so a row moving Paid -> Settled
    while carrying a corrected Actual recorded the figure and stayed Paid,
    answering 200.  Both controls are on the same popover -- the Status dropdown
    sits beside the Actual box -- so a user correcting a figure on the way to
    filing the row away got half of what they asked for, silently.

    ``Settled`` is TERMINAL (``settled: {settled}`` in the workflow map), so the
    archive is the LAST moment either fact can be stated.  Dropping either half
    loses something no later edit can restate.
    """

    #: A settle day that is NOT the user's today.  A settle stamps
    #: ``display_today()``, so on a today-dated row "the correction preserved
    #: the day" and "the seam re-stamped it" read the same and the assertion
    #: grades nothing -- finding **N-146**'s shape, which this arc has already
    #: paid for once.
    _SETTLED_DAY_OFFSET = timedelta(days=3)

    @classmethod
    def _settled_row(cls, seed_user, period, *, amount="100.00"):
        """Return a row settled at *amount*, on a PAST day, through the real verb."""
        template = _make_envelope_template(seed_user)
        template.is_envelope = False
        txn = _make_projected_txn(
            seed_user, period, template=template, estimated_amount=amount,
        )
        db.session.flush()
        transaction_service.apply_requested_status(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settled_on=display_today() - cls._SETTLED_DAY_OFFSET,
        )
        return txn

    def test_an_archive_carrying_a_correction_does_BOTH(
        self, app, db, seed_user, seed_periods,
    ):
        """Paid -> Settled at $123.45: the row archives AND records $123.45.

        Hand arithmetic: the row settles at its $100.00 plan, so the cash
        account is $100.00 down.  The correction restates the figure as
        $123.45, so the settled effect becomes -$123.45 -- the difference
        re-posted, not a second full booking.

        Shown to FIRE: restoring the early ``return`` after the correction
        leaves ``status_id`` at Paid while the figure and the ledger both move.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            assert settled_figure(txn) == Decimal("100.00")
            day = txn.settled_on

            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.SETTLED),
                submitted=Decimal("123.45"),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.SETTLED), (
                "the archive was dropped while the figure was recorded"
            )
            assert settled_figure(txn) == Decimal("123.45")
            assert txn.settled_basis_id == settlement_basis_id(
                SettlementBasisEnum.CORRECTED,
            )
            assert txn.settled_on == day, (
                "the archive moved the day the money moved"
            )
            # The POSTING LEDGER, read from ``budget.journal_entries``.
            # ``posting_service.settled_transaction_effect`` is NOT an oracle
            # here: it queries ``budget.transactions`` through
            # ``posting_reads.settled_figure_clause``, the query-tier twin of
            # ``row_valuation.settled_figure`` -- so asserting on it is the
            # line above restated in SQL, and deleting the reconcile from the
            # door leaves it green (measured by a neutral review, 2026-08-18).
            # The net per day is what separates "re-booked" from "booked twice".
            assert net_posted_by_day(
                JournalEntry.transaction_id == txn.id,
            ) == {day: Decimal("123.45")}

    def test_a_correction_with_no_status_move_still_records(
        self, app, db, seed_user, seed_periods,
    ):
        """The identity case: the figure lands and the status stays put.

        The other side of the composition -- proving the merged seam call did
        not make a status change a PRECONDITION of recording a figure.  The
        settle day is preserved, because a figure correction moves no day and
        ruling **R-FL** releases the clearing link on the DAY alone.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            day = txn.settled_on

            transaction_service.apply_requested_status(
                txn, txn.status_id, submitted=Decimal("87.10"),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert settled_figure(txn) == Decimal("87.10")
            assert txn.settled_on == day, (
                "a figure correction moved the settle day"
            )

    def test_a_REVERT_carrying_a_figure_is_refused_and_changes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """Two contradictory facts in one call is a programming error, not a save.

        A revert says the money did not move; a figure says this much did.  The
        door used to record the figure, post the ledger difference, and never
        revert -- booking money for a row it had just been told had not moved.
        It refuses now, BEFORE the seam runs, so the row is untouched.

        A FORM reaches this whenever the user CHANGED the box, which is the
        point: an untouched prefill is still dropped on the way out of the band
        (ruling **R-EG**, so the unlock path keeps working), and only a figure
        that differs from the record is refused.  ``figure_for_status`` draws
        that line, and until it did both were discarded.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            # The LEDGER before, from the journal rather than from the row --
            # see the archive test for why the row-tier reader is not an oracle.
            ledger_before = net_posted_by_day(
                JournalEntry.transaction_id == txn.id,
            )

            with pytest.raises(ValidationError) as exc:
                transaction_service.apply_requested_status(
                    txn, ref_cache.status_id(StatusEnum.PROJECTED),
                    submitted=Decimal("123.45"),
                )

            assert "has nothing to record" in str(exc.value)
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert settled_figure(txn) == Decimal("100.00")
            assert net_posted_by_day(
                JournalEntry.transaction_id == txn.id,
            ) == ledger_before, (
                "a refused request posted to the ledger anyway"
            )

    def test_an_ECHO_leaves_the_derived_basis_standing(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-posting the prefilled figure must not manufacture a correction.

        The echo rule's firing control at this tier: the basis is the only
        stored signal that a human read a number off a statement, and an
        untouched Save posts the box's contents back on every edit.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            derived = settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert txn.settled_basis_id == derived

            transaction_service.apply_requested_status(
                txn, txn.status_id, submitted=Decimal("100.00"),
            )

            assert txn.settled_basis_id == derived


class TestTheRetainedMapAnswersOnlyARetainedCORRECTION:
    """``retained_settle_amounts_by_id`` draws the re-book badge, and it lied.

    The badge's caption is *"the figure recorded before this row was set back to
    Projected"*, so a non-``None`` value must mean exactly that.  The map
    delegated to ``fixed_settle_amount``, whose FIRST arm is the purchases sum
    -- so every unsettled ENVELOPE carrying a purchase came back with a figure
    and got the badge, on a row that has never settled and records nothing
    (neutral review, 2026-08-18).  The function's own docstring already said
    such a row must answer ``None``.
    """

    def test_a_never_settled_envelope_with_a_purchase_gets_no_badge(
        self, app, db, seed_user, seed_periods,
    ):
        """The defect, stated as the row that produced it.

        A `$400.00` envelope with one `$25.00` purchase, never settled.  The
        cell already renders `25 / 400`; the badge would append `$25.00` again
        under a sentence that is false for this row.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="400.00",
            )
            _make_entry(txn.id, seed_user["user"].id, "25.00", "Milk")
            db.session.flush()

            assert txn.settled_basis_id is None, "fixture: nothing recorded"
            assert transaction_service.retained_settle_amounts_by_id(
                [txn],
            ) == {txn.id: None}

    def test_a_reverted_row_holding_a_correction_DOES_get_one(
        self, app, db, seed_user, seed_periods,
    ):
        """The firing control, and the case the badge exists for.

        A revert releases the assertion and KEEPS what moved, so this row's plan
        is what the balance counts while a re-settle books the retained
        `$245.32` -- two numbers about one row, and the second is on no other
        surface.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="500.00",
            )
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.DONE),
                submitted=Decimal("245.32"),
            )
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )

            assert transaction_service.retained_settle_amounts_by_id(
                [txn],
            ) == {txn.id: Decimal("245.32")}

    def test_a_reverted_row_holding_a_DERIVED_record_gets_none(
        self, app, db, seed_user, seed_periods,
    ):
        """The second control: only a HUMAN's figure outlives the revert.

        A ``derived`` record is the app's own inference about a moment that has
        passed, and a re-settle re-resolves it -- so the row's plan IS what a
        tick books and the badge would state a difference that does not exist.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(
                seed_user, seed_periods[0], template=template,
                estimated_amount="500.00",
            )
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )

            assert txn.settled_basis_id == settlement_basis_id(
                SettlementBasisEnum.DERIVED,
            )
            assert transaction_service.retained_settle_amounts_by_id(
                [txn],
            ) == {txn.id: None}
