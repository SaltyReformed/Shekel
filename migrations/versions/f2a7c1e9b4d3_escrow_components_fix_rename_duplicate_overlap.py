"""Collapse rename-duplicate escrow overlaps (data-only correction)

Revision ID: f2a7c1e9b4d3
Revises: e7c4a9f1b2d6
Create Date: 2026-07-06 12:00:00.000000

Review: solo developer, 2026-07-06 (a production-wide DATA-only correction -- no
schema change, no DDL.  It rewrites one column on the specific rows the temporal-
escrow migration ``d1e7c4a2f9b3`` corrupted; the downgrade is an intentional
documented no-op because re-introducing the corrected overlap is not a valid
state -- see below.)

## What this fixes

``d1e7c4a2f9b3`` turned ``budget.escrow_components`` into an effective-dated
series and backfilled EVERY row's ``effective_date`` to its loan's origination
date, and stamped the formerly-inactive (``is_active = false``) rows'
``end_date = GREATEST(updated_at::date, effective_date)``.  Its docstring assumed
"on real data there are no inactive components" -- which was FALSE for the real
Mortgage.  That loan's operator had RENAMED its escrow line: an old row
("Property Tax & Insurance") was deactivated and a new row ("Tax and Insurance",
same $7,403.88/yr) added.  Pre-migration only the active row counted ($616.99/mo);
the temporal backfill gave BOTH rows a range starting at origination, so they
OVERLAP for every date on/before the old row's ``end_date`` and any payment then
sums BOTH ($1,233.98) -- a double-count that inflated that payment's escrow leg
and drove its principal leg negative in the genesis loan ledger (see
``docs/audits/balance_architecture/`` and ``docs/design/loan_audit.md``).

## The correction

A rename-duplicate produced by that backfill has a precise signature: two rows on
the SAME account, with the SAME ``annual_amount`` and the SAME
``effective_date`` (both backfilled to the loan's origination floor), where one is
closed (``end_date`` set) and fully date-subsumed by the other (which extends
strictly further, including an open range).  This UPDATE collapses the subsumed
CLOSED row to an empty ``[end_date, end_date)`` range (``effective_date =
end_date``), which the ``ck_escrow_components_date_range`` CHECK admits as a valid
zero-length "never active" version.  The surviving row then covers every date
alone, restoring the correct single monthly escrow -- and matching the
pre-temporal semantics exactly (a deactivated line contributed nothing).

Idempotent: the ``ec.effective_date < ec.end_date`` guard makes a re-run a no-op
on an already-collapsed row.  Precise: the ``same amount + same effective_date``
predicate targets only the migration's rename-duplicate signature, so a genuinely
distinct escrow line (a different amount, or a later start reflecting its real
life) is never collapsed.  Verified against the dev database: exactly one row (the
Mortgage's old escrow name) matches.

## Scope limitation (same-amount only -- by design)

This corrects only the SAME-amount rename-duplicate.  ``d1e7c4a2f9b3`` created an
overlap for ANY deactivated escrow row, including an amount CHANGE expressed as
deactivate-old + add-new (e.g. Tax $1,200 -> Tax $1,400, both now starting at
origination), which double-counts pre-change payments.  Such a DIFFERENT-amount
subsumed row is NOT collapsed here, deliberately: it is indistinguishable from a
legitimate second escrow charge (a real Tax + PMI on the same start date), so
auto-collapsing it would risk erasing a real line -- the same reason the
``test_distinct_amount_subset_is_not_collapsed`` PMI case must survive.  A
different-amount overlap must therefore be reviewed by hand (is it an amount-change
duplicate, or two real charges?) and corrected via the escrow UI, not this
migration.  Confirmed ZERO different-amount overlaps on the dev database (a recent
prod clone) via::

    SELECT ec.account_id, ec.id, ec.name, ec.annual_amount, ec.effective_date,
           ec.end_date
    FROM budget.escrow_components ec
    WHERE ec.end_date IS NOT NULL AND ec.effective_date < ec.end_date
      AND EXISTS (SELECT 1 FROM budget.escrow_components other
        WHERE other.account_id = ec.account_id AND other.id <> ec.id
          AND other.effective_date = ec.effective_date
          AND other.annual_amount <> ec.annual_amount
          AND (other.end_date IS NULL OR other.end_date > ec.end_date));

Re-run that query against PROD before/at deploy; a non-zero result is a manual
review item, not a bug in this migration.  The broader fix (a stable escrow-line
identity so a rename/amount-change never manufactures an overlapping row) is the
proposed ``docs/design/escrow_line_identity_refactor.md`` work.

## Not a name-keyed guard, and no exclusion constraint

The two duplicate rows have DIFFERENT names, so a ``(account_id, name)`` overlap
constraint could not have caught this, and the go-forward add/delete flow already
produces adjacent (non-overlapping) ranges, so the double-count cannot recur
through the live app.  The real weakness -- the display ``name`` doubling as the
escrow line's identity key -- is addressed separately in the proposed
``line_id`` refactor documented at
``docs/design/escrow_line_identity_refactor.md`` (the agreed next piece of work),
where an overlap constraint keyed on a STABLE line id can prevent a rename-
duplicate.  This migration therefore adds no schema and no constraint.

## Self-contained

Imports nothing from ``app`` (the raw-SQL, name-resolved discipline the Step-2/3/4
backfills use).  On a fresh database the escrow table is loan-free, so the UPDATE
matches nothing and this migration is a no-op -- the ``d1e7c4a2f9b3`` bug it
corrects cannot exist on a fresh install.

## Re-sync of the affected postings

Correcting the escrow changes the affected payment's genesis-ledger split
(escrow / principal legs) and every downstream running balance.  The idempotent,
reconcile-to-target loan backfill in the post-migration deploy hook
(``scripts/init_database.py::backfill_loan_payment_postings_after_migration`` ->
``loan_posting_service.backfill_all_loan_postings``) re-splits every loan after
the chain reaches head, so the corrected escrow flows into the ledger on deploy.
A bare ``flask db upgrade`` / template rebuild does not run that hook (the
template is loan-free); a local verification runs the app-layer backfill directly.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = 'f2a7c1e9b4d3'
down_revision = 'e7c4a9f1b2d6'
branch_labels = None
depends_on = None


# Collapse each closed escrow version that is a rename-duplicate of another
# version on the same account -- SAME annual_amount and SAME effective_date (the
# temporal backfill's origination-floor signature), fully date-subsumed by the
# other (which extends strictly further, an open range counting as +infinity) --
# to an empty ``[end_date, end_date)`` range so it stops contributing.  The
# ``effective_date < end_date`` guard makes this idempotent.
_COLLAPSE_RENAME_DUPLICATE_SQL = (
    "UPDATE budget.escrow_components AS ec "
    "SET effective_date = ec.end_date "
    "WHERE ec.end_date IS NOT NULL "
    "  AND ec.effective_date < ec.end_date "
    "  AND EXISTS ( "
    "    SELECT 1 FROM budget.escrow_components AS other "
    "    WHERE other.account_id = ec.account_id "
    "      AND other.id <> ec.id "
    "      AND other.annual_amount = ec.annual_amount "
    "      AND other.effective_date = ec.effective_date "
    "      AND (other.end_date IS NULL OR other.end_date > ec.end_date) "
    "  )"
)


def upgrade():
    """Collapse the migration-created rename-duplicate escrow overlaps.

    Runs the single idempotent UPDATE (:data:`_COLLAPSE_RENAME_DUPLICATE_SQL`)
    that sets ``effective_date = end_date`` on every closed, subsumed rename-
    duplicate version, so the surviving version covers each date alone.  A no-op
    on a fresh (loan-free) database.  See the module docstring for the full
    rationale and the deploy hook that re-splits the affected postings.
    """
    op.execute(_COLLAPSE_RENAME_DUPLICATE_SQL)


def downgrade():
    """Intentional no-op: re-introducing the corrected overlap is not valid.

    This migration changes no schema, only data, and it corrects a known-bad
    double-count.  There is nothing to structurally reverse, and restoring the
    overlapping (double-counting) ranges would recreate the bug, so the downgrade
    deliberately does nothing.  This keeps the downgrade chain traversable (a
    downgrade through this revision to test a lower migration is not blocked).  To
    manually re-expand a specific collapsed row to its pre-correction range (NOT
    recommended -- it re-introduces the double-count), set its ``effective_date``
    back to the sibling version's ``effective_date`` by ``id``.
    """
