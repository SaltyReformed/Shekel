"""``scripts/seed_tax_brackets.py``'s seed commits nothing (plan step balance:X-cv, R-BAL122).

The deploy calls :func:`seed_tax_brackets` inside entrypoint step 3's one
transaction, so the function must leave the commit to its caller: a commit of
its own would end the deploy's session transaction, and any step after it would
then refuse (``Autobegin is disabled``).  Today it is the last step that uses
the session, where a stray commit is harmless -- which is why the deploy's own
tests cannot see one, and this test grades the function directly.
"""
from __future__ import annotations

from sqlalchemy import text

from app.services.tax_seed_data import DEFAULT_FICA
from scripts.seed_tax_brackets import seed_tax_brackets


def _committed_fica(db, user_id: int) -> int:
    """Count, over a connection of its own, the user's COMMITTED FICA configurations."""
    with db.engine.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM salary.fica_configs WHERE user_id = :u"
        ), {"u": user_id}).scalar_one()


class TestTheSeedLeavesTheCommitToItsCaller:
    """It stages every missing row and commits none of them."""

    def test_a_rollback_after_the_seed_discards_every_row_it_staged(
        self, app, db, seed_user,
    ):
        """The rows are staged in the caller's transaction; a rollback takes them all.

        ``seed_user`` holds no tax data, so the seed has every row to add.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            seed_tax_brackets()
            db.session.flush()
            staged = db.session.execute(text(
                "SELECT count(*) FROM salary.fica_configs WHERE user_id = :u"
            ), {"u": user_id}).scalar_one()
            assert staged == len(DEFAULT_FICA)
            assert _committed_fica(db, user_id) == 0

            db.session.rollback()

            assert _committed_fica(db, user_id) == 0
