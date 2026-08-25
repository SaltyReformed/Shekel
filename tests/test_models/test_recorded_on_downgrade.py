"""
Shekel Budget App -- Migration ``e5b2c8a17d34``'s both directions (**N-299**)

The migration adds ``account_anchor_history.recorded_on`` -- the civil day an
assertion was TYPED, on the application's clock --.  Its downgrade drops it,
which returns every row to the DERIVATION the column replaced.

**What is worth grading is the refusal, not the drop.**  Immediately after the
upgrade the backfill IS that derivation, so a downgrade loses nothing.  It stops
being lossless the first time the two clocks disagree -- an assertion typed in
the last second of a civil day, where ``display_today()`` answers one day and
PostgreSQL's ``now()`` the next.  Those rows are exactly the ones the column
exists for, and a downgrade that dropped them silently would revert each card
caption to the database's day with nothing said.  ``_APP_DATED`` is the query
that refuses; this suite proves it FIRES on such a row rather than trusting that
it would.

**Executed where it can be, source-checked where it cannot.**  The repo's other
migration-direction suites settle for a source-level check because the DDL needs
an ACCESS EXCLUSIVE lock that conflicts with the xdist workers
(``test_anchor_cache_downgrade`` states the rule).  That applies to the
``ALTER TABLE`` halves here and NOT to the two standalone SELECTs the pre-flight
and the refusal are built on, so those run against real rows.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta

from sqlalchemy import text

from app.extensions import db
from app.models.account import AccountAnchorHistory
from tests._test_helpers import load_migration_module

_MIGRATION_FILENAME = (
    "e5b2c8a17d34_an_assertion_records_the_day_it_was_typed.py"
)


def _migration():
    """Return the loaded migration module."""
    return load_migration_module(_MIGRATION_FILENAME)


def _app_dated_rows():
    """Run the downgrade's own refusal SELECT against the live database."""
    # Pylint: ``protected-access`` -- the migration's own module-level query
    # IS the subject under test.  Re-spelling it here would grade a copy and
    # pass while the migration's real one drifted, which is the whole failure
    # this suite exists to catch.
    # pylint: disable=protected-access
    return db.session.execute(_migration()._APP_DATED).fetchall()


class TestTheDowngradeRefusesWhatItCannotRebuild:
    """``_APP_DATED`` is the gate between a lossless drop and a silent one."""

    def test_it_is_silent_on_a_database_whose_rows_the_derivation_covers(
        self, app, seed_user,
    ):
        """The non-vacuity partner: a healthy database downgrades cleanly.

        Without this, a refusal that fired on EVERY database would look like a
        working control while making the downgrade unusable.
        """
        with app.app_context():
            # Non-vacuity: the refusal returning [] means nothing if the table
            # is empty.  ``seed_user`` provisions the account's opening
            # assertion, so there is a row for the query to have passed over.
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=seed_user["account"].id,
            ).count() >= 1

            assert _app_dated_rows() == [], (
                "the backfill is the derivation verbatim, so a freshly "
                "upgraded database has nothing a downgrade would destroy"
            )

    def test_it_FIRES_on_a_row_whose_two_clocks_disagreed(
        self, app, seed_user,
    ):
        """The row the column exists for must stop a downgrade.

        Built to satisfy the CHECK (``recorded_on >= observed_on``) while
        diverging from what ``created_at`` derives -- which is precisely the
        production state a true-up submitted in a civil day's last second
        produces, and precisely what dropping the column would discard.
        """
        with app.app_context():
            account = seed_user["account"]
            row = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .order_by(AccountAnchorHistory.id.desc())
                .first()
            )
            assert row is not None, "seed_user provisions an opening assertion"
            derived = db.session.execute(text(
                "SELECT (created_at AT TIME ZONE 'America/New_York')::date "
                "FROM budget.account_anchor_history WHERE id = :i"
            ), {"i": row.id}).scalar()

            row.observed_on = derived - timedelta(days=2)
            row.recorded_on = derived + timedelta(days=1)
            db.session.commit()

            refused = _app_dated_rows()

            assert [r[0] for r in refused] == [row.id], (
                "a row whose entered day the derivation does not reproduce "
                "must stop the downgrade, or the drop is silent data loss"
            )


class TestTheUpgradePreFlightsBothBinds:
    """Source-anchored: the ALTER halves cannot run under the xdist workers."""

    def test_it_counts_surviving_nulls_before_binding_not_null(self):
        """A bare constraint error names no row; the operator needs the query."""
        source = _migration_source()
        assert "_REMAINING_NULLS" in source
        assert "are still NULL after the backfill" in source
        assert source.index("_REMAINING_NULLS") < source.index(
            "nullable=False"
        ), "the count must precede the ALTER it protects"

    def test_the_downgrade_refuses_before_it_touches_any_DDL(self):
        """A refused downgrade must not have half-applied."""
        source = _migration_source()
        down = source[source.index("def downgrade("):]
        assert "_APP_DATED" in down
        assert down.index("_APP_DATED") < down.index("drop_column"), (
            "the refusal must precede any DDL, or a refused downgrade has "
            "already half-applied"
        )


def _migration_source():
    """Return the migration's source text."""
    return inspect.getsource(_migration())
