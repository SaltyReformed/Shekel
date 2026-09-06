"""The governing opening is the latest ROW, not the earliest transaction

Revision ID: c9f4b1e78d02
Revises: a7e3c95d2f18
Create Date: 2026-08-31 06:40:00.000000

Plan step ``balance:X-f3c-2b-2a``.  Found by that step's adversarial design
review; it opened and closed inside the step, so it was never a ledger row.

**What was wrong.**  ``budget.account_books_opened_on`` and its Python twin
both ordered ``created_at DESC, id DESC`` from one shared constant
(:data:`app.opening_infrastructure.GOVERNING_ORDER_SQL`), justified by the
claim that ``created_at`` is set on INSERT and is therefore monotone in
recording order.  It is not.
:class:`app.models.mixins.CreatedAtMixin` defaults the column to
``db.func.now()``, and PostgreSQL's ``now()`` is ``transaction_timestamp()`` --
the instant the transaction BEGAN.
``anchor_service._governing_loan_anchor`` states the same fact about the loan
twin in as many words; this table never carried it across.

**What it costs, in the shape a user reaches it.**  Two restatements from two
browser tabs.  Tab B's transaction opens first; tab A's opens later, takes the
owner's write lock first and commits; B then blocks on that lock, appends, and
commits SECOND while carrying the EARLIER instant.  Under ``created_at DESC``
B's row sorts BELOW the one it was meant to supersede -- so the owner is told
"Books restated", nothing moves, and the row is invisible forever on an
append-only table.  That is a silent no-op on the level every balance the app
renders for that account is stacked on.

**Why this revision exists at all, rather than the constant change being
enough.**  ``scripts/init_database.py`` re-applies the opening infrastructure
only on its FRESH-database path; ``migrate_existing_database`` runs the Alembic
chain and nothing else, and the two revisions that installed these functions
(``d3b6f1c8a274``, ``b8e3d5a06c94``) are already stamped.  So without this
revision a deployed database would keep the OLD function body while the Python
reader used the new order -- the two tiers disagreeing about which restatement
is in force, which is the single failure the shared constant exists to prevent.
The suite caught it: ``test_the_LATER_ROW_governs_even_when_its_instant_is_
EARLIER`` fails on the SQL side alone until the function is swapped.

**It moves no figure, and that is measured rather than assumed.**  Every
``budget.account_openings`` row on production was written by ``a7c41f9d2b60``
or ``d3b6f1c8a274``, each of which writes at most one row per account inside a
single transaction -- so ``created_at`` is constant within an account and ``id``
already carried the whole answer.  Measured on a production clone at head:
comparing the governing row under both orderings across all nine accounts gives
``differs = false`` on every one, so no opening day and no opening equity
changes.

Review: not required -- this alters no table, no column and no constraint.  It
replaces FUNCTION BODIES through ``CREATE OR REPLACE FUNCTION`` and touches
nothing else.

**That claim is true because the revision was narrowed to make it true**, which
a code review asked for on 2026-08-31.  It called
``apply_opening_infrastructure``, whose trigger half issues
``DROP TRIGGER IF EXISTS`` plus ``CREATE CONSTRAINT TRIGGER`` on three
constraint triggers -- the drop-and-recreate shape
``.claude/rules/database.md`` requires a ``Review:`` line for, restoring them
identically or not.  It calls
:func:`app.opening_infrastructure.apply_opening_functions` instead: a revision
that does not touch a constraint cannot be wrong about whether it touched one.
"""

from alembic import op

from app.opening_infrastructure import MOVEMENT_ARM, apply_opening_functions


# revision identifiers, used by Alembic.
revision = "c9f4b1e78d02"
# RE-PARENTED at the `origin/dev` merge, not at commit time (plan step
# balance:X-f3c-2b-2a).  This was authored against `b8e3d5a06c94` while
# `pay_calendar`'s `a7e3c95d2f18` sat on the same parent, uncommitted and
# unmerged: a `down_revision` may only name a revision present in the SAME
# tree, so pointing at it earlier would have broken `flask db upgrade`, the
# test-template build and CI on this side while the other branch stayed
# green.  That branch merged as PR #166, so the file resolves now.
down_revision = "a7e3c95d2f18"
branch_labels = None
depends_on = None


#: The function body this revision REPLACES, inlined so the downgrade restores
#: the previous revision's behaviour exactly.
#:
#: It is a literal rather than a second import of
#: ``GOVERNING_ORDER_SQL``, and that is the whole point: the constant now holds
#: the NEW order, so a downgrade built from it would leave the database on the
#: new behaviour while claiming to have reverted.  A frozen copy is the only
#: honest spelling of "what the previous revision installed", and it is the
#: same reason every downgrade in this chain restates rather than re-derives.
_PREVIOUS_OPENED_ON_SQL = """
CREATE OR REPLACE FUNCTION budget.account_books_opened_on(p_account_id INTEGER)
RETURNS DATE AS $$
    SELECT opened_on
      FROM budget.account_openings
     WHERE account_id = p_account_id
     ORDER BY created_at DESC, id DESC
     LIMIT 1;
$$ LANGUAGE sql STABLE
"""


def upgrade():
    """Swap the MOVEMENT arm's function bodies to the in-code definition.

    Every body this revision's arms cover, not only the one it changed, and
    that is deliberate: re-applying the set is what keeps the revision honest
    if a later edit moves a different one, where naming a single body would
    silently stop tracking the module it came from.
    ``CREATE OR REPLACE FUNCTION`` is idempotent, so re-stating an unchanged
    body costs nothing and asserts nothing false.

    *The count is not spelled out here on purpose.*  It read "all FOUR function
    bodies" until plan step balance:X-f3c-2b-2b grew the module to nine and the
    sentence went stale without anything noticing -- the decaying-premise shape
    this arc keeps finding.  The arm names what it covers; the module owns how
    many statements that is.

    The three constraint TRIGGERS are deliberately not re-applied -- see the
    module docstring for why that is what makes this revision's own
    ``Review:`` line true.
    """
    # ``arms=(MOVEMENT_ARM,)``: the MATCHED-LINE arm does not exist at this
    # point in history -- ``d1f6a83c9e47`` installs it, after censusing the
    # matched lines already in the way.  Naming the arm is what keeps this
    # revision installing what it declared rather than whatever the shared
    # builder has grown into; see ``opening_infrastructure.ALL_ARMS``.
    apply_opening_functions(op.execute, arms=(MOVEMENT_ARM,))


def downgrade():
    """Restore the governing-day lookup the previous revision installed.

    ONE body, because this revision changes one: the governing-day ordering.

    **It is NOT a full inverse of the upgrade, and since plan step
    balance:X-f3c-2b-2b it is further from one.**  ``upgrade`` calls
    ``apply_opening_functions(arms=(MOVEMENT_ARM,))``, which emits FIVE
    statements: the two BASE functions -- including ``budget.books_hold``,
    which does not exist below this revision at all -- and the movement arm's
    three.  This downgrade replaces one of them.  So downgrading to
    ``a7e3c95d2f18`` leaves ``budget.books_hold`` standing as an orphan, plus
    two predicate bodies that call it.

    **Nothing breaks**: the orphan survives precisely because nothing drops it,
    so every call still resolves, and ``d3b6f1c8a274``'s own downgrade
    (``remove_opening_infrastructure``) takes the whole boundary away one
    revision further down.  What is untrue is the tidier claim this docstring
    used to make -- that the predicates, the dispatcher and the triggers are
    "byte-identical across it and deliberately left alone".  They were, at the
    time.  Two of the three bodies now change here, because they ask
    ``budget.books_hold`` rather than spelling ruling R-HG's comparison.

    Left as it is rather than "fixed" by freezing more bodies: a frozen copy is
    the thing ``d1f6a83c9e47`` deleted, and re-introducing one here to tidy an
    intermediate stamp nobody deploys to would trade a real maintenance hazard
    for a cosmetic one.  Stated instead, which is what a reader needs.

    *A draft of this revision also carried an assertion bound into the
    openings predicate, and the suite refused it: the state it forbade is one
    existing rows already hold.  That rule lives at the door instead -- see
    :mod:`app.opening_infrastructure` for why, and finding N-400 for what
    making it structural would take.*

    No data is touched in either direction -- see the module docstring for the
    production measurement showing both orderings name the same governing row
    on all nine accounts.
    """
    op.execute(_PREVIOUS_OPENED_ON_SQL)
