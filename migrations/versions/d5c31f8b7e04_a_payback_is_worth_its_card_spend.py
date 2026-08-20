"""a CC payback's figure is DERIVED from the card spend it repays (X-au-i)

Plan step **X-au-i**, the first per-kind cutover of ruling **R-FI**'s amount
model.  It closes findings **N-243** (a payback's amount is a stored derived
value with two writers and no reconciler) and **N-252** (a hand edit to one is
unprotected, because ``is_override`` is unreachable for a link-less row).

**What a payback is worth, and why it takes two arms.**  A payback repays the
card spend of ONE source row, and what that source's card spend IS depends on
how the source holds its money.  ``Transaction.tracks_purchases`` is the app's
one published answer to that -- ``is_envelope`` for an ad-hoc row, its
template's for a generated one -- and the two kinds are DISJOINT by a write-door
refusal rather than by convention: ``routes/transactions/mutations.py`` refuses
Credit status on an entry-capable row (*"credit handling is per-entry, not
per-transaction"*) and the grid renders no Credit control for one.  So:

  * an entry-capable source keeps its spend in individual purchases, and the
    ones marked ``is_credit`` are what went on the card;
  * a single-spend source went on the card whole, so its card spend is the
    row's own resolved amount.

**Measured on a 2026-08-20 clone of PRODUCTION ITSELF**: 23 live paybacks, 0
soft-deleted, **10 with an entry-capable source and 13 with a single-spend
one**.  The step's original one-line specification -- *"a payback's figure is the
credit entries it repays"* -- would have valued those 13 at ``$0.00``, which is
what the census refuted and what the second arm exists for.

*A first census read 22 / 10 / 12 off a STALE dev clone.  It was re-taken
against production while rehearsing this release, which moved the single-spend
arm by one row and changed nothing else -- including the reported set below.*

Upgrade:

  1. Seed ``credit_source`` into ``ref.amount_sources`` -- the THIRD relation, a
     row's ``credit_payback_for_id``, which ``ck_transactions_one_pricing_link``
     already names as the third pricing link.
  2. REPORT every payback whose stored plan differs from what its arm derives,
     naming both figures and whether the row has settled.  Reported and NOT
     refused, on the ground migration ``f2b7c40d918e`` already established: a
     disagreeing row is one whose figure was WRONG, and this migration is the
     correction -- refusing would leave it broken.  On the production clone the
     reported set is exactly **one row, payback 2590**, hand-edited to
     ``$123.18`` on 2026-06-02 against credit entries summing to ``$181.58``
     (finding **N-252**'s own measurement, ``$58.40``).
  3. REFUSE when a single-spend source is ITSELF declared derived, because its
     amount column is then NULL and no SQL here can resolve it.  Unreachable
     today and stated rather than assumed: this is the FIRST per-kind cutover,
     so every source row still owns its figure (0 of 23 declare a relation).
     The later cutovers -- X-au-d salary, X-au-e template, X-au-f transfer --
     run after this one and change no source of a payback.
  4. Declare the paybacks derived: ``amount_source_id = credit_source`` and
     ``estimated_amount = NULL``, which ``ck_transactions_amount_ownership``
     pairs one-to-one.

**NO MONEY MOVES, and that is structural rather than lucky.**  All 23 live
paybacks have SETTLED, and ``row_valuation.fixed_contribution`` answers for a
settled row from its RECORD (``settled_amount`` / ``settled_basis_id`` /
``settled_on``) before the amount model is consulted at all -- plan step
X-au-c3.  ``estimated_amount`` is the PLAN, and no money reader of a settled row
reads the plan.  So emptying that column for these rows changes no balance, no
grid cell and no projection; what it changes is that a payback created from
tomorrow derives instead of storing.

**Reversible, with one documented asymmetry.**  The downgrade re-derives each
payback's figure into ``estimated_amount`` and clears ``amount_source_id``.  For
a row whose stored plan AGREED with its arm the restore is exact, which is 21 of
the 22.  For payback 2590 it restores the DERIVED ``$181.58`` rather than the
hand-edited ``$123.18`` -- the figure the app uses either side of this
migration, and restoring the typed one would re-create finding **N-252**.  The
same shape, and the same reasoning, as ``f2b7c40d918e``'s.  ``system.audit_log``
holds what the column said before either direction ran.

**The rollback path does not run this.**  ``deploy/shekel-deploy.sh`` rolls back
by re-pinning the previous image and REFUSES when the database has migrated past
what that image resolves (``repin_is_safe``); it never issues
``alembic downgrade``.  This downgrade is the developer's own step-back path.

**Not audited.**  ``ref.amount_sources`` is a read-only seed catalogue, excluded
from ``AUDITED_TABLES`` like every other ref catalogue, so no trigger is added
and ``EXPECTED_TRIGGER_COUNT`` is unchanged.  ``budget.transactions`` is already
audited, so the declaration change is recorded by its existing triggers.

Revision ID: d5c31f8b7e04
Revises: e4a7c0f13b92
Create Date: 2026-08-20 16:20:00.000000
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = 'd5c31f8b7e04'
down_revision = 'e4a7c0f13b92'
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# The third relation.  ``ON CONFLICT (name) DO NOTHING`` keeps it idempotent
# against a re-run and against the entrypoint's later reseed, which carries the
# identical row via ``app/ref_seeds.py`` -- the established dual-seed pattern
# (``b3f7c2a9d514`` seeded the first two the same way).  Migrations run below
# the app layer and must not import ``app`` code, so the name is spelled here
# rather than read off ``AmountSourceEnum``.
_SEED_SQL = (
    "INSERT INTO ref.amount_sources (name) VALUES ('credit_source') "
    "ON CONFLICT (name) DO NOTHING"
)

# Every live payback beside the figure its own arm derives.
#
# ``tracks_purchases`` is reproduced in SQL exactly as the model property states
# it: a generated row defers to its template's ``is_envelope`` and an ad-hoc row
# uses its own, which is what the COALESCE says -- ``tt`` is NULL precisely when
# ``template_id`` is.  The two arms are then a single CASE, so this query is the
# one statement of the rule in this file.
_DERIVED_SQL = """
SELECT p.id                      AS payback_id,
       p.estimated_amount        AS stored,
       p.settled_on IS NOT NULL  AS has_settled,
       s.id                      AS source_id,
       s.amount_source_id        AS source_declares,
       CASE WHEN COALESCE(tt.is_envelope, s.is_envelope)
            THEN (SELECT COALESCE(SUM(e.amount), 0)
                    FROM budget.transaction_entries e
                   WHERE e.transaction_id = s.id
                     AND e.is_credit)
            ELSE s.estimated_amount
       END                       AS derived
  FROM budget.transactions p
  JOIN budget.transactions s
    ON s.id = p.credit_payback_for_id
  LEFT JOIN budget.transaction_templates tt
    ON tt.id = s.template_id
 WHERE p.credit_payback_for_id IS NOT NULL
 ORDER BY p.id
"""


def _rows(conn):
    """Return every payback beside its derived figure, refusing an unresolvable one.

    Args:
        conn (sqlalchemy.engine.Connection): The Alembic connection.

    Returns:
        The result rows of :data:`_DERIVED_SQL`.

    Raises:
        RuntimeError: When a single-spend source is itself declared derived, so
            its amount column is NULL and this migration cannot resolve it.
    """
    rows = list(conn.execute(sa.text(_DERIVED_SQL)))
    unresolvable = [r for r in rows if r.derived is None]
    if unresolvable:
        raise RuntimeError(
            "X-au-i cannot declare these paybacks derived: their source rows "
            "are themselves declared derived, so no figure is stored to read. "
            + ", ".join(
                f"payback {r.payback_id} <- source {r.source_id}"
                for r in unresolvable
            )
            + ". This is the FIRST per-kind cutover and every source still owns "
            "its figure on production (0 of 23 declare a relation), so reaching "
            "here means a later cutover ran first. Resolve those sources' "
            "amounts through the app before running this."
        )
    return rows


def upgrade():
    """Seed the relation, report what moves, and declare every payback derived."""
    conn = op.get_bind()
    conn.execute(sa.text(_SEED_SQL))

    disagreeing = [r for r in _rows(conn) if r.stored != r.derived]
    for row in disagreeing:
        logger.info(
            "X-au-i: payback %s stored %s against the %s its source's card "
            "spend derives%s.",
            row.payback_id, row.stored, row.derived,
            " -- SETTLED, so no balance moves: a settled row is worth what it "
            "RECORDED (X-au-c3)" if row.has_settled
            else " -- PROJECTED, so this row's plan changes by "
                 f"{row.derived - row.stored}",
        )
    if not disagreeing:
        logger.info(
            "X-au-i: every payback's stored figure already equals what its "
            "source's card spend derives; the cutover moves nothing."
        )

    conn.execute(sa.text(
        "UPDATE budget.transactions SET "
        "  amount_source_id = (SELECT id FROM ref.amount_sources "
        "                       WHERE name = 'credit_source'), "
        "  estimated_amount = NULL "
        "WHERE credit_payback_for_id IS NOT NULL"
    ))


def downgrade():
    """Give every payback its derived figure back as a stored one."""
    conn = op.get_bind()
    # Written back one row at a time rather than as one correlated UPDATE
    # because the CASE above reads the payback's SOURCE, and the source of a
    # row-backed payback may be a row this same statement is not touching --
    # the read must therefore happen before any write lands.  22 rows on
    # production; the loop is not the cost.
    for row in _rows(conn):
        conn.execute(
            sa.text(
                "UPDATE budget.transactions "
                "SET estimated_amount = :amount, amount_source_id = NULL "
                "WHERE id = :id"
            ),
            {"amount": row.derived, "id": row.payback_id},
        )
    conn.execute(sa.text(
        "DELETE FROM ref.amount_sources WHERE name = 'credit_source'"
    ))
