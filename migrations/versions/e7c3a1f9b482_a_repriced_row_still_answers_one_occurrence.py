"""a re-priced row still answers one occurrence

Drops ``is_override = FALSE`` from the OCCURRENCE-keyed generation index on
both row tables, keeping it on the paycheck-keyed undated one.  Plan step
**balance:X-au-h**, ruling **R-JR** (developer, 2026-09-03).

The exemption was added by ``c79bfaef598e`` so an override sibling could sit
beside its rule-generated parent: carry-forward moves an unpaid item into a
target period that already holds the generated row, and the index was keyed on
the PAY PERIOD, so the two collided.  ``c8e5a2f31b47`` (plan step R17) re-keyed
the dated index onto ``occurs_on``, and a move does not change which occurrence
a row answers -- so on that index the exemption stopped guarding anything.

**X-au-h is what made keeping it cost something.**  That step makes
``is_override`` mean exactly one thing -- *this row is the owner's, not the
rule's* -- and raises it on a RE-PRICE as well as a move.  A row an owner
merely re-priced never moved and still answers exactly one occurrence, but the
exemption would have dropped it out of the unique index, leaving
``_recurrence_common.OccurrenceClaims`` in Python as the only thing preventing
a second row for that occurrence.  ``classify_maintain_work``'s own docstring
records this class firing "silently where the moved row is still an override",
and R17 measured its last occurrence at 8 rows / ``$1,482.93`` on a production
clone.

The UNDATED index keeps the exemption, because there the collision is real and
current: ``carry_forward_service._execute._create_target_override_row`` writes
an override row with NO ``occurs_on``, which lands in the paycheck-keyed index
and must be allowed to sit beside the canonical undated row.

**Verified installable before writing this**, on a clone of production
(2026-09-04): 39 dated override transactions and 5 dated override transfers
would newly enter the two indexes, and ZERO duplicate ``(template, scenario,
occurrence)`` triples exist among the rows the tightened predicates cover -- so
every one of the 44 is admitted rather than refused.

**It refuses rather than trusting that measurement**, which is the shape
``c8f3a5d2e714``'s pre-flight established: production moves between a
measurement and a deploy, and a unique index that cannot build fails the
migration with PostgreSQL's own message naming one arbitrary pair.  The
pre-flight below runs the tightened predicate as a SELECT and raises with the
offending triples named, before any DDL runs.

Review: Josh, 2026-09-04 -- APPROVED: presented as a design fork against
leaving the predicate alone and against dropping the term from BOTH indexes,
with the worked case of a row re-priced $500.00 -> $520.00 projecting $1,020.00
if a second row for its occurrence is ever written. Drop-and-recreate on two
unique indexes, so destructive under the migration rules and approved as such.

Revision ID: e7c3a1f9b482
Revises: c8f3a5d2e714
Create Date: 2026-09-04
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'e7c3a1f9b482'
down_revision = 'c8f3a5d2e714'
branch_labels = None
depends_on = None


#: The pre-flight predicate, as a STANDALONE statement so it can be executed
#: against real rows rather than only read.  The precedent is
#: ``c8e5a2f31b47``'s ``COLLIDING_PAIRS_SQL``: this migration's DDL needs an
#: ACCESS EXCLUSIVE lock that conflicts with the xdist workers, and its SELECT
#: does not -- so the half that DECIDES is the half a suite can run.
#:
#: It groups over the TIGHTENED predicate (no ``is_override`` term), which is
#: exactly the row set the new index must cover, so a pass here is a proof the
#: index builds rather than an argument that it should.
COLLIDING_OCCURRENCES_SQL = (
    "SELECT {fk}, scenario_id, occurs_on, count(*) AS n "
    "FROM budget.{table} "
    "WHERE {fk} IS NOT NULL AND occurs_on IS NOT NULL AND is_deleted = FALSE "
    "GROUP BY {fk}, scenario_id, occurs_on "
    "HAVING count(*) > 1 "
    "ORDER BY {fk}, scenario_id, occurs_on"
)

# (table, template foreign key, dated index name)
_TABLES = (
    ("transactions", "template_id", "idx_transactions_template_scenario_occurrence"),
    ("transfers", "transfer_template_id", "idx_transfers_template_scenario_occurrence"),
)


def _dated_where(fk: str, *, exempt_overrides: bool) -> str:
    """Return the dated index's partial predicate.

    One producer for both directions, so the upgrade and the downgrade cannot
    come to disagree about what the index covers apart from the one term this
    revision moves.

    Args:
        fk: The table's template foreign key column.
        exempt_overrides: Whether to include the ``is_override = FALSE`` term.
            ``False`` is this revision's state, ``True`` the previous one.

    Returns:
        The SQL predicate text.
    """
    clauses = [
        f"{fk} IS NOT NULL",
        "occurs_on IS NOT NULL",
        "is_deleted = FALSE",
    ]
    if exempt_overrides:
        clauses.append("is_override = FALSE")
    return " AND ".join(clauses)


def _reject_colliding_occurrences() -> None:
    """Raise unless every row the tightened indexes cover is unique.

    Runs BEFORE any DDL, so a refusal leaves the schema exactly as it was
    rather than half-migrated.  Names the offending triples, because "the index
    could not build" without them sends an operator to a query they have to
    write themselves at the worst possible moment.

    Raises:
        RuntimeError: When a ``(template, scenario, occurrence)`` triple has
            more than one live row.
    """
    conn = op.get_bind()
    for table, fk, _idx in _TABLES:
        rows = conn.exec_driver_sql(
            COLLIDING_OCCURRENCES_SQL.format(table=table, fk=fk)
        ).fetchall()
        if rows:
            offenders = "; ".join(
                f"{fk}={r[0]} scenario={r[1]} occurs_on={r[2]} rows={r[3]}"
                for r in rows
            )
            raise RuntimeError(
                f"budget.{table}: {len(rows)} occurrence(s) already answered by "
                f"more than one live row, so the tightened unique index cannot "
                f"build. Plan step X-au-h removes the is_override exemption "
                f"from this index, which means an override row no longer gets "
                f"a free pass. Resolve these before upgrading -- {offenders}"
            )


def upgrade():
    """Tighten both dated indexes to cover override rows as well."""
    _reject_colliding_occurrences()
    for table, fk, idx in _TABLES:
        op.drop_index(idx, table_name=table, schema="budget")
        op.create_index(
            idx, table, [fk, "scenario_id", "occurs_on"],
            unique=True, schema="budget",
            postgresql_where=_dated_where(fk, exempt_overrides=False),
        )


def downgrade():
    """Restore the ``is_override = FALSE`` exemption on both dated indexes.

    Always installable: it LOOSENS the predicate, so every row the tightened
    index admitted is still admitted and no data repair can be required.
    """
    for table, fk, idx in _TABLES:
        op.drop_index(idx, table_name=table, schema="budget")
        op.create_index(
            idx, table, [fk, "scenario_id", "occurs_on"],
            unique=True, schema="budget",
            postgresql_where=_dated_where(fk, exempt_overrides=True),
        )
