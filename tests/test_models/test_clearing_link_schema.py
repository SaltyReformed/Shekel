"""A line's clearing link names a statement of its OWN account, structurally.

Plan step **X-f3a-1**, ruling **R-FL**: *whether a line is INSIDE a declared
balance is a RECORDED FACT, not a comparison of two dates.*  Migration
``d5b8e2c74a19`` gives ``budget.transactions`` and
``budget.transaction_entries`` a ``reconciled_by_id`` naming the
``account_anchor_history`` row whose statement showed the line.

**Every test here is a FIRING CONTROL** (``docs/plans/verification.md`` standard
4).  No production row carries a link as of this step -- nothing is backfilled,
deliberately -- so nothing in the app exercises these keys, and a test that
merely asserted a constraint EXISTS would pass against one that admitted
everything.  Each test below writes the state the schema is supposed to refuse
and asserts the refusal by NAME at the database tier, which is the only tier
that can see a writer bypassing the ORM.

The shapes under test, and what each one would cost if it were writable:

* **a link to ANOTHER account's statement** -- the defect a single-column
  ``REFERENCES account_anchor_history (id)`` could not prevent.  Clearing is a
  per-account question (a checking statement shows a transfer's outgoing leg,
  the savings statement shows the incoming one), so such a link would record
  that a statement nobody read showed this money;
* **a link to the row's OWN account's statement** -- the legitimate act, which
  must be ACCEPTED, because a key that refuses the correct write is worse than
  none;
* **no link at all** -- ``MATCH SIMPLE``'s arm, which is what lets a composite
  key sit beside a nullable column: every row in the database is in this state
  today and none of them may be refused;
* **an entry whose ``account_id`` disagrees with its parent's** --
  ``fk_transaction_entries_parent_account``, without which the entry's own
  clearing key would be scoped by a column any writer could set wrong;
* **deleting an assertion a line still names** -- the ``ON DELETE RESTRICT``,
  which refuses rather than silently converting a recorded observation into
  "never observed";
* **the backfill**, driven through the migration's own
  ``BACKFILL_ENTRY_ACCOUNT_SQL`` so this test and the production migration
  cannot drift.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
import sqlalchemy.exc

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, status_seam
from tests._test_helpers import settlement_columns, settlement_if_settling
from tests._test_helpers import load_migration_module

_MIGRATION = load_migration_module("d5b8e2c74a19_clearing_is_a_recorded_fact.py")


def _opening_of(account_id: int) -> AccountAnchorHistory:
    """Return *account_id*'s opening assertion.

    Every account has one: ``account_service.create_account`` writes it in the
    same call that creates the row, which is the invariant migration
    ``cfb15e782f86`` established.

    Args:
        account_id: The account whose assertion to fetch.

    Returns:
        Its earliest :class:`~app.models.account.AccountAnchorHistory` row.
    """
    return (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.observed_on, AccountAnchorHistory.id)
        .first()
    )


def _cleared_by(assertion: AccountAnchorHistory) -> dict:
    """Return the field pair a line CLEARED by *assertion* carries.

    Both columns, always, because ``ck_*_cleared_needs_settle_day`` pairs them:
    a statement cannot have shown money that never moved, so a fixture that set
    only the link would be refused before it reached the key it means to test --
    which is how six tests in this module failed the first time they ran.

    The day is the assertion's OWN, which is what
    ``reconcile_service.record_settled_days`` stamps: the statement's day as an
    upper bound on the true posting day.

    Args:
        assertion: The statement the line is cleared by.

    Returns:
        ``{"reconciled_by_id": ..., "settled_on": ...}``, to splat into
        :func:`_make_transaction` or :func:`_make_entry`.
    """
    return {
        "reconciled_by_id": assertion.id,
        "settled_on": assertion.observed_on,
    }


def _make_transaction(data, **overrides) -> Transaction:
    """Return an unflushed Projected expense on the fixture's checking account.

    Args:
        data: The ``seed_full_user_data`` fixture payload.
        **overrides: Column values to set or replace -- notably
            ``reconciled_by_id``, the column under test.

    Returns:
        The unflushed :class:`~app.models.transaction.Transaction`.
    """
    expense_type = (
        _db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    fields = {
        "pay_period_id": data["periods"][0].id,
        "scenario_id": data["scenario"].id,
        "account_id": data["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Clearing control",
        "category_id": data["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "estimated_amount": Decimal("300.00"),
    }
    fields.update(overrides)
    # A row carrying a settle DAY carries the whole settlement RECORD, because
    # ``ck_transactions_settle_day_needs_basis`` requires it (plan step
    # X-au-c3).  The implication runs one way only: the record may outlive the
    # day, which is what a revert leaves behind.  Resolved here rather than in :func:`_cleared_by`
    # because that helper also feeds :func:`_make_entry`, and an ENTRY has no
    # settlement record -- its ``settled_on`` is the day its own purchase
    # posted.
    fields.update(
        settlement_columns(fields.get("settled_on"), fields["estimated_amount"])
    )
    return Transaction(**fields)


def _make_entry(data, parent: Transaction, **overrides) -> TransactionEntry:
    """Return an unflushed purchase against *parent*.

    Args:
        data: The ``seed_full_user_data`` fixture payload.
        parent: The flushed parent transaction.
        **overrides: Column values to set or replace.

    Returns:
        The unflushed :class:`~app.models.transaction_entry.TransactionEntry`.
    """
    fields = {
        "transaction_id": parent.id,
        "account_id": parent.account_id,
        "user_id": data["user"].id,
        "amount": Decimal("25.00"),
        "description": "Clearing control",
        "purchased_on": date(2026, 1, 5),
        "is_credit": False,
    }
    fields.update(overrides)
    return TransactionEntry(**fields)


class TestATransactionsClearingLinkIsScopedByAccount:
    """``fk_transactions_reconciled_by`` admits only this account's statements."""

    def test_another_accounts_statement_is_refused(
        self, app, db, seed_full_user_data,
    ):
        """A checking row may not name the savings account's assertion.

        The defect the composite key exists for.  A single-column FK to
        ``account_anchor_history (id)`` accepts this write, and nothing later
        could tell the resulting link from one the user made: the line would
        claim a statement showed it that was never about this account.
        """
        with app.app_context():
            savings_opening = _opening_of(
                seed_full_user_data["savings_account"].id,
            )
            txn = _make_transaction(
                seed_full_user_data, **_cleared_by(savings_opening),
            )
            db.session.add(txn)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transactions_reconciled_by",
            ):
                db.session.flush()
            db.session.rollback()

    def test_its_own_accounts_statement_is_accepted(
        self, app, db, seed_full_user_data,
    ):
        """The legitimate link -- this account's own assertion -- is allowed.

        Without this the suite could not tell a correct key from one that
        refuses every link, which would make the panel's write door a 500.
        """
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data, **_cleared_by(opening),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.reconciled_by_id == opening.id
            db.session.rollback()

    def test_no_link_at_all_is_accepted(self, app, db, seed_full_user_data):
        """An unlinked row is unaffected by the composite key.

        ``MATCH SIMPLE`` is what makes a composite foreign key legal beside a
        nullable column, and it is not a detail: EVERY row in the database is in
        this state the moment the migration lands, because nothing is
        backfilled.  A key that refused them would be a failed deploy.
        """
        with app.app_context():
            txn = _make_transaction(seed_full_user_data)
            db.session.add(txn)
            db.session.flush()

            assert txn.reconciled_by_id is None
            db.session.rollback()


class TestAPurchasesClearingLinkIsScopedByAccount:
    """``fk_transaction_entries_reconciled_by``, the purchase twin."""

    def test_another_accounts_statement_is_refused(
        self, app, db, seed_full_user_data,
    ):
        """A purchase against a checking envelope may not name savings'.

        The same defect one table down, and it is the one that would bite
        first: the reconcile panel's purchase arm is a bulk ``UPDATE``, so a
        scope it lost would stamp every matching row at once.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            savings_opening = _opening_of(
                seed_full_user_data["savings_account"].id,
            )

            entry = _make_entry(
                seed_full_user_data, parent,
                purchased_on=savings_opening.observed_on,
                **_cleared_by(savings_opening),
            )
            db.session.add(entry)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transaction_entries_reconciled_by",
            ):
                db.session.flush()
            db.session.rollback()

    def test_its_parents_accounts_statement_is_accepted(
        self, app, db, seed_full_user_data,
    ):
        """The legitimate link is allowed, so the panel's tick can land."""
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            opening = _opening_of(seed_full_user_data["account"].id)

            entry = _make_entry(
                seed_full_user_data, parent,
                # Bought on or before the statement day, which
                # ``ck_transaction_entries_settled_not_before_purchase``
                # requires: money cannot leave the account before it was spent.
                purchased_on=opening.observed_on,
                **_cleared_by(opening),
            )
            db.session.add(entry)
            db.session.flush()

            assert entry.reconciled_by_id == opening.id
            db.session.rollback()


class TestAPurchasesAccountIsItsParents:
    """``fk_transaction_entries_parent_account`` makes disagreement impossible."""

    def test_a_disagreeing_account_is_refused(
        self, app, db, seed_full_user_data,
    ):
        """An entry may not claim an account its parent does not have.

        This key is what the clearing key above RESTS ON: it scopes a purchase's
        link through ``account_id``, so an ``account_id`` a writer could set
        freely would make that scope decorative.  Both keys together are one
        rule -- a purchase clears against the statement of the account whose
        cash it actually leaves.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()

            entry = _make_entry(
                seed_full_user_data, parent,
                account_id=seed_full_user_data["savings_account"].id,
            )
            db.session.add(entry)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transaction_entries_parent_account",
            ):
                db.session.flush()
            db.session.rollback()

    def test_the_service_door_writes_the_parents_account(
        self, app, db, seed_user, seed_entry_template,
    ):
        """``entry_service.create_entry`` fills the column rather than the schema.

        The key above refuses a WRONG value; nothing refuses an ABSENT one
        except NOT NULL, and the app has exactly one door that creates a
        purchase.  This grades that door against the parent it was handed.
        """
        with app.app_context():
            parent = seed_entry_template["transaction"]
            entry = entry_service.create_entry(
                parent.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    amount=Decimal("12.50"),
                    description="Kroger",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            db.session.flush()

            assert entry.account_id == parent.account_id
            db.session.rollback()


class TestALinkCannotOutliveItsSettleDay:
    """``ck_*_cleared_needs_settle_day``, and the two doors that back it up.

    A statement cannot have shown money that never moved.  The columns record
    different facts -- WHEN the cash moved and WHICH statement was seen to show
    it -- but one entails the other, so a row carrying a link and no day asserts
    both that a statement showed this line and that nothing has been observed to
    leave the account.

    The constraint matters because both doors that CLEAR a settle day would
    otherwise leave the link behind, and for a PURCHASE that is not inert: the
    entry reservation reads ``is_cleared``, so a released purchase would go on
    reading as cleared and the envelope would go on not holding its budget back.
    """

    def test_a_transaction_may_not_name_a_statement_with_no_settle_day(
        self, app, db, seed_full_user_data,
    ):
        """A link on an undated row is refused at the database."""
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data,
                reconciled_by_id=opening.id, settled_on=None,
            )
            db.session.add(txn)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_cleared_needs_settle_day",
            ):
                db.session.flush()
            db.session.rollback()

    def test_a_purchase_may_not_name_a_statement_with_no_settle_day(
        self, app, db, seed_full_user_data,
    ):
        """The purchase twin, and the one with a figure behind it."""
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            opening = _opening_of(seed_full_user_data["account"].id)

            entry = _make_entry(
                seed_full_user_data, parent,
                reconciled_by_id=opening.id, settled_on=None,
            )
            db.session.add(entry)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transaction_entries_cleared_needs_settle_day",
            ):
                db.session.flush()
            db.session.rollback()

    def test_a_CARD_purchase_may_not_name_a_checking_statement(
        self, app, db, seed_full_user_data,
    ):
        """``ck_transaction_entries_card_purchase_clears_nowhere``.

        A credit-card purchase never touches checking -- it leaves later through
        its own CC Payback sibling -- so the account this link is scoped to is
        not the account the money left, and "the checking statement showed it"
        is false by construction.  The panel already refuses to OFFER one
        (``_purchases._outstanding_scope``'s ``is_credit IS FALSE``); this makes
        the state unwritable rather than merely unoffered, which is the
        difference plan step X-f3b turns into a posting.

        Production carries 18 card entries in history, so the shape is ordinary
        rather than exotic.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            opening = _opening_of(seed_full_user_data["account"].id)

            entry = _make_entry(
                seed_full_user_data, parent,
                is_credit=True,
                purchased_on=opening.observed_on,
                **_cleared_by(opening),
            )
            db.session.add(entry)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transaction_entries_card_purchase_clears_nowhere",
            ):
                db.session.flush()
            db.session.rollback()

    def test_leaving_the_settled_band_releases_a_transactions_link(
        self, app, db, seed_full_user_data,
    ):
        """``status_seam`` releases the link where it releases the day.

        The door half, so the CHECK above is a backstop rather than a reachable
        500 on an ordinary revert.  A revert is the user saying the money did
        NOT move; an observation that it appeared on a statement cannot survive
        that.
        """
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data,
                # Settled, because that is the only state a linked transaction
                # can legitimately be in: the CHECK pairs the link with a day
                # and the seam pairs the day with the settled band.
                status_id=ref_cache.status_id(StatusEnum.DONE),
                **_cleared_by(opening),
            )
            db.session.add(txn)
            db.session.flush()

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.PROJECTED)),
            )
            db.session.flush()

            assert txn.settled_on is None
            assert txn.reconciled_by_id is None
            db.session.rollback()

    def test_clearing_a_purchases_posting_day_releases_its_link(
        self, app, db, seed_user, seed_entry_template,
    ):
        """``entry_service.update_entry`` releases the link with the day.

        The purchase twin of the door above, and the one that moves a figure:
        the reservation reads the link, so an entry that kept it would go on
        being subtracted from its envelope's held-back budget after the user
        said the bank had not been seen to take it.
        """
        with app.app_context():
            parent = seed_entry_template["transaction"]
            opening = _opening_of(parent.account_id)
            entry = entry_service.create_entry(
                parent.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    amount=Decimal("12.50"),
                    description="Kroger",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            entry.settled_on = date(2026, 1, 6)
            entry.reconciled_by_id = opening.id
            db.session.flush()

            entry_service.update_entry(
                entry.id, seed_user["user"].id, settled_on=None,
            )

            assert entry.settled_on is None
            assert entry.reconciled_by_id is None
            db.session.rollback()

    def test_moving_a_purchases_posting_day_EARLIER_also_releases_it(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Any MOVE releases it, not only emptying it -- and that is measured.

        A first version of this rule released the link only when the day was
        cleared, on the reasoning that moving it earlier is a user REFINING what
        the panel stamped as an upper bound.  An adversarial review refuted it
        with money: a link whose day the date rule would not pick cannot be
        folded while an assertion resets the ledger, and on a production clone
        one such link made the balance render ``$2,246.58`` on a day its owner
        had asserted ``$2,746.58``.

        So the day wins and the observation drops back to UNKNOWN, where the
        date rule answers it exactly as it did before any of this shipped.  The
        user re-ticks on the next statement, which is one click on the screen
        they are already reading.  Refusing the edit instead would trap them
        against the panel's own copy -- *"correct it if your statement shows a
        different day"*.
        """
        with app.app_context():
            parent = seed_entry_template["transaction"]
            opening = _opening_of(parent.account_id)
            entry = entry_service.create_entry(
                parent.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    amount=Decimal("12.50"),
                    description="Kroger",
                    purchased_on=opening.observed_on,
                ),
            )
            entry.settled_on = opening.observed_on
            entry.reconciled_by_id = opening.id
            db.session.flush()

            entry_service.update_entry(
                entry.id, seed_user["user"].id,
                settled_on=opening.observed_on + timedelta(days=1),
            )

            assert entry.settled_on == opening.observed_on + timedelta(days=1)
            assert entry.reconciled_by_id is None
            db.session.rollback()

    def test_an_update_that_leaves_the_day_alone_keeps_the_link(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The control: editing the AMOUNT is not a retraction.

        Without this the suite could not tell "release on a day move" from
        "release on any update at all", and the second would quietly drop every
        observation a user ever recorded the first time they fixed a typo.
        """
        with app.app_context():
            parent = seed_entry_template["transaction"]
            opening = _opening_of(parent.account_id)
            entry = entry_service.create_entry(
                parent.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    amount=Decimal("12.50"),
                    description="Kroger",
                    purchased_on=opening.observed_on,
                ),
            )
            entry.settled_on = opening.observed_on
            entry.reconciled_by_id = opening.id
            db.session.flush()

            entry_service.update_entry(
                entry.id, seed_user["user"].id, amount=Decimal("13.75"),
            )

            assert entry.amount == Decimal("13.75")
            assert entry.reconciled_by_id == opening.id
            db.session.rollback()

    def test_correcting_a_transactions_settle_day_releases_its_link(
        self, app, db, seed_full_user_data,
    ):
        """``status_seam`` releases it on a day CORRECTION, not just a revert.

        The transaction twin, and the door is the grid's correction box: a
        settled row re-submitted with a different day runs the seam's explicit
        arm, which never touched the link until the review above.  The state it
        would otherwise leave -- a row named by a statement that closed on
        another day -- is the one the fold cannot render.
        """
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                **_cleared_by(opening),
            )
            db.session.add(txn)
            db.session.flush()

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settled_on=opening.observed_on + timedelta(days=2),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            db.session.flush()

            assert txn.settled_on == opening.observed_on + timedelta(days=2)
            assert txn.reconciled_by_id is None
            db.session.rollback()


class TestAnAssertionAlineNamesCannotBeDeleted:
    """``ON DELETE RESTRICT`` refuses rather than un-clearing the line."""

    def test_deleting_a_named_assertion_is_refused(
        self, app, db, seed_full_user_data,
    ):
        """Removing a statement a line still names is refused at the database.

        ``SET NULL`` is the wrong answer for a recorded observation: it would
        convert "this statement showed the money" into "no statement ever did"
        with nothing reporting it, and every figure resting on the difference
        would move silently.  There is no door in ``app/`` that deletes a single
        assertion today -- history goes only with its account -- so the refusal
        costs nothing and is what makes writing such a door a loud failure
        rather than a quiet balance change.
        """
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data, **_cleared_by(opening),
            )
            db.session.add(txn)
            db.session.flush()

            db.session.delete(opening)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transactions_reconciled_by",
            ):
                db.session.flush()
            db.session.rollback()


class TestTheEntryAccountBackfill:
    """The migration's only non-DDL step, driven through its own SQL."""

    def test_it_gives_every_purchase_its_parents_account(
        self, app, db, seed_full_user_data,
    ):
        """The backfill resolves the parent's account for every row.

        Driven through ``_MIGRATION.BACKFILL_ENTRY_ACCOUNT_SQL`` -- the literal
        string ``upgrade()`` executes -- so this test and the production
        migration cannot come to disagree about the derivation.  The column is
        blanked first because the live schema is already backfilled: asserting
        against rows that were never NULL would grade nothing.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            entry = _make_entry(seed_full_user_data, parent)
            db.session.add(entry)
            db.session.flush()

            # NOT NULL is dropped for the length of the probe and restored in
            # the same transaction, which the rollback below also guarantees.
            db.session.execute(sa.text(
                "ALTER TABLE budget.transaction_entries "
                "ALTER COLUMN account_id DROP NOT NULL"
            ))
            db.session.execute(sa.text(
                "UPDATE budget.transaction_entries SET account_id = NULL"
            ))
            blanked = db.session.execute(sa.text(
                "SELECT count(*) FROM budget.transaction_entries "
                "WHERE account_id IS NULL"
            )).scalar()
            assert blanked > 0, (
                "The probe blanked no row, so the backfill below would have "
                "nothing to resolve and this test would grade nothing."
            )

            db.session.execute(sa.text(
                _MIGRATION.BACKFILL_ENTRY_ACCOUNT_SQL
            ))

            unresolved = db.session.execute(sa.text(
                "SELECT count(*) FROM budget.transaction_entries "
                "WHERE account_id IS NULL"
            )).scalar()
            assert unresolved == 0, (
                f"{unresolved} purchase(s) resolved no parent account, which "
                f"the NOT NULL transaction_id foreign key makes impossible."
            )
            db.session.refresh(entry)
            assert entry.account_id == parent.account_id
            db.session.rollback()
