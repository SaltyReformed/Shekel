"""An assertion is append-only: refuse UPDATE and DELETE at the database tier

Revision ID: f4a7c2d9e51b
Revises: d3b6f1c8a274
Create Date: 2026-08-30 12:00:00.000000

Plan step ``balance:X-f3c-2c``, ruling **balance:R-HY**.  Closes finding
**balance:N-287**.

**What this closes.**  ``budget.account_anchor_history`` was append-only by
CONVENTION where ``budget.account_openings`` and ``budget.loan_anchor_events``
were append-only by an ORM listener -- and that listener tier sees only writes
SQLAlchemy mediates.  It is blind to a bulk ``query.update()``, which is not a
hypothetical spelling here: ``reconcile_service.record_settled_days`` already
stamps a statement's day onto ticked purchases exactly that way, holding no ORM
instance for a listener to fire on.  It is equally blind to a raw statement and
to a psql session.  An assertion's ``observed_on`` is what every clearing link
was recorded against (ruling **R-FL**), so a door that edited one would
re-point cleared purchases at a statement that did not show them, and no
surface would say so.

**What is installed.**  ``budget.refuse_append_only_change`` -- one trigger
function, attached ``BEFORE UPDATE OR DELETE FOR EACH ROW`` to all three
tables.  UPDATE is refused outright.  DELETE is refused only while the owning
account still exists, which is what separates picking a row off from disposing
of an account: ``AccountScopedMixin``'s ``ON DELETE CASCADE`` is executed by
PostgreSQL as an ``AFTER DELETE`` referential action, so by the time the child
DELETE runs the parent row has left the transaction's snapshot and the trigger
falls through.  The SQL lives in :mod:`app.append_only_infrastructure`, shared
with ``scripts/init_database.py`` (whose fresh-database path stamps past this
migration) and ``scripts/build_test_template.py``.

**No data changes and nothing to legalise.**  This refuses a STATEMENT rather
than a STATE, so no row that already exists can be in violation -- the contrast
with ``d3b6f1c8a274``, whose constraint refused a state and therefore had to
restate twelve production rows before it could be installed.  Verified on the
production clone before authoring: 78 assertions, 9 openings and 2 loan anchor
events, none of them touched.

**A future migration that must rewrite one of these tables calls
``remove_append_only_infrastructure(op.execute)`` first and re-applies after.**
That case is the norm here rather than the exception: this project puts
one-time backfills in the revision that changes the schema, and
``e5b2c8a17d34`` backfilled ``account_anchor_history.recorded_on`` exactly that
way.  Two lines, both visible in the diff, refused loudly if forgotten -- which
is the intended shape, because rewriting a stored assertion is the act ruling
**R-HJ** already says a repair may not perform.

**Downgrade** drops the three triggers and the function, returning the two
sibling tables to the ORM-listener-only posture they carried before and
``account_anchor_history`` to convention.  Value-lossless: nothing was written
or altered, so nothing is lost by removing it.
"""

from alembic import op

from app.append_only_infrastructure import (
    apply_append_only_infrastructure,
    remove_append_only_infrastructure,
)

# revision identifiers, used by Alembic.
revision = "f4a7c2d9e51b"
down_revision = "d3b6f1c8a274"
branch_labels = None
depends_on = None


def upgrade():
    """Install ``budget.refuse_append_only_change`` on the three tables.

    Idempotent; see the module docstring and
    :func:`app.append_only_infrastructure.apply_append_only_infrastructure`.
    """
    apply_append_only_infrastructure(op.execute)


def downgrade():
    """Drop the three triggers and the function they share.

    Idempotent and a clean no-op on a database that never carried them.
    """
    remove_append_only_infrastructure(op.execute)
