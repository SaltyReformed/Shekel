"""an employer contribution names the salary profile that funds it

Revision ID: e7c4b9a2f350
Revises: c8f3a5d2e714
Create Date: 2026-09-03 23:55:00.000000

Plan step **salary:R14-a** -- the ADDITIVE half of ruling **R-SAL5**
(developer, 2026-09-03).  Nothing reads the column this adds; the reader is
``salary:R14-b``, which is the leaf that MOVES MONEY.  Split that way on the
developer's own ruling **R-SAL6**: the expand lands on its own so the money
diff is reviewed alone.

## What it does

``budget.investment_params`` gains a nullable ``salary_profile_id`` FK to
``salary.salary_profiles``.  It answers ONE question -- *which job's paycheck
funds this account's payroll feed* -- and it exists because today nothing
answers it at all.

## Why the question has no answer today

An account's employer contribution is a percentage OF a gross, and the gross
has to come from a salary profile.  Where a paycheck deduction names the
account, the deduction carries ``salary_profile_id`` and the link is implicit.
Where NO deduction names it, there is no link anywhere, and
``income_service.get_current_gross_biweekly`` resolves one by taking the
owner's first ``is_active`` profile through an UNORDERED ``.first()`` across
every scenario.

That is not a hypothetical.  On the data measured below, the owner's Empower
401(k) carries a 5% flat employer contribution and NO deduction names it, so
its modelled employer money is priced off an arbitrarily chosen profile right
now.  With one active profile the choice is right by luck; ``R-F16``'s
adversarial review measured the same shape at a **39% swing** on a two-job
owner, flipping between renders with no data change.

Ruling **R-SAL2** removes the CLOCK from that call -- the gross becomes the
period's own rather than ``date.today()``'s.  It does not remove the PROFILE
ambiguity, which is why **R-SAL5** is a separate rule and this is its column.

## The backfill, and what it deliberately leaves NULL

A row is filled only where the account HAS a payroll feed -- an employer
contribution, or an active deduction naming it.  An account with neither has
no fact to record and stays NULL rather than being given a plausible one.

Where it has a feed, the profile is resolved in two steps:

1. the single distinct profile that is ACTIVE, belongs to the ACCOUNT'S OWN
   OWNER, and has an active deduction naming the account -- if there is
   exactly one; else
2. the owner's single active salary profile, if they have exactly one.

Both are exact rather than heuristic: step 1 reads the link the deduction
already states, and step 2 is the only profile the answer could be.  An owner
with several active profiles and no deduction naming the account is genuinely
ambiguous and stays NULL -- ``R14-b`` puts that question to the owner at a
door rather than guessing here.

**Step 1's two qualifiers are load bearing and an adversarial review of this
step added both.**  A first draft tested only the DEDUCTION's ``is_active``,
which reads the archived-job case backwards and ignores ownership entirely;
the comment above :data:`_BACKFILL` records what each one costs.

## Measured

On a THROWAWAY CLONE of the developer's database -- ``shekel_r14`` on
``shekel-dev-db``, cloned from ``shekel`` (itself stamped ``c8e5a2f31b47``)
and upgraded through this revision, measured 2026-09-04.  Naming the clone
rather than ``shekel`` matters: the runtime database is several revisions
behind and this migration cannot be run against it, so a table headed
*Measured* that cited it would be quoting a run nobody could reproduce.

  ==================================================  =======
  measurement                                          value
  ==================================================  =======
  ``budget.investment_params`` rows                        4
  of those, with an employer contribution                  1
  of those, with an active deduction naming them           0
  owners holding exactly one active salary profile         1
  rows FILLED by this backfill                         **1**
  rows left NULL because they model no payroll feed        3
  rows left NULL because the profile is ambiguous      **0**
  ==================================================  =======

The one filled row is the Empower 401(k), whose 5% flat employer contribution
is the figure ``R14-b`` re-prices.

**The developer's data exercises only step 2, so the other branches were
CONSTRUCTED on that clone and each was run through a real upgrade.**  A
backfill arm nobody has executed is a backfill arm nobody has tested, and two
of these four were defects an adversarial review found rather than cases that
merely passed:

  ===================================================  ==============  =========
  constructed case                                     pre-fix wrote   now
  ===================================================  ==============  =========
  deduction names it, one active same-owner profile    profile 1       profile 1
  no deduction, owner has one active profile           profile 1       profile 1
  no deduction, owner has TWO active profiles          NULL            NULL
  ARCHIVED profile's still-active deduction names it   **profile 3**   profile 1
  ANOTHER USER's deduction names it                    **profile 4**   NULL
  ===================================================  ==============  =========

The last two rows are the defects.  In the archived case the pre-fix predicate
wrote the profile of a job the owner no longer holds, and the step-2 fallback
that finds the job they DO hold never ran.  In the cross-owner case it wrote a
STRANGER's ``salary_profile_id`` onto this owner's row, which ``R14-b`` would
have priced an employer contribution from.  Both were measured by running the
pre-fix predicate and the fixed one against the same constructed state.

**Row 5's owner was given a SECOND active profile, and without that detail the
row is not reproducible** -- a second adversarial review asked for it.  Post-fix
the stranger's deduction is excluded, so ``naming_profiles`` is 0 and step 2
decides; with the single active profile the *Measured* table above reports,
step 2 would legitimately write that profile rather than NULL, and the row
would demonstrate nothing about owner scoping.  Two active profiles make step 2
ambiguous, so NULL is the only correct answer and any id proves the stranger's
profile was taken.  ``tests/test_integration/
test_employer_contribution_profile_backfill.py`` constructs it the same way and
says so.

## Both directions

The upgrade is additive -- a nullable column, an FK and an index -- so no
existing row is invalidated and no figure moves: nothing reads the column
until ``R14-b``.  The downgrade drops it.  **The drop is value-lossless in the
sense that matters**: every value this backfill writes is DERIVED from
``salary.paycheck_deductions.target_account_id`` and
``salary.salary_profiles.is_active``, both of which the downgrade leaves
untouched, so re-running the upgrade reproduces the same assignment exactly.
It is not byte-lossless for a row a HUMAN later re-points through ``R14-b``'s
door; that is why the reader ships behind its own migration rather than here.

``ondelete="RESTRICT"`` rather than ``SET NULL``: a salary profile is
ARCHIVED and never deleted in this application -- ``delete_profile`` in
``routes/salary/profiles.py`` sets ``is_active = False`` on the profile and
its template, having first called ``salary_profile_service.archive_profile``
to FREEZE the rows the profile priced (that call is about the money, not
about the flag, and the ordering between them is finding N-261's fix).  So
RESTRICT constrains nothing that happens today and states the intent for the
day a hard delete is added: a profile an employer contribution is priced from
cannot silently take that price to NULL.  It matches this table's other ref
FK, ``employer_contribution_type_id``, rather than
``paycheck_deductions.target_account_id``'s SET NULL, whose target (an
account) genuinely is deletable.

**What archiving does NOT touch is the deductions**, and that is why the
backfill tests the PROFILE's ``is_active`` rather than only the deduction's.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e7c4b9a2f350"
down_revision = "c8f3a5d2e714"
branch_labels = None
depends_on = None


# The two resolutions, in order.  Step 1 reads the link a deduction already
# states; step 2 is the only profile the answer could be.  Both are scoped to
# rows that HAVE a payroll feed, which is what the WHERE clause's last
# conjunct expresses: an employer contribution, or a deduction naming the
# account.
#
# **A NAMING DEDUCTION MUST BELONG TO AN ACTIVE PROFILE OF THIS ACCOUNT'S OWN
# OWNER, and both halves of that were missing from the first draft.**  An
# adversarial review of this step measured each:
#
#   * ``routes/salary/profiles.delete_profile`` archives a profile by setting
#     ``profile.is_active = False`` and its TEMPLATE's, and touches its
#     DEDUCTIONS not at all -- so an archived job's deductions stay
#     ``is_active`` forever.  Without the ``p.is_active`` test, an owner who
#     leaves job A and keeps job B gets A's archived profile written here,
#     and the step-2 fallback that would have written B never runs.  That is
#     exactly the multi-job owner the column exists for.
#   * ``paycheck_deductions.target_account_id`` has NO ownership validation at
#     its write door -- finding **N-534**: ``schemas/validation/salary.py:273``
#     declares it a bare ``RowId`` and neither ``add_deduction`` nor
#     ``update_deduction`` checks that the account is the requester's, where
#     ``routes/retirement.py:200-214`` validates the mirror-image FK and calls
#     the unvalidated form "a forged FK (IDOR)" -- so a deduction on one
#     owner's profile can name another owner's account.  Without the
#     ``p.user_id``
#     test, this migration would write one user's ``salary_profile_id`` onto
#     another user's row -- and ``R14-b`` would price their employer
#     contribution off a stranger's salary.  A migration is not exempt from
#     the rule that every read of user data is scoped by its owner.
_BACKFILL = """
WITH naming AS (
    SELECT ip.id AS params_id,
           count(DISTINCT d.salary_profile_id) AS naming_profiles,
           min(d.salary_profile_id) AS naming_profile_id
      FROM budget.investment_params ip
      JOIN budget.accounts a ON a.id = ip.account_id
      JOIN salary.paycheck_deductions d
        ON d.target_account_id = ip.account_id
       AND d.is_active
      JOIN salary.salary_profiles p
        ON p.id = d.salary_profile_id
       AND p.is_active
       AND p.user_id = a.user_id
     GROUP BY ip.id
),
feed AS (
    SELECT ip.id AS params_id,
           a.user_id AS owner_id,
           (ip.employer_contribution_type_id <> :none_id)
               AS has_employer_contribution,
           COALESCE(n.naming_profiles, 0) AS naming_profiles,
           n.naming_profile_id AS naming_profile_id
      FROM budget.investment_params ip
      JOIN budget.accounts a ON a.id = ip.account_id
      LEFT JOIN naming n ON n.params_id = ip.id
),
owner_profile AS (
    SELECT p.user_id,
           count(*) AS active_profiles,
           min(p.id) AS only_profile_id
      FROM salary.salary_profiles p
     WHERE p.is_active
     GROUP BY p.user_id
)
UPDATE budget.investment_params ip
   SET salary_profile_id = CASE
         WHEN f.naming_profiles = 1 THEN f.naming_profile_id
         ELSE o.only_profile_id
       END
  FROM feed f
  LEFT JOIN owner_profile o ON o.user_id = f.owner_id
 WHERE ip.id = f.params_id
   AND (f.has_employer_contribution OR f.naming_profiles > 0)
   AND (f.naming_profiles = 1 OR o.active_profiles = 1)
"""


def upgrade():
    """Add ``salary_profile_id`` and backfill it where it is unambiguous."""
    op.add_column(
        "investment_params",
        sa.Column("salary_profile_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_foreign_key(
        "fk_investment_params_salary_profile_id",
        "investment_params", "salary_profiles",
        ["salary_profile_id"], ["id"],
        source_schema="budget", referent_schema="salary",
        ondelete="RESTRICT",
    )
    # The child-FK index the house convention requires (F-071 / F-079 / C-42):
    # R14-b joins params to their profile on every projection that models an
    # employer contribution, and an unindexed child FK makes that a sequential
    # scan over every user's rows.
    op.create_index(
        "idx_investment_params_salary_profile",
        "investment_params", ["salary_profile_id"],
        schema="budget",
    )
    # The ref id is resolved HERE and the backfill is refused without it,
    # rather than being reached through a scalar subquery inside the SQL.  A
    # subquery that finds no row yields NULL, ``<> NULL`` yields NULL, and the
    # whole employer-contribution arm then matches NOTHING -- filling zero rows
    # with no error and no way to tell that state from success.  An adversarial
    # review of this step named that; this is the house ``_assert_no_nulls``
    # idiom applied to an input rather than to an output.
    bind = op.get_bind()
    none_id = bind.execute(sa.text(
        "SELECT id FROM ref.employer_contribution_types WHERE name = 'none'"
    )).scalar()
    if none_id is None:
        raise RuntimeError(
            "ref.employer_contribution_types has no 'none' row, so this "
            "migration cannot tell which investment_params rows carry an "
            "employer contribution. Seed the ref table before upgrading."
        )
    bind.execute(sa.text(_BACKFILL), {"none_id": none_id})


def downgrade():
    """Drop the column, its index and its FK.

    Value-lossless for every row this migration wrote: each assignment is
    derived from the deduction links and the active-profile set, which the
    downgrade does not touch, so re-running the upgrade reproduces it.
    """
    op.drop_index(
        "idx_investment_params_salary_profile",
        table_name="investment_params", schema="budget",
    )
    op.drop_constraint(
        "fk_investment_params_salary_profile_id",
        "investment_params", schema="budget", type_="foreignkey",
    )
    op.drop_column("investment_params", "salary_profile_id", schema="budget")
