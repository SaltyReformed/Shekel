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

from datetime import date, datetime

from app.utils.dates import display_today
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern, Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import transaction_service


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
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    txn_type = (
        db.session.query(TransactionType)
        .filter_by(name=txn_type_name).one()
    )

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

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
            assert txn.actual_amount == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on is not None

    def test_expense_includes_credit_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """Both debit and credit entries contribute to actual_amount.

        Per ``compute_actual_from_entries`` semantics, credit entries
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
            assert txn.actual_amount == Decimal("400.00")
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
            assert txn.actual_amount == Decimal("0.00")
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
            assert txn.actual_amount == Decimal("120.00")
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
            assert txn.actual_amount == Decimal("2500.00")
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
            assert reloaded.actual_amount is None
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
            every_period = (
                db.session.query(RecurrencePattern)
                .filter_by(name="Every Period").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=every_period.id,
            )
            db.session.add(rule)
            db.session.flush()
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
            assert txn.actual_amount == Decimal("75.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            # A rollback should reverse the helper's writes -- proving
            # the helper itself never committed or flushed.
            db.session.rollback()

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded is not None
            assert reloaded.actual_amount is None
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
            assert txn.actual_amount is None

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
                txn, actual_amount=Decimal("999.99"),
            )

            assert txn.actual_amount == Decimal("90.00")
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
                txn, actual_amount=Decimal("250.00"),
            )

            assert txn.actual_amount == Decimal("250.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_a_plain_row_with_no_actual_keeps_its_estimate(
        self, app, seed_user, seed_periods,
    ):
        """No actual supplied leaves the column untouched -- the one-click path.

        ``effective_amount`` is ``COALESCE(actual, estimated)``, so leaving it
        NULL is how a row settles at what was budgeted.  Asserted as NULL
        rather than as $500.00: writing the estimate INTO the actual column
        would be a second answer to what the row cost.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            template.is_envelope = False
            txn = _make_projected_txn(seed_user, seed_periods[0],
                                      template=template)
            db.session.flush()

            transaction_service.settle_transaction(txn)

            assert txn.actual_amount is None
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
