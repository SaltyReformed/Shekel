"""every plan item has one definition: the cutover to the one-definition shape

Revision ID: 596408fab6f1
Revises: ad573b07bede
Create Date: 2026-09-18

Plan step **balance:X-bi-7d-2** (leaf 7d-2 of ``X-bi-7d``, the one that MOVES
MONEY) of ``docs/audits/balance_architecture/README.md`` section 5.

Review: developer-ruled -- **R-BAL20** (the shape: every plan item has exactly
one definition, a one-off's a RULE-LESS one plus its placed row), **R-BAL21**
(the price lives on the definition, ONE version), **R-BAL22** (an undated
one-off is due on its paycheck's start, and the cutover writes that date),
**R-BAL25** (the row records its own due date as the occurrence it answers),
**R-BAL28** (a rule-less definition's row carries no flag), **R-BAL37** (a row
the 7b-1 interim left detached is re-attached), **R-BAL67** (the downgrade is
SCHEMA-ONLY and folds nothing) and **R-BAL73** (a row with no definition has
no flag, so its popover offers none), 2026-09-12 to 2026-09-18.  Destructive:
it drops two populated columns, drops and re-creates a CHECK and two foreign
keys, and empties a money column on every row it links.  Every figure it
empties is restated on the definition it mints, to the cent, and the grade is
EQUALITY of the balance, the grid and the companion pages across the cutover
(``tests/manual/verify_one_definition_cutover.py``).

## What it does

Every BARE row in ``budget.transactions`` -- one naming no definition, no
transfer and no credit source (``template_id``, ``transfer_id`` and
``credit_payback_for_id`` all NULL; soft-deleted rows included, because the
CHECK below binds on every row) -- becomes what every one-off the application
has made since plan step ``X-bi-7b-1`` deployed already is: a rule-less
definition plus one placed row (``app.services.one_off.place_one_off``).  In
this order, in one transaction:

1. **Refuse before any write** (:func:`rows_the_cutover_cannot_mint`): a bare
   row storing no figure (nothing to price its definition from), or whose due
   date -- stated, else its paycheck's ``pay_periods.start_date`` -- falls
   outside the version table's ``[2000-01-01, 2100-12-31]``.  Zero of each on
   the 2026-09-18 production restore.  A negative figure is NOT asked about:
   ``ck_transactions_estimated_amount`` (``>= 0``) already makes it
   unrepresentable on the source row.
2. **Mint one definition per bare row** (:func:`mint_definitions`) into
   ``budget.transaction_templates``: the row's ``user_id``, ``account_id``,
   ``category_id``, ``transaction_type_id`` and ``name``; ``default_amount`` =
   the row's stored figure; ``is_envelope`` and ``companion_visible`` FROM THE
   ROW'S OWN CELLS (the two facts only a bare row stated for itself);
   ``is_active`` TRUE, ``sort_order`` 0, ``version_id`` 1.  A SOFT-DELETED
   bare row (0 on the restore; the population includes them because the
   CHECK binds on every row) gets an active definition like any other: the
   application never pairs a deleted row with an inactive definition (a
   placed row's delete is a hard delete that takes the definition), so this
   writes the one shape the readers know.  **Paired by row
   id**: names repeat across the population (two Claude Max rows, two Dental
   Reimbursements), so the definition ids are drawn from the sequence into a
   temporary ``(transaction_id, template_id, due)`` map first and every later
   statement joins on it.
3. **Open each definition's series**: one ``budget.template_amount_versions``
   row, ``amount`` = the figure, ``effective_date`` = the row's due date
   (**R-BAL21**: ONE version, dated on the row's due date, so the date may
   later move freely over a flat series).
4. **Link, date, declare, record the occurrence** -- one UPDATE per row, so
   ``ck_transactions_template_row_needs_due_date`` and
   ``ck_transactions_amount_ownership`` each see a whole row: ``template_id``
   = its definition; ``due_date`` = the stated day, else the paycheck's start
   (**R-BAL22**; 26 of 34 undated on the restore); ``occurs_on`` = that date
   (**R-BAL25**, on all 34, not only the 26); ``amount_source_id`` =
   ``ref.amount_sources('template')`` with ``estimated_amount`` NULL (amount
   rule 3 now prices it through the series it just opened); ``is_override``
   FALSE (**R-BAL28**; 0 of 34 carry it).
5. **Re-attach residue rows** (:func:`reattach_residue_rows`, **R-BAL37**): a
   placed row -- linked to a definition with no ``recurrence_rules`` row --
   that OWNS its figure or carries ``is_override``, and whose definition holds
   no other row (soft-deleted counted: a many-row rule-less definition's OWN
   row is legitimate under **R-BAL43** and is left alone).  Between 7b-1
   (deployed 2026-09-14) and 7b-2 a typed figure landed OWN on such a row with
   the flag beside it and the definition kept the old price.  The version its
   due date reads is restated to the row's figure, ``default_amount`` follows,
   the row is declared TEMPLATE-priced and the flag cleared.  **0 on the
   restore** (first measurement; R-BAL37 said unmeasured); the arm stays for a
   database that ran the 7b-1 code alone, and it selects nothing otherwise.
6. **DDL**: drop ``is_envelope`` and ``companion_visible`` from the row (every
   cell they held is now on a definition or was ``false``: 0 of 354 shadows, 0
   of 31 paybacks and 0 of the 639 rows already naming a definition -- 636
   generated, 3 placed -- held ``true`` on the restore);
   re-cut ``ck_transactions_one_pricing_link`` from ``<= 1`` to ``= 1``; move
   ``template_id``'s key from ``ON DELETE SET NULL`` (unnamed today, Postgres'
   ``transactions_template_id_fkey``) to **``fk_transactions_template_id``**
   ``ON DELETE RESTRICT``, and ``fk_transactions_credit_payback_for`` from
   ``SET NULL`` to ``RESTRICT`` -- under ``= 1`` a nulled link is a zero-link
   row, so the keys may no longer manufacture one.  **The ``= 1`` CHECK is what
   proves step 4 covered every row**: ``ADD CONSTRAINT`` validates the whole
   table, so a bare survivor refuses the DDL and the transaction rolls back.

## What it costs: `$0.00` by construction, and graded as equality

A bare row OWNED its figure; after the cutover the same figure is the ONE
version of its definition's series, read on the row's own due date -- so amount
rule 3 answers what rule 1 answered, to the cent, for every row (34 of 34 on
the restore, asserted by the harness).  A settled row's ``settled_amount`` is
untouched, and every money reader answers from the settlement record.  The
ONE surface that moves is ``payment_timeliness_from_txns`` over the Paid
expenses that were undated and are now dated on their paycheck's start (7 on
the restore) -- **R-BAL22**'s accepted cost, printed per row by the harness.

## The RESTRICT window, disclosed (finding CC-352)

``definition_delete.permanently_delete_definition`` and a paycheck's retire
bulk-delete rows without asking ``credit_workflow.delete_payback_on_source_delete``;
under ``SET NULL`` that left a payback as a zero-link orphan still repaying
nothing, and under ``RESTRICT`` the same two acts are REFUSED (an
``IntegrityError`` rather than a designed 400).  The developer ruled the
fail-closed refusal ships as is and the card cutover, which deletes every
payback row (**R-CC17**), closes the window.

## The downgrade (R-BAL67): schema only, nothing folded

Both columns return as ``BOOLEAN NOT NULL DEFAULT false``, the CHECK returns
to ``<= 1``, both keys return to ``SET NULL`` under their prior names.  Every
row KEEPS its definition, its price and its date, and ``occurs_on`` stays: the
pre-cutover application (7b's code) reads and writes exactly this shape for
every one-off it makes, so a rollback loses no behaviour, and a re-upgrade
mints 0 and dates 0 because the predicates select nothing.  The fold
``from_scratch_architecture.md`` 10.8 first specified was superseded on the
evidence that its one-row signature matches the owner's own grid-made
one-offs (3 on the restore) and can no longer identify this migration's
output.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "596408fab6f1"
down_revision = "a16516c8ec05"
branch_labels = None
depends_on = None

#: The declaration, resolved from the ref table by NAME (the chain's own
#: pattern, ``c8f3a5d2e714``): a migration cannot read ``app.ref_cache``, so
#: the id is looked up in the statement that uses it.
_TEMPLATE_SOURCE_SQL = "(SELECT id FROM ref.amount_sources WHERE name = 'template')"

#: A BARE row: no definition, no transfer, no credit source.  Soft-deleted rows
#: included -- the ``= 1`` CHECK binds on every row.
_BARE_SQL = (
    "t.template_id IS NULL AND t.transfer_id IS NULL "
    "AND t.credit_payback_for_id IS NULL"
)

#: The temporary pairing of each bare row with the definition id minted for
#: it and the date it will carry.  Session-scoped, created and dropped by
#: :func:`mint_definitions` so a test may drive the function twice.
_PAIRS_TABLE = "pg_temp.x_bi_7d_pairs"

_MAKE_PAIRS_SQL = f"""
    CREATE TEMPORARY TABLE x_bi_7d_pairs AS
    SELECT bare.id AS transaction_id,
           nextval(pg_get_serial_sequence(
               'budget.transaction_templates', 'id')) AS template_id,
           bare.due
      FROM (SELECT t.id, COALESCE(t.due_date, p.start_date) AS due
              FROM budget.transactions t
              JOIN budget.pay_periods p ON p.id = t.pay_period_id
             WHERE {_BARE_SQL}
             ORDER BY t.id) AS bare
"""

_MINT_SQL = f"""
    INSERT INTO budget.transaction_templates (
        id, user_id, account_id, category_id, transaction_type_id, name,
        default_amount, is_envelope, companion_visible, is_active,
        sort_order, version_id
    )
    SELECT m.template_id, t.user_id, t.account_id, t.category_id,
           t.transaction_type_id, t.name, t.estimated_amount, t.is_envelope,
           t.companion_visible, TRUE, 0, 1
      FROM {_PAIRS_TABLE} m
      JOIN budget.transactions t ON t.id = m.transaction_id
"""

_OPEN_SERIES_SQL = f"""
    INSERT INTO budget.template_amount_versions (
        transaction_template_id, effective_date, amount
    )
    SELECT m.template_id, m.due, t.estimated_amount
      FROM {_PAIRS_TABLE} m
      JOIN budget.transactions t ON t.id = m.transaction_id
"""

_LINK_SQL = f"""
    UPDATE budget.transactions AS t
       SET template_id = m.template_id,
           due_date = m.due,
           occurs_on = m.due,
           amount_source_id = {_TEMPLATE_SOURCE_SQL},
           estimated_amount = NULL,
           is_override = FALSE
      FROM {_PAIRS_TABLE} m
     WHERE m.transaction_id = t.id
"""

#: The rows step 4 dated: bare, and stating no day of their own.
_UNDATED_SQL = f"""
    SELECT COUNT(*) FROM budget.transactions t
     WHERE {_BARE_SQL} AND t.due_date IS NULL
"""

#: A RESIDUE row (R-BAL37): placed by a rule-less definition, OWN or flagged,
#: and the only row its definition holds (soft-deleted rows counted).
_RESIDUE_FROM_SQL = """
    FROM budget.transactions t
    JOIN budget.transaction_templates d ON d.id = t.template_id
   WHERE NOT EXISTS (SELECT 1 FROM budget.recurrence_rules r
                      WHERE r.transaction_template_id = d.id)
     AND (t.amount_source_id IS NULL OR t.is_override)
     AND NOT EXISTS (SELECT 1 FROM budget.transactions o
                      WHERE o.template_id = d.id AND o.id <> t.id)
"""

#: The version a residue row's due date reads: the newest at or before it,
#: else the earliest -- ``template_amount_service.amount_as_of``'s rule, one
#: row at a time.
_IN_EFFECT_VERSION_SQL = """
    COALESCE(
        (SELECT v.id FROM budget.template_amount_versions v
          WHERE v.transaction_template_id = t.template_id
            AND v.effective_date <= t.due_date
          ORDER BY v.effective_date DESC, v.id DESC LIMIT 1),
        (SELECT v.id FROM budget.template_amount_versions v
          WHERE v.transaction_template_id = t.template_id
          ORDER BY v.effective_date ASC, v.id ASC LIMIT 1))
"""

_RESTATE_RESIDUE_VERSION_SQL = f"""
    UPDATE budget.template_amount_versions AS v
       SET amount = t.estimated_amount
      FROM (SELECT t.template_id, t.estimated_amount,
                   {_IN_EFFECT_VERSION_SQL} AS version_id
              {_RESIDUE_FROM_SQL}
               AND t.amount_source_id IS NULL) AS t
     WHERE v.id = t.version_id
"""

_RESYNC_RESIDUE_DEFINITION_SQL = f"""
    UPDATE budget.transaction_templates AS d
       SET default_amount = t.estimated_amount
      FROM (SELECT t.template_id, t.estimated_amount
              {_RESIDUE_FROM_SQL}
               AND t.amount_source_id IS NULL) AS t
     WHERE d.id = t.template_id
"""

_REATTACH_RESIDUE_SQL = f"""
    UPDATE budget.transactions AS r
       SET amount_source_id = {_TEMPLATE_SOURCE_SQL},
           estimated_amount = NULL,
           is_override = FALSE
     WHERE r.id IN (SELECT t.id {_RESIDUE_FROM_SQL})
"""

_ONE_LINK_SQL = (
    "(template_id IS NOT NULL)::int "
    "+ (transfer_id IS NOT NULL)::int "
    "+ (credit_payback_for_id IS NOT NULL)::int"
)


def rows_the_cutover_cannot_mint(bind) -> list:
    """Return the bare rows no definition can be minted for, and why.

    **Module-level so a test can drive it** (the chain's pattern), asked of the
    database at the moment the migration runs rather than of a clone the week
    before.  Two shapes, each a state the schema admits on a bare row and the
    definition's tables refuse:

    * **no stored figure** -- ``ck_transactions_amount_ownership`` lets a row
      declare a relation instead, and a bare row declaring one names nothing
      that could price it; ``default_amount`` is NOT NULL;
    * **a due date outside ``[2000-01-01, 2100-12-31]``** -- the day the row
      states, else its paycheck's start, is the version the cutover opens, and
      ``ck_template_amount_versions_effective_date_range`` refuses it.

    Args:
        bind: A SQLAlchemy connection to probe.

    Returns:
        ``(id, name, reason)`` per offending row, ascending by id; empty when
        every bare row can be minted.  Empty on the 2026-09-18 production
        restore over all 34.
    """
    probe = sa.text(f"""
        SELECT t.id, t.name,
               CASE
                 WHEN t.estimated_amount IS NULL
                   THEN 'stores no figure to price its definition from'
                 ELSE 'its due date is outside the version table''s range'
               END AS reason
          FROM budget.transactions t
          JOIN budget.pay_periods p ON p.id = t.pay_period_id
         WHERE {_BARE_SQL}
           AND (t.estimated_amount IS NULL
                OR COALESCE(t.due_date, p.start_date)
                   NOT BETWEEN DATE '2000-01-01' AND DATE '2100-12-31')
         ORDER BY t.id
    """)
    return [tuple(row) for row in bind.execute(probe)]


def mint_definitions(bind) -> tuple:
    """Mint a definition per bare row, open its series, link and date the row.

    Steps 2 to 4 of the module docstring, module-level so a test can drive
    them against planted rows.  The pairing table is created and dropped here
    so a second call in one session starts clean (and, the population being
    empty then, mints nothing).

    Args:
        bind: A SQLAlchemy connection to write through.

    Returns:
        ``(minted, dated)`` -- how many definitions were minted and how many
        of their rows were undated before the cutover wrote their date.
    """
    dated = bind.execute(sa.text(_UNDATED_SQL)).scalar()
    bind.execute(sa.text(f"DROP TABLE IF EXISTS {_PAIRS_TABLE}"))
    bind.execute(sa.text(_MAKE_PAIRS_SQL))
    minted = bind.execute(sa.text(_MINT_SQL)).rowcount
    bind.execute(sa.text(_OPEN_SERIES_SQL))
    bind.execute(sa.text(_LINK_SQL))
    bind.execute(sa.text(f"DROP TABLE {_PAIRS_TABLE}"))
    return minted, dated


def reattach_residue_rows(bind) -> int:
    """Re-attach every residue row to its definition (R-BAL37); return the count.

    Step 5 of the module docstring.  The order is load-bearing: the version
    the row's date reads and the definition's scalar are restated from the
    row's OWN figure FIRST, while the row still stores it; the row is then
    declared TEMPLATE-priced, its figure emptied and its flag cleared, in one
    statement so ``ck_transactions_amount_ownership`` sees a whole row.  A
    residue row that is flagged but already TEMPLATE-priced has no figure of
    its own to restate and only loses the flag.

    Args:
        bind: A SQLAlchemy connection to write through.

    Returns:
        How many rows were re-attached.  0 on the 2026-09-18 production
        restore.
    """
    bind.execute(sa.text(_RESTATE_RESIDUE_VERSION_SQL))
    bind.execute(sa.text(_RESYNC_RESIDUE_DEFINITION_SQL))
    return bind.execute(sa.text(_REATTACH_RESIDUE_SQL)).rowcount


def upgrade():
    """Refuse the unmintable, mint, re-attach, then state the shape in the schema.

    Raises:
        RuntimeError: When any bare row cannot be minted a definition
            (:func:`rows_the_cutover_cannot_mint`).  NOTHING has been changed.
    """
    bind = op.get_bind()
    unmintable = rows_the_cutover_cannot_mint(bind)
    if unmintable:
        detail = "; ".join(
            f"txn {row_id} ({name}): {reason}"
            for row_id, name, reason in unmintable
        )
        raise RuntimeError(
            f"X-bi-7d-2: {len(unmintable)} bare row(s) cannot be minted a "
            f"definition. NOTHING has been changed. {detail}"
        )
    minted, dated = mint_definitions(bind)
    reattached = reattach_residue_rows(bind)
    op.drop_column("transactions", "is_envelope", schema="budget")
    op.drop_column("transactions", "companion_visible", schema="budget")
    op.drop_constraint(
        "ck_transactions_one_pricing_link", "transactions", schema="budget",
        type_="check",
    )
    op.create_check_constraint(
        "ck_transactions_one_pricing_link", "transactions",
        f"{_ONE_LINK_SQL} = 1", schema="budget",
    )
    op.drop_constraint(
        "transactions_template_id_fkey", "transactions", schema="budget",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_transactions_template_id", "transactions", "transaction_templates",
        ["template_id"], ["id"], source_schema="budget",
        referent_schema="budget", ondelete="RESTRICT",
    )
    op.drop_constraint(
        "fk_transactions_credit_payback_for", "transactions", schema="budget",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_transactions_credit_payback_for", "transactions", "transactions",
        ["credit_payback_for_id"], ["id"], source_schema="budget",
        referent_schema="budget", ondelete="RESTRICT",
    )
    print(
        f"X-bi-7d-2: minted {minted} definition(s), dated {dated} row(s), "
        f"re-attached {reattached} residue row(s)."
    )


def downgrade():
    """Restore the schema and move no row (ruling R-BAL67)."""
    op.drop_constraint(
        "fk_transactions_credit_payback_for", "transactions", schema="budget",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_transactions_credit_payback_for", "transactions", "transactions",
        ["credit_payback_for_id"], ["id"], source_schema="budget",
        referent_schema="budget", ondelete="SET NULL",
    )
    op.drop_constraint(
        "fk_transactions_template_id", "transactions", schema="budget",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "transactions_template_id_fkey", "transactions", "transaction_templates",
        ["template_id"], ["id"], source_schema="budget",
        referent_schema="budget", ondelete="SET NULL",
    )
    op.drop_constraint(
        "ck_transactions_one_pricing_link", "transactions", schema="budget",
        type_="check",
    )
    op.create_check_constraint(
        "ck_transactions_one_pricing_link", "transactions",
        f"{_ONE_LINK_SQL} <= 1", schema="budget",
    )
    # The declaration ``aeb04f13caff`` first added them with, verbatim.
    for column in ("is_envelope", "companion_visible"):
        op.add_column(
            "transactions",
            sa.Column(
                column, sa.Boolean(), server_default="false", nullable=False,
            ),
            schema="budget",
        )
