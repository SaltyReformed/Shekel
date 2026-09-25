"""
Shekel Budget App -- A transfer's two LEGS, derived from its parent.

The PLAN half of ruling **R-BAL13** (plan step **X-bi-6a**), and since leaf
``X-bi-6-1`` (ruling **R-BAL87**) the GRID's view of a transfer as well: a
transfer's two legs are not rows in ``budget.transactions``.  A still-projected transfer is
ONE economic event, and what each account it touches will see is a
PROJECTION of the parent row -- the from-side an expense, the to-side an
income, each worth exactly what the parent resolves to.  This module states
that projection ONCE, as a value (:class:`TransferLeg`) and the loader
that produces it (:func:`planned_transfer_legs`), so every reader of an
account's plan derives the same pair from the same parent.

**What it replaces.**  Until this step every balance reader loaded the two
SHADOW rows the transfer service writes beside each parent -- an expense
``Transaction`` on the from-account and an income ``Transaction`` on the
to-account -- and priced each through amount rule 5 (*a shadow is worth its
parent*).  The shadows still exist and the transfer service still maintains
them, for the settle doors and the screens that render them; what changed is
that no reader FOLDING a projection reads one.  The settled half is each
shadow's covering MOVEMENT since plan step ``balance:X-bi-4a``, and plan
step ``X-bi-6`` deletes the rows, at which point the maintenance contract
that kept a shadow equal to its parent (Transfer Invariant 3) has nothing
left to keep in step.

**A leg is planned exactly while its own DATED movement does not exist**
(ruling **R-BAL79**, plan step ``balance:X-bi-4a``; ledger row
**BAL-500**).  The settled half reads a leg as the dated covering movement
under the transfer's shadow on that account, so the MOVEMENT -- not the
parent's status -- is what decides which relation a leg is in, per side:
a transfer whose parent is still Projected while one side's movement has
been dated (a state no door writes today and Transfer Invariant 3 forbids;
``X-bi-6``'s per-leg settle days make it the ordinary transitional state)
is emitted here for the OTHER side alone, so no leg is counted by both
halves.  Through ``X-bi-3e`` both legs were emitted off the parent's status
and that state read ``-$500.00`` on a `$250.00` transfer.

**A leg is DERIVED and carries its parent, deliberately.**  It stores no
figure, no period and no date of its own: :attr:`~TransferLeg.due_date`
and :attr:`~TransferLeg.pay_period_id` read the parent's columns, and
what the leg is WORTH is
:func:`app.services.cash_ledger.resolve_transfer_amount` over the parent --
the ONE producer ruling **R-BAL10** put a transfer's amount on.  A leg that
held a copy of any of those would be a shadow row in memory, the very shape
this step exists to stop reading.

**The same value is what the GRID draws in a transfer's cell** (leaf
``X-bi-6-1``, ruling **R-BAL87**): a leg's identity on a grid is the pair
``(transfer id, the account it is on)`` -- :attr:`~TransferLeg.cell_key` --
and its cell's doors are the transfer's own routes.  The grid's leg carries
one more thing the fold's never does: its RECORD, the covering movement the
status seam wrote when the transfer settled (:attr:`~TransferLeg.record`).
**R-BAL87's text says "its dated covering movement"; the record here is
that movement dated OR kept un-dated across a revert** (ruling **R-BAL61**),
one word wider than the ruling's, because the grid draws a reverted leg's
"marking paid records $X" caption off the kept movement exactly as it draws
a reverted row's (``retained_settle_amounts_by_id``'s rule); a settled leg's
record is dated, so the ruling's case is unchanged.  Through the interval
before ``X-bi-6``'s last leaf that movement hangs off the transfer's shadow
row on that account, so :func:`covering_movements_by_leg` reaches it through
ONE join -- the join :func:`planned_transfer_legs` already uses to decide
which relation a leg is in -- and the last leaf moves that join once when
the movement re-parents onto ``budget.transfers``.  The fold's loader still
emits only legs whose record is ``None``.

**One fold PREDICATE did change at 6-1, and it is pinned rather than
denied**: sharing the join gave :func:`planned_transfer_legs`' ``dated_leg``
test the term ``the shadow is live`` (``Transaction.is_deleted IS FALSE``)
that it did not carry before -- the term the settled half's
``balance_contributing_clause`` has always applied to the same movement.
On every door-written state the two predicates agree (no door soft-deletes
one shadow alone); on the double drift -- a dated movement under a shadow
deleted around the service, the parent still Projected -- the leg used to
vanish from both halves and is now counted once, by the plan (R-JA: the
parent decides).  ``tests/test_services/test_transfer_legs.py``'s drift
class pins it, red under the old predicate.

**A leg's LABEL is composed here** (:func:`leg_label`), from the endpoints'
CURRENT names: "Transfer to <to-account>" on the from-side, "Transfer from
<from-account>" on the to-side.  It is the one composition the shadow
constructor (``transfer_service._create.shadow_names``) and the grid's row
label read, so a renamed account re-labels every leg it touches where a
shadow's stored ``name`` went stale -- one of the two visible changes 6-1
makes.  The other: a leg's ``notes`` are its PARENT's (a shadow was written
with none and no writer mirrored them), so a transfer's notes show on its
grid cell's title where the shadow's showed nothing.

**Why a leaf module, below both readers.**  The cash ledger needs every leg an
account is on (both sides, for its cash fold); the loan loaders need the legs
INTO a loan (its projected payments); and ``cash_ledger`` imports
``loan_loaders`` for its loan term primitives, so the loader cannot live in
the cash ledger without closing that cycle -- ``cyclic-import`` traces a
call-time import too.  A leaf both tiers reach is the shape
:mod:`app.services.row_valuation` and :mod:`app.utils.amount_relationships`
already take, for the same reason.  It names models, the shared status
predicates, the reference cache and the date arithmetic, and no service.

**The SETTLED half reads its legs here too since leaf ``X-bi-6-4a``** (ruling
**R-BAL106**): :func:`transfer_movement_rows` / :func:`recorded_transfer_legs`
hand the cash fold, the ledger oracle and the savings metric each paid
transfer's covering movement with its transfer and side, and since that
leaf's second half the posting WRITER books every transfer movement under its
LEG (:func:`movement_parent`, :func:`transfer_family_movements`,
:func:`dated_leg_exists_clause`).  For THOSE readers and the writer this
module is the one place a movement is reached through a shadow row, and so
it is since leaf ``X-bi-6-4c-2`` for the reconcile panel
(:func:`offerable_transfer_legs`) and the recurrence engine's records
predicate (:func:`transfers_holding_records`).  **Other readers still reach
it themselves until their leaf moves them** and ``X-bi-6-4d`` must find each
-- among them statement match (6-4c-1) and DC-11's raw-SQL leg arm
(``scripts/integrity_check.py``).

**A database VIEW for this pair was refuted at the ruling**: a derive-mode loan
payment's leg cannot be priced without the amortization engine, so the pair
is Python.

**A PACKAGE since the X-bi-6-4 lineage brought the module to 970 of pylint's
1000 lines** (finding **N-152**'s answer to the same ceiling: a package, never
prose shaved off a measured claim), cut along the seam the module already had
and moved without a byte changed in any definition.  :mod:`._leg` is the value,
built from its parent (and the record a loader hands it), whose code is blind
to where a movement hangs -- :class:`TransferLeg`, its label
(:func:`leg_label`), its identity (:func:`cell_key`, :func:`key_order`) and its
construction (:func:`leg_of`).  :mod:`._records` holds the package's code that
knows a movement hangs off a shadow row: the one join from a transfer to a
leg's covering movement, its three expressions, every loader in this package
built on it (:func:`planned_transfer_legs` and the grid's
:func:`grid_transfer_legs` included, since each reads the join), and
:func:`movement_parent`, the join's Python twin over one loaded movement -- the
half of this package whose code ``X-bi-6-4d`` rewrites when the join moves off
the shadows.  ``_records`` imports ``_leg`` and never the reverse.  Every
public name is re-exported here, so no import statement changed.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclasses out; no Flask symbol, no writes, no clock.
"""

from app.services.transfer_legs._leg import (
    TRANSFER_FROM_PREFIX,
    TRANSFER_TO_PREFIX,
    PlanItem,
    TransferLeg,
    cell_key,
    expense_legs,
    key_order,
    leg_label,
    leg_of,
)
from app.services.transfer_legs._records import (
    covering_movements_by_leg,
    dated_leg_exists_clause,
    grid_transfer_leg,
    grid_transfer_legs,
    movement_parent,
    offerable_transfer_legs,
    planned_transfer_legs,
    recorded_transfer_legs,
    transfer_family_movements,
    transfer_movement_rows,
    transfers_holding_records,
)

__all__ = [
    "PlanItem",
    "TRANSFER_FROM_PREFIX",
    "TRANSFER_TO_PREFIX",
    "TransferLeg",
    "cell_key",
    "covering_movements_by_leg",
    "dated_leg_exists_clause",
    "expense_legs",
    "grid_transfer_leg",
    "grid_transfer_legs",
    "key_order",
    "leg_label",
    "leg_of",
    "movement_parent",
    "offerable_transfer_legs",
    "planned_transfer_legs",
    "recorded_transfer_legs",
    "transfer_family_movements",
    "transfer_movement_rows",
    "transfers_holding_records",
]
