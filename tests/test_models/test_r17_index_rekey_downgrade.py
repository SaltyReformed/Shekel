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

from app.extensions import db
from app.models.transaction import Transaction
from tests._test_helpers import (
    moved_by_the_owner,
    definition_firing_twice_in_a_paycheck,
    generate_row_of,
    load_migration_module,
    make_expense_template,
    populate_in_a_fresh_pass,
)

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
        self, app, db, seed_user,
    ):
        """The state the paycheck-keyed index cannot hold is REFUSED, not lost.

        Two rows, one paycheck, two occurrences -- exactly what plan step R17
        made storable and what the downgrade cannot store back.  Both carry
        money, so choosing one to delete is not the migration's call.

        The pair is the ENGINE's own output (plan step balance:X-cf): a
        monthly cadence inside a 60-day paycheck names two occurrences there
        (:func:`definition_firing_twice_in_a_paycheck`) and the generate pass
        writes both -- the "schedule that generated such a pair" the module
        docstring describes, rather than a hand-built copy of it.
        """
        with app.app_context():
            template, _first, period = definition_firing_twice_in_a_paycheck(
                db.session, seed_user, name="R17 downgrade guard",
            )
            populate_in_a_fresh_pass(seed_user["user"].id, [period.id])
            pair = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).order_by(Transaction.occurs_on).all()
            assert len(pair) == 2, "the cadence names two occurrences here"
            assert pair[0].pay_period_id == pair[1].pay_period_id

            assert _count_colliding("transactions", "template_id") == 1, (
                "the downgrade would have proceeded and then failed inside "
                "CREATE UNIQUE INDEX, after dropping the indexes that protect "
                "these rows"
            )

            # Resolve the pair the way the refusal's message asks, and the
            # guard falls silent -- so it is counting THIS pair and not merely
            # counting.
            db.session.query(Transaction).filter_by(
                id=pair[1].id,
            ).delete(synchronize_session=False)
            db.session.flush()
            assert _count_colliding("transactions", "template_id") == 0

    def test_an_override_sibling_is_not_a_collision(
        self, app, db, seed_user, seed_periods,
    ):
        """The predicate is the restored index's exact complement.

        The undated generation index is partial over ``is_override = FALSE``
        (the dated one dropped that term at plan step X-au-h), and so is the
        paycheck-keyed one the downgrade restores -- so an override sibling
        beside a canonical row is storable in BOTH directions and must not be
        refused.
        A guard that dropped the ``is_override`` clause would block every
        downgrade on any schedule that has ever used carry-forward.

        The sibling is the engine's row of the next paycheck, moved in by the
        two acts the move door performs (plan step balance:X-cf), which is one
        of the two ways the application produces an override beside a
        canonical row.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, name="R17 downgrade override",
            )
            generate_row_of(template, seed_periods[0])
            moved_by_the_owner(
                generate_row_of(template, seed_periods[1]), into=seed_periods[0],
            )

            assert _count_colliding("transactions", "template_id") == 0
