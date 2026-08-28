"""the two occurrences only a human could confirm

Revision ID: 94628d09816b
Revises: 95e7938240e4
Create Date: 2026-08-28 06:11:38.418293

Plan step **recurrence:R17**, closing the residue its backfill deliberately
refuses to guess at.

``scripts/stamp_occurrences.py`` assigns a row its occurrence by two
DEDUCTIONS -- the due date that occurrence computes, then the row's own pay
period.  It carried a third rule until adversarial review: "one row and one
occurrence left, so they must be each other".  That deduces nothing unless
every row answers some occurrence, which the script's own NULL case denies, and
it was reproduced pairing two INDEPENDENT anomalies -- a `$12.34` carry-forward
envelope row, which answers nothing, with an occurrence whose row had been
deleted -- stamping the envelope as a car payment nine paychecks away.  Under
the predicate leaf that reads this column, such a row SUPPRESSES generation of
the real bill: a payment silently disappears, which is worse than the duplicate
R17 exists to stop, because nothing shows it.  The rule was cut.

**Cutting it leaves exactly two rows on this database unstamped**, and both are
the same shape: the owner MOVED the row to a neighbouring paycheck AND edited
its due date, so neither deduction can reach it.  The developer confirmed both
pairings by hand on 2026-08-28, which is what makes them literal values rather
than a guess, and a confirmed one-time correction belongs in a migration.

  * ``Strawberry Picking`` id 2380 -- `$200.00`, Cancelled, sitting in pay
    period 3 with its due date edited to ``2026-05-23`` -- answers
    ``2026-04-15``.
  * ``Walmart+ Membership`` id 1514 -- `$104.62`, Paid and settled, sitting in
    pay period 2 with its due date edited to ``2026-04-30`` -- answers
    ``2026-03-31``.

Each template is a YEARLY rule with three rows and three occurrences whose
other two rows match their occurrence's period exactly (Strawberry in periods
28 and 54, Walmart+ in 27 and 53), so the remaining pairing is the only one
available -- but "the only one available" is the very argument the cut rule
made, and the difference is that a person checked these two.

**FINGERPRINTED, so it cannot stamp the wrong row.**  Each ``UPDATE`` matches
on the id AND the pay period AND the due date measured above, and only where
``occurs_on`` is still NULL.  On any database whose rows differ -- another
install, or this one after someone moves the row again -- nothing matches and
the migration is a no-op rather than a wrong write.  It reports what it
touched, so a zero is visible rather than silent.

**No figure moves.**  ``occurs_on`` is read by nothing until R17's second leaf;
this migration writes two dates onto two rows and changes no amount, status,
period or due date.

**Round-trips.**  The downgrade returns both rows to NULL under the same
fingerprint, which is the state this migration found them in.
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = '94628d09816b'
down_revision = '95e7938240e4'
branch_labels = None
depends_on = None

#: ``(row id, pay period, due date, the occurrence it answers)`` -- the
#: fingerprint each UPDATE matches on, and the value it writes.  Developer
#: confirmation, 2026-08-28; see this module's docstring for why each is the
#: only pairing available and why a person rather than the backfill made it.
_CONFIRMED = (
    (2380, 3, '2026-05-23', '2026-04-15'),
    (1514, 2, '2026-04-30', '2026-03-31'),
)


def upgrade():
    """Stamp the two confirmed occurrences, matching on the full fingerprint."""
    stamped = 0
    for row_id, period_id, due_date, occurs_on in _CONFIRMED:
        result = op.get_bind().exec_driver_sql(
            "UPDATE budget.transactions SET occurs_on = %(occurs_on)s "
            "WHERE id = %(row_id)s AND pay_period_id = %(period_id)s "
            "AND due_date = %(due_date)s AND occurs_on IS NULL",
            {
                "occurs_on": occurs_on, "row_id": row_id,
                "period_id": period_id, "due_date": due_date,
            },
        )
        stamped += result.rowcount
    print(f"R17: stamped {stamped} of {len(_CONFIRMED)} confirmed occurrence(s)")


def downgrade():
    """Return both rows to NULL, the state this migration found them in."""
    cleared = 0
    for row_id, period_id, due_date, occurs_on in _CONFIRMED:
        result = op.get_bind().exec_driver_sql(
            "UPDATE budget.transactions SET occurs_on = NULL "
            "WHERE id = %(row_id)s AND pay_period_id = %(period_id)s "
            "AND due_date = %(due_date)s AND occurs_on = %(occurs_on)s",
            {
                "occurs_on": occurs_on, "row_id": row_id,
                "period_id": period_id, "due_date": due_date,
            },
        )
        cleared += result.rowcount
    print(f"R17: cleared {cleared} of {len(_CONFIRMED)} confirmed occurrence(s)")
