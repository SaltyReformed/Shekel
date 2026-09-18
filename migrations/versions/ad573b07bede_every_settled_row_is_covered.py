"""every settled row is covered: the cutover to the covering movement

Revision ID: ad573b07bede
Revises: d2e9f4a17c63
Create Date: 2026-09-18

Plan step **balance:X-bi-3d** (leaf 3d of ``X-bi-3``, the one that MOVES
MONEY) of ``docs/audits/balance_architecture/README.md`` section 5, under
rulings **R-BAL40** (developer, 2026-09-15: the cutover is a MIGRATION by a
total rule, fail-closed), **R-BAL61** and **R-BAL62** (developer, 2026-09-18:
the figure's source is the writer's, and the downgrade leaves the rows)::

    budget.transaction_entries    +1 COVERING MOVEMENT per settled row on the
                                  ``derived`` / ``corrected`` basis whose
                                  figure is not zero and which holds none

**What a covering movement is.**  Since plan step X-bi-3a every settle on the
MANUAL branch -- a bill ticked Paid, a paycheck Received, each leg of a
settled transfer -- is mirrored by the status seam as ONE row of this table,
the payment row that records the settle's money the way a purchase records
an envelope's: the figure and WHO WROTE it, the day the money moved and how
that day is known, the statement that showed it, marked ``covers_settlement``
so the seam finds its own mirror again (``status_seam._covering``, rulings
**R-BAL39**, **R-BAL41**).  Every row settled BEFORE that seam deployed still
records its money on the row alone.  This revision writes the mirror those
rows would carry had the seam existed when they settled, so that after it
EVERY settled row is covered and ``opening + SUM(movements)`` is an identity
``X-bi-4`` can re-point the fold onto.

**By a TOTAL RULE and nothing else** (**R-BAL40**): every column of a payment
row is a function of the settled row's own stored facts, and no row asks for
a judgment.  Per settled row on a basis that stores its figure::

    amount                = settled_amount        (a $0.00 record writes NONE:
                                                   ``amount <> 0`` says a
                                                   movement of nothing is not
                                                   one, the seam's own arm)
    figure_source_id      = resolved  where settled_basis is ``derived``
                            typed     where settled_basis is ``corrected``
    description           = name      (the plan's name as it reads NOW; the
                                       name as it read at the settle is not a
                                       stored fact of the row, so the current
                                       one is the total rule's answer)
    purchased_on          = settled_on  (a payment has no other day, R-BAL39)
    settled_on            = settled_on
    settled_day_basis_id  = settled_day_basis_id
    reconciled_by_id      = reconciled_by_id
    account_id            = account_id  (``fk_transaction_entries_parent_account``
                                         admits no other value)
    user_id               = user_id     (the AUTHOR column; the seam records
                                         on the owner's behalf, as here)
    is_credit             = false
    covers_settlement     = true

**The figure's source is the WRITER's, stated and never inferred from the
day** (**R-BAL61**, amending R-BAL40's "else an observed day -> observed"
arm).  A ``derived`` record is the settle's own pricing (``resolved``).  A
``corrected`` record was STATED, and on production every one was stated by a
person: the only bank writer of a settled row's figure is the statement
matcher's re-price, which has never fired there -- of the 26 writes that ever
set a settled row's day to ``observed``, 26 wrote no figure.  The day's own
basis and the clearing link already record the bank's agreement; this column
answers the one question those do not, and a day-only confirmation says
nothing about who wrote the number beside it.  The seam still infers the
source from the day for a re-record; making the settle record carry the
writer's statement is leaf ``X-bi-3e``, so the PROOF below grades the money
facts of a pre-existing movement and not its label.

**Which rows** (the settled band is ``ref.statuses.is_settled``, the two
type-specific settle targets).  Every row in it whose ``settled_basis_id`` is
``derived`` or ``corrected``, soft-deleted rows INCLUDED: the seam leaves a
movement standing through a soft delete (it is worth nothing under a
non-contributing parent, as the row's own leg is), and a restore that does
not pass through the seam -- ``recurrence_engine._conflicts`` for a generated
row -- would otherwise revive an uncovered settled row.  A ``purchases``-basis
row is not covered: its purchases ARE the record.  A row already holding a
covering movement -- a database that ran the seam before this revision, which
production has not -- is left alone, so the write is idempotent.

**Fail-closed, before any write** (R-BAL40: no judgment).  Each refusal names
its count and the repair, in the shape ``9c1e4b7a2d3f``'s downgrade set:

* a settled row storing NO figure -- no record at all, or a figure-storing
  basis with none beside it -- which ``row_valuation.settled_figure`` refuses
  and no door can write (``Settlement.__post_init__``); asked of the whole
  band (less the ``purchases`` basis) and FIRST, because a recordless row is
  outside the population and would otherwise be skipped in silence, and as
  one leg of a transfer would slip past the pair check below (``COUNT
  (DISTINCT ...)`` reads a NULL beside a figure as agreement);
* a settled row of the population with NO settle day -- the movement has no
  day to carry (ruling R-EG's popover day box is the repair);
* a transfer whose pair is BROKEN: not exactly two shadows, a shadow whose
  status differs from its parent's, or two shadows disagreeing on the figure,
  the day or either basis -- Transfer Invariant 3 drift, which
  ``transfer_service`` writes around no door; each leg's movement is its own
  leg's record, so a pair that disagrees would be mirrored as two different
  events, which is the judgment this migration may not make.

**Proved after the write, and refused if false** (both directions of the
total rule, so the docstring's claim is graded rather than described): every
row in the population holds exactly one covering movement -- none for a
``$0.00`` record -- agreeing with the row on the figure, the day, the day's
basis, the link and the account; and no covering movement stands under any
row OUTSIDE the population (a row out of the settled band, or on the
``purchases`` basis), because the seam withdrew one on the way out of the
band on every tree this revision can meet, so a survivor was written around
it.  (Plan step ``balance:X-bi-3e-2`` later made a revert KEEP the movement,
un-dated -- ruling **R-BAL61** -- but that code runs only on a database
already past this revision: the deploy migrates before the app boots, so the
premise holds for every database this upgrade runs against.  Stated
2026-09-18, after this revision had deployed at 12:35 EDT.)

**Balance-neutral BY CONSTRUCTION, and measured.**  Ruling **R-FM**'s
identity: ``cash_ledger.settled_cash_leg`` books a settled row's figure MINUS
its posted purchases, and each posted movement is its own dated fact in the
parent's direction -- so a covered row's own leg reads exactly zero and its
movement carries the whole figure on the same day.  Measured on a production
restore (the 2026-09-18 02:01 EDT dump, stamp ``9c1e4b7a2d3f``, rows to
2026-09-16), migrated to ``d2e9f4a17c63`` and then through this revision with
the deploy's own ``resync_all_cash_postings`` after each:

* population **162 rows / $89,741.80** -- 38 transfer shadows in 19 pairs, 28
  CC paybacks, 96 plain (68 expense, 28 income); 149 ``derived`` / 13
  ``corrected``; on Checking 143, Money Market 7, Mortgage 6, Van Loan 5,
  Fidelity Savings 1; 0 dateless, 0 figureless, 0 at ``$0.00``, 0 soft-deleted,
  0 broken pairs; exactly 162 ``INSERT`` rows in ``system.audit_log``;
* ``tests/manual/verify_balance_baseline.py`` (9 accounts, 448 grid cells,
  6,272 daily points) and ``verify_statement_baseline.py`` (2 users, 143
  statements): **byte-identical** before and after;
* the posted ledger's net per ``(ledger account, entry_date)``:
  **byte-identical** before and after -- the resync re-books each of the 124
  non-shadow rows as a purchase-sourced entry and reverses its
  transaction-sourced one on the same day (``124 transaction(s) and 0
  transfer(s)`` re-posted, then ``0, 0`` on a second pass); a shadow's
  movement posts nowhere until ``X-bi-6`` (ruling **R-BAL45**), so the
  transfer arm re-posts nothing;
* the downgrade then the upgrade again: the 162 rows stand, the fold is
  byte-identical, the second upgrade writes 0 rows, the resync reads ``0, 0``.

**What that equality GRADES, stated so it is not read as more.**  Through the
interval the row's ``settled_amount`` still stands beside its mirror, and
R-FM's identity sums the family to the ROW's figure whatever the mirror says
(``figure - M + M``): a covering movement written at ``$364.61`` under a
``$364.60`` record moved NEITHER harness by a cent (measured), while the same
movement moved one DAY moved both (two grid cells; the ledger's
``2026-09-16`` net from ``-502.53`` to ``-137.93`` and a new ``-364.60`` on
the 17th).  So the before/after equality proves the day, the account and the
count of every movement written, and the FIGURE is proved by the refusal
below (``e.amount = t.settled_amount`` on every population row, which fires
under that same cent in the suite) -- until ``X-bi-4`` reads movements alone
and the figure becomes what the fold sees.

**No second ledger writer.**  The posted ledger is reconcile-to-target and
``posting_service.resync_all_cash_postings`` re-derives it on every deploy
(``scripts/init_database.py``, the hook that runs right after this chain),
walking every settled row and every row holding a posted purchase; this
revision writes movement rows and touches no journal entry.

**The downgrade leaves the rows in place** (**R-BAL62**, amending R-BAL40's
"delete exactly the entries whose parent does not track purchases").  By the
time a downgrade can run, the deploy has booked each movement as a
purchase-sourced journal entry linked ``ON DELETE SET NULL``; deleting the
rows would orphan 124 entries no migration may reverse, and the older image's
next resync would then re-post each row's own leg -- ``$48,991.00``
double-booked on Checking over the 124 non-shadow rows (Electricity
``$364.60`` reading ``$729.20`` on its day).  On every money fact, every
state this revision leaves is one the revision below produces for its own
settles (the one difference is the LABEL: the five ``corrected`` rows on a
bank-observed day read ``typed`` here where that seam writes ``observed``,
R-BAL61's amendment and not money), the older tree reads a covered row by
R-FM's identity, and the row's ``settled_amount`` stands, so leaving them is
lossless; stepping further past ``b5c7e9a1d2f4`` turns them
into ordinary purchases with their postings intact, which that revision's
downgrade already states.

**Rollback across this release is a dump restore, not a downgrade**: the
deploy script takes a pre-deploy dump and re-pinning the old image undoes no
migration (``deploy/shekel-deploy.sh``).  A rollback after the resync has
committed restores the dump.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ad573b07bede"
down_revision = "d2e9f4a17c63"
branch_labels = None
depends_on = None


def _basis(name: str) -> str:
    """Return a scalar subquery for one ``ref.settlement_bases`` id by NAME.

    A migration resolves a ref row by its name because the id is assigned by
    the sequence and differs between databases; application code never does
    this (``ref_cache.settlement_basis_id`` is that door).
    """
    return f"(SELECT id FROM ref.settlement_bases WHERE name = '{name}')"


def _source(name: str) -> str:
    """Return a scalar subquery for one ``ref.movement_figure_sources`` id."""
    return f"(SELECT id FROM ref.movement_figure_sources WHERE name = '{name}')"


#: The rows this revision covers: the settled band (``ref.statuses.is_settled``
#: is the semantic column; no status NAME is compared) on a basis that stores
#: its figure.  Soft-deleted rows included, per the module docstring.
_POPULATION_FROM = (
    "FROM budget.transactions t "
    "JOIN ref.statuses s ON s.id = t.status_id "
    "WHERE s.is_settled "
    f"AND t.settled_basis_id IN ({_basis('derived')}, {_basis('corrected')})"
)

# Band-wide rather than population-wide, and asked FIRST: a settled row with
# NO record at all (``settled_basis_id`` NULL, which the pairing CHECK makes
# ``settled_amount`` NULL too) is outside the population and would otherwise
# be skipped in silence -- and, as one leg of a transfer, would leave the pair
# check reading one NULL beside one figure as agreement.  A ``purchases``-basis
# row stores no figure by design and is excluded by name.
_FIGURELESS_SQL = (
    "SELECT COUNT(*) FROM budget.transactions t "
    "JOIN ref.statuses s ON s.id = t.status_id "
    "WHERE s.is_settled AND t.settled_amount IS NULL "
    f"AND t.settled_basis_id IS DISTINCT FROM {_basis('purchases')}"
)

# Population-wide: a dateless ``purchases``-basis row is the fold's to refuse
# (``balance_predicates.settled_day``), not this migration's, which writes it
# nothing.
_DATELESS_SQL = f"SELECT COUNT(*) {_POPULATION_FROM} AND t.settled_on IS NULL"

# A transfer whose parent or any shadow is in the settled band must hold
# exactly two shadows, every shadow at the parent's status, and both shadows
# agreeing on the record (figure, day, both bases).  ``COUNT(DISTINCT ...)``
# reads NULL as no value, so two NULL days count as one, which is agreement.
_BROKEN_PAIRS_SQL = (
    "SELECT COUNT(*) FROM ("
    "  SELECT x.id"
    "  FROM budget.transfers x"
    "  JOIN ref.statuses sx ON sx.id = x.status_id"
    "  LEFT JOIN budget.transactions t ON t.transfer_id = x.id"
    "  LEFT JOIN ref.statuses st ON st.id = t.status_id"
    "  GROUP BY x.id, sx.is_settled"
    "  HAVING (sx.is_settled OR bool_or(COALESCE(st.is_settled, false)))"
    "     AND ("
    "       COUNT(t.id) <> 2"
    "       OR bool_or(t.status_id <> x.status_id)"
    "       OR COUNT(DISTINCT t.settled_amount) > 1"
    "       OR COUNT(DISTINCT t.settled_on) > 1"
    "       OR COUNT(DISTINCT t.settled_basis_id) > 1"
    "       OR COUNT(DISTINCT t.settled_day_basis_id) > 1"
    "     )"
    ") broken"
)

_COVER_SQL = (
    "INSERT INTO budget.transaction_entries ("
    "  transaction_id, account_id, user_id, amount, description, purchased_on,"
    "  settled_on, settled_day_basis_id, reconciled_by_id, is_credit,"
    "  covers_settlement, figure_source_id"
    ") "
    "SELECT t.id, t.account_id, t.user_id, t.settled_amount, t.name, t.settled_on,"
    "  t.settled_on, t.settled_day_basis_id, t.reconciled_by_id, false,"
    "  true,"
    f"  CASE WHEN t.settled_basis_id = {_basis('derived')}"
    f"       THEN {_source('resolved')} ELSE {_source('typed')} END "
    f"{_POPULATION_FROM} "
    "AND t.settled_amount <> 0 "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM budget.transaction_entries e"
    "  WHERE e.transaction_id = t.id AND e.covers_settlement"
    ")"
)

# Every population row holds exactly one movement agreeing with it on the
# money facts -- and a $0.00 record holds NONE, so for one the count is of
# every covering movement rather than of the agreeing ones (an agreeing
# movement of zero cannot exist under ``amount <> 0``, which would make the
# zero arm vacuous).  ``IS DISTINCT FROM`` so a NULL link on both sides is
# agreement.
_UNCOVERED_SQL = (
    "SELECT COUNT(*) FROM ("
    f"  SELECT t.id {_POPULATION_FROM}"
    "  AND ("
    "    SELECT COUNT(*) FROM budget.transaction_entries e"
    "    WHERE e.transaction_id = t.id AND e.covers_settlement"
    "      AND (t.settled_amount = 0 OR ("
    "        e.amount = t.settled_amount"
    "        AND e.settled_on = t.settled_on"
    "        AND e.settled_day_basis_id = t.settled_day_basis_id"
    "        AND e.reconciled_by_id IS NOT DISTINCT FROM t.reconciled_by_id"
    "        AND e.account_id = t.account_id"
    "      ))"
    "  ) <> CASE WHEN t.settled_amount <> 0 THEN 1 ELSE 0 END"
    ") uncovered"
)

# A covering movement under a row the total rule does not cover.
_STRAY_SQL = (
    "SELECT COUNT(*) FROM budget.transaction_entries e "
    "JOIN budget.transactions t ON t.id = e.transaction_id "
    "JOIN ref.statuses s ON s.id = t.status_id "
    "WHERE e.covers_settlement AND NOT ("
    "  s.is_settled "
    f"  AND t.settled_basis_id IN ({_basis('derived')}, {_basis('corrected')})"
    ")"
)


def _refuse_if_any(bind, sql: str, sentence: str) -> None:
    """Refuse with the count and *sentence* when *sql* counts anything.

    Args:
        bind: A SQLAlchemy connection.
        sql: A ``SELECT COUNT(*)`` statement.
        sentence: What the count means and how it is repaired; the count is
            prefixed and the diagnostic query appended.
    """
    count = bind.execute(sa.text(sql)).scalar()
    if count:
        raise RuntimeError(f"{count} {sentence}  Diagnose with: {sql}")


def refuse_unanswerable_rows(bind) -> None:
    """Refuse the cutover while any settled row cannot be mirrored without a judgment.

    **Module-level so a test can DRIVE each refusal** (the chain's own
    pattern, ``b5c7e9a1d2f4.classify_figure_sources``).  Three predicates,
    each named in the module docstring; the 2026-09-18 production restore
    holds zero of each.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming the count, the repair and the diagnostic query.
    """
    _refuse_if_any(
        bind, _FIGURELESS_SQL,
        "settled budget.transactions row(s) store no figure -- no record at "
        "all, or a basis that stores its figure with none beside it -- which "
        "no door can write (status_seam.Settlement refuses it) and "
        "row_valuation.settled_figure refuses to value.  State each row's "
        "figure, and its day where it has none, through the full-edit "
        "popover's Actual and day boxes; then re-run.",
    )
    _refuse_if_any(
        bind, _DATELESS_SQL,
        "settled budget.transactions row(s) record a figure and no settle "
        "day, so the covering movement has no day to carry.  Date each "
        "through the full-edit popover's day box (ruling R-EG); then re-run.",
    )
    _refuse_if_any(
        bind, _BROKEN_PAIRS_SQL,
        "budget.transfers row(s) hold a broken shadow pair -- not exactly two "
        "shadows, a shadow whose status differs from the parent's, or two "
        "shadows disagreeing on the figure, the day or a basis (Transfer "
        "Invariant 3).  Each leg's movement is its own leg's record, so a "
        "disagreeing pair cannot be mirrored without deciding which leg is "
        "right.  Revert and re-settle the transfer through its own form so "
        "one record reaches both legs; then re-run.",
    )


def cover_settled_rows(bind) -> int:
    """Write one covering movement per uncovered settled row; return the count.

    The ``INSERT ... SELECT`` of the module docstring, idempotent over rows
    already holding a mirror.  Module-level so a test can drive it against
    rows staged in the pre-cutover shape.

    Args:
        bind: A SQLAlchemy connection.

    Returns:
        How many movements were written.
    """
    return bind.execute(sa.text(_COVER_SQL)).rowcount


def refuse_unless_total(bind) -> None:
    """Refuse unless every population row is covered and nothing else is.

    The proof of the total rule in both directions (module docstring), run
    after the write so a false claim aborts the transaction rather than
    shipping.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming the count and the diagnostic query.
    """
    _refuse_if_any(
        bind, _UNCOVERED_SQL,
        "settled budget.transactions row(s) do not hold exactly one covering "
        "movement agreeing with the row's figure, day, day basis, link and "
        "account after the cutover (none for a $0.00 record); the total rule "
        "did not hold and nothing was committed.",
    )
    _refuse_if_any(
        bind, _STRAY_SQL,
        "covering movement(s) stand under a row the total rule does not cover "
        "-- one out of the settled band or on the purchases basis.  The seam "
        "withdrew a movement on the way out of the band on every tree this "
        "revision can meet, so each was written around it; nothing was "
        "committed.",
    )


def upgrade():
    """Refuse the unanswerable, cover every settled row, prove it total."""
    bind = op.get_bind()
    refuse_unanswerable_rows(bind)
    written = cover_settled_rows(bind)
    refuse_unless_total(bind)
    print(f"X-bi-3d: wrote {written} covering movement(s) for settled rows.")


def downgrade():
    """Leave the covering movements in place (ruling R-BAL62).

    The rows are legal under the revision below, which writes identical ones
    for its own settles; deleting them would orphan their posted journal
    entries (``ON DELETE SET NULL``) with no writer allowed to reverse them.
    The module docstring carries the measured cost of the alternative.
    """
