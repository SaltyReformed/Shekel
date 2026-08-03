"""The loan-payment reconciliation oracle (Build-Order Step 4, Commit 7).

The correctness gate for the loan half of the double-entry posting ledger.  Steps
2 / 3 posted settled transfer and cash movements; Step 4 layers a balanced
CORRECTION on each confirmed loan payment's Step-2 cash entry that backs the
interest / escrow / (payoff) refund off the loan, so the loan-linked ledger nets
to the REAL principal paid.  Reads still flow through the resolver / ``balance_at``
seam (Step 4 changes no read path), so -- exactly as in Steps 2 / 3 -- the ledger
is validated against the SOURCE (the shadow cash rows and the anchor), never
against a displayed balance.  The invariants below are plan Section 8.

  1. **Parallel run vs the resolver (the headline, plan 8.2).**  The ledger's view
     of the current balance is the genesis ``-(sum of the loan's linked-ledger
     postings)`` -- the opening (-original_principal), every true-up, and every
     payment's principal (Step-2 cash plus the Step-4 correction).  The resolver
     derives its balance INDEPENDENTLY -- it
     never reads the ledger; it replays the SCHEDULED payment
     (``principal = period_pi - interest``) forward from the same latest anchor
     and discards the cash entirely (``rate_period_engine.replay_schedule``).  So
     on an ON-SCHEDULE payment (cash == the resolver's ``monthly_payment``, no
     escrow) the two must AGREE to the penny; on an OFF-SCHEDULE payment they
     must DIVERGE by exactly the extra / short principal, and the ledger equals
     the hand-computed REAL balance -- proving the ledger is the more-correct
     record the read switch will move onto (plan Section 10), where the resolver
     needs an anchor true-up.
  2. **Supersedes the cash per-account invariant for loans (plan 8.7).**  The
     Step-2 / Step-3 oracle asserts ``account_posting_total(A) ==
     settled_transfer_effect(A) + settled_transaction_effect(A)``.  That BREAKS
     for a loan once corrections exist: the linked ledger nets to -(current
     balance), not to the cash.  The loan-aware invariant is
     ``account_posting_total(loan) == settled_transfer_effect(loan) -
     per_loan_correction_net`` where ``per_loan_correction_net`` is the sum of the
     per-loan legs -- interest / escrow / excess PLUS the opening-equity legs (the
     negatives of the opening + true-up).  One test drives this where the cash
     invariant provably fails.
  3. **Completeness over the full post-anchor set (plan 8.3).**  Every eligible
     confirmed payment whose split has a non-zero non-principal part carries a
     correction -- no Step-2 cash entry on a loan ledger is left uncorrected; an
     all-principal payment legitimately carries none.  Future-dated settled
     payments (none in these fixtures) are asserted absent, not silently passed
     (they are a read-switch concern).
  4. **Per-entry balance and trial balance (plan 8.4).**  Every journal entry's
     legs ``SUM(amount) = 0`` with ``COUNT >= 2`` (also DB-enforced by
     ``ck_account_postings_balanced``), and the whole ledger -- corrections
     included -- sums to zero.
  5. **Scenario and owner isolation (plan 8.8).**  A correction carries no owner
     of its own; its owner is its journal entry's, and one scenario's / owner's
     loan reconciliation never picks up another's.
  6. **Backfill == go-forward (plan 8.8).**  The historical backfill and the
     go-forward wiring post identical corrections, so a ledger rebuilt by the
     backfill reconciles identically to the go-forward one.
  7. **The DATED posting window parallel run (plan 4-commit-6 / 8.2).**
     Invariants 1-6 pin the posted LEDGER against the resolver via the test's OWN
     unbounded ``-(sum of linked postings)`` query (``_ledger_balance``); this
     pins the ``entry_date``-BOUNDED read of the same postings -- at a point in
     time (``_posted_balance``) and at every period boundary
     (``_posted_period_map``) -- as a THIRD derivation, proven == the resolver
     on-schedule and divergent by exactly the extra / short principal
     off-schedule, including a pre-true-up payment, a mid-life true-up, a
     calendar-year boundary, two scenarios, and the unconfigured -> ``None`` route.
     Both windows are the test suite's own (``tests/_test_helpers.py``): plan step
     E1e deleted the production readers, whose only remaining job was to be graded
     here.
     ``TestPostedLoanBalanceSums`` pins the same posted sums against
     hand-computed literals; this pins them against the independent resolver, so a
     ledger bug a literal happened to share is still caught (the ``+$10`` injection
     below fails these too).

Three adversarial cases prove the oracle is not vacuous: tampering a settled
payment's ``actual_amount`` makes the loan-aware invariant FAIL and the real
sweep helper raise (a real ledger drift would be caught), injecting one extra leg
makes the trial balance go non-zero (the ``= 0`` assertion is a real check, not
one the per-entry trigger makes unconditionally true), and injecting $10 of
phantom interest into the walk fails the parallel-run VALUE checks while the
structural sweep -- an accounting identity -- correctly survives (the executable
form of the "+$10 injection failed 9 of 11 tests" evidence).  A companion guard
(``TestResolverIsLedgerFree``) proves the resolver reference reads none of the
posted ledger, so the parallel run cannot silently become a tautology.

**Non-tautological by construction**, three independent ways -- the same discipline
as the Step-2 / Step-3 oracles.  The SPLIT VALUES are pinned by the first and third
(hand-computed literals and the resolver); the second pins the READERS:

  * **hand-computed literals** -- a $100,000 balance at 6% accrues exactly $500.00
    the first month (``100000 * 0.06 / 12``); the trueup anchor ($100,000) is
    distinct from origination ($250,000), so a correct interest figure also
    proves the walk seeds from the trueup anchor.  These, and the resolver below,
    are what pin the posted interest / principal;
  * **the resolver as an independent oracle** -- the parallel run pits the posted
    ledger against a producer that shares none of its code path and never reads
    the ledger, so an on-schedule agreement / off-schedule divergence pins the
    split VALUE that the internal reconciliation identities (structural, see
    ``_assert_loan_reconciles``) cannot; and
  * **independent cross-table queries** -- the ledger side
    (``_independent_loan_linked_net`` / ``_per_loan_correction_net``) reads
    ``account_postings`` through a different join shape than the
    ``posting_service`` readers, and the source side
    (``_independent_settled_income_cash``) reads ``transactions``.  This pins the
    READERS (a scenario-scope or ledger-resolution bug in ``account_posting_total``
    is caught here), NOT the split value -- which the sweep's identities hold
    regardless of.

**Scope (deliberately non-duplicative).**  The per-payment split VALUES
(on-schedule / extra / short / payoff-refund / ARM / escrow-effective-dating) are
hand-computed at the unit level in
``tests/test_services/test_loan_posting_service.py``; the lifecycle wiring
(settle / revert / delete / restore / true-up / rate / N1 back-post) and the
full-cash-reversal CRITICAL regression are driven through their real chokepoints
in ``tests/test_integration/test_loan_posting_wiring.py``; the backfill's
idempotency / coverage / deploy-hook contract in
``tests/test_integration/test_loan_posting_backfill.py``.  This oracle does NOT
re-assert those; it adds the reconciliation-level checks those suites do not make
-- the parallel run against the resolver, the production-wide superseding
invariant + completeness + trial-balance sweep, scenario / owner isolation of the
whole sweep, and the two non-vacuity proofs -- exactly as the Step-3 cash oracle
sits above the cash lifecycle / backfill suites.

Loans and payments are built through the SAME production primitives the other
suites use: ``create_loan_with_trueup`` (the canonical account factory + a
controlled latest anchor) and ``create_settled_transfer`` (the sole transfer
writer, which auto-posts the Step-2 cash entry AND -- via the Commit-5 wiring --
the Step-4 correction).  So every reconciled row was produced exactly as marking a
loan payment Paid produces it.  "today" is frozen to 2026-05-15 (after every
payment period used) so the wiring's and the resolver's ``date.today()`` as-of is
deterministic and every settled payment is historical.  All money is ``Decimal``
from strings, with the arithmetic shown per the testing standard.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import pkgutil
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
    TxnTypeEnum,
)
from app.extensions import db as _db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.loan_features import RateHistory
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import anchor_service, balance_at, loan_ledger, loan_loaders, loan_payment_service, loan_posting_service, loan_resolver, pay_period_service, posting_service, transfer_service
from app.services.loan_resolver._periods import _replay_from_anchor
from app.utils.money import round_money
from app.services.balance_at import _kernel as net_worth_kernel
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.rate_period_engine import monthly_due_date
from app.utils.balance_predicates import settled_status_ids
from app.utils.money import accrue_monthly_interest
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    find_loan_ledger_account,
    freeze_today,
    insert_tracking_start_event,
    ledger_net,
    load_migration_module,
    loan_correction_entries,
    loan_income_shadow,
    posted_loan_balance_at,
    posted_loan_balance_map,
    seam_confirmed_view,
    settle_instant_on,
    SPLIT_LOAN,
)

# The shared synthetic split-loan fixture ($250,000 @ 6%, trued up to $100,000 --
# distinct so a correct interest figure proves the walk's anchor reset); see
# SPLIT_LOAN in tests/_test_helpers.py.  _P1/_P2/_P3 are the seed_periods indices
# (payment_day=1) due 02-01 / 03-01 / 04-01 -- distinct months after the anchor,
# so the resolver's biweekly-collision redistribution never shifts a date and the
# parallel run is exact.
(_ORIGINATION_PRINCIPAL, _ORIGINATION_DATE, _RATE, _ANCHOR_BALANCE,
 _ANCHOR_DATE, _P1, _P2, _P3) = SPLIT_LOAN

# The frozen as-of: after every payment period used, so each settled payment is
# historical (eligible) and the resolver / wiring see the same today.
_AS_OF = date(2026, 5, 15)

# The two boundary migrations, loaded so their raw-SQL teardowns can reproduce
# the pre-wiring historical state directly (the same pattern the backfill suite
# uses).  The genesis teardown (opening / true-up + equity accounts) runs before
# the Step-4 payment teardown -- the real downgrade-chain order.
_BACKFILL_MIGRATION = load_migration_module(
    "e2a9f1c7b4d6_backfill_loan_payment_split_postings.py"
)
_GENESIS_MIGRATION = load_migration_module(
    "f3d6b1a8c2e4_loan_genesis_postings_data_boundary.py"
)


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Freeze today to 2026-05-15 so the wiring's and resolver's as-of is fixed.

    ``create_settled_transfer`` fires the Commit-5 wiring, which syncs as of
    ``date.today()``; the parallel run resolves as of the same date.  2026-05-15
    is after every payment period used (P1/P2/P3 in Jan-Mar), so each settled
    payment is historical regardless of the wall-clock date.
    """
    freeze_today(monkeypatch, _AS_OF)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_loan(
    user, *, anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
    rate=_RATE, name="Oracle Loan", escrow_annual=None, payment_day=1,
):
    """Create a resolvable amortizing loan with the suite's controlled anchor.

    ``anchor_date`` defaults to 2026-01-10 (before every payment period used, so
    every settled payment is post-anchor and eligible); a caller pins a LATER
    date to place a payment pre-anchor (the read-switch boundary case).
    ``payment_day`` defaults to the 1st; a caller pins another day to control
    where each installment's due date falls relative to the biweekly grid.
    """
    return create_loan_with_trueup(
        user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=_ORIGINATION_DATE, name=name,
        escrow_annual=escrow_annual, payment_day=payment_day,
    )


def _confirmed_rows_at(loan_account_id, scenario_id, as_of):
    """Return the seam's CONFIRMED schedule rows for a loan at *as_of*.

    The walk-built confirmed view's rows (:func:`seam_confirmed_view`), which
    since plan step E1d-b are the confirmed slice of the loan's resolved schedule
    -- and, before it, were the deleted ``confirmed_loan_history_rows`` read of
    the posted ledger.  Pinned at an explicit scenario and as-of for the same
    reason :func:`_resolved_at` is.

    Args:
        loan_account_id: The loan account whose confirmed rows to read.
        scenario_id: The scenario to scope to.
        as_of: The display boundary.

    Returns:
        The chronological confirmed ``AmortizationRow`` list, or ``None`` when
        the seam has no confirmed view for this loan (not configured, no
        baseline, or not yet originated).
    """
    view = seam_confirmed_view(loan_account_id, scenario_id, as_of)
    return None if view is None else view.history_rows


def _resolved_at(loan_account_id, scenario_id, as_of):
    """Resolve ONE loan through the seam, at an explicit scenario and as-of.

    The suite's whole-loan read.  It goes through the seam's public entry
    (:func:`app.services.balance_at._resolution.resolved_loan`, where the db-facing
    resolution has lived since plan step E1d-a) rather than reaching into the
    private module behind it, and it pins the scenario EXPLICITLY -- several
    tests here resolve a loan under a named scenario (a what-if) or at a frozen
    as-of, which ``BalanceContext.build`` would not reproduce (it looks the
    user's BASELINE up for itself).

    Args:
        loan_account_id: The loan account to resolve.
        scenario_id: The scenario to scope the payment history to.
        as_of: The date to resolve the loan AT.

    Returns:
        The :class:`~app.services.balance_at._resolution.ResolvedLoan`, or ``None`` when the
        account is not a configured loan.
    """
    loan = _db.session.get(Account, loan_account_id)
    return resolved_loan(loan, BalanceContext(
        user_id=loan.user_id,
        scenario=_db.session.get(Scenario, scenario_id),
        as_of=as_of,
    ))


def _settle(
    user, loan, period, amount=Decimal("1000.00"), scenario=None,
    settled_on=None,
):
    """Settle a Checking -> loan payment transfer through the service.

    Routes through ``create_settled_transfer`` (the sole transfer writer), which
    posts the Step-2 cash entry AND fires the Commit-5 wiring that posts the
    Step-4 correction -- so the returned payment is fully posted, exactly as
    marking it Paid produces it.

    Settles ON the period's start by default (``settled_on=None``), so the
    payment is visible from its period start under C2's settled-date clock -- the
    on-time basis the resolver's confirmed-payment eligibility uses, keeping the
    reader-vs-resolver parallel run exact.  A test exercising an early / late
    settle passes an explicit ``settled_on`` civil date.
    """
    return create_settled_transfer(
        user, _db.session, user["account"], loan, period,
        amount=amount, scenario=scenario,
        settled_on=period.start_date if settled_on is None else settled_on,
    )


def _make_tracking_start_loan(
    user, *, tracking_balance=_ANCHOR_BALANCE, tracking_date=_ANCHOR_DATE,
    rate=_RATE, name="Tracking Loan",
):
    """A resolvable loan whose OPENING is a tracking-start (no true-up).

    The mid-life-import shape: origination is $250,000 (2025-01-01) but the
    confirmed ledger opens at *tracking_balance* as of *tracking_date* (default
    the suite's $100,000 / 2026-01-10, before every payment period).  Both the
    resolver and the genesis reader synthesize the opening from that tracking-start
    (their shared ``load_loan_anchor_facts``), so the parallel run pits their
    disjoint balance math against the SAME tracking-start anchor.
    """
    loan = create_loan_account(
        user, _db.session, name=name, principal=_ORIGINATION_PRINCIPAL,
        rate=rate, origination_date=_ORIGINATION_DATE, term=360,
    )
    insert_tracking_start_event(
        loan_loaders.load_loan_params(loan.id), tracking_balance, tracking_date,
    )
    _db.session.commit()
    return loan


def _add_rate_change(loan, effective_date, rate):
    """Append a :class:`RateHistory` rate change (an ARM recast) to a loan.

    Mirrors the unit suite's helper (``test_loan_posting_service._add_rate_change``)
    so both suites drive an ARM step the same way: the rate feed the walk AND
    the resolver read (via ``load_rate_history`` / ``load_loan_context``) both
    pick it up, so the rate step reaches both producers of the parallel run.
    """
    _db.session.add(RateHistory(
        account_id=loan.id, effective_date=effective_date, interest_rate=rate,
    ))
    _db.session.flush()


def _seed_boundary_loan(bare_user):
    """Set up a fresh user with periods straddling 2025-12-31 and a loan.

    The year-boundary fixture (extracted so the boundary test does not carry the
    setup as a dozen locals).  ``seed_periods`` locks its owner to 2026 and
    ``generate_pay_periods`` rejects backfilling earlier periods, so a
    boundary-straddling window needs a periodless ``bare_user``.  Generates six
    biweekly periods from 2025-12-25 (so ``periods[0]`` straddles the year end and
    ``periods[2]`` is a distinct January month), a baseline scenario, a Checking
    account to pay from, and an origination-only $100,000 loan originated a month
    before the window (both payments post-origination -- a clean sum with no
    anchor-reset subtlety; a 360-month term so it never pays off here).

    Args:
        bare_user: The ``bare_user`` fixture dict (a user with no periods).

    Returns:
        ``(loan, ctx, checking, periods)`` -- the loan account, a seed-user-shaped
        context dict (``user`` + baseline ``scenario``) the transfer/loan helpers
        accept, the Checking account to pay from, and the ordered pay periods.
    """
    user_id = bare_user["user"].id
    periods = pay_period_service.generate_pay_periods(
        user_id=user_id, start_date=date(2025, 12, 25),
        num_periods=6, cadence_days=14,
    )
    _db.session.flush()
    scenario = Scenario(user_id=user_id, name="Baseline", is_baseline=True)
    _db.session.add(scenario)
    _db.session.flush()
    ctx = {"user": bare_user["user"], "scenario": scenario}
    checking = create_account_of_type(ctx, _db.session, "Checking", "Checking")
    loan = create_loan_account(
        ctx, _db.session, name="Boundary Loan",
        principal=Decimal("100000.00"), rate=Decimal("0.06000"),
        origination_date=date(2025, 11, 1), term=360,
    )
    return loan, ctx, checking, periods


def _clear_all_loan_postings():
    """Reproduce the pre-wiring historical state: remove EVERY loan posting.

    Runs the two boundary migrations' own raw-SQL teardowns in real
    downgrade-chain order -- the genesis teardown FIRST (the opening / true-up
    entries + per-loan opening-equity accounts), THEN the Step-4 payment teardown
    (the loan_payment corrections + interest / escrow / refund accounts) -- then
    commits.  The order is load-bearing: the genesis boundary migration
    (``f3d6b1a8c2e4``) is the head, above the Step-4 boundary, so its teardown
    runs first in a real downgrade; running the payment teardown first would
    strand the per-loan opening-equity legs on an already-deleted account.
    Single-sources that ordering so the callers (the backfill-equivalence and the
    reader-gap tests) cannot drift on it -- the same helper the backfill suite
    keeps as ``_clear_corrections``.
    """
    _GENESIS_MIGRATION._remove_loan_genesis_postings(_db.session)
    _BACKFILL_MIGRATION._remove_loan_payment_postings(_db.session)
    _db.session.commit()


# ---------------------------------------------------------------------------
# Independent reconciliation queries (test-authored, NOT the service helpers)
# ---------------------------------------------------------------------------
#
# These re-derive each side from scratch so the oracle is a genuine second
# opinion: the ledger side reads ``account_postings`` with an independently
# written join shape (keyed off the REAL loan account / the per-loan discriminator
# rather than resolving the ledger account first, as ``account_posting_total``
# does), and the source side reads ``transactions``.  ``_trial_balance`` /
# ``_entries_violating_balance`` mirror the Step-2 / Step-3 oracles; the
# duplication is DELIBERATE -- each oracle keeps its OWN independent queries so it
# stays a self-contained second opinion.


def _independent_loan_linked_net(loan_account_id: int, scenario_id: int) -> Decimal:
    """Sum a loan's LINKED-ledger posting legs in a scenario (independent query).

    Joins ``account_postings`` -> ``journal_entries`` (for the scenario) ->
    ``ledger_accounts`` and keys on the REAL ``ledger_accounts.account_id ==
    loan_account_id`` -- a different join shape than
    ``posting_service.account_posting_total`` (which resolves the ledger account
    first), so the two cannot share a lookup bug.  The linked ledger carries the
    Step-2 cash (+cash) AND the Step-4 correction's loan leg
    (-(interest+escrow+excess)), so its net is exactly the summed real principal.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.account_id == loan_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _per_loan_correction_net(loan_account_id: int, scenario_id: int) -> Decimal:
    """Sum a loan's PER-LOAN (interest/escrow/refund) ledger legs (independent).

    Keys on ``ledger_accounts.loan_account_id == loan_account_id`` -- the per-loan
    Expense / Asset accounts the correction backs the non-principal onto, which
    carry ``account_id IS NULL``.  Their net is exactly ``sum(interest + escrow +
    excess)`` across the loan's corrections: the amount the correction moves OFF
    the loan.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.loan_account_id == loan_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _independent_settled_income_cash(
    loan_account_id: int, scenario_id: int
) -> Decimal:
    """Sum a loan's settled income-shadow cash (independent query).

    The independent restatement of ``settled_transfer_effect`` for a loan: over
    the loan's settled, non-deleted transfer income shadows in *scenario_id*, sum
    ``effective = COALESCE(actual, estimated)``.  A loan's shadows are all income
    (the to-account leg), so every term is ``+effective`` -- the cash that flowed
    in.  Reads ``transactions``, a different table than the ledger queries above,
    so asserting the ledger reconciles against this ties the postings to the
    transaction source of truth.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    effective = _db.func.coalesce(
        Transaction.actual_amount, Transaction.estimated_amount
    )
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(effective), Decimal("0"))
        )
        .filter(
            Transaction.account_id == loan_account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .scalar()
    )


def _trial_balance() -> Decimal:
    """Return ``SUM(account_postings.amount)`` over the whole ledger."""
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .scalar()
    )


def _entries_violating_balance() -> list[tuple[int, Decimal, int]]:
    """Return ``(entry_id, leg_sum, leg_count)`` for every malformed entry.

    A well-formed double-entry has ``leg_sum == 0`` and ``leg_count >= 2``.  Any
    row returned here is a violation -- the per-entry invariant the deferred
    trigger also enforces, re-checked from the ORM side.
    """
    rows = (
        _db.session.query(
            Posting.journal_entry_id,
            _db.func.sum(Posting.amount),
            _db.func.count(Posting.id),
        )
        .group_by(Posting.journal_entry_id)
        .all()
    )
    return [
        (entry_id, leg_sum, leg_count)
        for entry_id, leg_sum, leg_count in rows
        if leg_sum != 0 or leg_count < 2
    ]


# ---------------------------------------------------------------------------
# Parallel-run and sweep helpers
# ---------------------------------------------------------------------------


def _resolver_balance(
    loan_account_id: int, scenario_id: int, as_of: date
) -> Decimal:
    """Return the un-seeded anchor-replay balance -- the parallel reference.

    The oracle's genuinely independent producer: it replays the SCHEDULED payment
    (``principal = period_pi - interest``) forward from the latest anchor and
    discards the cash, so it NEVER reads the posted ledger -- the load-bearing
    property the whole parallel run rests on (module docstring invariants 1 / 7).

    Since the read switch (plan Section 8) the PRODUCTION balance reads the
    ledger's facts (the seam's fold; the seeded resolver threaded the confirmed
    balance in until plan step D2a deleted its balance field entirely) -- so a
    production window here would make the "resolver" read the very ledger it is
    meant to cross-check, collapsing the parallel run to a tautology.  This
    helper therefore builds the SAME ``LoanInputs`` and runs the replay
    derivation directly, preserving the schedule-replay reference.  The
    production path is verified separately -- that it equals the
    ledger off-schedule is the read switch, pinned by
    ``TestReadSwitchProductionPath``.
    """
    params = loan_loaders.load_loan_params(loan_account_id)
    assert params is not None, "loan is not resolvable (no LoanParams)"
    anchor_facts = loan_loaders.load_loan_anchor_facts(params)
    ctx = loan_payment_service.load_loan_context(
        loan_account_id, scenario_id, params,
    )
    inputs = loan_resolver.LoanInputs(
        params, anchor_facts, ctx.payments, ctx.rate_changes,
    )
    # The replay derivation directly (``LoanState.current_balance`` carried it
    # until plan step D2a deleted the field): unchanged as the oracle's
    # independent schedule-replay reference.
    periods = loan_resolver.resolve_periods(params, inputs.rate_changes)
    return round_money(
        _replay_from_anchor(inputs, periods, as_of).balance_as_of
    )


def _ledger_balance(loan_account_id: int, scenario_id: int) -> Decimal:
    """Return the ledger's genesis view of the current balance.

    ``-(sum of the loan's linked-ledger postings)``: under genesis the linked
    ledger carries the opening (-original_principal), every true-up correction,
    and every payment's principal (the Step-2 cash plus the Step-4 correction), so
    the negated sum IS the confirmed balance the read switch (plan Section 10)
    displays -- no anchor read, no boundary rule.  Numerically identical to the
    Step-4 formula this replaces (``anchor - linked_net``) on a
    post-anchor-only loan, because the opening + true-up legs sum to -(anchor) on
    the linked ledger; but genesis also SUMS a pre-anchor payment correctly (its
    principal is subsumed by the true-up's owed_before), so -- unlike the Step-4
    formula -- there is no pre-anchor exclusion to assume away.
    """
    return -_independent_loan_linked_net(loan_account_id, scenario_id)


def _posted_balance(
    loan_account_id: int, scenario_id: int, as_of: date = _AS_OF,
) -> Decimal:
    """Return what the postings say the loan owed on a DATE (the dated window).

    :func:`tests._test_helpers.posted_loan_balance_at`: the same linked-ledger
    legs :func:`_ledger_balance` sums, but bounded by ``entry_date <= as_of``
    and guarded by the OPENING sentinel.  A distinct derivation, so the two
    windows agreeing is a real check rather than one query written twice, and
    the only one that can answer a historical date at all.  Asserts non-``None``
    because every caller here builds a CONFIGURED loan (an opening is posted), so
    a ``None`` would be a defect, not an unconfigured loan (the ``None`` route is
    proven directly by its own test).
    """
    result = posted_loan_balance_at(
        loan_account_id, scenario_id, as_of,
    )
    assert result is not None, (
        f"posting window returned None for configured loan {loan_account_id} in "
        f"scenario {scenario_id} -- no OPENING posting where one was expected"
    )
    return result


def _posted_period_map(
    loan_account_id: int, scenario_id: int, periods: list[PayPeriod],
) -> "dict[int, Decimal]":
    """Return the dated posting window at every period END (the per-period form).

    :func:`tests._test_helpers.posted_loan_balance_map` -- the scalar window
    applied at each period's END, which is what lets the early-settle parallel
    run below check period boundaries past the frozen today (no posted entry in
    these fixtures is dated later, so such a period carries the confirmed sum
    flat).  Because it IS the scalar per period, never assert the two agree --
    that identity cannot fail; the assertions here are against the independent
    resolver.  Asserts non-``None`` for the same reason as
    :func:`_posted_balance`.
    """
    result = posted_loan_balance_map(
        loan_account_id, scenario_id, periods,
    )
    assert result is not None, (
        f"posting window map returned None for configured loan {loan_account_id} "
        f"in scenario {scenario_id} -- no OPENING posting where one was expected"
    )
    return result


def _assert_completeness(
    loan_account_id: int, scenario_id: int, as_of: date
) -> None:
    """Assert every settled payment that owes a correction has one.

    Plan 8.3: for each settled payment (the split walk's output -- since the R1
    split-at-settlement fix that is EVERY settled payment, including one whose
    pay period has not yet begun), a non-zero non-principal part (interest +
    escrow + excess) means the loan's cash is partly non-principal, so a
    correction MUST exist; an all-principal payment (a zero-rate, no-escrow,
    no-overpay payment) legitimately carries none.  The pre-R1 "future-dated
    settled payment is flagged, not covered" sentinel is retired: settlement is
    now the confirming event, so an early-settled payment is inside this
    guarantee, not outside it.
    """
    splits = loan_ledger.compute_loan_payment_splits(
        loan_account_id, scenario_id,
    )
    # Every caller settles at least one payment before reconciling, so an empty
    # split walk means the loader silently found nothing (a scenario-scope or
    # settled-filter regression) and the completeness loop below would pass
    # vacuously -- assert the enumeration is non-empty so it cannot.
    assert splits, (
        f"loan {loan_account_id} scenario {scenario_id}: the split walk found no "
        f"settled payments to check completeness over -- the sweep would be vacuous"
    )
    for split in splits:
        non_principal = split.interest + split.escrow + split.excess
        entries = loan_correction_entries(_db.session, split.income_shadow.id)
        if non_principal != Decimal("0"):
            assert entries, (
                f"settled payment shadow {split.income_shadow.id} has non-"
                f"principal {non_principal} but no correction -- an uncorrected "
                f"Step-2 cash entry"
            )


def _assert_loan_reconciles(
    loan, scenario_id: int, as_of: date
) -> None:
    """Assert a loan's whole ledger reconciles: three-way + completeness + trial.

    The production-wide sweep run after each fixture's mutations (plan 8.3 / 8.4 /
    8.7).  It ties together, for one loan in one scenario:

    * (a) the production reader ``account_posting_total`` equals the independent
      linked-ledger query -- the two computed the same net two different ways;
    * (b) the loan-aware superseding invariant, entirely from independent queries:
      ``linked_net == settled_income_cash - per_loan_correction_net`` (the loan
      nets to cash minus the non-principal moved off it);
    * (c) the same invariant through the PRODUCTION readers
      (``account_posting_total == settled_transfer_effect - per_loan_net``), so
      the posted-ledger readers the balance sheet and statements consume satisfy
      it too;
    * (d) completeness -- every eligible payment owing a correction has one;
    * (e) per-entry balance and a zero whole-ledger trial balance.

    IMPORTANT -- this is a STRUCTURAL / reader-consistency sweep, NOT a split-VALUE
    check.  (a)/(b)/(c)/(e) are accounting IDENTITIES: given Step 2 is correct and
    every correction balances (both DB-enforced by ``ck_account_postings_balanced``),
    they hold no matter WHAT interest/principal the split posted -- if the split
    booked interest 700 instead of 500, ``per_loan`` and the loan leg both shift and
    the identities still pass (this is why, in the injected-``+$10`` experiment, only
    the two invariant-only tests survived).  So every caller MUST pair this with a
    value assertion -- a parallel-run ``ledger == resolver`` or a hand-computed
    literal -- which is what actually pins the split; the sweep's job is to catch a
    reader / scenario-scope / routing / balance defect the value checks do not.
    (b)/(c) also assume the loan has NO OUTBOUND transfer:
    ``_independent_settled_income_cash`` sums income shadows only, which restates
    ``settled_transfer_effect`` faithfully only while every loan shadow is income (a
    to-account leg) -- true for a payment, false for a hypothetical disbursement.
    That assumption is now ENFORCED, not merely relied on: a transfer OUT of a
    loan is rejected at creation
    (``transfer_service`` / ``_reject_transfer_out_of_loan``, review R6), so every
    loan shadow is a payment IN by construction.
    """
    loan_id = loan.id
    linked_reader = posting_service.account_posting_total(loan_id, scenario_id)
    linked_independent = _independent_loan_linked_net(loan_id, scenario_id)
    per_loan = _per_loan_correction_net(loan_id, scenario_id)
    income_independent = _independent_settled_income_cash(loan_id, scenario_id)
    income_reader = posting_service.settled_transfer_effect(loan_id, scenario_id)

    # (a) production reader == independent linked-ledger query.
    assert linked_reader == linked_independent, (
        f"loan {loan_id}: account_posting_total {linked_reader} != independent "
        f"linked net {linked_independent} in scenario {scenario_id}"
    )
    # (b) superseding invariant, fully independent.
    assert linked_independent == income_independent - per_loan, (
        f"loan {loan_id}: linked net {linked_independent} != income cash "
        f"{income_independent} - non-principal corrections {per_loan}"
    )
    # (c) superseding invariant through the production readers.
    assert income_reader == income_independent, (
        f"loan {loan_id}: settled_transfer_effect {income_reader} != independent "
        f"income cash {income_independent}"
    )
    assert linked_reader == income_reader - per_loan, (
        f"loan {loan_id}: account_posting_total {linked_reader} != "
        f"settled_transfer_effect {income_reader} - corrections {per_loan}"
    )
    # (d) completeness.
    _assert_completeness(loan_id, scenario_id, as_of)
    # (e) per-entry balance + trial balance (whole-ledger self-checks).
    assert _entries_violating_balance() == []
    assert _trial_balance() == Decimal("0")


def _loan_ledger_net(loan, kind, scenario_id: int) -> Decimal:
    """Return the net of a loan's per-loan ledger of *kind* (0 if not minted)."""
    ledger = find_loan_ledger_account(_db.session, loan.id, kind)
    if ledger is None:
        return Decimal("0")
    return ledger_net(_db.session, ledger.id, scenario_id)


# ---------------------------------------------------------------------------
# 1. Parallel run vs the resolver (plan 8.2)
# ---------------------------------------------------------------------------


class TestParallelRunAgainstResolver:
    """The posted ledger and the independent resolver agree on-schedule, diverge off."""

    def test_on_schedule_payment_matches_resolver(
        self, app, db, seed_user, seed_periods,
    ):
        """Paying exactly the scheduled P&I keeps the ledger == the resolver.

        The resolver reports the loan's scheduled monthly P&I (``period_pi``);
        settling a payment of exactly that amount is on-schedule, so the ledger's
        real principal (cash - interest) equals the resolver's scheduled principal
        (period_pi - interest) and the two balances -- derived by disjoint code
        paths from the SAME anchor -- must agree to the penny.  A payment did post
        (the sweep is not vacuous), and Checking is untouched by the loan sync.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            # The resolver's scheduled monthly P&I; paying exactly it is
            # on-schedule (the loan carries no escrow, so cash == P&I).
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=scheduled_pi,
            )
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == resolver, (
                f"on-schedule ledger {ledger} != resolver {resolver}"
            )
            # Non-vacuity: the genesis linked total is opening (-250000) + true-up
            # (+150000) + the real principal (scheduled P&I - round(100000*0.005)
            # = -500.00 interest), i.e. principal - _ANCHOR_BALANCE.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == scheduled_pi - Decimal("500.00") - _ANCHOR_BALANCE
            shadow = loan_income_shadow(db.session, xfer.id, loan.id)
            assert len(loan_correction_entries(db.session, shadow.id)) == 1
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_tracking_start_opening_matches_resolver(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-life tracking-start loan: an on-schedule payment keeps ledger == resolver.

        The opening is a tracking_start ($100,000 as of 2026-01-10), NOT the
        $250,000 origination.  Both the genesis ledger reader and the independent
        resolver seed from that tracking-start (their shared anchor facts), so an
        on-schedule payment keeps the two disjoint balance paths locked to the
        penny -- and the balance opens from $100,000, provably below the
        origination principal.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_tracking_start_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == resolver, (
                f"tracking-start ledger {ledger} != resolver {resolver}"
            )
            # Opened at the tracking-start 100000, so the balance is far below
            # the 250000 origination -- proving the walk did NOT seed from it.
            assert ledger < _ORIGINATION_PRINCIPAL
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_on_schedule_multi_payment_matches_resolver(
        self, app, db, seed_user, seed_periods,
    ):
        """Two on-schedule payments keep the ledger == the resolver across the walk.

        Both payments are exactly the scheduled P&I, in distinct due months, so
        the ledger's real-principal walk and the resolver's scheduled-principal
        walk stay locked step-for-step from the shared anchor -- the balances
        agree after both.  This exercises the running-balance coupling that a
        single payment cannot.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            _settle(seed_user, loan, seed_periods[_P2], amount=scheduled_pi)
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == resolver
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_short_payment_diverges_and_ledger_owes_more(
        self, app, db, seed_user, seed_periods,
    ):
        """A short payment: the ledger owes MORE than the contractual resolver.

        Arithmetic (100000 @ 6%): interest 500.00; a $1,000 payment is short of
        the ~$1,498.88 scheduled P&I, so the real principal is 1000 - 500 =
        500.00 and the ledger balance is 100000 - 500 = 99,500.00 (hand-computed,
        no need for the schedule).  The resolver books the FULL scheduled
        principal (it ignores the cash), so it shows a LOWER balance -- they
        diverge, and the ledger is the honest record of a partial paydown that the
        resolver would need an anchor true-up to reflect.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The ledger's real balance, hand-computed.
            assert ledger == Decimal("99500.00")
            # Genesis: opening (-250000) + true-up (+150000) + principal (+500).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")
            # They DIVERGE, and the ledger (short paydown) owes MORE.
            assert ledger != resolver
            assert resolver < ledger
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_extra_principal_diverges_and_ledger_owes_less(
        self, app, db, seed_user, seed_periods,
    ):
        """Extra principal: the ledger owes LESS than the contractual resolver.

        Arithmetic (100000 @ 6%): interest 500.00; a $2,000 payment exceeds the
        ~$1,498.88 scheduled P&I, so the real principal is 2000 - 500 = 1,500.00
        and the ledger balance is 100000 - 1500 = 98,500.00 (hand-computed).  The
        resolver books only the scheduled principal, so it shows a HIGHER balance
        -- the extra $500-ish of principal the ledger captured automatically is
        exactly what the resolver drops on the floor (and would need a true-up to
        recover).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("2000.00"))
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == Decimal("98500.00")
            # Genesis: opening (-250000) + true-up (+150000) + principal (+1500).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-98500.00")
            # They DIVERGE, and the ledger (extra paydown) owes LESS.
            assert ledger != resolver
            assert ledger < resolver
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_arm_rate_step_matches_a_hand_computed_post_step_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """An ARM rate step: the ledger balance equals a HAND-COMPUTED literal.

        The parallel run agrees on-schedule and diverges off, but BOTH the walk
        and the resolver read the same ``rate_period_engine``, so a shared
        rate-step bug would move the two producers together and the parallel run
        alone would NOT catch it (review M4(b) / M7).  This case closes that gap
        with a hand-computed post-step ledger balance -- the one producer a
        shared-kernel bug cannot hide from -- which is exactly why the fixture
        does NOT read the balance back from either producer.

        A rate step to 12% effective 2026-03-01 governs P3 (pay period starts
        2026-03-13, due 2026-04-01) while P1 (due 2026-02-01) keeps the 6%
        origination rate.  Each payment is a $1,000 SHORT payment, so the
        ledger's REAL principal (cash - interest) is hand-computable without the
        schedule (the same partition ``test_arm_rate_step_changes_interest``
        pins at the unit level):
          P1 (6%):  interest = round(100000 * 0.06 / 12) = 500.00;
                    principal = 1000 - 500 = 500.00; balance 99,500.00.
          P3 (12%): interest = round( 99500 * 0.12 / 12) = 995.00;
                    principal = 1000 - 995 =   5.00; balance 99,495.00.
        So the ledger balance is 99,495.00 and the genesis linked total is
        opening (-250000) + true-up (+150000) + P1 principal (+500) + P3
        principal (+5) = -99,495.00.  The reader reads that same ledger, so it
        agrees to the penny; the resolver books the (recast, far larger)
        SCHEDULED principal at each rate, so it shows a LOWER balance -- the
        short-payment divergence, now proven to survive an ARM step.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _add_rate_change(loan, date(2026, 3, 1), Decimal("0.12000"))
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            _settle(seed_user, loan, seed_periods[_P3], amount=Decimal("1000.00"))
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            reader = _posted_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The hand-computed post-step balance -- the shared-kernel teeth.
            assert ledger == Decimal("99495.00")
            assert reader == Decimal("99495.00")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99495.00")
            # The resolver books scheduled principal at each rate; short payments
            # mean it owes LESS -- divergence survives the rate step.
            assert resolver < ledger
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_pre_anchor_payment_is_correctly_summed_under_genesis(
        self, app, db, seed_user, seed_periods,
    ):
        """A pre-anchor payment is SPLIT and summed, not excluded -- genesis fix.

        Genesis retires the Step-4 pre-anchor exclusion (the read-switch boundary
        the prior draft carved out): the walk splits EVERY payment from
        origination, and the opening + true-up corrections absorb the pre-anchor
        principal, so the from-origination sum-of-postings reproduces the resolver
        with NO boundary rule.  With the trueup at 2026-02-15, P1 (due 2026-02-01)
        is pre-trueup: it splits on the $250,000 origination balance (interest
        round(250000 * 0.005) = 1250.00, principal 1000 - 1250 = -250.00, so the
        walk carries 250250 to the trueup).  The opening books -250000 and the
        true-up 250250 - 100000 = +150250; with P1's cash (+1000) and correction
        loan leg (-1250) the loan-linked net is -100000.00 -- exactly -(the
        resolver's 100000 anchor, which subsumes P1) -- so the pre-anchor payment
        is summed correctly, never the old double-count.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Trueup AFTER P1's 2026-02-01 due date, so P1 is pre-trueup.
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 15))
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()
            shadow = loan_income_shadow(db.session, xfer.id, loan.id)

            # Genesis SPLITS the pre-trueup payment (Step 4 excluded it).
            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, scenario_id,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")
            assert loan_correction_entries(db.session, shadow.id) != []

            # Post the opening + true-up (the read-switch corrections).
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id,
            )
            db.session.commit()

            # -(linked net) reproduces the resolver's anchor, which subsumes P1.
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert resolver == _ANCHOR_BALANCE  # 100000.00
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == -_ANCHOR_BALANCE  # -100000.00, no pre-anchor pollution


# ---------------------------------------------------------------------------
# 2. Supersedes the cash per-account invariant for loans (plan 8.7)
# ---------------------------------------------------------------------------


class TestSupersedesCashInvariantForLoans:
    """The loan-aware invariant holds exactly where the cash per-account one breaks."""

    def test_loan_aware_invariant_holds_where_cash_invariant_breaks(
        self, app, db, seed_user, seed_periods,
    ):
        """Once a correction exists, the loan nets to principal, not to the cash.

        The Step-2 / Step-3 oracle's per-account invariant is
        ``account_posting_total(A) == settled_transfer_effect(A) +
        settled_transaction_effect(A)``.  For this loan after one $1,000 payment
        the settled transfer effect (cash in) is +1,000 and there are no ordinary
        transactions, so the cash invariant would demand the ledger net +1,000 --
        but under genesis the loan-linked ledger nets -99500 (the $500 interest
        moved off it, plus the opening -250000 and true-up +150000).  So the cash
        invariant PROVABLY breaks (-99500 != +1000), while the loan-aware
        superseding invariant (``linked == settled_transfer_effect - per-loan
        corrections``, the per-loan side now including the opening-equity legs)
        holds.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()

            linked = posting_service.account_posting_total(loan.id, scenario_id)
            cash_effect = posting_service.settled_transfer_effect(
                loan.id, scenario_id,
            )
            txn_effect = posting_service.settled_transaction_effect(
                loan.id, scenario_id,
            )
            non_principal = _per_loan_correction_net(loan.id, scenario_id)

            # The cash per-account invariant would demand linked == cash + txn.
            assert cash_effect == Decimal("1000.00")
            assert txn_effect == Decimal("0.00")
            # Genesis: opening (-250000) + true-up (+150000) + principal (+500).
            assert linked == Decimal("-99500.00")
            assert linked != cash_effect + txn_effect  # the cash invariant BREAKS
            # The loan-aware superseding invariant holds; the per-loan side is the
            # $500 interest plus the opening-equity legs (+100000 = -(opening
            # -250000 + true-up +150000)).
            assert non_principal == Decimal("100500.00")
            assert linked == cash_effect - non_principal
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)


# ---------------------------------------------------------------------------
# 3. A rich fixture: interest + escrow + refund legs all reconcile (8.1/8.3/8.4)
# ---------------------------------------------------------------------------


class TestRichFixtureFullSweep:
    """A payoff-overpayment on an escrow loan books all four parts and reconciles."""

    def test_escrow_and_refund_reconcile_full_sweep(
        self, app, db, seed_user, seed_periods,
    ):
        """One $2,000 payment on a $1,000 escrow loan books interest, escrow, refund.

        Arithmetic (anchor 1,000 @ 6%, escrow $1,200/yr = $100.00/mo): interest =
        round(1000 * 0.005) = 5.00; escrow 100.00; principal0 = 2000 - 5 - 100 =
        1,895.00 > 1,000, so principal caps at the 1,000 balance and the surplus
        1,895 - 1,000 = 895.00 routes to the Refund receivable; the loan closes at
        0.  So the correction books interest +5.00 (Expense), escrow +100.00
        (Expense), refund +895.00 (Asset), and the loan leg -1,000.00.  The
        payment contributes +1,000 of principal; with the opening (-250000) and
        true-up (+249000 = 250000 - 1000) the loan-linked ledger nets 0.00 -- the
        loan is paid off.  FOUR per-loan ledgers are minted (interest, escrow,
        refund, opening-equity) and the whole sweep ties (this is the only fixture
        exercising interest + escrow + refund together).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(
                seed_user, anchor_balance=Decimal("1000.00"),
                escrow_annual=Decimal("1200.00"),
            )
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("2000.00"))
            db.session.commit()

            # Each per-loan leg is hand-computed.
            assert _loan_ledger_net(
                loan, LedgerAccountKindEnum.LOAN_INTEREST, scenario_id,
            ) == Decimal("5.00")
            assert _loan_ledger_net(
                loan, LedgerAccountKindEnum.LOAN_ESCROW, scenario_id,
            ) == Decimal("100.00")
            assert _loan_ledger_net(
                loan, LedgerAccountKindEnum.LOAN_REFUND, scenario_id,
            ) == Decimal("895.00")
            # Paid off: opening (-250000) + true-up (+249000) + principal (+1000).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("0.00")
            # Per-loan net = non-principal (1000) + opening-equity legs (+1000,
            # the negatives of opening -250000 + true-up +249000).
            assert _per_loan_correction_net(
                loan.id, scenario_id,
            ) == Decimal("2000.00")
            # The loan is paid off: the ledger balance is zero.
            assert _ledger_balance(loan.id, scenario_id) == Decimal("0.00")
            # Four per-loan ledger accounts: interest, escrow, refund, opening-equity.
            assert (
                db.session.query(LedgerAccount)
                .filter_by(loan_account_id=loan.id)
                .count()
            ) == 4
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)


# ---------------------------------------------------------------------------
# 4. Scenario and owner isolation of the whole sweep (plan 8.8)
# ---------------------------------------------------------------------------


class TestScenarioAndOwnerIsolation:
    """Corrections in one scenario / owner never reconcile against another's."""

    def test_two_scenarios_reconcile_independently(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment in each of two scenarios reconciles independently.

        The loan's anchor and rate live on the account, so a $1,000 payment splits
        the same in both scenarios (interest 500, principal 500); the opening +
        true-up post per-scenario too.  Scoped to the baseline the loan-linked
        ledger nets -99500 (opening -250000 + true-up +150000 + principal 500);
        scoped to the what-if it also nets -99500 -- and neither picks up the
        other (never -199000).  Each scenario's whole sweep ties on its own.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _make_loan(seed_user)
            _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
                scenario=baseline,
            )
            _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
                scenario=whatif,
            )
            db.session.commit()

            # Each scenario nets -99500 in isolation -- never -199000.
            assert posting_service.account_posting_total(
                loan.id, baseline.id,
            ) == Decimal("-99500.00")
            assert posting_service.account_posting_total(
                loan.id, whatif.id,
            ) == Decimal("-99500.00")
            _assert_loan_reconciles(loan, baseline.id, _AS_OF)
            _assert_loan_reconciles(loan, whatif.id, _AS_OF)

    def test_two_owners_reconcile_independently(
        self, app, db, seed_user, seed_second_user, seed_periods,
        seed_second_periods,
    ):
        """Two owners each settle a loan payment; neither sees the other's.

        Owner 1 settles a $1,000 payment on their loan (real principal 500); owner
        2 settles a $2,000 payment on theirs (real principal 1,500).  Each loan
        reconciles in its owner's own baseline scenario, every correction's
        journal entry carries its own owner's ``user_id``, and a ``Posting``
        carries no ``user_id`` of its own -- so ownership is reachable only through
        the entry and cannot cross-contaminate.
        """
        with app.app_context():
            loan1 = _make_loan(seed_user, name="Owner1 Loan")
            _settle(seed_user, loan1, seed_periods[_P1], amount=Decimal("1000.00"))
            loan2 = _make_loan(seed_second_user, name="Owner2 Loan")
            _settle(
                seed_second_user, loan2, seed_second_periods[_P1],
                amount=Decimal("2000.00"),
            )
            db.session.commit()

            scenario1 = seed_user["scenario"].id
            scenario2 = seed_second_user["scenario"].id
            # Genesis linked net = opening (-250000) + true-up (+150000) + real
            # principal: owner 1 -> -99500 (+500), owner 2 -> -98500 (+1500).
            assert posting_service.account_posting_total(
                loan1.id, scenario1,
            ) == Decimal("-99500.00")
            assert posting_service.account_posting_total(
                loan2.id, scenario2,
            ) == Decimal("-98500.00")

            # Ownership is normalized onto the journal entry, not the posting.
            assert not hasattr(Posting, "user_id")
            owner1_id = seed_user["user"].id
            owner2_id = seed_second_user["user"].id
            loan_payment_source = ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT,
            )
            corrections = (
                db.session.query(JournalEntry)
                .filter_by(source_kind_id=loan_payment_source)
                .all()
            )
            assert corrections  # both owners posted at least one
            for entry in corrections:
                assert entry.user_id in (owner1_id, owner2_id)
                for posting in entry.postings:
                    assert posting.ledger_account.user_id == entry.user_id

            _assert_loan_reconciles(loan1, scenario1, _AS_OF)
            _assert_loan_reconciles(loan2, scenario2, _AS_OF)


# ---------------------------------------------------------------------------
# 5. Backfill == go-forward (plan 8.8)
# ---------------------------------------------------------------------------


class TestBackfillEqualsGoForward:
    """The backfill reproduces the go-forward ledger AND makes the reader authoritative.

    Two faces of the C7 backfill guarantee (plan 8.8 / Section 4 commit 7): a
    ledger rebuilt by the backfill reconciles identically to the go-forward one
    (leg for leg), and the dated posting window -- ``None`` while no opening is
    posted -- reads back == the resolver to the penny once the backfill posts it.
    The second is the plan's "the oracle detects the unposted-opening gap before
    and zero mismatches after," proven at a point in time AND at every period
    boundary, so the historical-data path is pinned directly, not only by
    transitivity through Sections 5 and 7.
    """

    def test_backfilled_ledger_reconciles_identically(
        self, app, db, seed_user, seed_periods,
    ):
        """Clearing then backfilling a payment's correction restores the same ledger.

        Settling posts the payment corrections AND the opening / true-up
        go-forward; the sweep ties.  Clearing every loan posting (the anchor
        corrections first, then the migration's own payment teardown) reproduces
        the pre-wiring historical state (settled, no postings), and the historical
        backfill (``backfill_all_loan_postings``, reusing the identical go-forward
        sync) re-posts them all.  The linked and per-loan nets return to their
        exact go-forward values and the whole sweep reconciles again -- backfill
        == go-forward, leg for leg.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            _settle(seed_user, loan, seed_periods[_P2], amount=Decimal("1000.00"))
            db.session.commit()

            forward_linked = _independent_loan_linked_net(loan.id, scenario_id)
            forward_per_loan = _per_loan_correction_net(loan.id, scenario_id)
            # Opening (-250000) + true-up (+150000) + principal (500 + 502.50).
            assert forward_linked == Decimal("-98997.50")
            # Interest (500 + 497.50) + opening-equity legs (+100000).
            assert forward_per_loan == Decimal("100997.50")
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

            # Reproduce the pre-wiring state (no loan postings), then backfill.
            _clear_all_loan_postings()
            assert _per_loan_correction_net(loan.id, scenario_id) == Decimal("0")
            loan_posting_service.backfill_all_loan_postings()
            db.session.commit()

            # The backfilled nets equal the go-forward nets, and the sweep ties.
            assert _independent_loan_linked_net(
                loan.id, scenario_id,
            ) == forward_linked
            assert _per_loan_correction_net(
                loan.id, scenario_id,
            ) == forward_per_loan
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_reader_reads_none_before_backfill_then_matches_resolver_after(
        self, app, db, seed_user, seed_periods,
    ):
        """The reader shows the unposted-opening gap before backfill, == resolver after.

        The read switch (plan Section 8) turns every displayed loan balance onto the
        genesis reader, so the C7 backfill is what makes that reader authoritative on
        HISTORICAL data -- a loan / payment settled before the go-forward wiring
        shipped, carrying no postings.  This is the plan's "gap before, zero
        mismatches after," pinned on the exact producers the switch flips onto:

        * BEFORE the backfill (openings cleared) the reader returns ``None`` --
          needs-setup, the unposted-opening GAP -- in BOTH forms of the window,
          while the resolver, which never reads the ledger, still reports the true
          balance unchanged.  Any surface reading the postings HERE would wrongly
          show this loan needs-setup.
        * AFTER the backfill both read back == the resolver to the penny.

        On-schedule (cash == scheduled P&I, no escrow), so the ledger's real
        principal equals the resolver's scheduled principal and the two agree
        exactly.  The resolver is invariant across the clear / backfill (the
        teardowns remove only postings, not the anchors / transactions it replays),
        so it is the stable independent reference both phases pin to.  Non-vacuity:
        the after-balance is the $100,000 anchor less the real principal (scheduled
        P&I - 500.00 interest), not the untouched anchor, and the map steps strictly
        down as the payment lands.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment
            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            # The resolver never reads the ledger, so its balance is invariant across
            # the clear / backfill -- the stable reference for both phases.  The
            # go-forward reader already matches it (the C6 gate); the contrast is the
            # point: authoritative now, None in a moment, == again after.
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert _posted_balance(loan.id, scenario_id) == resolver

            # Reproduce the pre-wiring historical state (no loan postings).
            _clear_all_loan_postings()

            # BEFORE: the unposted-opening gap.  No OPENING leg -> the reader cannot
            # produce a balance (both producers return None), yet the resolver -- which
            # never read the ledger -- is unchanged, so the gap is purely the ledger's.
            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) is None
            assert posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            ) is None
            assert _resolver_balance(loan.id, scenario_id, _AS_OF) == resolver

            loan_posting_service.backfill_all_loan_postings()
            db.session.commit()

            # AFTER: zero mismatch.  The scalar (C8 producer) and every period of the
            # map (C9 producer) read back == the resolver to the penny.
            reader = _posted_balance(loan.id, scenario_id)
            assert reader == resolver, f"reader {reader} != resolver {resolver}"
            balance_map = _posted_period_map(loan.id, scenario_id, seed_periods)
            for period in seed_periods:
                assert balance_map[period.id] == _resolver_balance(
                    loan.id, scenario_id, period.start_date,
                ), (
                    f"period {period.period_index}: map {balance_map[period.id]} "
                    f"!= resolver at {period.start_date}"
                )
            # Non-vacuity: the anchor less the real principal (not the untouched
            # anchor), and the map steps strictly down as the payment lands.
            assert reader == _ANCHOR_BALANCE - (scheduled_pi - Decimal("500.00"))
            assert balance_map[seed_periods[0].id] == _ANCHOR_BALANCE
            assert (
                balance_map[seed_periods[_P1].id]
                < balance_map[seed_periods[0].id]
            )


# ---------------------------------------------------------------------------
# 6. Adversarial: the oracle is not vacuous (it fails on a broken seed)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the superseding invariant and trial balance catch real breakage."""

    def test_superseding_invariant_catches_a_tampered_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Tampering a payment's actual cash breaks the loan-aware invariant.

        A reconciled $1,000 payment has linked net +500, income cash +1,000, and
        non-principal corrections +500, so ``linked == income - non_principal``
        holds.  Forcing the income shadow's ``actual_amount`` to 9,999 via raw SQL
        (no re-sync) pushes the income cash to +9,999 while the posted ledger is
        unchanged -- so ``income - non_principal`` becomes 9,499, no longer the
        +500 linked net.  The superseding invariant the sweep relies on now FAILS,
        proving it is a real comparison, not one that passes unconditionally.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()
            shadow = loan_income_shadow(db.session, xfer.id, loan.id)

            linked = _independent_loan_linked_net(loan.id, scenario_id)
            non_principal = _per_loan_correction_net(loan.id, scenario_id)
            income = _independent_settled_income_cash(loan.id, scenario_id)
            # Reconciled before tampering.
            assert linked == income - non_principal

            # Tamper the settled actual cash (transactions carry no balance
            # trigger, so this commits); the posted ledger is left untouched.
            db.session.execute(_db.text(
                "UPDATE budget.transactions SET actual_amount = 9999 "
                "WHERE id = :i"
            ), {"i": shadow.id})
            db.session.commit()

            tampered_income = _independent_settled_income_cash(
                loan.id, scenario_id,
            )
            assert tampered_income == Decimal("9999.00")  # source drifted
            assert _independent_loan_linked_net(
                loan.id, scenario_id,
            ) == linked  # ledger unchanged
            # The invariant the sweep checks now fails -- the drift is caught.
            assert linked != tampered_income - non_principal
            # Drive the REAL production-wide sweep helper (not just the inline
            # re-derivation above) so a regression that broke the helper itself --
            # e.g. one that stopped comparing the income cash -- would fail here.
            # ``match`` pins the SUPERSEDING invariant's message specifically, so a
            # future edit that weakened THAT comparison but left some other assertion
            # (a non-empty guard, the trial balance) firing under tamper no longer
            # keeps this test green -- the tooth cannot be lost undetected (L-1).
            with pytest.raises(AssertionError, match="non-principal corrections"):
                _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_trial_balance_catches_an_injected_leg(
        self, app, db, seed_user, seed_periods,
    ):
        """Injecting one extra leg on a correction pushes the trial balance off zero.

        A balanced book has trial balance 0.00.  Inserting one unmatched +50 leg
        onto the correction entry (raw SQL, flushed but never committed so the
        DEFERRED per-entry balanced trigger -- which validates only at COMMIT --
        never fires) makes the whole-ledger sum 0 + 50 = 50.00, so the
        trial-balance ``= 0`` assertion is a real check, not one the trigger makes
        vacuously true.  Rolled back so the leg never lands.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()
            shadow = loan_income_shadow(db.session, xfer.id, loan.id)
            assert _trial_balance() == Decimal("0.00")

            # Inject one extra, unmatched leg onto the correction entry, on the
            # loan's interest ledger.  Flush (not commit) makes it visible; the
            # deferred balanced trigger validates only at COMMIT, never reached.
            correction = loan_correction_entries(db.session, shadow.id)[0]
            interest_ledger = find_loan_ledger_account(
                db.session, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": correction.id,
                "l": interest_ledger.id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            })
            db.session.flush()

            assert _trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert _trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            db.session.rollback()

    def test_walk_interest_injection_fails_the_value_checks_not_the_sweep(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A +$10 interest bug in the walk fails the parallel run, survives the sweep.

        The executable form of the review's "+$10 interest injection failed 9 of
        11 tests" evidence (previously only prose in commit messages and
        docstrings, so a later edit that weakened a value assertion lost the
        oracle's teeth undetected).  Injecting $10 of phantom interest into the
        WALK's accrual -- and ONLY the walk; the resolver accrues through
        ``rate_period_engine``'s separate import, so it stays the honest reference
        -- makes every split book $10 too much interest and therefore $10 too
        little principal, so both ledger producers over-state the debt by exactly
        $10.  This proves two things at once, mirroring the manual experiment:

        * the parallel-run VALUE assertions (``ledger == resolver`` and
          ``reader == resolver``) -- the ones that actually PIN the split -- now
          FAIL, so they have teeth and are not passing unconditionally; but
        * the STRUCTURAL sweep ``_assert_loan_reconciles`` STILL PASSES, because
          it is an accounting identity -- the correction's loan leg and the
          per-loan leg both shift by the same $10, so ``linked == income -
          per_loan`` holds no matter what interest the split booked.  This is
          exactly why the injection failed the value tests but not the
          invariant-only ones -- now a CI-run negative control, not a claim.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            # The honest scheduled P&I, read BEFORE injecting: paying exactly it
            # is on-schedule, so absent the bug ledger == resolver to the penny.
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            # Inject $10 of phantom interest into the WALK's accrual only.  The
            # module binding ``_split.accrue_monthly_interest`` is patched; the
            # resolver's ``rate_period_engine.accrue_monthly_interest`` is a
            # DISTINCT import and stays honest, so the two diverge by exactly $10.
            monkeypatch.setattr(
                "app.services.loan_ledger._split"
                ".accrue_monthly_interest",
                lambda balance, rate: (
                    accrue_monthly_interest(balance, rate) + Decimal("10.00")
                ),
            )

            # Settle on-schedule WITH the bug active: the split posts $10 too much
            # interest -> $10 too little principal -> the ledger over-states debt.
            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            ledger = _ledger_balance(loan.id, scenario_id)
            reader = _posted_balance(loan.id, scenario_id)
            # Both ledger producers over-state the debt by exactly the phantom $10.
            assert ledger == resolver + Decimal("10.00")
            assert reader == resolver + Decimal("10.00")

            # The value checks that PIN the split now fail -- proving their teeth.
            with pytest.raises(AssertionError):
                assert ledger == resolver
            with pytest.raises(AssertionError):
                assert reader == resolver

            # ...yet the structural sweep is (by design) blind to the split value
            # and STILL reconciles: per_loan and the loan leg both shifted by $10.
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)


# ---------------------------------------------------------------------------
# 6b. The resolver stays ledger-free -- the parallel run is not a tautology
# ---------------------------------------------------------------------------
#
# The parallel run (Sections 1 / 7) is honest ONLY because the un-seeded resolver
# derives its balance from the schedule and the transaction source, never from the
# posted ledger it is meant to cross-check.  Review finding M4: that independence
# was convention-only -- a future refactor letting a resolver input loader consult
# the ledger would silently collapse the parallel run to a tautology and nothing
# would fail.  These guards make it mechanical.
#
# The fence covers the WHOLE resolver reference path, at the granularity each
# module allows.  ``loan_loaders`` and the ``loan_resolver`` package are pure
# resolver-support modules with no legitimate ledger read, so they are fenced at
# FILE granularity.  ``loan_payment_service`` is different: :func:`_resolver_balance`
# runs through its ``load_loan_context`` loader, but the module ALSO holds a read-
# switch function that reads the ledger by design (``confirmed_loan_view``; the
# seeding wrappers ``resolve_loan_seeded`` / ``resolve_loan_bundle`` moved into the
# balance seam as ``balance_at._resolution``, which reads the ledger only THROUGH
# ``confirmed_loan_view`` -- and the whole seam is already a ledger token here, so
# it needs no fencing of its own).  A file-granularity
# fence would flag that legitimate read, so that mixed module is fenced at FUNCTION
# granularity instead -- every function except the read-switch allowlist must stay
# ledger-free (2026-07-02 follow-up review M-1: without this, wiring the ledger into
# ``load_loan_context`` or its sibling loaders would taint the reference uncaught).

# The names that appear inside a posted-ledger import path.  A resolver-stack
# import whose dotted path contains any of these reads the posted ledger.  This is
# a denylist, so it must stay COMPLETE: the coverage guard
# ``test_ledger_import_tokens_cover_every_ledger_reader`` fails if any real
# ledger-reading module is not matched here, so a newly added reader cannot evade
# the fence silently (2026-07-02 follow-up review M-2).
_LEDGER_IMPORT_TOKENS = (
    "journal_entry",
    "ledger_account",
    "posting_service",
    "posting_reads",
    # The Step-5 shared reconcile primitives (delta legs, posted-correction
    # reader, correction-entry emitter) -- a ledger reader/writer helper the
    # resolver stack has no business importing.  Its consumers
    # (loan_posting_service, account_posting_service) are covered by the
    # "posting_service" substring.
    "_posting_reconcile",
    # The C6 balanced-write leaf (``_PostingLeg`` / ``_emit_balanced_entry``
    # / the UTC civil-date rule), split below ``posting_service`` so the
    # account correction package can share the write path without an import
    # cycle -- equally off-limits to the resolver stack.
    "_posting_write",
    "loan_posting_service",
    "ledger_account_service",
    # The Step-5 reporting package (income statement / balance sheet): its
    # attribution core reads the posted ledger, so a resolver import of it must
    # trip the fence.  ``ledger_account_service`` above does NOT cover it -- the
    # substring "ledger_account_service" is not in "ledger_report_service" --
    # and the M-2 coverage guard fails loud until this token is present (the
    # objective proof the denylist stays complete as new readers land).
    "ledger_report_service",
    "balance_at",
    "pay_period_admin",
)

# The posted-ledger MODEL modules -- the concrete data classes (Posting,
# JournalEntry, LedgerAccount) every ledger query ultimately goes through.  The
# coverage guard treats importing one of these as the objective definition of
# "reads the posted ledger".
_LEDGER_MODEL_MODULES = ("app.models.journal_entry", "app.models.ledger_account")
# Their leaf names -- for the ``from app.models import journal_entry`` import shape.
_LEDGER_MODEL_NAMES = tuple(name.rsplit(".", 1)[-1] for name in _LEDGER_MODEL_MODULES)
# The row-model CLASS names -- for the ``from app.models import Posting`` package
# re-export shape (F-1: the class pulled off ``app.models`` rather than its
# defining submodule, which the leaf-name list above never matches). Mirrors the
# production-side W9908 ``shekel-ledger-model-bypass`` fence; both import-fence
# detectors below check it so a resolver import (or a novel reader) cannot evade
# the fence by re-exporting the model name off the package.
_LEDGER_MODEL_CLASS_NAMES = ("Posting", "JournalEntry", "LedgerAccount")

def _resolver_stack_modules() -> list:
    """Return every FILE-fenced module the un-seeded resolver reference is built from.

    ``loan_loaders`` (the input loaders), ``loan_payment_service`` (the unified
    context loader), and the whole ``loan_resolver`` package, its submodules
    discovered dynamically so a newly added one is fenced automatically.  These are
    the resolver-support modules :func:`_resolver_balance` runs through; NONE has
    any legitimate reason to read the posted ledger, so each is scanned WHOLE.

    ``loan_payment_service`` joined them at plan step E1d-b: it held the read
    switch's ``confirmed_loan_view`` until then, which is why it was fenced at
    function granularity behind a hand-written allowlist instead (review M-1).
    The confirmed slice seeds from the walk now, that function is deleted, and the
    allowlist went with it -- so the exemption is closed by STRUCTURE, and a
    ledger import added anywhere in the module (top-level or in-function) is
    caught.
    """
    modules = [loan_loaders, loan_payment_service, loan_resolver]
    for info in pkgutil.iter_modules(loan_resolver.__path__):
        modules.append(
            importlib.import_module(f"app.services.loan_resolver.{info.name}")
        )
    return modules


def _imports_a_ledger_model(source: str) -> bool:
    """Return whether *source* imports a posted-ledger MODEL module.

    Catches every import shape that reaches ``Posting`` / ``JournalEntry`` /
    ``LedgerAccount``: ``from app.models.journal_entry import ...``,
    ``from app.models import journal_entry`` (leaf module), ``from app.models
    import Posting`` (the F-1 class re-export), and plain
    ``import app.models.ledger_account``.  The objective test for "this module
    reads the posted ledger" the coverage guard is built on.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.module in _LEDGER_MODEL_MODULES:
                return True
            if node.module == "app.models" and any(
                alias.name in _LEDGER_MODEL_NAMES
                or alias.name in _LEDGER_MODEL_CLASS_NAMES
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name in _LEDGER_MODEL_MODULES for alias in node.names):
                return True
    return False


def _ledger_model_importer_names() -> set:
    """Return the import-name of every ``app.services`` module reading the ledger.

    Walks the ``app/services`` source tree on disk (no imports, so no side effects)
    and, for each module whose source imports a posted-ledger model
    (:func:`_imports_a_ledger_model`), records the name a by-name import would carry
    -- the top-level module/package under ``app/services`` (e.g.
    ``loan_posting_service`` for its ``_reader`` submodule).  The coverage guard
    asserts :data:`_LEDGER_IMPORT_TOKENS` matches every one, so a NEW ledger reader
    cannot silently evade the resolver fence (review M-2).
    """
    services_dir = os.path.dirname(loan_payment_service.__file__)
    names: set = set()
    for root, _dirs, files in os.walk(services_dir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            with open(os.path.join(root, fname), encoding="utf-8") as handle:
                source = handle.read()
            if not _imports_a_ledger_model(source):
                continue
            relative = os.path.relpath(os.path.join(root, fname), services_dir)
            top = relative.split(os.sep, maxsplit=1)[0]
            names.add(top[:-3] if top.endswith(".py") else top)
    return names


def _ledger_imports_in_source(source: str) -> list[str]:
    """Return every posted-ledger import in *source* (empty == ledger-free).

    Walks the AST (``ast.walk`` visits nested nodes, so a lazy in-function import
    is seen as well as a top-level one) and collects any dotted path that contains
    a posted-ledger token.  For a ``from X import a, b`` node BOTH the module ``X``
    AND each imported name are inspected, so the common
    ``from app.services import posting_service`` shape is caught, not only the
    ``from app.services.posting_service import ...`` submodule shape.  The
    ``from app.models import Posting`` class re-export (F-1) is caught separately:
    the token denylist is module leaf names, which never match a CamelCase class,
    so the ledger class names imported off the ``app.models`` package are checked
    explicitly.  Docstring ``:func:`` cross-references are string literals, not
    import nodes, so they never appear here -- only a real import does.
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            candidates = [node.module or ""]
            candidates += [alias.name for alias in node.names]
            if node.module == "app.models":
                hits += [
                    alias.name for alias in node.names
                    if alias.name in _LEDGER_MODEL_CLASS_NAMES
                ]
        elif isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        else:
            continue
        for candidate in candidates:
            if any(token in candidate for token in _LEDGER_IMPORT_TOKENS):
                hits.append(candidate)
    return hits


class TestResolverIsLedgerFree:
    """The resolver reference reads NONE of the posted ledger -- mechanically.

    The static import fence covers the WHOLE resolver reference path:
    ``loan_loaders`` + the ``loan_resolver`` package at FILE granularity, and the
    resolver-feeding functions of the mixed ``loan_payment_service`` at FUNCTION
    granularity (its read-switch functions read the ledger by design and are held
    out) -- closing the blind spot the file-only fence had on ``load_loan_context``
    (2026-07-02 follow-up review M-1).  A runtime read fence additionally proves
    ``_resolver_balance`` produces its value with the module-qualified ledger
    readers monkeypatched to raise -- a defense-in-depth regression backstop, not
    the primary guarantee (see its docstring for what it does and does not catch).
    Two negative controls give the static fence teeth (it bites the import shapes
    it must catch, and it genuinely scopes around ``loan_payment_service``'s real
    ledger read), and a coverage guard keeps the token denylist complete so a new
    ledger reader cannot evade the fence unnoticed (review M-2).  Together they make
    it impossible for a future refactor to wire the ledger into the resolver
    reference without a red test.
    """

    def test_resolver_stack_imports_no_ledger_module(self):
        """No code the resolver reference runs through imports a posted-ledger module.

        Runs :func:`_ledger_imports_in_source` over each FILE-fenced
        resolver-support module -- ``loan_loaders``, ``loan_payment_service``, and
        the ``loan_resolver`` package, every one of them scanned WHOLE since plan
        step E1d-b closed the last exemption.  Any hit is a resolver-reference code
        unit reaching for the ledger, which would make the parallel run a tautology.
        """
        offenders = {}
        for module in _resolver_stack_modules():
            hits = _ledger_imports_in_source(inspect.getsource(module))
            if hits:
                offenders[module.__name__] = hits
        assert not offenders, (
            f"the resolver reference imported posted-ledger modules {offenders} -- "
            f"the parallel-run oracle would become a tautology (review M4 / M-1)"
        )

    def test_loan_payment_service_is_fenced_whole_and_the_fence_bites(self):
        """``loan_payment_service`` is scanned WHOLE, with no exemption left.

        Non-vacuity for the fence's simplification at plan step E1d-b.  The module
        used to be MIXED -- its resolver-feeding loaders had to stay ledger-free
        while ``confirmed_loan_view`` read the ledger by design -- so it was fenced
        at function granularity behind a hand-written allowlist (review M-1).  The
        confirmed slice seeds from the WALK now and that function is deleted, so
        the module is ledger-free whole and the allowlist is gone.  This proves the
        replacement is real and not merely quieter:

        * the module IS in the file-fenced set (so it is scanned at all);
        * its resolver-feeding loaders are genuinely in that scanned source (the
          fence has the target it always had, not an emptied one);
        * no read-switch function survives to need an exemption;
        * and the scanner BITES on that source -- a ledger import spliced into it
          is caught -- so the green result above means "ledger-free", not "not
          looking".
        """
        assert loan_payment_service in _resolver_stack_modules()
        source = inspect.getsource(loan_payment_service)
        # The resolver-feeding loaders are in scope, whole-module.
        assert "def load_loan_context" in source
        assert "def get_payment_history" in source
        assert "def prepare_payments_for_engine" in source
        # No read-switch function survives -- there is nothing left to exempt.
        assert "def confirmed_loan_view" not in source
        assert "def resolve_loan_seeded" not in source
        # Genuinely ledger-free...
        assert not _ledger_imports_in_source(source)
        # ...and the scanner would say so if it were not: splicing in the exact
        # import the deleted read switch used to carry trips the fence.
        assert _ledger_imports_in_source(
            source + "\nfrom app.services import loan_posting_service\n"
        ), "the ledger-import scanner failed to bite on a real ledger import"

    def test_ledger_import_tokens_cover_every_ledger_reader(self):
        """The token denylist catches every real posted-ledger reader module.

        The fence keys off :data:`_LEDGER_IMPORT_TOKENS`, a denylist -- so a NEW
        ledger reader with a novel name would evade it, and a resolver import of it
        would slip through (review M-2).  This discovers every ``app.services``
        module that imports a posted-ledger MODEL -- the objective, complete
        criterion for "reads the posted ledger" -- and asserts each name is matched
        by a token, so an uncovered reader fails HERE (loud), not silently in the
        resolver fence.
        """
        readers = _ledger_model_importer_names()
        assert readers, (
            "no posted-ledger reader modules discovered -- the coverage guard is "
            "vacuous (expected at least posting_reads / loan_posting_service)"
        )
        uncovered = sorted(
            name for name in readers
            if not any(token in name for token in _LEDGER_IMPORT_TOKENS)
        )
        assert not uncovered, (
            f"posted-ledger reader modules {uncovered} are not matched by any "
            f"_LEDGER_IMPORT_TOKENS substring -- a resolver import of one would "
            f"evade the fence; add a covering token (review M-2)"
        )

    def test_import_fence_flags_a_ledger_import(self):
        """Negative control: the fence detects the import shapes it must catch.

        Without this, a fence bug that silently returned ``[]`` for everything
        would leave :meth:`test_resolver_stack_imports_no_ledger_module` passing
        vacuously.  Feeds the detector synthetic source using every risky shape --
        the submodule ``from`` (``from app.services.posting_service import ...``),
        the name ``from`` (``from app.services import posting_service``), a ledger
        model submodule, the ``from app.models import Posting`` class re-export
        (F-1), and a plain ``import`` -- and asserts each is flagged, while
        genuinely ledger-free source produces no hits.  Both import-fence
        detectors are proven to close the F-1 blind spot the production W9908
        ``shekel-ledger-model-bypass`` fence closes.
        """
        flagged = _ledger_imports_in_source(
            "from app.services.posting_service import account_posting_total\n"
            "from app.services import posting_service\n"
            "from app.models.journal_entry import JournalEntry\n"
            "from app.models import Posting\n"
            "import app.services.loan_posting_service\n"
        )
        assert any("posting_service" in hit for hit in flagged)
        assert any("journal_entry" in hit for hit in flagged)
        assert any("loan_posting_service" in hit for hit in flagged)
        # The name-``from`` shape specifically -- the one an earlier draft of the
        # fence missed by inspecting only the module of the import.
        assert "posting_service" in flagged
        # F-1: the ``from app.models import Posting`` class re-export -- the shape
        # the token denylist (module leaf names) cannot match.  Detector 2 (the
        # resolver fence) now flags it explicitly.
        assert "Posting" in flagged
        # F-1 for detector 1 (the coverage-guard criterion): the class re-export
        # counts as "reads the posted ledger", and a non-ledger app.models import
        # does not (no false positive that would over-report an innocent module).
        assert _imports_a_ledger_model("from app.models import LedgerAccount\n")
        assert not _imports_a_ledger_model("from app.models import PayPeriod\n")
        # Ledger-free source produces no hits (no false positives).
        assert not _ledger_imports_in_source(
            "from app.models.loan_params import LoanParams\n"
            "from app.services.rate_period_engine import monthly_due_date\n"
            "from app.utils.balance_predicates import settled_status_ids\n"
        )

    def test_resolver_balance_reads_no_ledger_at_runtime(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """``_resolver_balance`` produces its value with the ledger readers forbidden.

        A defense-in-depth regression backstop that complements the static import
        fence -- NOT the primary guarantee.  Captures the resolver balance with the
        readers intact, monkeypatches the posted-ledger readers on the service
        module objects the code calls them through to raise, and asserts
        ``_resolver_balance`` returns the SAME value.

        Scope, stated honestly (2026-07-02 follow-up review M-3): because the
        resolver is ledger-free TODAY it never calls these readers, so passing gives
        no signal on the CURRENT code -- the static fence is what proves that.  As a
        regression guard this catches only a MODULE-QUALIFIED call
        (``posting_service.account_posting_total(...)``) to one of the listed
        symbols through the patched module object; a name-import binding
        (``from ... import account_posting_total``) or a reader not in the list would
        NOT fire here.  Those shapes are the STATIC fence's job
        (:meth:`test_resolver_stack_imports_no_ledger_module`, now path-complete over
        the resolver reference including ``loan_payment_service``), which is why this
        runtime check is a backstop rather than the load-bearing guard.

        The list shrank by two at plan step E1e: the loan balance readers
        ``confirmed_loan_balance_at`` / ``confirmed_loan_balance_map`` were DELETED
        from ``loan_posting_service``, so there is no attribute left to patch.  That
        is a strictly stronger state than fencing them -- the resolver cannot call a
        function that does not exist -- and the remaining three are the whole set of
        posted-ledger reads a resolver could still reach.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()

            # The honest resolver balance, read with the ledger readers intact.
            expected = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert expected is not None

            def _forbid_ledger_read(*_args, **_kwargs):
                raise AssertionError(
                    "the resolver read the posted ledger -- the parallel run "
                    "would be a tautology (review M4)"
                )

            for target in (
                "app.services.posting_service.account_posting_total",
                "app.services.posting_service.settled_transfer_effect",
                "app.services.posting_service.settled_transaction_effect",
            ):
                monkeypatch.setattr(target, _forbid_ledger_read)

            # The same value, now with every ledger reader fenced off -> the
            # resolver derived it without reading the ledger.
            assert _resolver_balance(loan.id, scenario_id, _AS_OF) == expected


# ---------------------------------------------------------------------------
# 7. The dated posting-window parallel run (plan 4-commit-6)
# ---------------------------------------------------------------------------


class TestReaderParallelRunAgainstResolver:
    """The dated posting window reads back == the resolver.

    Sections 1-6 pin the posted LEDGER against the resolver through the test's OWN
    unbounded ``-(sum of linked postings)`` query.  This section pins the
    ``entry_date``-BOUNDED read of those same postings -- at a point in time and
    at every period boundary -- as a THIRD derivation run in the SAME test as the
    resolver.  On an on-schedule
    payment the two must agree to the penny; off-schedule they must diverge by
    exactly the extra / short principal (the reader books the REAL principal from
    the cash, the resolver only the SCHEDULED principal).

    Non-duplicative with the ``TestPostedLoanBalanceSums`` UNIT tests: those pin
    the posted sums against hand-computed literals; this pins them against the
    resolver, an independent producer that shares none of the ledger's code path
    and never reads it -- so a ledger bug the literal happened to share is still
    caught.  The ``+$10`` interest injection (module docstring) fails every
    test here that asserts a value, exactly as it fails Sections 1-3.
    """

    def test_scalar_reader_matches_resolver_on_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """On-schedule, the reader's point-in-time balance == the resolver's.

        Paying exactly the scheduled monthly P&I is on-schedule (the loan carries
        no escrow, so cash == P&I), so the reader's real principal (cash - interest)
        equals the resolver's scheduled principal (P&I - interest) and the two
        balances -- derived by disjoint code paths from the same $100,000 anchor --
        agree to the penny.  Non-vacuity: the balance dropped below the anchor by
        exactly the real principal (scheduled P&I - round(100000 * 0.005) = P&I -
        500.00 interest), and the production reader equals the test's own
        independent linked-net query (a genuine third opinion, not the resolver).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            reader = _posted_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # Three independent producers agree: the production reader, the resolver
            # replay, and the test's own independent linked-net query.
            assert reader == resolver, f"reader {reader} != resolver {resolver}"
            assert reader == _ledger_balance(loan.id, scenario_id)
            # Non-vacuity: the balance is the $100,000 anchor less the real
            # principal (P&I - 500.00 interest), not the untouched anchor.
            assert reader == _ANCHOR_BALANCE - (scheduled_pi - Decimal("500.00"))

    def test_period_map_matches_resolver_across_the_window(
        self, app, db, seed_user, seed_periods,
    ):
        """The per-period map == the resolver at every period start (walk + tail).

        Two on-schedule payments (periods 1 and 3, due 02-01 / 03-01) net in as
        their pay periods begin.  The reader map keys each period by its START, and
        the resolver caps its replay by the SAME pay-period start
        (``rate_period_engine.replay_schedule``), so ``map[P]`` equals the resolver
        resolved as of ``P.start_date`` for every period here -- through the
        stepping-down region (periods 0-3) and the carried-flat tail (periods 4-9,
        where ``current_balance`` counts only confirmed payments, so it too carries
        flat).  Non-vacuity: the map is not constant -- it steps strictly down as
        each payment lands, then holds.

        The equivalence is exact because every anchor precedes the read window: the
        opening (clamped to period 0) and the SPLIT_LOAN true-up (2026-01-10, in
        period 0) both net in at period 0, so no period shows a pre-true-up
        balance.  The map across a MID-LIFE true-up -- where the reader keeps
        pre-true-up history while the resolver reseeds from the later anchor, a
        deliberate divergence -- is the per-period read switch's concern (plan
        Section 9), not this gate's.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            _settle(seed_user, loan, seed_periods[_P2], amount=scheduled_pi)
            db.session.commit()

            balance_map = _posted_period_map(loan.id, scenario_id, seed_periods)
            # Per-period parallel run: the map's value for each period equals the
            # resolver resolved as of that period's START (both select confirmed
            # postings / payments by pay-period start <= the date).
            for period in seed_periods:
                resolver_at_start = _resolver_balance(
                    loan.id, scenario_id, period.start_date,
                )
                assert balance_map[period.id] == resolver_at_start, (
                    f"period {period.period_index} (start {period.start_date}): "
                    f"map {balance_map[period.id]} != resolver {resolver_at_start}"
                )
            # Non-vacuity: not trivially constant -- 100,000 before any payment,
            # strictly down as P1 (period 1) then P2 (period 3) net in, then flat.
            assert balance_map[seed_periods[0].id] == _ANCHOR_BALANCE
            assert (
                balance_map[seed_periods[_P1].id]
                < balance_map[seed_periods[0].id]
            )
            assert (
                balance_map[seed_periods[_P2].id]
                < balance_map[seed_periods[_P1].id]
            )
            # The tail carries the last confirmed balance flat (no later payment).
            assert (
                balance_map[seed_periods[9].id]
                == balance_map[seed_periods[_P2].id]
            )

    def test_early_settled_payment_keeps_the_parallel_run_exact(
        self, app, db, monkeypatch, seed_user, seed_periods,
    ):
        """A payment settled before its period begins never desyncs the ledger.

        The R1 regression lock for the 2026-07-02 review's H2 ("early settle,
        then time passes"): with today re-frozen MID-window (2026-02-10), the
        P3 payment (due 04-01) settles EARLY.  Its Step-2 cash entry posts at
        settle -- and since R1 its split correction posts in the SAME moment --
        so when its period later begins, the linked ledger nets to the REAL
        principal, not the raw cash.  Pinned via the independent resolver: the
        map equals the un-seeded replay at EVERY period start, through the
        early-settled period and the carried-flat tail (on-schedule cash, so
        the two producers must agree to the penny).  On the pre-R1 ledger the
        map read LOW by the payment's full interest from P3's period start
        until the next loan write -- exactly what this parallel run catches.

        Today's displays stay untouched: the scalar reader at the frozen today
        equals the resolver at today (both exclude the not-yet-begun period).
        The full reconciliation sweep runs last -- completeness now covers the
        early-settled payment (its correction must exist at settle).
        """
        with app.app_context():
            frozen = date(2026, 2, 10)
            freeze_today(monkeypatch, frozen)
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            monthly_pi = _resolved_at(
                loan.id, scenario_id, frozen,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=monthly_pi)
            _settle(seed_user, loan, seed_periods[_P3], amount=monthly_pi)
            db.session.commit()
            # The premise: P1's period has begun by the frozen today; P3's has
            # not -- an EARLY settle.
            assert seed_periods[_P1].start_date <= frozen
            assert seed_periods[_P3].start_date > frozen

            # The parallel run holds at EVERY period start -- including P3's
            # period and the tail after it, where the pre-R1 ledger carried the
            # raw cash (no interest backout) and this assertion fails.
            balance_map = _posted_period_map(loan.id, scenario_id, seed_periods)
            for period in seed_periods:
                resolver_at_start = _resolver_balance(
                    loan.id, scenario_id, period.start_date,
                )
                assert balance_map[period.id] == resolver_at_start, (
                    f"period {period.period_index} (start {period.start_date}):"
                    f" map {balance_map[period.id]} != resolver "
                    f"{resolver_at_start}"
                )
            # Non-vacuity: the early-settled period genuinely steps the balance
            # down (the map is not trivially flat past P1).
            assert (
                balance_map[seed_periods[_P3].id]
                < balance_map[seed_periods[_P1].id]
            )

            # Today's scalar is untouched by the early settle: reader ==
            # resolver at the frozen today (both exclude the unbegun period).
            assert _posted_balance(
                loan.id, scenario_id, frozen,
            ) == _resolver_balance(loan.id, scenario_id, frozen)

            # The sweep: identities, per-entry balance, trial balance, and the
            # completeness guarantee that now covers the early settle.
            _assert_loan_reconciles(loan, scenario_id, frozen)

    def test_scalar_reader_diverges_by_the_exact_principal_delta_off_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """Off-schedule, the reader diverges from the resolver by exactly the delta.

        The reader books the REAL principal from the actual cash; the resolver
        replays only the SCHEDULED principal (it ignores the cash) and would need an
        anchor true-up to catch up.  On the $100,000 balance @ 6% (interest 500.00):
        an EXTRA $2,000 payment -> real principal 1,500, reader owes 98,500.00, LESS
        than the resolver by exactly (cash 2,000 - scheduled P&I); a SHORT $1,000
        payment -> real principal 500, reader owes 99,500.00, MORE than the resolver
        by exactly (scheduled P&I - cash 1,000).  Two loans with identical params,
        so the same scheduled P&I governs both deltas.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            extra_loan = _make_loan(seed_user, name="Extra Loan")
            short_loan = _make_loan(seed_user, name="Short Loan")
            # Identical params -> identical scheduled P&I (rate-period level
            # payment, balance-independent), so one figure governs both deltas.
            monthly_pi = _resolved_at(
                extra_loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(
                seed_user, extra_loan, seed_periods[_P1], amount=Decimal("2000.00"),
            )
            _settle(
                seed_user, short_loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            extra_reader = _posted_balance(extra_loan.id, scenario_id)
            extra_resolver = _resolver_balance(extra_loan.id, scenario_id, _AS_OF)
            short_reader = _posted_balance(short_loan.id, scenario_id)
            short_resolver = _resolver_balance(short_loan.id, scenario_id, _AS_OF)

            # Extra: real principal 2000 - 500 = 1500 -> 98,500.00, owing LESS than
            # the resolver by exactly (cash - scheduled P&I).
            assert extra_reader == Decimal("98500.00")
            assert extra_reader < extra_resolver
            assert extra_resolver - extra_reader == Decimal("2000.00") - monthly_pi
            # Short: real principal 1000 - 500 = 500 -> 99,500.00, owing MORE than
            # the resolver by exactly (scheduled P&I - cash).
            assert short_reader == Decimal("99500.00")
            assert short_reader > short_resolver
            assert short_reader - short_resolver == monthly_pi - Decimal("1000.00")

    def test_reader_includes_the_pre_trueup_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A pre-true-up payment is summed by the reader, not excluded -- == resolver.

        Genesis retires the read-switch boundary the prior draft carved out: the
        reader sums EVERY payment from origination with no post-anchor filter, so a
        payment made BEFORE the latest anchor is absorbed by the anchor correction,
        never double-counted.  With the true-up at 2026-02-15, P1 (due 02-01) is
        pre-true-up; the reader still reproduces the resolver's $100,000 anchor
        balance (which subsumes P1) exactly -- no pre-anchor pollution to exclude.

        The reader VALUE here is split-INVARIANT: a wrong interest split on the
        pre-true-up payment cancels against the true-up's ``owed_before`` on the
        one running-balance walk, so ``reader == 100000`` no matter what interest
        posted.  So the split is pinned DIRECTLY (as the Section-1 sibling
        ``test_pre_anchor_payment_is_correctly_summed_under_genesis`` does) -- else
        the ``+$10`` injection would slip past this test, which the class docstring
        claims it does not.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # True-up AFTER P1's 2026-02-01 due date, so P1 is pre-true-up.
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 15))
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()

            # Pin the split value directly (the reader value below is
            # split-invariant): the pre-true-up payment splits on the $250,000
            # origination balance -> interest round(250000 * 0.005) = 1250.00.
            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, scenario_id,
            )
            assert splits[0].interest == Decimal("1250.00")

            reader = _posted_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The pre-true-up payment is summed AND absorbed by the true-up, so the
            # reader reproduces the resolver's anchor balance to the penny.
            assert reader == resolver
            assert reader == _ANCHOR_BALANCE  # 100000.00, no pre-anchor pollution

    def test_reader_reflects_a_mid_life_true_up_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """A user balance true-up moves the reader to the verified value -- == resolver.

        After a $1,000 payment the reader owes 99,500.00.  The user reconciles the
        statement and asserts the real balance is $95,000 on 2026-03-01 -- an
        append-only true-up correction posted through the real chokepoint
        (``anchor_service.apply_loan_anchor_true_up``), not an edit.  The reader
        jumps to 95,000.00 (the true-up's ``owed_before`` absorbs the earlier
        payment) and the resolver, reseeded from the new latest anchor, agrees --
        95,000 is distinct from both the pre-true-up 99,500 and the 100,000 anchor,
        so the reader demonstrably reflects the correction.  The whole ledger still
        reconciles (the correction is a balanced linked + equity pair).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()
            # Pre-true-up: after the $1,000 payment the reader owes 99,500.00
            # (100000 - (1000 cash - 500 interest)).
            assert _posted_balance(loan.id, scenario_id) == Decimal("99500.00")

            outcome = anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("95000.00"),
                anchor_date=date(2026, 3, 1),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            reader = _posted_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The reader jumps to the verified value; the resolver reseeds to it.
            assert reader == Decimal("95000.00")
            assert reader == resolver
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_reader_bounds_confirmed_postings_at_the_year_boundary(
        self, app, db, bare_user,
    ):
        """The reader's settled-date bound separates December from January.

        A pay period straddling 2025-12-31 (start 2025-12-25) holds a December
        payment settled on its 12-25 start; a later period (start 2026-01-22) holds
        a January one settled on its 01-22 start.  The reader bounds by each
        payment's SETTLED date (step C2), so as of 2025-12-31 it counts the
        December payment (settled 12-25 <= 12-31) but NOT the January one (settled
        01-22 > 12-31) -- matching the resolver, which caps its replay at the same
        boundary.  As of a later date it counts both.  This proves the date bound
        (and the resolver parallel run) hold across a calendar-year rollover, the
        foundation the year-end / tax surfaces (plan 3.6 / commit 10) build on.

        Uses ``bare_user`` via ``_seed_boundary_loan``: ``seed_periods`` locks its
        owner to 2026 and ``generate_pay_periods`` rejects backfilling earlier
        periods, so a boundary-straddling window needs a periodless user.
        """
        with app.app_context():
            loan, ctx, checking, periods = _seed_boundary_loan(bare_user)
            scenario_id = ctx["scenario"].id
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            # periods[0] (2025-12-25 .. 2026-01-07) straddles 12-31; periods[2]
            # (2026-01-22 .. 2026-02-04, due 02-01) is a distinct January month.
            create_settled_transfer(
                ctx, db.session, checking, loan, periods[0], amount=scheduled_pi,
                settled_on=periods[0].start_date,
            )
            create_settled_transfer(
                ctx, db.session, checking, loan, periods[2], amount=scheduled_pi,
                settled_on=periods[2].start_date,
            )
            db.session.commit()

            year_end = date(2025, 12, 31)
            reader_dec = _posted_balance(loan.id, scenario_id, year_end)
            # As of Dec 31: only the December (straddling) payment has netted in --
            # 100,000 less its real principal (scheduled P&I - 500.00 interest).
            assert reader_dec == _resolver_balance(loan.id, scenario_id, year_end)
            assert reader_dec == Decimal("100000.00") - (
                scheduled_pi - Decimal("500.00")
            )
            # As of after both periods: both payments have netted in, still ==
            # resolver, and strictly below the Dec-31 balance (January lowered it).
            reader_both = _posted_balance(loan.id, scenario_id, _AS_OF)
            assert reader_both == _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert reader_both < reader_dec
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_reader_is_scenario_scoped_and_none_when_unopened(
        self, app, db, seed_user, seed_periods,
    ):
        """The reader reads each scenario's own balance, and None where unopened.

        On-schedule payments net independently per scenario: the baseline gets one
        (balance 100,000 - one principal) and the what-if two (100,000 - two
        principals), each reading its OWN balance == its OWN resolver, the what-if's
        second payment lowering only the what-if (neither leaks into the other).  A
        THIRD scenario the loan was never opened into has no OPENING posting, so the
        reader returns ``None`` -- routing a read-switch caller to needs-setup,
        never a misleading $0 or another scenario's balance (the M2 latent
        multi-scenario guard, plan Section 4).
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            unopened = Scenario(
                user_id=seed_user["user"].id, name="Unopened", is_baseline=False,
            )
            db.session.add_all([whatif, unopened])
            db.session.commit()

            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, baseline.id, _AS_OF,
            ).state.monthly_payment
            # Baseline: one on-schedule payment.  What-if: two (a second period).
            _settle(
                seed_user, loan, seed_periods[_P1], amount=scheduled_pi,
                scenario=baseline,
            )
            _settle(
                seed_user, loan, seed_periods[_P1], amount=scheduled_pi,
                scenario=whatif,
            )
            _settle(
                seed_user, loan, seed_periods[_P2], amount=scheduled_pi,
                scenario=whatif,
            )
            db.session.commit()

            baseline_reader = _posted_balance(loan.id, baseline.id)
            whatif_reader = _posted_balance(loan.id, whatif.id)
            # Each scenario reads its OWN balance == its own resolver.
            assert baseline_reader == _resolver_balance(
                loan.id, baseline.id, _AS_OF,
            )
            assert whatif_reader == _resolver_balance(loan.id, whatif.id, _AS_OF)
            # Isolation: the what-if's SECOND payment lowers only the what-if; the
            # baseline still reflects just its one payment (neither leaks).
            assert whatif_reader < baseline_reader
            assert baseline_reader == _ANCHOR_BALANCE - (
                scheduled_pi - Decimal("500.00")
            )
            # A scenario the loan was never opened into -> None (needs-setup),
            # never the baseline's balance or a bare $0.  Both readers agree.
            assert posted_loan_balance_at(
                loan.id, unopened.id, _AS_OF,
            ) is None
            assert posted_loan_balance_map(
                loan.id, unopened.id, seed_periods,
            ) is None
            _assert_loan_reconciles(loan, baseline.id, _AS_OF)
            _assert_loan_reconciles(loan, whatif.id, _AS_OF)

    def test_biweekly_due_month_collision_reconciles_and_only_row_dates_differ(
        self, app, db, seed_user, seed_periods,
    ):
        """Two payments in one due month: balance AND per-period attribution agree.

        A biweekly cadence sometimes lands two monthly due dates in one calendar
        month.  For display the resolver's replay REDISTRIBUTES the second to the
        next month (``loan_payment_service._redistribute_to_distinct_months``, a
        resolver-only display fix, because the MONTHLY engine needs one payment
        per due month); the genesis reader keeps every payment at its true due
        date.  The only surviving difference is therefore the display ROW DATES.

        The per-period BALANCE divergence this test used to pin (review M7 /
        Step-4 note M2) is now CLOSED.  It existed because redistribution
        overwrote the shifted payment's ``payment_date`` -- the pay period the
        cash actually moved in -- with the invented next-month DUE date, which
        the replay then read as a pay-period start.  Its "has this period begun?"
        cap became ``2026-03-01 <= 2026-01-30`` (false), so the replay dropped a
        payment that had genuinely been made and the resolver read HIGHER than the
        ledger at period 2.  Redistribution now shifts only ``due_date`` and
        carries ``payment_date`` through untouched, so both producers bucket every
        paydown into its TRUE pay period and the per-period maps agree.

        ``seed_periods[1]`` (starts 2026-01-16) and ``seed_periods[2]`` (starts
        2026-01-30) both have monthly due date 2026-02-01 (payment_day=1) -- a
        February collision.  Both are paid on-schedule (cash == the scheduled
        P&I), and no rate change spans the shifted month, so the resolver's
        scheduled-principal walk and the reader's real-principal walk stay locked
        step-for-step and the scalar balances agree to the penny.  The reader then
        dates BOTH rows 2026-02-01 (the true due date), where the resolver, having
        shifted the second payment's display slot, dates its rows 2026-02-01 and
        2026-03-01 -- the one legitimate, display-only disagreement that remains.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="Collision Loan")
            params = loan_loaders.load_loan_params(loan.id)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            # The collision is real: both payments' true monthly due date is 02-01.
            assert monthly_due_date(
                seed_periods[1].start_date, params.payment_day,
            ) == date(2026, 2, 1)
            assert monthly_due_date(
                seed_periods[2].start_date, params.payment_day,
            ) == date(2026, 2, 1)

            _settle(seed_user, loan, seed_periods[1], amount=scheduled_pi)
            _settle(seed_user, loan, seed_periods[2], amount=scheduled_pi)
            db.session.commit()

            # The balance reconciles three ways despite the collision.
            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            reader = _posted_balance(loan.id, scenario_id)
            assert ledger == resolver == reader
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

            # Attribution, DISPLAY rows: the confirmed view keeps BOTH rows at the
            # true February due date; the resolver replay redistributes the second
            # to March (inlined -- the row-date lists ARE the assertion, not
            # locals).
            assert [
                row.payment_date
                for row in _confirmed_rows_at(loan.id, scenario_id, _AS_OF)
            ] == [date(2026, 2, 1), date(2026, 2, 1)]
            ctx = loan_payment_service.load_loan_context(
                loan.id, scenario_id, params,
            )
            replay_state = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    params, loan_loaders.load_loan_anchor_facts(params),
                    ctx.payments, ctx.rate_changes,
                ),
                _AS_OF,
            )
            assert [
                row.payment_date
                for row in replay_state.schedule if row.is_confirmed
            ] == [date(2026, 2, 1), date(2026, 3, 1)]

            # Attribution, LEDGER per-period buckets (the branch's namesake): the
            # reader's per-period map reflects BOTH paydowns by period 2, so it
            # equals the final balance and steps strictly below period 1.  The
            # resolver's per-period view now AGREES: redistribution shifts only a
            # payment's DUE date, never its ``payment_date`` (the pay period the
            # cash actually moved in), so payment 2 still clears the replay's
            # "has this period begun?" cap at period 2's start.  Overwriting
            # ``payment_date`` with the invented March due date (the pre-fix
            # behaviour) made that cap read ``2026-03-01 <= 2026-01-30`` -- false
            # -- so the replay DROPPED a payment that had genuinely been made,
            # and the resolver read HIGHER than the ledger.  That was the
            # per-period divergence (review M7 / Step-4 M2); it is now CLOSED.
            balance_map = _posted_period_map(loan.id, scenario_id, seed_periods)
            assert balance_map[seed_periods[2].id] == reader
            assert balance_map[seed_periods[2].id] < balance_map[seed_periods[1].id]
            assert balance_map[seed_periods[2].id] == _resolver_balance(
                loan.id, scenario_id, seed_periods[2].start_date,
            )


class TestReadSwitchProductionPath:
    """The production loan read path returns the LEDGER-true balance (the flip).

    Every displayed loan balance flows through the ``balance_at`` seam scalar
    (the fold over the loan's recorded events; the seeded resolver carried a
    ledger-fed ``current_balance`` until plan step D2a deleted the field).
    Before the read switch the displayed balance replayed the SCHEDULED payment
    from the anchor and dropped the cash, so off-schedule it disagreed with the
    posted ledger (that disagreement is what the classes above pin, via the
    un-seeded ``_resolver_balance``).  Now the production balance EQUALS the
    ledger / reader and DIVERGES from the un-seeded schedule replay by exactly
    the extra / short principal.

    This is the read switch itself, pinned end-to-end at the service the surfaces
    call.  It is the deliberate complement of ``_resolver_balance``: that helper
    stays on the un-seeded replay to keep the oracle's parallel run honest; this
    class proves the SEEDED production path moved onto the ledger.
    """

    def test_production_path_reads_the_ledger_off_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """The seam scalar == the ledger / reader, NOT the schedule replay.

        Two identical $100,000 @ 6% loans (interest 500.00, so one scheduled P&I
        governs both deltas).  An EXTRA $2,000 payment books real principal 1,500
        -> the production path owes 98,500.00, equal to the reader and the
        independent linked-net query, and LESS than the un-seeded replay by exactly
        (cash 2,000 - scheduled P&I).  A SHORT $1,000 payment books real principal
        500 -> 99,500.00, MORE than the replay by exactly (scheduled P&I - cash
        1,000).  The strict divergence from the replay is what makes this
        non-vacuous: before the flip the production path WAS the replay, so it
        would have equalled it here.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            extra_loan = _make_loan(seed_user, name="Extra Prod Loan")
            short_loan = _make_loan(seed_user, name="Short Prod Loan")
            # Identical params -> one scheduled P&I (balance-independent) governs
            # both deltas; reading it does not perturb the balance being tested.
            monthly_pi = _resolved_at(
                extra_loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(
                seed_user, extra_loan, seed_periods[_P1], amount=Decimal("2000.00"),
            )
            _settle(
                seed_user, short_loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            # The production read path every displayed loan balance flows
            # through: the balance_at seam scalar (the fold; the seeded
            # resolver's balance field died at plan step D2a).
            bctx = BalanceContext(
                user_id=extra_loan.user_id, scenario=seed_user["scenario"],
                as_of=_AS_OF,
            )
            extra_production = balance_at.balance_at(extra_loan, bctx, _AS_OF)
            short_production = balance_at.balance_at(short_loan, bctx, _AS_OF)

            # The flip: production == ledger == reader (the hand-computed balances).
            assert extra_production == Decimal("98500.00")
            assert extra_production == _ledger_balance(extra_loan.id, scenario_id)
            assert extra_production == _posted_balance(extra_loan.id, scenario_id)
            assert short_production == Decimal("99500.00")
            assert short_production == _ledger_balance(short_loan.id, scenario_id)
            assert short_production == _posted_balance(short_loan.id, scenario_id)

            # Non-vacuous: production is NOT the un-seeded schedule replay -- it
            # diverges by exactly the extra / short principal the replay drops.
            extra_replay = _resolver_balance(extra_loan.id, scenario_id, _AS_OF)
            short_replay = _resolver_balance(short_loan.id, scenario_id, _AS_OF)
            assert extra_production < extra_replay
            assert extra_replay - extra_production == Decimal("2000.00") - monthly_pi
            assert short_production > short_replay
            assert short_production - short_replay == monthly_pi - Decimal("1000.00")

    def test_production_schedule_confirmed_rows_are_the_ledger_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """The production schedule's confirmed rows ARE the ledger history (C11).

        The history read switch: ``resolve_loan_bundle``'s schedule -- the
        amortization table, the chart's history prefix, the date-precise
        ``balance_at`` walk -- carries the RECORD-derived confirmed rows, equal to
        the seam's own ``confirmed_view`` rows verbatim (the walk-built view since
        plan step E1d-b; the posted-ledger reader it replaced before that).  Off-schedule (an
        EXTRA $2,000 payment on the $100,000 balance: interest 500.00, real
        principal 1,500.00, balance 98,500.00) the row shows the ACTUAL
        economics, while the un-seeded replay's row still shows the SCHEDULED
        principal (period P&I - 500.00) and a higher balance -- the strict
        per-row divergence that makes the flip non-vacuous.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="History Flip Loan")
            _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("2000.00"),
            )
            db.session.commit()

            resolved = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            )
            assert resolved is not None, "configured loan must resolve"
            state = resolved.state
            confirmed_rows = [r for r in state.schedule if r.is_confirmed]
            assert confirmed_rows == _confirmed_rows_at(
                loan.id, scenario_id, _AS_OF,
            )
            (row,) = confirmed_rows
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("1500.00")
            assert row.remaining_balance == Decimal("98500.00")

            # The un-seeded replay's row is the SCHEDULED split -- strictly
            # different, so "production rows == ledger rows" is non-vacuous.
            params = loan_loaders.load_loan_params(loan.id)
            ctx = loan_payment_service.load_loan_context(
                loan.id, scenario_id, params,
            )
            replay_state = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    params, loan_loaders.load_loan_anchor_facts(params),
                    ctx.payments, ctx.rate_changes,
                ),
                _AS_OF,
            )
            (replay_row,) = [
                r for r in replay_state.schedule if r.is_confirmed
            ]
            assert replay_row.principal != row.principal
            assert replay_row.remaining_balance > row.remaining_balance

    def test_production_path_matches_replay_on_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """On-schedule the seed is invisible: production == replay == ledger.

        Paying exactly the scheduled P&I books the same real principal the replay
        schedules, so seeding the confirmed balance changes nothing: the production
        path, the un-seeded replay, and the ledger all agree.  This guards against
        the seed perturbing the on-schedule case (a regression that would show a
        loan card drifting from its schedule even when the user pays exactly).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            bctx = BalanceContext(
                user_id=loan.user_id, scenario=seed_user["scenario"],
                as_of=_AS_OF,
            )
            production = balance_at.balance_at(loan, bctx, _AS_OF)
            assert production == _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert production == _ledger_balance(loan.id, scenario_id)


class TestLatePaidPaymentDating:
    """A payment settled LATE is credited to the installment it actually paid.

    The real-world case the suite was blind to, and the root defect it hid.  A
    monthly loan payment is due on the loan's ``payment_day``, but the operator
    marks it paid whenever they actually pay it -- and over a weekend or a
    holiday that is routinely a few days late, which lands the transaction in the
    NEXT biweekly pay period.  Its pay period then no longer contains its due
    date.

    The engine used to RE-DERIVE each payment's due date from its pay period
    (``monthly_due_date`` of the period start).  For a late payment that returns
    the FOLLOWING month's installment: a February payment recorded as a March
    one.  Two consequences, both pinned here:

    * the payment history / amortization table shows an installment the operator
      never paid (the reported symptom); and
    * the CONFIRMED schedule row is stamped with a date that can be in the
      FUTURE, so every date-basis balance walk that reads the schedule silently
      disagrees with the ledger, which books by pay period.

    The engine now reads the payment's OWN stored ``due_date``
    (``loan_loaders.loan_payment_due_date``), so the pay period governs only the
    CASH (which period booked it) and the due date governs only the INSTALLMENT.
    """

    def _settle_late(self, seed_user, loan, db, seed_periods):
        """Settle a payment due 2026-02-01 LATE, into the 2026-02-13 period.

        ``seed_periods[2]`` (2026-01-30 .. 02-12) is the period that CONTAINS the
        2026-02-01 due date.  Paying late lands the payment in
        ``seed_periods[3]`` (2026-02-13 .. 02-26) instead -- a period that does
        not contain its own due date, exactly the production shape.  The stored
        ``due_date`` still records the installment it paid.
        """
        payment = _settle(seed_user, loan, seed_periods[3])
        shadow = (
            db.session.query(Transaction)
            .filter_by(transfer_id=payment.id, account_id=loan.id)
            .one()
        )
        shadow.due_date = date(2026, 2, 1)
        db.session.flush()
        loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
        db.session.commit()
        return shadow

    def test_late_payment_is_dated_at_the_installment_it_paid(
        self, app, db, seed_user, seed_periods,
    ):
        """The history row reads 2026-02-01, not the re-derived 2026-03-01.

        Non-vacuous: the pre-fix derivation is
        ``monthly_due_date(seed_periods[3].start_date, payment_day=1)`` ==
        2026-03-01, asserted here to be a DIFFERENT date -- so a producer that
        still derived from the pay period would report March and fail.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="Late Paid Loan")
            self._settle_late(seed_user, loan, db, seed_periods)

            # The re-derivation the engine used to perform lands a month later.
            assert monthly_due_date(seed_periods[3].start_date, 1) == date(
                2026, 3, 1,
            )

            rows = _confirmed_rows_at(loan.id, scenario_id, _AS_OF)
            assert [row.payment_date for row in rows] == [date(2026, 2, 1)]

            history = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert [row.due_date for row in history] == [date(2026, 2, 1)]

    def test_every_surface_equals_the_ledger_for_a_late_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """Ledger, loan card, seam scalar and per-period map all agree at today.

        The cross-producer lock for the late-paid shape.  Before the fix the
        confirmed row carried a FUTURE date (2026-03-01 > _AS_OF), so the seam's
        date-basis scalar walked past it and read the loan's PRE-payment balance
        while the ledger had already booked the paydown -- the divergence that
        surfaced as a mortgage that appeared to GROW.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="Late Paid Surfaces")
            self._settle_late(seed_user, loan, db, seed_periods)
            scenario = seed_user["scenario"]

            ledger = _ledger_balance(loan.id, scenario_id)
            # The dated posting window (entry_date <= as_of) --
            # the fourth independent access path beside the raw posting sum,
            # the seam scalar, and the seam map (the seeded resolver window
            # died with LoanState.current_balance at plan step D2a).
            reader = _posted_balance(loan.id, scenario_id)
            bctx = BalanceContext(
                user_id=loan.user_id, scenario=scenario, as_of=_AS_OF,
            )
            scalar = balance_at.balance_at(loan, bctx, _AS_OF)
            period_map = balance_at.build_maps(
                [loan], bctx, seed_periods,
            )[loan.id]
            current_period = next(
                p for p in seed_periods
                if p.start_date <= _AS_OF <= p.end_date
            )

            # The ARITHMETIC, pinned first: agreement alone is not enough, since
            # a bug that moved all four producers TOGETHER (they now share one
            # ledger source) would keep them equal while being uniformly wrong.
            #   anchor 100,000.00 @ 6% -> interest = 100000 * 0.06/12 = 500.00
            #   principal = cash 1000.00 - 500.00 = 500.00
            #   balance   = 100000.00 - 500.00 = 99,500.00
            assert ledger == Decimal("99500.00")
            assert reader == ledger
            assert scalar == ledger
            assert period_map[current_period.id] == ledger
            # The paydown is real: the balance is strictly below the anchor.
            assert ledger < _ANCHOR_BALANCE

    def test_projected_liability_map_never_increases(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan's per-period balance is monotonically non-increasing.

        The general invariant that makes this whole bug class impossible to ship
        again: a loan amortizes, so it cannot GROW.  This is the defect the
        operator saw plotted -- a mortgage that rose $276.72 for one period and
        then fell back -- and it needed BOTH halves of the bug to appear, so this
        reproduces both.

        Geometry (payment_day=5, today=2026-05-15).  ``_settle_late`` alone is not
        enough: the rise only shows when a mis-dated CONFIRMED row lands in the
        FUTURE and a projected period ENDS BEFORE it, so the projection falls back
        to an EARLIER confirmed row carrying a higher balance.

        * An on-time payment for the 2026-04-05 installment, in the period that
          contains it.
        * A LATE payment for the 2026-05-05 installment, settled into the
          2026-05-08 period.  The pre-fix derivation
          (``monthly_due_date(2026-05-08, 5)``) dates it 2026-06-05 -- the FUTURE.
        * The ledger books it now (its period has begun), so the CURRENT period
          reads the post-payment balance...
        * ...while the next period (2026-05-22 .. 06-04) ENDS BEFORE 2026-06-05,
          so the pre-fix projection walked back to the April row and handed that
          period the loan's OLDER, higher balance.  The plotted liability rose.
        """
        with app.app_context():
            loan = _make_loan(seed_user, name="Monotonic Loan", payment_day=5)

            # Two extra periods so a FUTURE period exists that ends before the
            # mis-derived 2026-06-05 date -- the window the rise appears in.
            future_periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=date(2026, 5, 22), num_periods=2, cadence_days=14,
            )
            db.session.flush()
            periods = list(seed_periods) + list(future_periods)

            # On-time: the 2026-04-05 installment, in the period containing it.
            on_time_period = next(
                p for p in periods
                if p.start_date <= date(2026, 4, 5) <= p.end_date
            )
            _settle(seed_user, loan, on_time_period)

            # Late: the 2026-05-05 installment, settled into the 05-08 period.
            late_period = next(
                p for p in periods if p.start_date == date(2026, 5, 8)
            )
            late = _settle(seed_user, loan, late_period)
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=late.id, account_id=loan.id)
                .one()
            )
            shadow.due_date = date(2026, 5, 5)
            db.session.flush()
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            # The pre-fix derivation dates the late payment in the FUTURE, and
            # the next period ends BEFORE it -- the window the rise appeared in.
            assert monthly_due_date(date(2026, 5, 8), 5) == date(2026, 6, 5)
            assert date(2026, 6, 5) > _AS_OF
            next_period = next(
                p for p in periods if p.start_date == date(2026, 5, 22)
            )
            assert next_period.end_date < date(2026, 6, 5)

            period_map = balance_at.build_maps(
                [loan], BalanceContext.build(seed_user["user"].id), periods,
            )[loan.id]

            series = [period_map[p.id] for p in periods]
            rises = [
                (periods[i].start_date, series[i - 1], series[i])
                for i in range(1, len(series))
                if series[i] > series[i - 1]
            ]
            assert not rises, f"loan balance ROSE across periods: {rises}"


class TestTrueUpAfterLastPaymentIsRead:
    """A true-up dated after the last payment reaches every balance surface.

    A true-up is a BALANCE event, not a payment: the ledger books it, but it has
    no amortization-schedule row.  So a schedule walk -- which sees payment rows
    only -- reports the balance as of the last PAYMENT and silently misses any
    true-up after it.  On production data that read a real loan $3.94 above what
    it owed, splitting the year-end debt-progress scalar from the loan card.

    The seam's scalar now reads the genesis ledger for any date at or before
    today, so the past comes from the one producer that books every balance
    event.  This is the structural reason the past can never be re-derived from
    the schedule.
    """

    def test_scalar_reads_the_ledger_not_the_stale_last_payment_row(
        self, app, db, seed_user, seed_periods,
    ):
        """balance_at == ledger, though the last schedule row predates the true-up."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            scenario = seed_user["scenario"]
            loan = _make_loan(seed_user, name="Trued Up After Payment")
            _settle(seed_user, loan, seed_periods[2])
            db.session.commit()

            # A true-up asserted AFTER that payment's due date: the ledger books
            # it; the schedule has no row for it.
            outcome = anchor_service.apply_loan_anchor_true_up(
                account=loan,
                anchor_balance=Decimal("96000.00"),
                anchor_date=date(2026, 2, 20),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            bctx = BalanceContext(
                user_id=loan.user_id, scenario=scenario, as_of=_AS_OF,
            )
            scalar = balance_at.balance_at(loan, bctx, _AS_OF)
            reader = _posted_balance(loan.id, scenario_id)
            schedule = net_worth_kernel.generate_debt_schedules(
                [loan], bctx,
            )[loan.id]

            # The true-up IS the balance; the reader and scalar both report it.
            assert ledger == Decimal("96000.00")
            assert reader == ledger
            assert scalar == ledger

            # Non-vacuity: the schedule's last CONFIRMED row still carries the
            # pre-true-up balance, so a walk over it would have read HIGHER --
            # the exact stale read this test exists to forbid.
            confirmed_rows = [
                row for row in schedule.schedule if row.is_confirmed
            ]
            assert confirmed_rows
            assert confirmed_rows[-1].remaining_balance != ledger


class TestDueDateEditReconcilesTheLedger:
    """Editing a loan payment's due_date re-derives the posted ledger.

    ``due_date`` became a POSTING INPUT with the installment-basis fix: the
    genesis write walk orders payments by it and applies its strict
    ``anchor_date < due_date`` post-anchor boundary against it, so moving it
    changes which payments an anchor SUBSUMES and therefore the POSTED balance.

    It was NOT in ``transfer_service._POSTING_RELEVANT_FIELDS``, because before
    the fix the walk derived the due date from ``pay_period_id`` (which IS in the
    set).  Left out, a due-date edit would move every live READER (the history
    rows, the payment table, the resolver's replay) while the posted ledger kept
    the old numbers -- a silent ledger-vs-resolver divergence that would persist
    until some unrelated chokepoint happened to fire.
    """

    def test_editing_due_date_reposts_the_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A due-date edit alone moves the ledger, and it stays self-consistent."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Anchor 2026-01-10; the payment's due date starts AFTER it, so the
            # payment is post-anchor and its principal is booked.
            loan = _make_loan(seed_user, name="Due Date Edit Loan")
            payment = _settle(seed_user, loan, seed_periods[2])
            db.session.commit()

            posted_before = _ledger_balance(loan.id, scenario_id)
            assert posted_before == Decimal("99500.00")   # 100,000 - 500 principal

            # Move the due date to BEFORE the anchor: the payment is now
            # subsumed by the anchor's reset, so its principal is no longer a
            # post-anchor paydown and the posted balance returns to the anchor.
            transfer_service.update_transfer(
                payment.id, seed_user["user"].id, due_date=date(2026, 1, 5),
            )
            db.session.commit()

            posted_after = _ledger_balance(loan.id, scenario_id)
            assert posted_after == _ANCHOR_BALANCE

            # And every reader agrees with the ledger -- the edit reconciled,
            # rather than leaving the posted numbers behind.
            bctx = BalanceContext(
                user_id=loan.user_id, scenario=seed_user["scenario"],
                as_of=_AS_OF,
            )
            assert balance_at.balance_at(loan, bctx, _AS_OF) == posted_after
            assert _posted_balance(loan.id, scenario_id) == posted_after
