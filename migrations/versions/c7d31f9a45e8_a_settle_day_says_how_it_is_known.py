"""a settle day says how it is known

Plan step **X-az** of ``docs/audits/balance_architecture/README.md`` section 5,
closing finding **N-332**.  ``settled_on`` carried three different kinds of fact
and nothing on the row said which one it held.  This migration gives the day its
own basis column on both tables that carry it.

**The three kinds, and the three writers that produce them.**  The reconcile
panel writes the day a BALANCE was asserted for -- an UPPER BOUND, in
``reconcile_service._purchases.record_settled_days``' own words, *"``settled_on``
is an UPPER BOUND on the true posting day"*.  The statement matcher writes the
day the bank POSTED the line -- an observation.  The date box writes whatever the
owner typed.  The matcher told the first apart by testing whether
``reconciled_by_id`` was populated, which is verbatim the shape **N-241** deleted
one column over: ``settled_basis_id`` exists precisely so that *"which one a
figure is stands in ``settled_basis_id`` rather than being inferred from a column
being populated"*.  The inference was exact over the three writers it happened to
meet and BLIND to the third of them -- a day the owner typed read as a day the
bank had shown -- and reading a bound as an observation had already cost **50
duplicate purchases worth `$3,590.00`** on the developer's dev database before
``f633d46a``.

Four things, and **no row's balance moves**: the day's basis is metadata ABOUT a
day, and every balance, fold and posting reads the day itself, which this
migration does not touch on any row.

  1. **ref.settled_day_bases**, seeded ``observed`` / ``asserted`` / ``entered``.
     There is deliberately no ``not_settled`` row: carrying no settle day is the
     ABSENCE of a basis, which is what lets the pairing CHECKs below be written
     over two NULL-nesses instead of freezing a ref id into the schema (the shape
     ``ref.amount_sources`` and ``ref.settlement_bases`` both use).
  2. **``settled_day_basis_id``** on ``budget.transactions`` AND
     ``budget.transaction_entries``.  Both, because both carry ``settled_on`` and
     all three kinds of day are written to each -- where the FIGURE's basis
     needed only the one table, a purchase having no figure of its own.
  3. **The backfill**, which classifies every dated row and is measured rather
     than assumed (the counts are in :func:`upgrade`).
  4. **The pairing CHECKs**, one per table, as BICONDITIONALS.

**The pairing is a BICONDITIONAL where ``ck_transactions_settled_amount_needs_
basis`` is a bare implication, and the asymmetry is deliberate** (developer,
2026-08-22).  That constraint had its ``<-`` direction removed because
``settled_amount`` legitimately OUTLIVES the assertion that recorded it: a revert
releases the day and KEEPS what moved, so a figure with no day is the legal
RETAINED state.  The day and ITS basis have no such split lifetime -- the basis
describes the day, so the two are born and released in the same statement
(``app.services.settle_day.record_settle_day``) -- and forbidding a basis left
behind with no day costs nothing while removing the only residue a revert could
otherwise leave.

**The backfill's ``observed`` arm exists because the specification's claim that
it could not was measured FALSE.**  The step specification read *"no row can be
shown to be ``observed`` retrospectively, because the door that would have said
so is the one this step adds"*.  ``budget.statement_match_members`` is that door
and it already ran: a row belonging to an accepted match took that match's own
``max(posted_on)`` as its day (``statement_match._offers.MatchDays.of``, written
by ``_accept._apply_day``).  Measured on the developer's dev database
2026-08-22: 165 purchases and 70 transactions belong to a match, and **235 of 235
carry exactly that match's own posting day**.  Backfilling those ``entered``
would launder a bank observation into a typed guess -- the mirror image of the
defect ruling **R-FQ** refused to create when it declined to backfill
``reconciled_by_id`` from the date rule.

**EVERY arm is a PREDICATE, not a measurement.**  ``observed`` requires the
row's day to EQUAL its match's ``max(posted_on)`` and ``asserted`` requires it to
EQUAL its anchor's ``observed_on``, so a row whose day was later moved by hand
falls through to ``entered`` instead of claiming evidence it no longer holds.
Zero rows are in that state today; the arms are written that way so the claim
cannot go stale -- and the ``asserted`` arm tested only ``reconciled_by_id IS NOT
NULL`` until an adversarial review pointed out that this is verbatim the shape
the step exists to delete, kept as the classifier of record.

**One door into the stale state is worth naming**: ``record_statement``'s undo
deletes an import's lines and RELEASES its matches while deliberately leaving the
app rows alone (*"the days an accepted match wrote are the APP's own record and
they stay"*, ledger **N-333**).  A row undone that way keeps a stored
``observed`` basis this migration could no longer derive -- which is precisely
why the basis is STORED rather than derived per read, and precisely why a
re-run of this backfill after such an undo would under-claim rather than
over-claim.

**Measured on PRODUCTION 2026-08-22** (stamp ``a4c6f1d92b73``): 0 statement
matches exist there at all, so the ``observed`` arm classifies nothing and
production's rows land 0 / 0 / 173 (transactions) and 0 / 66 / 9 (entries).  The
arm costs production nothing and is what keeps the developer's own database
truthful.

**Not audited.**  ``ref.settled_day_bases`` is a read-only seed catalogue,
deliberately excluded from ``app.audit_infrastructure.AUDITED_TABLES`` on the
same criteria that keep ``ref.settlement_bases`` and every other ref catalogue
out.  No trigger is attached and ``EXPECTED_TRIGGER_COUNT`` is unchanged.
``budget.transactions`` and ``budget.transaction_entries`` are already audited,
so their existing triggers record the new column with no change here.

**Inline seed rationale.**  The three rows are seeded here rather than deferred
to the entrypoint's ``seed_reference_data`` pass, so ``ref_cache.init()`` resolves
``SettledDayBasisEnum`` immediately after a bare ``flask db upgrade`` -- an enum
member with no matching row is a fatal ``RuntimeError`` at app start.
``ON CONFLICT (name) DO NOTHING`` keeps it idempotent against a re-run and against
the entrypoint's later reseed, which carries the identical rows via
``app/ref_seeds.py``.

**The downgrade is lossless for every value the older schema can hold.**  It drops
a column the code it returns to does not read: before this revision the matcher
re-derived the same distinction from ``reconciled_by_id``, which this migration
does not touch on any row, so the older code answers exactly what it answered
before the upgrade.  What is lost is the ``observed`` / ``entered`` distinction,
which the older schema had no way to hold and no reader for -- stated rather than
hidden, and it is not money: no balance, posting or fold reads this column.

**One CONSTRAINT IS RENAMED, and the rename is the only destructive act here**
(developer approval 2026-08-22).  ``ck_transactions_settle_day_needs_basis``
means *a row claiming a settle DAY must record what MOVED* -- its predicate is
``settled_on IS NULL OR settled_basis_id IS NOT NULL``, and ``settled_basis_id``
is the FIGURE's provenance.  Beside this revision's
``ck_transactions_settle_day_basis_pairing`` that name reads as if it were about
the DAY's basis, and TWO live comments already read it that way
(``transfer_recurrence`` and ``recurrence_engine._maintain``, both corrected in
this step).  It becomes
``ck_transactions_settle_day_needs_a_record``, which is what its predicate says
and is already the name of the service-tier refusal that mirrors it
(``status_seam._refusals.reject_settle_day_without_a_record``).  Drop and
recreate on the IDENTICAL predicate: no row is read, no row is written, and the
downgrade restores the old name exactly.  Found by two independent adversarial
reviews 2026-08-22.

Review: Josh (developer), 2026-08-22

Revision ID: c7d31f9a45e8
Revises: af6cb5df0c45
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c7d31f9a45e8"
down_revision = "af6cb5df0c45"
branch_labels = None
depends_on = None


# The rows ``SettledDayBasisEnum`` names.  Written as literal SQL rather than
# built from a Python tuple, and that is not style: the cross-migration
# inline-seed guard (``tests/test_models/test_posting_ref_seed_parity.py``) scans
# this chain for each enum value as a SINGLE-QUOTED literal inside an
# ``INSERT INTO`` its own ref table, so a value assembled at run time from a
# double-quoted tuple would be invisible to it and the dual seed would go
# unguarded for this table.
_SEED_SETTLED_DAY_BASES_SQL = (
    "INSERT INTO ref.settled_day_bases (name) VALUES "
    "('observed'), "
    "('asserted'), "
    "('entered') "
    "ON CONFLICT (name) DO NOTHING"
)

#: The day a row's accepted statement match states, as a scalar subquery over
#: that match's own bank lines.  ``max(posted_on)`` is
#: ``statement_match._offers.MatchDays.of``'s rule for ``posts_on`` -- the day
#: every member row is moved onto -- restated in SQL because a migration may not
#: import ``app/``.  ``uq_statement_match_members_transaction`` and its entry
#: twin make the membership at most one row, so the join is 1:1.
_MATCH_POSTS_ON = (
    "SELECT max(l.posted_on) "
    "FROM budget.statement_match_members subject "
    "JOIN budget.statement_match_members line_member "
    "  ON line_member.match_id = subject.match_id "
    "JOIN budget.bank_statement_lines l "
    "  ON l.id = line_member.bank_statement_line_id "
    "WHERE subject.{column} = {alias}.id"
)


def _basis(name: str) -> str:
    """Return a scalar subquery for one ``ref.settled_day_bases`` id.

    A migration resolves a ref row by its NAME because the id is assigned by the
    sequence and differs between databases; application code never does this
    (``ref_cache.settled_day_basis_id`` is that door), which is why the lookup is
    spelled here rather than imported.

    Args:
        name: The ``SettledDayBasisEnum`` value to resolve.

    Returns:
        The SQL for a parenthesised scalar subquery yielding that row's id.
    """
    return f"(SELECT id FROM ref.settled_day_bases WHERE name = '{name}')"


def upgrade():
    """Create the day-basis catalogue, add both columns, and classify every day.

    Order is load-bearing: the ref table exists before either foreign key targets
    it, the backfill runs before the pairing CHECKs so no intermediate state has
    to satisfy them, and the three arms run OBSERVED first because each later arm
    narrows on ``settled_day_basis_id IS NULL`` -- so a row proven by the bank
    cannot be re-claimed by a weaker arm.

    Measured on the developer's dev database 2026-08-22 (1,059 transactions of
    which 220 are dated; 196 purchases of which 178 are dated), and every dated
    row lands in exactly one arm:

      * **transactions: 70 observed / 0 asserted / 150 entered.**  The 70 belong
        to accepted matches and every one carries that match's own posting day.
        No transaction has ever carried a clearing link on this database, which
        is why the asserted arm is empty here and not empty on production.
      * **purchases: 165 observed / 11 asserted / 2 entered.**

    And on PRODUCTION 2026-08-22 (stamp ``a4c6f1d92b73``, 0 statement matches):

      * **transactions: 0 / 0 / 173.**
      * **purchases: 0 / 66 / 9.**  All 66 linked purchases carry exactly their
        anchor's ``observed_on``, which is what the asserted arm now REQUIRES of
        them rather than merely observing.

    **One classification is deliberately softer than the code that replaces
    it**, and saying so is part of stating the backfill.
    ``statement_match._create.create_purchase_from_line`` closes an ENVELOPE on
    a bank line's ``posted_on`` and records a match naming the PURCHASE, not the
    envelope -- so arm 1 cannot see the envelope and it lands in ``entered``.
    46 transactions on the developer's dev database carry a day equal to a bank
    line matched to one of their own purchases, which is an UPPER BOUND on that
    population (some are coincidence, and the migration has no way to tell).
    The go-forward door stamps ``observed`` there; the backfill under-claims,
    which is the only safe direction for a fact nothing can prove.
    """
    op.create_table(
        "settled_day_bases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_SETTLED_DAY_BASES_SQL)

    for table, fk_name in (
        ("transactions", "fk_transactions_settled_day_basis_id"),
        ("transaction_entries", "fk_transaction_entries_settled_day_basis_id"),
    ):
        op.add_column(
            table,
            sa.Column("settled_day_basis_id", sa.Integer(), nullable=True),
            schema="budget",
        )
        op.create_foreign_key(
            fk_name, table, "settled_day_bases",
            ["settled_day_basis_id"], ["id"],
            source_schema="budget", referent_schema="ref",
            ondelete="RESTRICT",
        )

    for table, member_column, alias in (
        ("transactions", "transaction_id", "t"),
        ("transaction_entries", "transaction_entry_id", "t"),
    ):
        posts_on = _MATCH_POSTS_ON.format(column=member_column, alias=alias)
        # (1) OBSERVED -- an accepted statement match states this row's day, and
        # the row still carries exactly that day.  The EQUALITY is what makes
        # this a predicate rather than a stale measurement: a row whose day was
        # later corrected by hand no longer holds the bank's answer, so it falls
        # through to the ``entered`` arm instead of claiming an observation.
        # Zero rows are in that state on either database today.
        op.execute(
            f"UPDATE budget.{table} {alias} SET "
            f"settled_day_basis_id = {_basis('observed')} "
            f"WHERE {alias}.settled_on IS NOT NULL "
            f"AND {alias}.settled_on = ({posts_on})"
        )
        # (2) ASSERTED -- the row names the balance assertion whose statement was
        # seen to show it, AND still carries that assertion's own
        # ``observed_on``, which is the day the reconcile panel writes.  That
        # day is an UPPER BOUND: the money may have moved days earlier and the
        # assertion merely contained it.  Narrowed on ``settled_day_basis_id IS
        # NULL`` so a row the bank has since CONFIRMED keeps the stronger answer
        # arm 1 gave it.
        #
        # **The day EQUALITY is what makes this a predicate rather than a
        # measurement, and its absence was a finding** (adversarial design
        # review, 2026-08-22).  Testing ``reconciled_by_id IS NOT NULL`` alone
        # is verbatim the "infer a fact from a column being populated" shape
        # this whole step exists to delete, kept as the classifier of record --
        # and that column answers WHICH statement was seen, not what kind of day
        # the row holds, so a row whose day was later corrected by hand would
        # still be called a bound.  Encoding the equality costs ZERO rows: all
        # 66 linked purchases on production and all 11 on the developer's dev
        # database carry exactly their anchor's ``observed_on`` (measured
        # 2026-08-22).  A stale-linked row falls through to arm 3 and is called
        # ``entered``, which is the honest answer for a day nothing stands
        # behind.
        op.execute(
            f"UPDATE budget.{table} {alias} SET "
            f"settled_day_basis_id = {_basis('asserted')} "
            f"WHERE {alias}.settled_on IS NOT NULL "
            f"AND {alias}.settled_day_basis_id IS NULL "
            f"AND {alias}.settled_on = ("
            "SELECT a.observed_on FROM budget.account_anchor_history a "
            f"WHERE a.id = {alias}.reconciled_by_id)"
        )
        # (3) ENTERED -- every other dated row.  No bank line and no assertion
        # stands behind the day, so what it records is the app's own: a day the
        # owner typed into the correction box, or the day a settle door stamped
        # when they marked the row paid.
        op.execute(
            f"UPDATE budget.{table} {alias} SET "
            f"settled_day_basis_id = {_basis('entered')} "
            f"WHERE {alias}.settled_on IS NOT NULL "
            f"AND {alias}.settled_day_basis_id IS NULL"
        )

    # The FIGURE basis constraint's name, said the way its own predicate says it
    # (developer approval 2026-08-22; see the module docstring).  Dropped and
    # recreated on the IDENTICAL predicate, so no row is examined and none can
    # fail: PostgreSQL validates the new constraint over the table, and every
    # row already satisfied the one being dropped.
    op.drop_constraint(
        "ck_transactions_settle_day_needs_basis", "transactions",
        type_="check", schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_settle_day_needs_a_record", "transactions",
        "settled_on IS NULL OR settled_basis_id IS NOT NULL",
        schema="budget",
    )

    op.create_check_constraint(
        "ck_transactions_settle_day_basis_pairing", "transactions",
        "(settled_on IS NULL) = (settled_day_basis_id IS NULL)",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transaction_entries_settle_day_basis_pairing", "transaction_entries",
        "(settled_on IS NULL) = (settled_day_basis_id IS NULL)",
        schema="budget",
    )


def downgrade():
    """Drop both columns and the catalogue behind them.

    **Lossless for every value the older schema can hold, and it is the column's
    own design that makes that true.**  The renamed FIGURE-basis constraint goes
    back to ``ck_transactions_settle_day_needs_basis`` on the same predicate, so
    the older chain finds the name it expects.  ``settled_day_basis_id`` is metadata
    ABOUT ``settled_on`` and nothing else reads it: no balance, no fold, no
    posting and no valuation.  The code this returns to re-derives the one
    distinction it used (bound vs point) from ``reconciled_by_id``, which this
    migration never wrote to on any row, so every reader answers exactly what it
    answered before the upgrade.

    **What is lost is stated rather than hidden**: the ``observed`` / ``entered``
    split, which the older schema has no column for and no reader of.  Re-running
    :func:`upgrade` reconstructs it from the same two relations it was derived
    from, so the loss is also reversible for as long as those relations stand.
    """
    op.drop_constraint(
        "ck_transaction_entries_settle_day_basis_pairing", "transaction_entries",
        type_="check", schema="budget",
    )
    op.drop_constraint(
        "ck_transactions_settle_day_basis_pairing", "transactions",
        type_="check", schema="budget",
    )
    # The FIGURE basis constraint goes back to the name the older chain knows
    # it by, on the same predicate it has always carried.
    op.drop_constraint(
        "ck_transactions_settle_day_needs_a_record", "transactions",
        type_="check", schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_settle_day_needs_basis", "transactions",
        "settled_on IS NULL OR settled_basis_id IS NOT NULL",
        schema="budget",
    )
    for table, fk_name in (
        ("transaction_entries", "fk_transaction_entries_settled_day_basis_id"),
        ("transactions", "fk_transactions_settled_day_basis_id"),
    ):
        op.drop_constraint(
            fk_name, table, type_="foreignkey", schema="budget",
        )
        op.drop_column(table, "settled_day_basis_id", schema="budget")
    op.drop_table("settled_day_bases", schema="ref")
