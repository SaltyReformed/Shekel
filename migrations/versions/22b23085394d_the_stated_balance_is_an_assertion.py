"""The balance stated at setup is an assertion; drop LoanParams.current_principal.

Plan step ``recurrence:R20``, ruling **R-R72** part 3, finding **REC-519**.

The loan setup form REQUIRED a "Current Principal" and ``create_params`` stored
it in ``budget.loan_params.current_principal`` -- demoted to a nullable,
non-authoritative seed at E-18 / Commit 15 (``c4f0a5b71e83``), its drop
deferred since, and read by NOTHING.  A loan configured mid-life therefore had
only its synthesized origination assertion, and under ruling **R-R71** (the
forward charge calendar is every contractual installment after the loan's
latest assertion) every unrecorded month since origination read as unpaid: a
``$250,000`` mortgage from 2023-06-01 set up without a tracking-start read
``$302,586.63`` after its first plan payment and never cleared, where a paying
borrower owes ``$240,215.10``.

The balance the owner states at setup IS a dated assertion, and the setup door
now records it as one: a ``tracking_start`` :class:`LoanAnchorEvent` on the
"as of" day the owner picks (the setup date by default), whenever the loan
originated before that day.  The column had no reader to lose, so this
migration does two things:

1. **Backfills the assertion the door would have written** for every loan the
   old door configured mid-life and whose owner never asserted a balance since:
   ``origination_date`` strictly before the setup day, a non-NULL
   ``current_principal``, and no ``user_trueup`` or ``tracking_start`` row of
   any date.  The row is a ``tracking_start`` at the setup day carrying the
   stored balance.  The setup day is ``loan_params.created_at`` read as the
   owner's civil day, ``(created_at AT TIME ZONE 'America/New_York')::date``
   -- the derivation ``e5b2c8a17d34`` used for the day a cash assertion was
   typed, spelled as a literal for the same reason (a migration is a historical
   record and must keep meaning the same thing if the app's display zone ever
   changes).  Legacy ``origination`` rows do not count as an assertion: every
   reader synthesizes the origination from the params and ignores them
   (``loan_loaders.load_loan_anchor_facts``), so a loan carrying only one is
   exactly the loan this backfill exists for.

   **Measured on a fresh production clone (``shekel_r20``, 2026-09-19): the
   predicate matches 0 of 2 loans**, so production backfills nothing.  Both live
   loans carry a ``user_trueup`` (the Mortgage a ``tracking_start`` too); the
   Van Loan's stored ``$17,020.47`` is already its 2026-05-22 ``user_trueup``,
   written by ``d3d25212504b``'s own backfill from this same column.

2. **Drops the column and its CHECK** ``ck_loan_params_curr_principal``.

The downgrade re-adds the column NULL and the CHECK.  It does not read the
balance back out of the backfilled rows -- the append-only event table is the
assertion's home now and the column was never one -- and it leaves those rows
in place, as the append-only contract requires (the Commit-15 downgrade below
this one restores NOT NULL, and its own diagnostic names the NULL rows).

Review: solo developer ruling R-R72 (2026-09-11); migration written 2026-09-19.
Destructive (drops a column) -- the ``Review:`` line per
``docs/coding-standards.md``; the data the column held is either an
assertion this migration records or a value nothing read.

Revision ID: 22b23085394d
Revises: 97f92340fffc
Create Date: 2026-09-19 07:40:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '22b23085394d'
down_revision = '97f92340fffc'
branch_labels = None
depends_on = None


#: The loans the old door configured mid-life whose owner never asserted a
#: balance since -- the ones whose stated balance is not yet recorded as the
#: assertion it is (see the module docstring, item 1).  Exposed so a test can
#: build the shape and grade the mapping without re-running the chain.
_UNASSERTED_MIDLIFE_LOANS_SQL = (
    "SELECT lp.account_id, "
    "       (lp.created_at AT TIME ZONE 'America/New_York')::date AS setup_day, "
    "       lp.current_principal "
    "  FROM budget.loan_params lp "
    " WHERE lp.current_principal IS NOT NULL "
    "   AND lp.origination_date "
    "       < (lp.created_at AT TIME ZONE 'America/New_York')::date "
    "   AND NOT EXISTS ( "
    "     SELECT 1 FROM budget.loan_anchor_events ev "
    "       JOIN ref.loan_anchor_sources s ON s.id = ev.source_id "
    "      WHERE ev.account_id = lp.account_id "
    "        AND s.name IN ('user_trueup', 'tracking_start') "
    "   ) "
    " ORDER BY lp.account_id"
)

#: The assertion the door would have written, one per row of the SELECT above.
#: ``id`` (sequence) and ``created_at`` (the DB clock) fill themselves; the
#: audit trigger on the table records the INSERT with no request user, as
#: ``d3d25212504b``'s backfill did.
_BACKFILL_TRACKING_START_SQL = (
    "INSERT INTO budget.loan_anchor_events "
    "    (account_id, anchor_date, anchor_balance, source_id) "
    "SELECT u.account_id, u.setup_day, u.current_principal, "
    "       (SELECT id FROM ref.loan_anchor_sources "
    "         WHERE name = 'tracking_start') "
    f"  FROM ({_UNASSERTED_MIDLIFE_LOANS_SQL}) u"
)


def upgrade():
    """Record the unrecorded stated balances, then drop the column."""
    bind = op.get_bind()
    unasserted = bind.execute(sa.text(_UNASSERTED_MIDLIFE_LOANS_SQL)).fetchall()
    op.execute(_BACKFILL_TRACKING_START_SQL)
    # The count is the migration's own measurement, printed so the operator
    # can compare it with the clone rehearsal's (0 on production, 2026-09-19).
    print(
        f"22b23085394d: recorded {len(unasserted)} stated balance(s) as "
        f"tracking_start assertions: "
        f"{[(r[0], r[1].isoformat(), str(r[2])) for r in unasserted]}"
    )
    op.drop_constraint(
        "ck_loan_params_curr_principal", "loan_params",
        schema="budget", type_="check",
    )
    op.drop_column("loan_params", "current_principal", schema="budget")


def downgrade():
    """Re-add ``current_principal`` NULL and its CHECK; the assertions stay."""
    op.add_column(
        "loan_params",
        sa.Column("current_principal", sa.Numeric(12, 2), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_loan_params_curr_principal", "loan_params",
        "current_principal >= 0", schema="budget",
    )
