"""
Shekel Budget App -- Account Posting Service (Build-Order Step 5)

Posts every NON-loan account's anchor assertions into the append-only
double-entry ledger as balanced OPENING / TRUE-UP corrections, mirroring the
shipped loan genesis pattern (:mod:`app.services.loan_posting_service`).
After Step 5, every linked ledger sums to an ABSOLUTE balance and the trial
balance closes app-wide::

    confirmed balance = latest anchor assertion
                        + settled facts recorded after that assertion moment

An anchor assertion is a FACT; modeled growth / appreciation / interest
between assertions is a derivation and is never posted (Option D's
fact-versus-derivation line).  A true-up whose delta is $0 means the app
tracked reality perfectly; a non-zero delta is the checking "deliberate
cheat" made visible as an explicit equity adjustment.

## Package layout

Mirrors :mod:`app.services.loan_posting_service` (split by concern):

* :mod:`._anchors` -- the opening + true-up reconcile: per-(source kind,
  civil date) targets, posted-leg read-back, one balanced delta per key
  that differs.
* :mod:`._sync` -- the entry points (per-scenario, all-scenarios, per-user,
  the deploy backfill, the several-accounts re-derive and the effect-time
  self-heal that gates it) and the CHECKED-PROJECTION assert every one of them
  ends on.

**This package had a THIRD module, and plan step X-d deleted it.**  ``_walk``
replayed the account's :class:`~app.models.account.AccountAnchorHistory` rows
against source facts read back from its LINKED LEDGER -- the posted copy of the
account's events -- so the corrections were computed from the ledger they were
then written into, and the app carried two representations of one event set: the
transaction rows the balance seam folds, and the postings this walk folded.
Ruling R-H had already ruled that only ONE walk closes that, and plan step 3 made
the two absorb loops textually identical precisely so this step would be a
DELETION.  The writer now consumes
:func:`app.services.cash_ledger.walk_cash_ledger`, and
``_sync._assert_checked_projection`` grades the result: ``sum(postings) ==
fold(ACTUAL events)``, per date, after every reconcile, so a stale posting is a
detectable, repairable cache inconsistency instead of a second opinion (E1a's
shape, for cash).

## Shared infrastructure and isolation

Books through :mod:`app.services.posting_service`'s shared balanced-write
path and the reconcile primitives in
:mod:`app.services._posting_reconcile` (``delta_legs`` /
``posted_correction_legs`` / ``emit_anchor_correction_entry`` -- shared with
the loan package, so the two correction families can never drift on the
delta math or the correction-entry shape).  Ledger rows are minted only via
:func:`app.services.ledger_account_service.get_or_create_anchor_equity_account`,
whose non-loan guard keeps the loan and account correction families on
disjoint charts.  Flask-isolated: plain data in, plain values out; flushes
but never commits (the caller owns the transaction boundary).

**Write status.**  WIRED as of C6, at seven lifecycle chokepoints: account
create (``account_service.create_account``), the anchor true-up
(``anchor_service.apply_anchor_true_up``), the direct anchor edit
(``routes.accounts.crud.update_account``), the pay-period reset
(``pay_period_admin.reset_pay_periods``), the effect-time self-heal at the
``posting_service`` sync tails (:func:`self_heal_anchor_corrections`), the
``create_baseline`` recovery path, and the account-type boundary changes
(the crud/type routes re-sync an allowed crossing; the validation guards
refuse one on a posted account).  C7 adds the deploy-wide historical
backfill (:func:`backfill_all_account_anchor_postings`), reusing the
identical go-forward sync so a backfilled correction is identical to a
go-forward one.
"""

from ._anchors import reconcile_account_anchor_corrections
from ._sync import (
    backfill_all_account_anchor_postings,
    resync_anchor_postings,
    resync_user_account_anchor_postings,
    self_heal_anchor_corrections,
    sync_account_anchor_postings,
    sync_account_anchor_postings_all_scenarios,
)

__all__ = [
    "backfill_all_account_anchor_postings",
    "reconcile_account_anchor_corrections",
    "resync_anchor_postings",
    "resync_user_account_anchor_postings",
    "self_heal_anchor_corrections",
    "sync_account_anchor_postings",
    "sync_account_anchor_postings_all_scenarios",
]
