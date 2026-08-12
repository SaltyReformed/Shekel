"""create budget.template_amount_versions and backfill it from the generated rows

Revision ID: a9d3c15e7f42
Revises: c7f3a9d1e864
Create Date: 2026-08-11 00:00:00.000000

Plan step **X-au-a**, rule 1 of ruling **R-FI**: a recurring definition's amount
becomes an effective-dated SERIES instead of a scalar with no time dimension.
ADDITIVE ONLY -- ``transaction_templates.default_amount`` and
``transfer_templates.default_amount`` are untouched and still authoritative, and
nothing prices a row from the new table yet (plan step X-au-b builds the
resolver, X-au-e cuts generated rows over).  Behaviour on deploy is unchanged.

## The new table

``budget.template_amount_versions`` -- one effective-dated version per recurring
definition, owned by an EXCLUSIVE ARC of two typed FKs with a CHECK that exactly
one is set, so one table serves both template kinds.  A version is in effect from
its ``effective_date`` until the next version of the same template supersedes it:
no ``end_date``, so an overlap is unrepresentable.  The DDL matches
``app/models/template_amount_version.py`` (a later ``flask db migrate
--autogenerate`` yields an empty diff), and the table is added to
``app/audit_infrastructure.py::AUDITED_TABLES`` and gets an audit trigger here by
the ``d3d25212504b`` / ``c4f8a1b6e9d2`` precedent (a narrow manual DROP+CREATE,
NOT ``apply_audit_infrastructure``, so an earlier fresh-DB replay of the rebuild
migration is not asked to trigger a table that does not exist yet).

## The backfill: the price history the ROWS already record

A template's past prices were never stored, but the rows it generated recorded
them, and which rows are TRUSTWORTHY evidence is not a judgement call -- it falls
straight out of what regeneration does to each row
(``app/services/_recurrence_common.py::partition_regeneration_rows``):

  * a row that is **overridden or soft-deleted** is a CONFLICT the sweep leaves
    untouched, so its amount is either a figure the user typed for that one row
    or a price that has gone stale.  **Not mined.**
  * a row that is **immutable (settled)** is skipped by the sweep, so its
    ``estimated_amount`` is frozen at the price in effect when it was generated.
    **Mined** -- this is where the real history lives.
  * every **other** row is deleted and recreated at the template's current
    ``default_amount``.  **Mined** -- consistently the newest price, which is
    what makes the last mined run agree with the scalar.

So the mining predicate is exactly ``NOT is_override AND NOT is_deleted``, plus
the baseline scenario and a non-null ``due_date``.

**It is the best predicate available and it is NOT airtight, which an
adversarial review established rather than assumed.**  Three ways a figure a
human chose reaches it, all recorded as findings rather than papered over:

  * the conflict chooser's "use" action writes the template's CURRENT amount
    onto a conflicted row and clears ``is_override``
    (``recurrence_engine.resolve_conflicts``).  On a back-dated
    ``effective_from`` that lands today's price on a PAST row, so the run
    encoding can read one price change as three (**N-244**);
  * a grid full-edit of a transfer SHADOW's amount reaches
    ``transfer_service.update_transfer`` without the override flag
    (``routes/transactions/_shadow_mutations.py``), so a hand-typed transfer
    figure stays minable (**N-245**);
  * a hand-edited ``due_date`` moves the encoding's own sort key without
    setting the flag either, which can reorder a history or put two amounts on
    one date -- the state ``_assert_no_contradictory_evidence`` now REFUSES
    rather than crashing on (**N-246**).

None of the three occurs on production: the derived series there is the minimum
the evidence supports (one version per template, three for ``Geico``, two for
the money-market contribution), and the contradiction guard finds nothing on
either arm.  The rows that still HOLD an override are what the predicate
reliably excludes, and that is the bulk of the hazard.

The derivation, per eligible template:

  1. **Eligibility.**  Skip a template whose amount is DERIVED rather than
     stated: a salary-linked transaction template (the paycheck calculator
     prices its rows) and a derive-mode loan-payment transfer template
     (``default_amount`` is a stored P&I + escrow snapshot).  Building a price
     series over a derived quantity manufactures a history nobody stated.  A
     MANUAL loan payment is eligible -- there the operator owns the base cash.
     The same predicate the application applies
     (``template_amount_service.owns_its_amount``), restated in SQL here because
     a migration imports nothing from ``app``.
  2. **Runs.**  Order the minable rows by ``(due_date, id)`` and run-length-encode
     them on amount; each run's FIRST row's ``due_date`` becomes a version at
     that run's amount.  The DUE date, not the pay period's, because that is the
     date a row's amount is resolved on (developer, 2026-08-11; the rule ruling
     D5 already applies to a loan payment's escrow).
  3. **The scalar's tail.**  ``default_amount`` is authoritative today, so the
     series must end at it.  When a template has NO minable row, its only version
     carries ``default_amount`` at the template's ``created_at`` date -- the day
     the price was first stated, and the only date the database holds.  When the
     newest mined run DISAGREES with ``default_amount``, a final version carries
     the scalar at the template's ``updated_at`` date, the only record of when it
     was set; if that date is not strictly after the newest mined run the two
     cannot be ordered and the migration ABORTS with a diagnostic rather than
     inventing one (the ``c4f8a1b6e9d2`` fail-loud precedent).

**Measured read-only against production 2026-08-11, so the size is known rather
than estimated:** 44 eligible templates (38 transaction + 6 transfer) yield 47
versions.  Only ``Geico`` has a real history -- ``$178.00`` (bills due
2026-04-01, 05-01), ``$178.32`` (due 06-01), ``$165.30`` (due 09-01 onward) --
and only the ``Checking -> Fidelity Money Market`` contribution has two
(``$500.00`` then ``$250.00`` from 2026-05-21); every other eligible template
yields one.  ``Data Manager`` is the sole excluded template (salary-linked), and
it is also the sole one whose newest minable row disagrees with its scalar, which
is the exclusion demonstrating itself.  Step 3's no-row arm fires once (transfer
template ``Rogue Equipment``, whose only row is hand-edited); its
disagreement arm fires zero times and its abort never.  No template has two
minable rows on one date on either arm, which is what
``_assert_no_contradictory_evidence`` now REFUSES rather than argues from.

Every step is idempotent (``NOT EXISTS`` guards), so a re-run after a partial
failure inserts nothing new.  **Self-contained:** imports nothing from ``app``;
all reads and writes are raw SQL, the discipline every backfill here uses.  The
backfill helpers are module-level so
``tests/test_models/test_template_amount_versions_backfill_migration.py`` can
drive the money-critical derivation directly against engineered rows.

**Downgrade** drops the new table (its audit trigger cascades with it).  Fully
reversible with no data loss: no existing column was read destructively or
written, so the evidence the backfill mines is still there and a re-upgrade
rebuilds the table from it.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "a9d3c15e7f42"
down_revision = "c7f3a9d1e864"
branch_labels = None
depends_on = None


# A transaction template is INELIGIBLE while an active salary profile drives its
# amounts -- the SQL twin of ``template_amount_service.is_salary_linked_template``.
_TXN_ELIGIBLE = (
    "NOT EXISTS ("
    "  SELECT 1 FROM salary.salary_profiles sp"
    "  WHERE sp.template_id = t.id AND sp.is_active"
    ")"
)

# A transfer template is INELIGIBLE while its loan-payment settings say the
# amount DERIVES from the loan -- the SQL twin of the same predicate's other arm.
# A template with no settings row is not a loan payment at all, so it is eligible.
_XFER_ELIGIBLE = (
    "NOT EXISTS ("
    "  SELECT 1 FROM budget.loan_payment_settings lps"
    "  WHERE lps.transfer_template_id = t.id AND lps.derive_from_loan"
    ")"
)


def _runs_sql(*, row_table: str, fk_column: str, amount_column: str,
              template_table: str, eligible: str) -> str:
    """Build the run-length-encoding SELECT for one template kind.

    Emits ``(template_id, effective_date, amount)`` -- one row per RUN of equal
    consecutive amounts among the template's minable rows, dated at the run's
    first row's ``due_date``.  The classic ``row_number() - row_number()``
    gaps-and-islands grouping: the difference between a row's position in the
    template's whole ordering and its position among the template's rows of that
    amount is constant exactly across a consecutive run.

    Both kinds run the identical statement over different column names, so it is
    generated once here rather than written twice -- the two recurrence engines'
    own parallelism, and the reason ``_recurrence_common`` exists.

    Two filters beyond the minable predicate, and both are exclusions rather
    than guesses.  A row with **no due date** carries no dated evidence at all
    (``due_date`` is nullable and the transfer edit form can clear it), so it
    cannot place a version and is dropped rather than sorted to one end.  And
    only the **baseline scenario** is read: regeneration is baseline-scoped
    (``_recurrence_conflict_chooser.regenerate_or_conflict_chooser``), so a
    what-if scenario's rows can hold a price the definition has since left, and
    interleaving two scenarios' rows on one date would manufacture a run
    boundary out of the ordering rather than out of a price change.

    Args:
        row_table: The generated-row table (``budget.transactions`` /
            ``budget.transfers``).
        fk_column: That table's template foreign key.
        amount_column: That table's amount column.
        template_table: The template table to join for eligibility.
        eligible: The eligibility predicate, over the template aliased ``t``.

    Returns:
        The SELECT statement text.
    """
    return (
        "SELECT tid, amt AS amount, min(due_date) AS effective_date "
        "FROM ("
        f"  SELECT r.{fk_column} AS tid, r.{amount_column} AS amt, r.due_date, "
        f"    row_number() OVER (PARTITION BY r.{fk_column} "
        "       ORDER BY r.due_date, r.id) "
        f"    - row_number() OVER (PARTITION BY r.{fk_column}, r.{amount_column} "
        "       ORDER BY r.due_date, r.id) AS island "
        f"  FROM {row_table} r "
        f"  JOIN {template_table} t ON t.id = r.{fk_column} "
        "  JOIN budget.scenarios s "
        "    ON s.id = r.scenario_id AND s.is_baseline "
        "  WHERE NOT r.is_override AND NOT r.is_deleted "
        "    AND r.due_date IS NOT NULL "
        f"    AND {eligible}"
        ") runs "
        "GROUP BY tid, amt, island"
    )


def _insert_runs_sql(*, fk_column: str, runs: str) -> str:
    """Wrap a run SELECT into the idempotent INSERT for its arm.

    ``NOT EXISTS`` on ``(fk, effective_date)`` makes a re-run insert nothing new
    and is also what the partial unique index would enforce, so the guard and the
    constraint state the same rule.

    Args:
        fk_column: The version table's owning FK column for this arm.
        runs: The run SELECT from :func:`_runs_sql`.

    Returns:
        The INSERT statement text.
    """
    return (
        "INSERT INTO budget.template_amount_versions "
        f"  ({fk_column}, effective_date, amount, created_at, updated_at) "
        "SELECT runs.tid, runs.effective_date, runs.amount, now(), now() "
        f"FROM ({runs}) runs "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM budget.template_amount_versions v "
        f"  WHERE v.{fk_column} = runs.tid "
        "    AND v.effective_date = runs.effective_date"
        ")"
    )


def _seed_scalar_sql(*, fk_column: str, template_table: str,
                     eligible: str) -> str:
    """Build the INSERT that gives a template with NO minable row its one version.

    Its ``default_amount`` at its ``created_at`` date -- the day the price was
    first stated, and the only date the database holds for a template whose rows
    are all hand-edited or removed.  Guarded on the template having no version at
    all, which makes it idempotent and confines it to the no-evidence case (a
    template WITH runs is served by :func:`_insert_runs_sql`).

    Args:
        fk_column: The version table's owning FK column for this arm.
        template_table: The template table to read.
        eligible: The eligibility predicate, over the template aliased ``t``.

    Returns:
        The INSERT statement text.
    """
    return (
        "INSERT INTO budget.template_amount_versions "
        f"  ({fk_column}, effective_date, amount, created_at, updated_at) "
        "SELECT t.id, (t.created_at AT TIME ZONE 'America/New_York')::date, "
        "       t.default_amount, now(), now() "
        f"FROM {template_table} t "
        f"WHERE {eligible} "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM budget.template_amount_versions v "
        f"    WHERE v.{fk_column} = t.id"
        "  )"
    )


def _contradiction_sql(*, row_table: str, row_fk: str, amount_column: str,
                       template_table: str, eligible: str) -> str:
    """Build the SELECT naming a date whose minable rows disagree on the amount.

    Two minable rows on ONE date carrying DIFFERENT amounts is contradictory
    evidence: each opens a run, both runs date at that same day, and a day cannot
    hold two prices -- which the partial unique index says structurally.  There is
    no derivation that resolves it, only a choice between two figures, so the
    migration refuses rather than picking one (the ``c4f8a1b6e9d2``
    overlap-guard precedent).

    **It is a guard rather than a measurement, and an adversarial review is why.**
    The first draft argued from production ("no template has two minable rows on
    one date") and let the insert run; a hand-edited ``due_date`` reaches that
    state without setting ``is_override`` (``routes/transactions/mutations.py``
    applies the column through its generic field loop while the override flag
    fires only for an amount or period change), so the shape is ordinary user
    input and the failure was an ``IntegrityError`` mid-upgrade.

    Args:
        row_table: The generated-row table.
        row_fk: That table's template foreign key.
        amount_column: That table's amount column.
        template_table: The template table to join for eligibility.
        eligible: The eligibility predicate, over the template aliased ``t``.

    Returns:
        The SELECT statement text.
    """
    return (
        f"SELECT r.{row_fk} AS tid, t.name, r.due_date, "
        f"       count(DISTINCT r.{amount_column}) AS distinct_amounts "
        f"FROM {row_table} r "
        f"JOIN {template_table} t ON t.id = r.{row_fk} "
        "JOIN budget.scenarios s ON s.id = r.scenario_id AND s.is_baseline "
        "WHERE NOT r.is_override AND NOT r.is_deleted "
        "  AND r.due_date IS NOT NULL "
        f"  AND {eligible} "
        f"GROUP BY r.{row_fk}, t.name, r.due_date "
        f"HAVING count(DISTINCT r.{amount_column}) > 1"
    )


def _assert_no_contradictory_evidence(bind, **arm) -> None:
    """Abort when one date's minable rows record two different amounts.

    See :func:`_contradiction_sql` for why this cannot be derived away.  Zero
    rows on production 2026-08-11, on both arms.

    Args:
        bind: A SQLAlchemy connection/bind exposing ``execute``.
        **arm: The per-kind column names passed through to
            :func:`_contradiction_sql`.

    Raises:
        RuntimeError: When any (template, date) carries more than one amount.
    """
    rows = bind.execute(sa.text(_contradiction_sql(**arm))).fetchall()
    if not rows:
        return
    detail = "; ".join(
        f"{arm['template_table']} {row.tid} ({row.name!r}) on {row.due_date}: "
        f"{row.distinct_amounts} different amounts"
        for row in rows
    )
    raise RuntimeError(
        "cannot backfill budget.template_amount_versions: a recurring "
        "definition's rows record two different amounts on ONE date, so its "
        "price on that date cannot be derived -- one of them is a hand edit "
        "that did not mark the row as overridden. Correct or override the "
        "wrong row, then re-run. "
        f"Offending dates: {detail}"
    )


def _tail_disagreement_sql(*, fk_column: str, template_table: str,
                           row_table: str, row_fk: str, eligible: str) -> str:
    """Build the SELECT naming templates whose newest version misses the scalar.

    ``default_amount`` is authoritative today, so a series whose newest version
    states something else would contradict the column it is being derived beside.
    That happens when the template's amount was edited but regeneration produced
    no minable row carrying the new figure -- an edit made while every forward row
    is already settled does exactly that, since the sweep skips an immutable row.

    It reports THREE dates, and the third is the one that matters: the newest
    version's ``effective_date``, and the newest minable ROW's ``due_date``, which
    is where that version's run ENDS.  **The ordering guard has to read the run's
    end, not its start**, and an adversarial review found it reading the start:
    with rows at $100.00 due Jan/Feb/Mar and the scalar stated on Feb 15, the
    scalar was inserted at Feb 15 -- inside the run -- and the March row then
    resolved to a price it does not carry.

    Args:
        fk_column: The version table's owning FK column for this arm.
        template_table: The template table to read.
        row_table: The generated-row table.
        row_fk: That table's template foreign key.
        eligible: The eligibility predicate, over the template aliased ``t``.

    Returns:
        The SELECT statement text.
    """
    return (
        "SELECT t.id, t.name, t.default_amount, "
        "       (t.updated_at AT TIME ZONE 'America/New_York')::date "
        "         AS stated_on, "
        "       newest.effective_date, newest.amount, "
        "       COALESCE(evidence.last_due, newest.effective_date) AS last_due "
        f"FROM {template_table} t "
        "JOIN LATERAL ("
        "  SELECT v.effective_date, v.amount "
        "  FROM budget.template_amount_versions v "
        f"  WHERE v.{fk_column} = t.id "
        "  ORDER BY v.effective_date DESC LIMIT 1"
        ") newest ON true "
        "LEFT JOIN LATERAL ("
        "  SELECT max(r.due_date) AS last_due "
        f"  FROM {row_table} r "
        "  JOIN budget.scenarios s ON s.id = r.scenario_id AND s.is_baseline "
        f"  WHERE r.{row_fk} = t.id "
        "    AND NOT r.is_override AND NOT r.is_deleted "
        "    AND r.due_date IS NOT NULL"
        ") evidence ON true "
        f"WHERE {eligible} AND newest.amount <> t.default_amount"
    )


def _apply_scalar_tail(bind, *, fk_column: str, template_table: str,
                       row_table: str, row_fk: str, eligible: str) -> None:
    """End every series at its template's authoritative ``default_amount``.

    For each template whose newest version disagrees with the scalar, append one
    version carrying the scalar at the template's ``updated_at`` date -- the only
    record the database holds of when that figure was set.  When that date is not
    strictly after the newest minable ROW's due date, the evidence and the scalar
    cannot be ordered; a date is INVENTED rather than derived at that point, so
    the migration fails loud with both figures instead (the ``c4f8a1b6e9d2``
    overlap-guard precedent).  Zero rows on production 2026-08-11.

    Args:
        bind: A SQLAlchemy connection/bind exposing ``execute``.
        fk_column: The version table's owning FK column for this arm.
        template_table: The template table to read.
        row_table: The generated-row table.
        row_fk: That table's template foreign key.
        eligible: The eligibility predicate, over the template aliased ``t``.

    Raises:
        RuntimeError: When a template's ``updated_at`` date does not fall after
            the last minable row its series was derived from.
    """
    rows = bind.execute(sa.text(
        _tail_disagreement_sql(
            fk_column=fk_column, template_table=template_table,
            row_table=row_table, row_fk=row_fk, eligible=eligible,
        ),
    )).fetchall()
    unorderable = [row for row in rows if row.stated_on <= row.last_due]
    if unorderable:
        detail = "; ".join(
            f"{template_table} {row.id} ({row.name!r}): default_amount "
            f"{row.default_amount} stated on {row.stated_on}, but its rows "
            f"record {row.amount} through {row.last_due}"
            for row in unorderable
        )
        raise RuntimeError(
            "cannot backfill budget.template_amount_versions: a template's "
            "default_amount disagrees with the newest price its rows record, "
            "and its updated_at is not after the last of those rows, so the "
            "date the amount was stated cannot be derived. Resolve each by "
            "editing the template (which restates its amount as of today), "
            f"then re-run. Offending templates: {detail}"
        )
    for row in rows:
        bind.execute(
            sa.text(
                "INSERT INTO budget.template_amount_versions "
                f"  ({fk_column}, effective_date, amount, created_at, "
                "   updated_at) "
                "VALUES (:tid, :eff, :amount, now(), now())"
            ),
            {"tid": row.id, "eff": row.stated_on,
             "amount": row.default_amount},
        )


def backfill_template_amount_versions(bind) -> None:
    """Populate ``budget.template_amount_versions`` from the generated rows.

    Runs the four derivation steps for each template kind: the contradiction
    guard, the run-length encoding of every minable row's amount, the
    single-version seed for a template with no minable row, and the scalar tail
    that makes each series end at its template's authoritative
    ``default_amount``.  The guard runs FIRST, so a refusal never leaves half a
    series behind.  Idempotent.  Exposed at module scope so the migration test
    can drive it against engineered rows.

    Args:
        bind: A SQLAlchemy connection/bind exposing ``execute``
            (``op.get_bind()`` in the migration; the test session in tests).

    Raises:
        RuntimeError: See :func:`_assert_no_contradictory_evidence` and
            :func:`_apply_scalar_tail`.
    """
    for fk_column, row_table, row_fk, amount_column, template_table, eligible in (
        (
            "transaction_template_id", "budget.transactions", "template_id",
            "estimated_amount", "budget.transaction_templates", _TXN_ELIGIBLE,
        ),
        (
            "transfer_template_id", "budget.transfers", "transfer_template_id",
            "amount", "budget.transfer_templates", _XFER_ELIGIBLE,
        ),
    ):
        _assert_no_contradictory_evidence(
            bind, row_table=row_table, row_fk=row_fk,
            amount_column=amount_column, template_table=template_table,
            eligible=eligible,
        )
        bind.execute(sa.text(_insert_runs_sql(
            fk_column=fk_column,
            runs=_runs_sql(
                row_table=row_table, fk_column=row_fk,
                amount_column=amount_column,
                template_table=template_table, eligible=eligible,
            ),
        )))
        bind.execute(sa.text(_seed_scalar_sql(
            fk_column=fk_column, template_table=template_table,
            eligible=eligible,
        )))
        _apply_scalar_tail(
            bind, fk_column=fk_column, template_table=template_table,
            row_table=row_table, row_fk=row_fk, eligible=eligible,
        )


def _attach_audit_trigger(table: str) -> None:
    """Attach the shared audit trigger to a new ``budget`` table (idempotent).

    A narrow manual DROP+CREATE pair (the ``d3d25212504b`` / ``c4f8a1b6e9d2``
    precedent) rather than ``apply_audit_infrastructure``, so an earlier fresh-DB
    replay of the rebuild migration is never asked to trigger a table that does
    not exist yet.

    Args:
        table: The ``budget``-schema table name to attach ``audit_<table>`` to.
    """
    op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON budget.{table}")
    op.execute(
        f"CREATE TRIGGER audit_{table} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{table} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def upgrade():
    """Create the version table + audit trigger, then backfill from the rows.

    Additive only -- neither template's ``default_amount`` is read destructively
    or written.  See the module docstring for the model and the derivation.
    """
    op.create_table(
        "template_amount_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_template_id", sa.Integer(), nullable=True),
        sa.Column("transfer_template_id", sa.Integer(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["transaction_template_id"], ["budget.transaction_templates.id"],
            ondelete="CASCADE",
            name="fk_template_amount_versions_transaction_template_id",
        ),
        sa.ForeignKeyConstraint(
            ["transfer_template_id"], ["budget.transfer_templates.id"],
            ondelete="CASCADE",
            name="fk_template_amount_versions_transfer_template_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(transaction_template_id IS NULL) <> "
            "(transfer_template_id IS NULL)",
            name="ck_template_amount_versions_one_owner",
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name="ck_template_amount_versions_nonneg_amount",
        ),
        sa.CheckConstraint(
            "transfer_template_id IS NULL OR amount > 0",
            name="ck_template_amount_versions_transfer_positive_amount",
        ),
        sa.CheckConstraint(
            "effective_date >= DATE '2000-01-01' "
            "AND effective_date <= DATE '2100-12-31'",
            name="ck_template_amount_versions_effective_date_range",
        ),
        schema="budget",
    )
    op.create_index(
        "uq_template_amount_versions_transaction_effective",
        "template_amount_versions",
        ["transaction_template_id", "effective_date"],
        unique=True, schema="budget",
        postgresql_where=sa.text("transaction_template_id IS NOT NULL"),
    )
    op.create_index(
        "uq_template_amount_versions_transfer_effective",
        "template_amount_versions",
        ["transfer_template_id", "effective_date"],
        unique=True, schema="budget",
        postgresql_where=sa.text("transfer_template_id IS NOT NULL"),
    )

    _attach_audit_trigger("template_amount_versions")

    backfill_template_amount_versions(op.get_bind())


def downgrade():
    """Drop the version table (its audit trigger cascades with it).

    Reversible with no data loss -- both ``default_amount`` columns and every
    generated row were left untouched, so the evidence the backfill mines is
    still there and a re-upgrade rebuilds the table from it.
    """
    op.drop_table("template_amount_versions", schema="budget")
