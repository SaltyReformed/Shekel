"""the bank's own transaction day

Plan step **bank_import:X-f6a-3a** of
``docs/plans/implementation_plan_bank_import.md`` -- ruling **R-FW**: *a match
corrects a purchase's DAY as well as its posting day, and the day it writes is
the one the bank STATED rather than the one it cleared on*.

``bank_statement_lines.transaction_on`` becomes NULLABLE, and the NULL means
"this source states no separate transaction day" rather than "unknown".

**It was a COPY on every row, which is why this is a correctness change and not
a nicety.**  The SECU CSV adapter wrote ``transaction_on = posted_on``
unconditionally, so nothing downstream could tell a day the bank OBSERVED the
swipe on from a restatement of the day it cleared -- a derived value stored
beside its own source with nothing reconciling the two.  X-f6a-3a writes that
day onto a matched purchase's ``purchased_on``, and doing so from a copy would
record every card purchase as having been made on the day it cleared.

Measured on the developer's own 2026-08-16 export (361 lines):

* the CSV states a transaction day on **182** of them, all POINT OF SALE lines,
  every one derivable, gaps of 0-4 days from the posting day, and **2 genuine
  year rollovers** (posted 2026-01-02 stating ``DATE 12-31``, so 2025-12-31);
* the OFX states none at all: its ``DTUSER`` equals ``DTPOSTED`` on **359** of
  361 and is one day LATER on the other two.

So a NOT NULL column is a copy on at least half of every statement this app can
read, and on all of one of them.

**Review: Josh, 2026-08-18 -- APPROVED**: the bank owns both of a purchase's
days, chosen over correcting only the posting day, after the measurement that
27 of 44 currently-matched purchases would move (18 of them BACKWARD onto a
clearing day) if the app took the bank's day unconditionally, against 3 of 44
if it takes it only where the bank CONTRADICTS what the app holds.

**This migration moves no money.**  It widens a column and erases a copy in
one, and neither is a balance input: nothing dates a posting, a ledger leg or a
projection off ``transaction_on``.  The table is empty in production
(X-f6a-1's tables have never been deployed).

**A row already carrying ``transaction_on = posted_on`` IS backfilled to NULL,
and a first draft of this migration wrongly left it alone.**  Such a row was
written by an adapter that could not distinguish the two days, so its value is
a copy -- and after this step ``transaction_on = posted_on`` no longer reads as
"this source distinguishes nothing", it reads as "the bank OBSERVED the swipe
on the day it cleared", which ``corrected_purchase_day`` then writes onto a
matched purchase's ``purchased_on``.  Leaving the copy in place would launder
it into an observation nobody made, which is the precise thing the NULL was
introduced to prevent.

**The draft justified leaving it by a claim that is FALSE**: that "a re-import
records the line afresh".  It does not -- a recognised line goes through
``statement_import._record._absorb_gained_facts``, which fills only the columns
a later export GAINS, and ``transaction_on`` was not among them.  Measured by
adversarial financial review 2026-08-18: after a re-import the copy was still
there.  Both halves are fixed -- this backfill for rows that already exist, and
that function for rows re-imported later.

Nothing is derived: the backfill only ERASES a copy, and no parsed day is
written into a historical row.  Production carries none of these rows at all
(X-f6a-1's tables have never been deployed), so the backfill's whole audience
is the developer's own clones -- which is where every measurement quoted here
was taken, and therefore exactly where a laundered copy would have corrupted
one.

Revision ID: a4c6f1d92b73
Revises: c1e7d4b3a850
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4c6f1d92b73'
down_revision = 'c1e7d4b3a850'
branch_labels = None
depends_on = None


def upgrade():
    """Widen ``transaction_on`` so a source stating no such day says so."""
    op.alter_column(
        'bank_statement_lines', 'transaction_on',
        existing_type=sa.Date(), nullable=True, schema='budget',
    )
    # Erase the COPY a pre-X-f6a-3a adapter wrote.  See the module docstring:
    # after this step an equal value is a claim that the bank observed the
    # swipe on the clearing day, and a match writes that day onto a purchase.
    op.execute(
        "UPDATE budget.bank_statement_lines "
        "SET transaction_on = NULL WHERE transaction_on = posted_on"
    )


def downgrade():
    """Narrow it back, restating the copy the NULL replaced.

    **The NULL has to become something, and ``posted_on`` is the only honest
    candidate**: it is exactly what the pre-X-f6a-3a adapter wrote for every
    line, so a downgraded database holds what it would have held had this step
    never shipped.  The rewrite is the downgrade's own act and is stated here
    rather than left to a NOT NULL violation on a real statement.
    """
    op.execute(
        "UPDATE budget.bank_statement_lines "
        "SET transaction_on = posted_on WHERE transaction_on IS NULL"
    )
    op.alter_column(
        'bank_statement_lines', 'transaction_on',
        existing_type=sa.Date(), nullable=False, schema='budget',
    )
