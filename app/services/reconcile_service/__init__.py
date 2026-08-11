"""
Shekel Budget App -- The outstanding set: what a bank statement can still settle

The reconcile step's readers and writers, and the definitions of "outstanding"
they share (plan steps X-f2-c1..c3, rulings **R-EW** / **R-FA** / **R-FB**).  A
balance assertion says what the account really held on a civil day; this
package answers the question that follows it -- *which of the things you have
recorded had the bank not yet taken by then* -- and records the answer the user
gives.

**Why it is not in :mod:`app.services.entry_service`, where it was born.**  Two
reasons, and only the second is structural.

* The subject moved.  As shipped at plan step S1-c the set was purchases and
  nothing else, so it sat naturally beside the CRUD for a purchase.  Ruling
  **R-EW** widens it to everything a statement can settle -- purchases nested
  under their own envelope, the envelope's own close, bills, transfer shadows
  -- and three of those four are not entries at all.  A module named for one
  row type would then own the rule about four.
* ``entry_service`` stood at **991 of pylint's 1000-line ceiling** when this
  step opened, so the transaction half of the scope could not have been added
  there in any case.

**Why it is a PACKAGE, measured rather than felt.**  X-f2-c1 shipped it as one
module and said so deliberately: ``cash_ledger`` / ``balance_at`` /
``loan_ledger`` are packages because each exports twenty or more symbols over
several independent verbs, and a package for two functions is the speculative
structure rule 13 forbids.  X-f2-c2's own specification then made the split an
OWED first act rather than a trigger left in a docstring, because both leaves
that widen this code MOVE MONEY and a structural refactor folded into one of
them is what ruling **R-EY** refused for X-ad and how findings **N-152** /
**N-156** / **N-201** were each created.

That measurement was taken and it BINDS.  The single module stood at **482
lines**; two independent projections of the two remaining arms put it at
**~1,028** (the shipped arm costs 482 less ~209 of shared shape, so each
further arm costs ~273) and at **972-1,137** (a per-responsibility projection
whose optimistic end assumes every new docstring is SHORTER than its shipped
twin, on arms that have strictly more to explain).  Both cross a 1,000-line
ceiling.

**And the ceiling only revealed a cut the subject already had.**  There is ONE
question here -- what can this statement still settle, and record it -- asked
over THREE row kinds whose settle verbs are genuinely different:

* a PURCHASE settles by stamping one column, and moves no status
  (:mod:`._purchases`);
* a TRANSACTION settles through the status seam and a posting reconcile, at
  the verb the grid's Mark Paid shares (ruling **R-FA**), and a BILL's tick may
  correct its amount while an envelope's close may not (ruling **R-FB**) --
  plan step X-f2-c2;
* a TRANSFER SHADOW settles through ``transfer_service.update_transfer`` so
  both legs and the parent move together, carrying the loan-payment freeze --
  plan step X-f2-c3.

So the package is one module per arm, plus the value types they all publish
(:mod:`._offers`) and the assembly that turns their offers into the panel's
blocks (:mod:`._assemble`).  Each arm owns its own SCOPE, and that per-arm
reading is load-bearing: the scope shared between an arm's reader and its
writer is what stops a forged id settling something the panel never offered,
and one scope over three arms could not state it.

**The privacy is structural, not conventional.**  No ``app/`` or ``scripts/``
module can import ``_purchases`` or ``_offers`` in any spelling -- the
``shekel-private-module-import`` checker (W9910) is name-independent and
carries no allowlist -- so this docstring's list of public names IS the
boundary rather than a description of one.  **The scope of that sentence is
exact and a looser draft of it was wrong**: `.pre-commit-config.yaml` runs
`tests/` with ``--disable=all --enable=shekel-decimal-from-float``, so W9910
never fires there and a test COULD reach a private module.  None does; three
name one in a docstring only.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, frozen dataclasses out.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
  - The writers mutate and do NOT commit -- the caller owns the session
    boundary.
"""

from app.services.reconcile_service._assemble import (
    outstanding_set,
    record_reconciliation,
)
from app.services.reconcile_service._offers import (
    OfferKind,
    OutstandingGroup,
    OutstandingPurchase,
    OutstandingSet,
    OutstandingTransaction,
    ReconcileSubmission,
)
from app.services.reconcile_service._purchases import record_settled_days
from app.services.reconcile_service._transactions import (
    record_settled_transactions,
)

__all__ = [
    "OfferKind",
    "OutstandingGroup",
    "OutstandingPurchase",
    "OutstandingSet",
    "OutstandingTransaction",
    "ReconcileSubmission",
    "outstanding_set",
    "record_reconciliation",
    "record_settled_days",
    "record_settled_transactions",
]
