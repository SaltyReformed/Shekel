"""the closing bound is the owner's word, and the window CHECK lands

Revision ID: bf50951a3599
Revises: 4d7123cd9803
Create Date: 2026-09-13 14:30:00.000000

Plan step **recurrence:R7d-g** (leaf R7d-g-1) of
``docs/plans/implementation_plan_recurrence_redesign.md`` section 4, closing
finding **D56** under ruling **R-R80** (developer, 2026-09-13) and landing the
CHECK plan ledger row **D35** has carried since plan step R7c-b::

    budget.recurrence_rules:  end_date IS NULL OR end_date >= starts_on

Two steps, in one transaction, and the order is load-bearing:

1. **NULL ``end_date`` on the rows the deleted writers filled.**  Until this
   step ten chokepoints wrote a loan's derived payoff into the authored
   bound's own column (``loan_recurrence_sync.sync_recurring_payment_bounds``,
   from every loan-params / rate edit, balance true-up, and transfer settle /
   revert / edit / delete / restore of a loan payment).  The code that wrote
   it is gone in the same commit; what it left behind is a CACHE of a
   derivation the composed door (``recurring_definition``) recomputes on every
   read, and a cache with no writer is a stale value forever.  **Which rows**
   is ruling **R-R80**, because the schema records who wrote a bound nowhere
   and a NULL-every-loan-payment predicate cannot tell the cache from a stop
   an owner typed:

   * the STANDING payment of every configured loan -- the oldest active
     recurring transfer into it, which is the ONE definition every chokepoint
     targeted (``active_recurring_transfer_template``: same filter, same
     ``ORDER BY id``); and
   * every ARCHIVED recurring transfer into a configured loan.  A former
     standing payment's column is the chokepoints' cache, which the Archived
     drawer was showing as its owner's word (``recurring_definition`` limit
     (2)); a former SECOND transfer's would be its owner's, and the ruling
     erases that too, because the schema cannot tell the two apart and the
     value re-enters through the doors when the definition is unarchived
     (zero such rows on production).

   A SECOND active transfer into a loan is NOT touched: its stop is its
   owner's (rulings **R-R60**, **R-R77**), honoured by the summed ESTIMATED
   tier (ruling **R-R37**), and erasing it would project money the owner said
   would not move.  ``max_occurrences`` is not touched on any row: the writers
   REPLACED a count with the derived date and never wrote one, so a count is
   an owner's word wherever it stands.

2. **Bind ``ck_recurrence_rules_valid_window``.**  PostgreSQL validates the
   predicate over every row as part of the ``ALTER TABLE``, so a stored pair
   step 1 did not clear -- an owner's stop below an owner's start -- fails
   the migration whole and the deploy rolls back.  Every row it then holds is
   an owner's word, and what keeps it TRUE afterwards is ONE comparison at
   the ONE writer: ``recurrence._authoring._author`` grades the pair it is
   about to store (the NORMALISED start beside the stated stop) and refuses
   an inverted one (``EmptyAuthoredWindowError``) before touching the row.
   The two authoring doors (``require_end_bound_after_start``,
   ``refuse_inverted_window``) grade the AUTHORED pair for the sentence a
   user sees; the loan-params door translates the writer's refusal into one
   naming the transfer.

Measured on a fresh clone of production (``shekel_r7dg``, dumped 2026-09-13
13:13 EDT at head ``4d7123cd9803``): TWO rows match step 1 -- rule 40
(template 2 "Mortgage", ``end_date`` 2048-12-01) and rule 48 (template 9 "Van
Payment", ``end_date`` 2029-02-22) -- both active, both their loan's standing
payment, both holding exactly the payoff the resolver derives today, so the
rows they generate to are unchanged by the NULL; zero archived transfers into
a loan, zero second transfers, zero count bounds, zero owner-authored stops
on a loan payment.  Step 2 then validates 44 rules, none inverted.
``$0.00`` moves.

**The downgrade drops the CHECK and restores NOTHING**, and says so on the
console: the values step 1 cleared were a cache of a derivation, recomputable
on any read by ``loan_payment_window`` for as long as the loan exists -- but
the pre-R7d-g code that would READ a restored cache is the code this step
deletes, so a restored column would be a second opinion with no reader.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "bf50951a3599"
down_revision = "4d7123cd9803"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_recurrence_rules_valid_window"

#: Step 1's predicate (ruling **R-R80**), mirroring
#: ``recurring_transfer_query.active_recurring_transfer_templates`` -- owner,
#: destination, ``is_active``, carries a rule, oldest ``id`` first -- and
#: "configured loan" as the ``budget.loan_params`` row the seam keys on.
_NULL_THE_CACHE = sa.text(
    """
    UPDATE budget.recurrence_rules AS rr
    SET end_date = NULL
    FROM budget.transfer_templates AS tt
    JOIN budget.loan_params AS lp ON lp.account_id = tt.to_account_id
    WHERE rr.transfer_template_id = tt.id
      AND rr.end_date IS NOT NULL
      AND (
        NOT tt.is_active
        OR tt.id = (
          SELECT min(standing.id)
          FROM budget.transfer_templates AS standing
          JOIN budget.recurrence_rules AS standing_rule
            ON standing_rule.transfer_template_id = standing.id
          WHERE standing.to_account_id = tt.to_account_id
            AND standing.user_id = tt.user_id
            AND standing.is_active
        )
      )
    """
)


def upgrade():
    """NULL the deleted writers' cache, then bind the window CHECK."""
    cleared = op.get_bind().execute(_NULL_THE_CACHE).rowcount
    print(
        f"budget.recurrence_rules.end_date cleared on {cleared} row(s): the "
        "standing payment of each configured loan and every archived "
        "recurring transfer into one (ruling R-R80)."
    )
    op.create_check_constraint(
        _CONSTRAINT,
        "recurrence_rules",
        "end_date IS NULL OR end_date >= starts_on",
        schema="budget",
    )


def downgrade():
    """Drop the CHECK.  The cleared cache is not restored, and cannot be."""
    op.drop_constraint(
        _CONSTRAINT, "recurrence_rules", schema="budget", type_="check",
    )
    print(
        "ck_recurrence_rules_valid_window dropped.  The end_date values the "
        "upgrade cleared are NOT restored: they were a cache of the loan's "
        "derived payoff, which loan_payment_window recomputes on read, and "
        "the code that read the cache is gone with the writers."
    )
