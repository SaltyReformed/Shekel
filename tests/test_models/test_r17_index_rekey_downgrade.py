"""
Shekel Budget App -- Migration ``c8e5a2f31b47``'s downgrade guard (plan step R17)

The migration re-keys each table's generation index off the pay period and onto
the occurrence a row answers.  That direction is total; the DOWNGRADE is not.
The paycheck-keyed index it restores forbids two non-override rows of one
template in one paycheck, and the re-keyed pair permits exactly that when the
two answer different occurrences -- so a schedule that generated such a pair
while this revision was applied cannot be re-keyed back without deleting one of
two rows that both hold real money.  ``downgrade()`` refuses instead.

**The refusal is the part worth grading**, and nothing else can grade it: a
source-level check sees the ``raise`` but not whether the predicate under it is
the correct complement of the index being restored.  A predicate that is
accidentally always-false lets the downgrade proceed and the ``CREATE UNIQUE
INDEX`` then fails deep inside Alembic, mid-migration, on a database that has
already dropped the two indexes protecting it.

**Executed, not read**, following ``test_anchor_cache_downgrade.py``: this
migration's DDL needs an ACCESS EXCLUSIVE lock that conflicts with the xdist
workers, and its SELECT does not.  ``COLLIDING_PAIRS_SQL`` exists as a
module-level constant for that reason -- the half that DECIDES is the half a
suite can run, against real rows.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from tests._test_helpers import load_migration_module
from app.models.amount_ownership import AmountOwnership

MIGRATION = "c8e5a2f31b47_a_row_answers_an_occurrence_not_a_paycheck.py"


def _count_colliding(table, fk):
    """Run the downgrade's own refusal predicate against the live rows."""
    module = load_migration_module(MIGRATION)
    return db.session.execute(
        db.text(module.COLLIDING_PAIRS_SQL.format(table=table, fk=fk)),
    ).scalar()


class TestTheDowngradeRefusesAnUnrestorablePair:
    """``downgrade()`` names what it cannot store rather than destroying it."""

    def test_a_clean_schedule_counts_no_collision(
        self, app, db, seed_user, seed_periods,
    ):
        """The ordinary case: one row per paycheck, so the guard is silent.

        The control that keeps the case below from being vacuous -- a predicate
        that counted everything would also "detect" the pair, and this is what
        tells the two apart.
        """
        with app.app_context():
            assert _count_colliding("transactions", "template_id") == 0
            assert _count_colliding("transfers", "transfer_template_id") == 0

    def test_two_occurrences_in_one_paycheck_are_counted(
        self, app, db, seed_user, seed_periods,
    ):
        """The state the paycheck-keyed index cannot hold is REFUSED, not lost.

        Two rows, one paycheck, two occurrences -- exactly what plan step R17
        made storable and what the downgrade cannot store back.  Both carry
        money, so choosing one to delete is not the migration's call.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense").one()
            status = db.session.query(Status).filter_by(name="Projected").one()
            category = list(seed_user["categories"].values())[0]
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=txn_type.id,
                name="R17 downgrade guard",
                default_amount=Decimal("100.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.flush()

            for occurrence, label in (
                (date(2026, 1, 15), "first installment"),
                (date(2026, 2, 15), "second installment"),
            ):
                row = Transaction(
                    template_id=template.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=status.id,
                    name=label,
                    category_id=category.id,
                    transaction_type_id=txn_type.id,
                    amount_ownership=AmountOwnership.own(Decimal("100.00")),
                    occurs_on=occurrence,
                    is_override=False,
                    is_deleted=False,
                )
                db.session.add(row)
            db.session.flush()

            assert _count_colliding("transactions", "template_id") == 1, (
                "the downgrade would have proceeded and then failed inside "
                "CREATE UNIQUE INDEX, after dropping the indexes that protect "
                "these rows"
            )

            # Resolve the pair the way the refusal's message asks, and the
            # guard falls silent -- so it is counting THIS pair and not merely
            # counting.
            db.session.query(Transaction).filter_by(
                template_id=template.id, occurs_on=date(2026, 2, 15),
            ).delete(synchronize_session=False)
            db.session.flush()
            assert _count_colliding("transactions", "template_id") == 0

    def test_an_override_sibling_is_not_a_collision(
        self, app, db, seed_user, seed_periods,
    ):
        """The predicate is the restored index's exact complement.

        Both generation indexes are partial over ``is_override = FALSE``, and
        so is the one the downgrade restores -- so an override sibling beside a
        canonical row is storable in BOTH directions and must not be refused.
        A guard that dropped the ``is_override`` clause would block every
        downgrade on any schedule that has ever used carry-forward.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense").one()
            status = db.session.query(Status).filter_by(name="Projected").one()
            category = list(seed_user["categories"].values())[0]
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=txn_type.id,
                name="R17 downgrade override",
                default_amount=Decimal("100.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.flush()

            for occurrence, override in (
                (date(2026, 1, 15), False),
                (date(2026, 1, 1), True),
            ):
                db.session.add(Transaction(
                    template_id=template.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=status.id,
                    name=f"override={override}",
                    category_id=category.id,
                    transaction_type_id=txn_type.id,
                    amount_ownership=AmountOwnership.own(Decimal("100.00")),
                    occurs_on=occurrence,
                    is_override=override,
                    is_deleted=False,
                ))
            db.session.flush()

            assert _count_colliding("transactions", "template_id") == 0
