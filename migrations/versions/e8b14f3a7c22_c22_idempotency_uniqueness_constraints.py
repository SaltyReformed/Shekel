"""C-22 idempotency uniqueness constraints

Adds the four partial / composite uniqueness backstops described in
commit C-22 of the 2026-04-15 security remediation plan:

  1. ``uq_transfers_adhoc_dedupe`` -- partial unique index on
     ``budget.transfers (user_id, from_account_id, to_account_id,
     amount, pay_period_id, scenario_id)`` with predicate
     ``transfer_template_id IS NULL AND is_deleted = FALSE``.  Prevents
     a second active ad-hoc transfer with identical parameters from
     reaching the database, closing F-050 (Medium): a double-submit
     of the ad-hoc transfer form would otherwise create two parent
     transfers and four shadow transactions, silently doubling the
     user's projected debit and credit until they noticed the drift.
     scenario_id is included so an ad-hoc in baseline does not block
     the same shape in a what-if scenario.

  2. ``uq_anchor_history_account_period_balance_day`` -- partial
     unique expression index on
     ``budget.account_anchor_history (account_id, pay_period_id,
     anchor_balance, ((created_at AT TIME ZONE 'UTC')::date))``.
     Closes F-103 (Low) by rejecting a literal duplicate audit row
     inserted on the same calendar day; legitimate same-day
     corrections (different balance) are still allowed because
     ``anchor_balance`` is part of the key.  Pinning the truncation
     to UTC is required by PostgreSQL: the bare ``::date`` cast on a
     ``timestamptz`` is not IMMUTABLE (it depends on session
     TimeZone) and PostgreSQL refuses to index non-IMMUTABLE
     expressions.  UTC is the application's storage timezone for
     every ``timestamptz`` so the index pins the day-of-record
     exactly, irrespective of the connection's TimeZone setting.

  3. ``uq_rate_history_account_effective_date`` -- composite unique
     constraint on ``budget.rate_history (account_id, effective_date)``.
     Closes F-104 (Low): each rate change has exactly one effective
     date by definition; a same-day correction is expressed by
     editing the existing row rather than appending a duplicate.

  4. ``uq_pension_profiles_user_name`` -- composite unique constraint
     on ``salary.pension_profiles (user_id, name)``.  Closes F-105
     (Low): a duplicate pension profile would be displayed twice on
     the retirement dashboard and double-counted by gap analysis.

Pre-flight checks: PostgreSQL refuses to create unique indexes /
constraints when existing rows violate the predicate, but the
resulting error message points at the constraint name rather than
the offending data.  Each step here runs an explicit detection query
first so the operator receives the offending tuples and can plan a
single cleanup pass.  The migration aborts with ``RuntimeError``
before any DDL runs when violations are present -- per the
``docs/coding-standards.md`` rule that destructive migrations
require explicit approval, no row is ever deleted automatically.

Audit reference: F-050 + F-103 + F-104 + F-105 / commit C-22 of the
2026-04-15 security remediation plan.

Revision ID: e8b14f3a7c22
Revises: c21a1f0b8e74
Create Date: 2026-05-07 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "e8b14f3a7c22"
down_revision = "c21a1f0b8e74"
branch_labels = None
depends_on = None


# ── Constraint / index names ──────────────────────────────────────
#
# Each literal must stay in sync with the corresponding model
# declaration and the route-layer ``is_unique_violation`` helper.

ADHOC_TRANSFER_INDEX = "uq_transfers_adhoc_dedupe"
ADHOC_TRANSFER_PREDICATE = (
    "transfer_template_id IS NULL "
    "AND is_deleted = FALSE"
)

ANCHOR_HISTORY_INDEX = "uq_anchor_history_account_period_balance_day"

RATE_HISTORY_CONSTRAINT = "uq_rate_history_account_effective_date"

PENSION_PROFILE_CONSTRAINT = "uq_pension_profiles_user_name"


def _refuse(label: str, rows, render):
    """Abort the migration with a structured error message.

    Used by every pre-flight to surface the offending tuples so the
    operator can resolve duplicates manually before retrying.

    Args:
        label: Short human label for the constraint being added.
        rows: Sequence of database rows from the detection query.
        render: Callable that turns one row into a one-line string.
    """
    details = "; ".join(render(r) for r in rows)
    raise RuntimeError(
        f"Refusing to add {label}: {len(rows)} pre-existing duplicate "
        f"group(s) violate the constraint.  Resolve them manually "
        f"(typically by soft-deleting all but one of each set after "
        f"confirming with the user which row to keep) and rerun the "
        f"migration.  Offending rows: {details}."
    )


def upgrade():
    """Run all four pre-flight detection queries, then create the indexes.

    Each pre-flight refuses the migration cleanly when violators are
    found; combined with the all-or-nothing transaction Alembic wraps
    around the upgrade, no partial state can be left in the database.
    """
    bind = op.get_bind()

    # ── 1. Ad-hoc transfer duplicates ─────────────────────────────
    adhoc_dupes = bind.execute(
        sa.text(
            "SELECT user_id, from_account_id, to_account_id, "
            "       amount, pay_period_id, scenario_id, COUNT(*) AS cnt "
            "FROM budget.transfers "
            "WHERE transfer_template_id IS NULL "
            "  AND is_deleted = FALSE "
            "GROUP BY user_id, from_account_id, to_account_id, "
            "         amount, pay_period_id, scenario_id "
            "HAVING COUNT(*) > 1 "
            "ORDER BY user_id, from_account_id, to_account_id"
        )
    ).fetchall()
    if adhoc_dupes:
        _refuse(
            ADHOC_TRANSFER_INDEX,
            adhoc_dupes,
            lambda r: (
                f"user={r[0]} from={r[1]} to={r[2]} amount={r[3]} "
                f"period={r[4]} scenario={r[5]} count={r[6]}"
            ),
        )

    # ── 2. Anchor-history duplicates (same balance, same UTC day) ─
    # The detection query mirrors the index expression exactly --
    # ``(created_at AT TIME ZONE 'UTC')::date`` -- so the operator
    # sees the same offending tuples the index would reject when it
    # is created.  Anchoring to UTC matches the index's IMMUTABLE
    # requirement and the application's storage timezone.
    anchor_dupes = bind.execute(
        sa.text(
            "SELECT account_id, pay_period_id, anchor_balance, "
            "       ((created_at AT TIME ZONE 'UTC')::date) AS day, "
            "       COUNT(*) AS cnt "
            "FROM budget.account_anchor_history "
            "GROUP BY account_id, pay_period_id, anchor_balance, "
            "         ((created_at AT TIME ZONE 'UTC')::date) "
            "HAVING COUNT(*) > 1 "
            "ORDER BY account_id, pay_period_id"
        )
    ).fetchall()
    if anchor_dupes:
        _refuse(
            ANCHOR_HISTORY_INDEX,
            anchor_dupes,
            lambda r: (
                f"account={r[0]} period={r[1]} balance={r[2]} "
                f"day={r[3]} count={r[4]}"
            ),
        )

    # ── 3. Rate-history duplicates (same effective date) ──────────
    rate_dupes = bind.execute(
        sa.text(
            "SELECT account_id, effective_date, COUNT(*) AS cnt "
            "FROM budget.rate_history "
            "GROUP BY account_id, effective_date "
            "HAVING COUNT(*) > 1 "
            "ORDER BY account_id, effective_date"
        )
    ).fetchall()
    if rate_dupes:
        _refuse(
            RATE_HISTORY_CONSTRAINT,
            rate_dupes,
            lambda r: (
                f"account={r[0]} effective_date={r[1]} count={r[2]}"
            ),
        )

    # ── 4. Pension-profile duplicate names ────────────────────────
    pension_dupes = bind.execute(
        sa.text(
            "SELECT user_id, name, COUNT(*) AS cnt "
            "FROM salary.pension_profiles "
            "GROUP BY user_id, name "
            "HAVING COUNT(*) > 1 "
            "ORDER BY user_id, name"
        )
    ).fetchall()
    if pension_dupes:
        _refuse(
            PENSION_PROFILE_CONSTRAINT,
            pension_dupes,
            lambda r: (
                f"user={r[0]} name={r[1]!r} count={r[2]}"
            ),
        )

    # ── Apply DDL ─────────────────────────────────────────────────

    # 1. Ad-hoc transfer partial unique index.
    op.create_index(
        ADHOC_TRANSFER_INDEX,
        "transfers",
        [
            "user_id", "from_account_id", "to_account_id",
            "amount", "pay_period_id", "scenario_id",
        ],
        unique=True,
        schema="budget",
        postgresql_where=sa.text(ADHOC_TRANSFER_PREDICATE),
    )

    # 2. Anchor-history functional unique index.  PostgreSQL refuses
    # to index a non-IMMUTABLE expression, and ``timestamptz::date``
    # depends on session TimeZone so it is volatile by definition.
    # ``AT TIME ZONE 'UTC'`` pins the truncation to UTC, which is
    # the storage timezone every ``CreatedAtMixin`` row uses, and
    # the resulting cast IS IMMUTABLE.  The literal parentheses
    # match the model's declarative form so Alembic autogenerate
    # does not produce a spurious diff against the post-migration
    # schema.
    op.execute(
        f"CREATE UNIQUE INDEX {ANCHOR_HISTORY_INDEX} "
        "ON budget.account_anchor_history "
        "(account_id, pay_period_id, anchor_balance, "
        "((created_at AT TIME ZONE 'UTC')::date))"
    )

    # 3. Rate-history composite unique constraint.
    op.create_unique_constraint(
        RATE_HISTORY_CONSTRAINT,
        "rate_history",
        ["account_id", "effective_date"],
        schema="budget",
    )

    # 4. Pension-profile composite unique constraint.
    op.create_unique_constraint(
        PENSION_PROFILE_CONSTRAINT,
        "pension_profiles",
        ["user_id", "name"],
        schema="salary",
    )


def downgrade():
    """Drop every constraint and index this migration created.

    Drops are emitted in reverse order of creation so a partial
    failure produces a recognisable post-state for forensic review.
    """
    op.drop_constraint(
        PENSION_PROFILE_CONSTRAINT,
        "pension_profiles",
        schema="salary",
        type_="unique",
    )
    op.drop_constraint(
        RATE_HISTORY_CONSTRAINT,
        "rate_history",
        schema="budget",
        type_="unique",
    )
    op.execute(
        f"DROP INDEX IF EXISTS budget.{ANCHOR_HISTORY_INDEX}"
    )
    op.drop_index(
        ADHOC_TRANSFER_INDEX,
        table_name="transfers",
        schema="budget",
        postgresql_where=sa.text(ADHOC_TRANSFER_PREDICATE),
    )
