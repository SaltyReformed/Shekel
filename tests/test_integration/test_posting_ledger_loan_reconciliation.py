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
  7. **The genesis READER parallel run (the read switch's gate, plan 4-commit-6 /
     8.2).**  Invariants 1-6 pin the posted LEDGER against the resolver via the
     test's OWN independent ``-(sum of linked postings)`` query (``_ledger_balance``);
     this pins the PRODUCTION reader the read switch actually wires --
     ``confirmed_loan_balance_at`` (a point in time) and ``confirmed_loan_balance_map``
     (every period boundary) -- as a THIRD independent producer, proven == the
     resolver on-schedule and divergent by exactly the extra / short principal
     off-schedule, including a pre-true-up payment, a mid-life true-up, a
     calendar-year boundary, two scenarios, and the unconfigured -> ``None`` route.
     The ``TestConfirmedLoanBalanceReader`` UNIT tests pin the reader against
     hand-computed literals; this pins it against the independent resolver, so a
     reader bug a literal happened to share is still caught (the ``+$10`` injection
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
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.loan_features import RateHistory
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import (
    anchor_service,
    loan_loaders,
    loan_payment_service,
    loan_posting_service,
    loan_resolver,
    pay_period_service,
    posting_service,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.rate_period_engine import monthly_due_date
from app.utils.balance_predicates import settled_status_ids
from app.utils.money import accrue_monthly_interest
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    find_loan_ledger_account,
    freeze_today,
    ledger_net,
    load_migration_module,
    loan_correction_entries,
    loan_income_shadow,
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
    rate=_RATE, name="Oracle Loan", escrow_annual=None,
):
    """Create a resolvable amortizing loan with the suite's controlled anchor.

    ``anchor_date`` defaults to 2026-01-10 (before every payment period used, so
    every settled payment is post-anchor and eligible); a caller pins a LATER
    date to place a payment pre-anchor (the read-switch boundary case).
    """
    return create_loan_with_trueup(
        user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=_ORIGINATION_DATE, name=name,
        escrow_annual=escrow_annual,
    )


def _settle(user, loan, period, amount=Decimal("1000.00"), scenario=None):
    """Settle a Checking -> loan payment transfer through the service.

    Routes through ``create_settled_transfer`` (the sole transfer writer), which
    posts the Step-2 cash entry AND fires the Commit-5 wiring that posts the
    Step-4 correction -- so the returned payment is fully posted, exactly as
    marking it Paid produces it.
    """
    return create_settled_transfer(
        user, _db.session, user["account"], loan, period,
        amount=amount, scenario=scenario,
    )


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
    """Return the UN-SEEDED resolver's current balance -- the parallel reference.

    The oracle's genuinely independent producer: it replays the SCHEDULED payment
    (``principal = period_pi - interest``) forward from the latest anchor and
    discards the cash, so it NEVER reads the posted ledger -- the load-bearing
    property the whole parallel run rests on (module docstring invariants 1 / 7).

    Since the read switch (plan Section 8) the PRODUCTION path
    ``resolve_account_loan`` reads the ledger -- it threads the confirmed balance
    in as the ``ConfirmedLedgerView`` -- so calling it here would make the "resolver"
    read the very ledger it is meant to cross-check, collapsing the parallel run
    to a tautology.  This helper therefore builds the SAME ``LoanInputs`` but runs
    ``resolve_loan`` WITHOUT the seed, preserving the schedule-replay reference.
    The SEEDED production path is verified separately -- that it now equals the
    ledger off-schedule is the read switch, pinned by
    ``TestReadSwitchProductionPath``.
    """
    params = loan_loaders.load_loan_params(loan_account_id)
    assert params is not None, "loan is not resolvable (no LoanParams)"
    anchor_facts = loan_loaders.load_loan_anchor_facts(params)
    ctx = loan_payment_service.load_loan_context(
        loan_account_id, scenario_id, params,
    )
    state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            params, anchor_facts, ctx.payments, ctx.rate_changes,
        ),
        as_of,
    )
    return state.current_balance


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


def _reader_balance(
    loan_account_id: int, scenario_id: int, as_of: date = _AS_OF,
) -> Decimal:
    """Return the genesis reader's confirmed balance -- the read switch's producer.

    Runs the PRODUCTION scalar reader
    (:func:`app.services.loan_posting_service.confirmed_loan_balance_at`) the read
    switch (plan Section 8) turns every displayed loan balance onto, so the
    parallel run pits the exact function that will feed the balance -- not a
    stand-in -- against the resolver.  Asserts non-``None`` because every caller
    here builds a CONFIGURED loan (an opening is posted), so a ``None`` would be a
    reader defect, not an unconfigured loan (the ``None`` route is proven directly
    by its own test).
    """
    result = loan_posting_service.confirmed_loan_balance_at(
        loan_account_id, scenario_id, as_of,
    )
    assert result is not None, (
        f"reader returned None for configured loan {loan_account_id} in scenario "
        f"{scenario_id} -- no OPENING posting where one was expected"
    )
    return result


def _reader_period_map(
    loan_account_id: int, scenario_id: int, periods: list[PayPeriod],
) -> "dict[int, Decimal]":
    """Return the genesis reader's per-period balance map (the C9 producer).

    Runs the PRODUCTION per-period reader
    (:func:`app.services.loan_posting_service.confirmed_loan_balance_map`) the
    per-period read switch (plan Section 9) turns the AMORTIZING confirmed region
    onto.  Asserts non-``None`` for the same reason as :func:`_reader_balance`.
    """
    result = loan_posting_service.confirmed_loan_balance_map(
        loan_account_id, scenario_id, periods,
    )
    assert result is not None, (
        f"reader map returned None for configured loan {loan_account_id} in "
        f"scenario {scenario_id} -- no OPENING posting where one was expected"
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
    splits = loan_posting_service.compute_loan_payment_splits(
        loan_account_id, scenario_id, as_of,
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
      the readers a later step will switch balances onto satisfy it too;
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
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

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
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

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
            reader = _reader_balance(loan.id, scenario_id)
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
            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, scenario_id, _AS_OF,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")
            assert loan_correction_entries(db.session, shadow.id) != []

            # Post the opening + true-up (the read-switch corrections).
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
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
    (leg for leg), and the genesis READER the read switch consumes -- ``None``
    while no opening is posted -- reads back == the resolver to the penny once the
    backfill posts it.  The second is the plan's "the oracle detects the unposted-
    opening gap before and zero mismatches after," proven on the exact
    ``confirmed_loan_balance_at`` / ``confirmed_loan_balance_map`` producers the
    read switch (plan Sections 8-9) flips onto -- so the historical-data path is
    pinned directly, not only by transitivity through Sections 5 and 7.
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
          needs-setup, the unposted-opening GAP -- for BOTH the scalar (the C8
          producer) and the per-period map (the C9 producer), while the resolver,
          which never reads the ledger, still reports the true balance unchanged.
          A read switch flipped HERE would wrongly show this loan needs-setup.
        * AFTER the backfill both read back == the resolver to the penny.

        On-schedule (cash == scheduled P&I, no escrow), so the reader's real
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
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment
            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            # The resolver never reads the ledger, so its balance is invariant across
            # the clear / backfill -- the stable reference for both phases.  The
            # go-forward reader already matches it (the C6 gate); the contrast is the
            # point: authoritative now, None in a moment, == again after.
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert _reader_balance(loan.id, scenario_id) == resolver

            # Reproduce the pre-wiring historical state (no loan postings).
            _clear_all_loan_postings()

            # BEFORE: the unposted-opening gap.  No OPENING leg -> the reader cannot
            # produce a balance (both producers return None), yet the resolver -- which
            # never read the ledger -- is unchanged, so the gap is purely the ledger's.
            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) is None
            assert loan_posting_service.confirmed_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            ) is None
            assert _resolver_balance(loan.id, scenario_id, _AS_OF) == resolver

            loan_posting_service.backfill_all_loan_postings()
            db.session.commit()

            # AFTER: zero mismatch.  The scalar (C8 producer) and every period of the
            # map (C9 producer) read back == the resolver to the penny.
            reader = _reader_balance(loan.id, scenario_id)
            assert reader == resolver, f"reader {reader} != resolver {resolver}"
            balance_map = _reader_period_map(loan.id, scenario_id, seed_periods)
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
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

            # Inject $10 of phantom interest into the WALK's accrual only.  The
            # module binding ``_walk.accrue_monthly_interest`` is patched; the
            # resolver's ``rate_period_engine.accrue_monthly_interest`` is a
            # DISTINCT import and stays honest, so the two diverge by exactly $10.
            monkeypatch.setattr(
                "app.services.loan_posting_service._walk"
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
            reader = _reader_balance(loan.id, scenario_id)
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
# runs through its ``load_loan_context`` loader, but the module ALSO holds the read-
# switch functions that read the ledger by design (``confirmed_loan_view`` /
# ``resolve_loan_seeded`` / ``resolve_account_loan``).  A file-granularity fence
# would flag those legitimate reads, so that mixed module is fenced at FUNCTION
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
    "loan_posting_service",
    "ledger_account_service",
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

# The ``loan_payment_service`` functions permitted to read the posted ledger: the
# read-switch seam (the sole call sites of the confirmed-ledger readers).  Every
# OTHER function in that module is on, or feeds, the resolver reference and must
# stay ledger-free -- so the function-granularity fence holds these out and scans
# the rest.  A newly added function defaults into the SCANNED set (the safe
# polarity): a ledger read wired into a resolver-feeding loader fails the fence.
_LEDGER_READ_SWITCH_FUNCTIONS = frozenset({
    "confirmed_loan_view",
    "resolve_loan_seeded",
    "resolve_account_loan",
})


def _resolver_stack_modules() -> list:
    """Return every FILE-fenced module the un-seeded resolver reference is built from.

    ``loan_loaders`` (the input loaders) and the whole ``loan_resolver`` package,
    its submodules discovered dynamically so a newly added one is fenced
    automatically.  These are the pure resolver-support modules
    :func:`_resolver_balance` runs through; none has any legitimate reason to read
    the posted ledger, so each is scanned whole.  The mixed ``loan_payment_service``
    is fenced separately, at function granularity, by
    :func:`_loan_payment_service_resolver_feeding_source`.
    """
    modules = [loan_loaders, loan_resolver]
    for info in pkgutil.iter_modules(loan_resolver.__path__):
        modules.append(
            importlib.import_module(f"app.services.loan_resolver.{info.name}")
        )
    return modules


def _loan_payment_service_resolver_feeding_source() -> str:
    """Return ``loan_payment_service`` source MINUS its read-switch functions.

    :func:`_resolver_balance` builds the resolver reference through
    ``loan_payment_service.load_loan_context``, so that module is on the resolver's
    path -- but it is MIXED: its read-switch functions
    (:data:`_LEDGER_READ_SWITCH_FUNCTIONS`) read the posted ledger by design, so the
    file-granularity fence cannot cover the whole module without flagging those.
    Excise exactly those functions' source (so their legitimate lazy ledger imports
    are not scanned) and return the remainder -- the module's top-level imports plus
    ``load_loan_context`` and its sibling loaders (``get_payment_history`` /
    ``compute_contractual_pi`` / ``prepare_payments_for_engine`` and their local
    helpers) -- so the AST import fence can prove THEY stay ledger-free (review M-1).
    Excising by source-segment (not by name-scan) keeps top-level imports in scope,
    so a ledger import added at module top is caught too, not only an in-function one.
    """
    module_source = inspect.getsource(loan_payment_service)
    tree = ast.parse(module_source)
    scanned = module_source
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in _LEDGER_READ_SWITCH_FUNCTIONS:
            continue
        segment = ast.get_source_segment(module_source, node)
        if segment:
            scanned = scanned.replace(segment, "")
    return scanned


def _imports_a_ledger_model(source: str) -> bool:
    """Return whether *source* imports a posted-ledger MODEL module.

    Catches every import shape that reaches ``Posting`` / ``JournalEntry`` /
    ``LedgerAccount``: ``from app.models.journal_entry import ...``,
    ``from app.models import journal_entry``, and plain
    ``import app.models.ledger_account``.  The objective test for "this module
    reads the posted ledger" the coverage guard is built on.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.module in _LEDGER_MODEL_MODULES:
                return True
            if node.module == "app.models" and any(
                alias.name in _LEDGER_MODEL_NAMES for alias in node.names
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
    ``from app.services.posting_service import ...`` submodule shape.  Docstring
    ``:func:`` cross-references are string literals, not import nodes, so they
    never appear here -- only a real import does.
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            candidates = [node.module or ""]
            candidates += [alias.name for alias in node.names]
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

        Runs :func:`_ledger_imports_in_source` over each FILE-fenced resolver-support
        module (``loan_loaders`` + the ``loan_resolver`` package) AND over
        ``loan_payment_service``'s resolver-feeding source (``load_loan_context`` and
        its sibling loaders; the read-switch functions that legitimately read the
        ledger are held out -- review M-1).  Any hit is a resolver-reference code
        unit reaching for the ledger, which would make the parallel run a tautology.
        """
        offenders = {}
        for module in _resolver_stack_modules():
            hits = _ledger_imports_in_source(inspect.getsource(module))
            if hits:
                offenders[module.__name__] = hits
        feeding_hits = _ledger_imports_in_source(
            _loan_payment_service_resolver_feeding_source()
        )
        if feeding_hits:
            offenders["app.services.loan_payment_service (resolver-feeding)"] = (
                feeding_hits
            )
        assert not offenders, (
            f"the resolver reference imported posted-ledger modules {offenders} -- "
            f"the parallel-run oracle would become a tautology (review M4 / M-1)"
        )

    def test_loan_payment_service_function_fence_is_scoped_and_bites(self):
        """The ``loan_payment_service`` function fence scans loaders, spares the reads.

        Non-vacuity for the M-1 extension.  The mixed module is fenced at function
        granularity, so this proves the scoping is correct AND that the fence has a
        genuine target (it is not passing because the module is trivially
        ledger-free or because the source extraction returned nothing):

        * the resolver-feeding loaders ARE in scope (``load_loan_context`` and its
          siblings appear in the scanned source);
        * the read-switch functions -- which read the ledger by design -- are held
          OUT (their source is excised);
        * scanning the WHOLE module (read-switch functions included) DOES surface a
          ledger import, while the resolver-feeding remainder surfaces none -- so the
          fence passes because the loaders are genuinely ledger-free, exactly around
          a real ledger read.
        """
        feeding = _loan_payment_service_resolver_feeding_source()
        # The resolver-feeding loaders are in scope.
        assert "def load_loan_context" in feeding
        assert "def get_payment_history" in feeding
        assert "def prepare_payments_for_engine" in feeding
        # The read-switch functions (permitted to read the ledger) are held out.
        assert "def confirmed_loan_view" not in feeding
        assert "def resolve_loan_seeded" not in feeding
        assert "def resolve_account_loan" not in feeding
        # The module really does read the ledger somewhere, so the fence has a
        # genuine target: the whole module trips it; the feeding remainder does not.
        assert _ledger_imports_in_source(
            inspect.getsource(loan_payment_service)
        ), "expected loan_payment_service's read-switch functions to import the ledger"
        assert not _ledger_imports_in_source(feeding)

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
        model, and a plain ``import`` -- and asserts each is flagged, while
        genuinely ledger-free source produces no hits.
        """
        flagged = _ledger_imports_in_source(
            "from app.services.posting_service import account_posting_total\n"
            "from app.services import posting_service\n"
            "from app.models.journal_entry import JournalEntry\n"
            "import app.services.loan_posting_service\n"
        )
        assert any("posting_service" in hit for hit in flagged)
        assert any("journal_entry" in hit for hit in flagged)
        assert any("loan_posting_service" in hit for hit in flagged)
        # The name-``from`` shape specifically -- the one an earlier draft of the
        # fence missed by inspecting only the module of the import.
        assert "posting_service" in flagged
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
                "app.services.loan_posting_service.confirmed_loan_balance_at",
                "app.services.loan_posting_service.confirmed_loan_balance_map",
            ):
                monkeypatch.setattr(target, _forbid_ledger_read)

            # The same value, now with every ledger reader fenced off -> the
            # resolver derived it without reading the ledger.
            assert _resolver_balance(loan.id, scenario_id, _AS_OF) == expected


# ---------------------------------------------------------------------------
# 7. The genesis reader parallel run -- the read switch's gate (plan 4-commit-6)
# ---------------------------------------------------------------------------


class TestReaderParallelRunAgainstResolver:
    """The genesis READER reads back == the resolver -- the gate before the flip.

    Sections 1-6 pin the posted LEDGER against the resolver through the test's OWN
    independent ``-(sum of linked postings)`` query.  This section pins the
    PRODUCTION reader the read switch (plan Sections 8-10) turns every displayed
    loan balance onto -- ``confirmed_loan_balance_at`` at a point in time and
    ``confirmed_loan_balance_map`` at every period boundary -- as a THIRD
    independent producer run in the SAME test as the resolver.  On an on-schedule
    payment the two must agree to the penny; off-schedule they must diverge by
    exactly the extra / short principal (the reader books the REAL principal from
    the cash, the resolver only the SCHEDULED principal).

    Non-duplicative with the ``TestConfirmedLoanBalanceReader`` UNIT tests: those
    pin the reader against hand-computed literals; this pins it against the
    resolver, an independent producer that shares none of the reader's code path
    and never reads the ledger -- so a reader bug the literal happened to share is
    still caught.  The ``+$10`` interest injection (module docstring) fails every
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
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            reader = _reader_balance(loan.id, scenario_id)
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
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            _settle(seed_user, loan, seed_periods[_P2], amount=scheduled_pi)
            db.session.commit()

            balance_map = _reader_period_map(loan.id, scenario_id, seed_periods)
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
            monthly_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, frozen,
            )[1].monthly_payment

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
            balance_map = _reader_period_map(loan.id, scenario_id, seed_periods)
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
            assert _reader_balance(
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
            monthly_pi = loan_payment_service.resolve_account_loan(
                extra_loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

            _settle(
                seed_user, extra_loan, seed_periods[_P1], amount=Decimal("2000.00"),
            )
            _settle(
                seed_user, short_loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            extra_reader = _reader_balance(extra_loan.id, scenario_id)
            extra_resolver = _resolver_balance(extra_loan.id, scenario_id, _AS_OF)
            short_reader = _reader_balance(short_loan.id, scenario_id)
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
            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, scenario_id, _AS_OF,
            )
            assert splits[0].interest == Decimal("1250.00")

            reader = _reader_balance(loan.id, scenario_id)
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
            assert _reader_balance(loan.id, scenario_id) == Decimal("99500.00")

            outcome = anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("95000.00"),
                anchor_date=date(2026, 3, 1),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            reader = _reader_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The reader jumps to the verified value; the resolver reseeds to it.
            assert reader == Decimal("95000.00")
            assert reader == resolver
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_reader_bounds_confirmed_postings_at_the_year_boundary(
        self, app, db, bare_user,
    ):
        """The reader's pay-period-start bound separates December from January.

        A pay period straddling 2025-12-31 (start 2025-12-25) holds a December
        payment; a later period (start 2026-01-22) holds a January one.  The reader
        bounds by pay-period START, so as of 2025-12-31 it counts the straddling
        period's payment (start 12-25 <= 12-31) but NOT the January one (start
        01-22 > 12-31) -- matching the resolver, which caps its replay by the same
        pay-period start.  As of a later date it counts both.  This proves the date
        bound (and the resolver parallel run) hold across a calendar-year rollover,
        the foundation the year-end / tax surfaces (plan 3.6 / commit 10) build on.

        Uses ``bare_user`` via ``_seed_boundary_loan``: ``seed_periods`` locks its
        owner to 2026 and ``generate_pay_periods`` rejects backfilling earlier
        periods, so a boundary-straddling window needs a periodless user.
        """
        with app.app_context():
            loan, ctx, checking, periods = _seed_boundary_loan(bare_user)
            scenario_id = ctx["scenario"].id
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

            # periods[0] (2025-12-25 .. 2026-01-07) straddles 12-31; periods[2]
            # (2026-01-22 .. 2026-02-04, due 02-01) is a distinct January month.
            create_settled_transfer(
                ctx, db.session, checking, loan, periods[0], amount=scheduled_pi,
            )
            create_settled_transfer(
                ctx, db.session, checking, loan, periods[2], amount=scheduled_pi,
            )
            db.session.commit()

            year_end = date(2025, 12, 31)
            reader_dec = _reader_balance(loan.id, scenario_id, year_end)
            # As of Dec 31: only the December (straddling) payment has netted in --
            # 100,000 less its real principal (scheduled P&I - 500.00 interest).
            assert reader_dec == _resolver_balance(loan.id, scenario_id, year_end)
            assert reader_dec == Decimal("100000.00") - (
                scheduled_pi - Decimal("500.00")
            )
            # As of after both periods: both payments have netted in, still ==
            # resolver, and strictly below the Dec-31 balance (January lowered it).
            reader_both = _reader_balance(loan.id, scenario_id, _AS_OF)
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
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, baseline.id, _AS_OF,
            )[1].monthly_payment
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

            baseline_reader = _reader_balance(loan.id, baseline.id)
            whatif_reader = _reader_balance(loan.id, whatif.id)
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
            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, unopened.id, _AS_OF,
            ) is None
            assert loan_posting_service.confirmed_loan_balance_map(
                loan.id, unopened.id, seed_periods,
            ) is None
            _assert_loan_reconciles(loan, baseline.id, _AS_OF)
            _assert_loan_reconciles(loan, whatif.id, _AS_OF)

    def test_biweekly_due_month_collision_reconciles_but_attribution_differs(
        self, app, db, seed_user, seed_periods,
    ):
        """Two payments in one due month: the balance reconciles, the DATING differs.

        A biweekly cadence sometimes lands two monthly due dates in one calendar
        month.  For display the resolver's replay REDISTRIBUTES the second to the
        next month (``loan_payment_service._redistribute_to_distinct_months``, a
        resolver-only fix); the genesis reader keeps every payment at its true
        due date.  This is the one place the reader and the resolver attribution
        legitimately disagree (review M7 / Step-4 note M2), never pinned until
        now.  It pins that the disagreement is DISPLAY-ONLY -- the two producers
        book the SAME running balance, so the balance reconciles three ways --
        while the row DATING differs by exactly the redistribution.

        ``seed_periods[1]`` (starts 2026-01-16) and ``seed_periods[2]`` (starts
        2026-01-30) both have monthly due date 2026-02-01 (payment_day=1) -- a
        February collision.  Both are paid on-schedule (cash == the scheduled
        P&I), and no rate change spans the shifted month, so the resolver's
        scheduled-principal walk and the reader's real-principal walk stay locked
        step-for-step and the balances agree to the penny.  The reader then dates
        BOTH rows 2026-02-01 (the true due date), where the resolver, having
        shifted the second payment, dates its rows 2026-02-01 and 2026-03-01.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="Collision Loan")
            params = loan_loaders.load_loan_params(loan.id)
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

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
            reader = _reader_balance(loan.id, scenario_id)
            assert ledger == resolver == reader
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

            # Attribution: the reader keeps BOTH rows at the true February due
            # date; the resolver replay redistributes the second to March.
            reader_rows = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            assert [row.payment_date for row in reader_rows] == [
                date(2026, 2, 1), date(2026, 2, 1),
            ]
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
            replay_dates = [
                row.payment_date
                for row in replay_state.schedule if row.is_confirmed
            ]
            assert replay_dates == [date(2026, 2, 1), date(2026, 3, 1)]


class TestReadSwitchProductionPath:
    """The production loan read path now returns the LEDGER balance (the flip).

    Every displayed loan balance flows through
    ``loan_payment_service.resolve_account_loan``.  Before the read switch it
    replayed the SCHEDULED payment from the anchor and dropped the cash, so
    off-schedule it disagreed with the posted ledger (that disagreement is what
    the classes above pin, via the un-seeded ``_resolver_balance``).  Since plan
    Section 8 it threads the genesis-ledger confirmed view in (the
    ``ConfirmedLedgerView`` bundle since C11), so its ``current_balance`` now EQUALS the ledger /
    reader and DIVERGES from the un-seeded schedule replay by exactly the extra /
    short principal.

    This is the read switch itself, pinned end-to-end at the service the surfaces
    call.  It is the deliberate complement of ``_resolver_balance``: that helper
    stays on the un-seeded replay to keep the oracle's parallel run honest; this
    class proves the SEEDED production path moved onto the ledger.
    """

    def test_production_path_reads_the_ledger_off_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """resolve_account_loan == the ledger / reader, NOT the schedule replay.

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
            monthly_pi = loan_payment_service.resolve_account_loan(
                extra_loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

            _settle(
                seed_user, extra_loan, seed_periods[_P1], amount=Decimal("2000.00"),
            )
            _settle(
                seed_user, short_loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            # The production read path every displayed loan balance flows through.
            extra_production = loan_payment_service.resolve_account_loan(
                extra_loan.id, scenario_id, _AS_OF,
            )[1].current_balance
            short_production = loan_payment_service.resolve_account_loan(
                short_loan.id, scenario_id, _AS_OF,
            )[1].current_balance

            # The flip: production == ledger == reader (the hand-computed balances).
            assert extra_production == Decimal("98500.00")
            assert extra_production == _ledger_balance(extra_loan.id, scenario_id)
            assert extra_production == _reader_balance(extra_loan.id, scenario_id)
            assert short_production == Decimal("99500.00")
            assert short_production == _ledger_balance(short_loan.id, scenario_id)
            assert short_production == _reader_balance(short_loan.id, scenario_id)

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

        The history read switch: ``resolve_account_loan``'s schedule -- the
        amortization table, the chart's history prefix, the date-precise
        ``balance_at`` walk -- now carries the LEDGER-derived confirmed rows,
        equal to ``confirmed_loan_history_rows`` verbatim.  Off-schedule (an
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

            _params, state = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )
            confirmed_rows = [r for r in state.schedule if r.is_confirmed]
            assert confirmed_rows == (
                loan_posting_service.confirmed_loan_history_rows(
                    loan.id, scenario_id, _AS_OF,
                )
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
            scheduled_pi = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            production = loan_payment_service.resolve_account_loan(
                loan.id, scenario_id, _AS_OF,
            )[1].current_balance
            assert production == _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert production == _ledger_balance(loan.id, scenario_id)
