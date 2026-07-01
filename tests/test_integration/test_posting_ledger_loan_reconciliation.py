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
     of the current balance is ``anchor_balance - sum(principal postings)`` (the
     linked-ledger net is exactly the summed real principal, Step-2 cash plus the
     Step-4 correction).  The resolver derives its balance INDEPENDENTLY -- it
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
     for a loan once corrections exist: the linked ledger nets to principal, not
     to the cash.  The loan-aware invariant is ``account_posting_total(loan) ==
     settled_transfer_effect(loan) - sum(interest + escrow + excess corrections)
     == anchor - current_balance``.  One test drives this where the cash
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

Two adversarial cases prove the oracle is not vacuous: tampering a settled
payment's ``actual_amount`` makes the loan-aware invariant FAIL (a real ledger
drift would be caught), and injecting one extra leg makes the trial balance go
non-zero (the ``= 0`` assertion is a real check, not one the per-entry trigger
makes unconditionally true).

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
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import (
    loan_payment_service,
    loan_posting_service,
    posting_service,
)
from app.utils.balance_predicates import settled_status_ids
from tests._test_helpers import (
    create_loan_with_trueup,
    create_settled_transfer,
    find_loan_ledger_account,
    freeze_today,
    ledger_net,
    load_migration_module,
    loan_correction_entries,
    loan_income_shadow,
)

# A 6% loan on a $100,000 anchor accrues exactly $500.00 the first month
# (100000 * 0.06 / 12); the round numbers keep every split hand-computable.  The
# trueup anchor ($100,000) is deliberately distinct from origination ($250,000),
# so a correct interest figure also proves the walk seeds from the trueup anchor.
_ANCHOR_BALANCE = Decimal("100000.00")
_RATE = Decimal("0.06000")
_ANCHOR_DATE = date(2026, 1, 10)
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)

# The frozen as-of: after every payment period used, so each settled payment is
# historical (eligible) and the resolver / wiring see the same today.
_AS_OF = date(2026, 5, 15)

# seed_periods indices whose monthly due date (payment_day=1) lands in a DISTINCT
# month after the anchor -- so the resolver's biweekly-collision redistribution
# never shifts a payment's date and the parallel run is exact: P1 start
# 2026-01-16 -> due 02-01; P2 start 2026-02-13 -> due 03-01; P3 start 2026-03-13
# -> due 04-01.
_P1, _P2, _P3 = 1, 3, 5

# The Commit-6 boundary migration, loaded so its idempotent raw-SQL teardown
# (``_remove_loan_payment_postings``) can reproduce the pre-wiring historical
# state directly -- the same pattern the backfill suite uses.
_BACKFILL_MIGRATION = load_migration_module(
    "e2a9f1c7b4d6_backfill_loan_payment_split_postings.py"
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


def _settled_income_shadows_after(
    loan_account_id: int, scenario_id: int, as_of: date
) -> int:
    """Count a loan's settled income shadows whose pay period begins after as_of.

    A future-dated settled payment is a projection the confirmed ledger must not
    silently absorb (plan 8.3 / the read switch); the completeness sweep asserts
    this is zero so such a row is flagged, not passed over.
    """
    return (
        _db.session.query(Transaction.id)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            Transaction.account_id == loan_account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id
            == ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
            PayPeriod.start_date > as_of,
        )
        .count()
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
    """Return the resolver's current balance for a loan (the parallel reference).

    Runs the production read path (``resolve_account_loan``) that every displayed
    loan balance flows through.  The resolver derives its balance by replaying the
    SCHEDULED payment forward from the latest anchor and discards the cash, so it
    is a genuinely independent producer -- it never reads the posted ledger.
    """
    resolved = loan_payment_service.resolve_account_loan(
        loan_account_id, scenario_id, as_of,
    )
    assert resolved is not None, "loan is not resolvable (no LoanParams)"
    _, state = resolved
    return state.current_balance


def _ledger_balance(
    loan_account_id: int, scenario_id: int, anchor_balance: Decimal
) -> Decimal:
    """Return the ledger's view of the current balance.

    ``anchor_balance - sum(principal postings)``: the linked-ledger net is exactly
    the summed real principal paid since the anchor (Step-2 cash plus the Step-4
    correction), so subtracting it from the anchor is the ledger's current
    balance -- the quantity the read switch (plan Section 10) will display.

    ASSUMES the loan has NO PRE-ANCHOR settled payment.  Step 2 posted every
    settled transfer's cash, including one that came due before the latest anchor;
    such a payment's cash sits on the linked ledger with NO Step-4 correction
    (the split excludes it, plan 2.5), so it would over-reduce this figure.  The
    latest anchor already subsumes that history, so reconciling / netting it out
    is the read switch's "pre-anchor cleanup" (plan Section 10), out of scope for
    this write-only step.  Every fixture here settles only POST-anchor payments
    (the trueup anchor is 2026-01-10; the payment due dates are 02-01/03-01/04-01),
    so the identity holds; the parallel run must not be given a pre-anchor payment.
    """
    return anchor_balance - _independent_loan_linked_net(
        loan_account_id, scenario_id,
    )


def _assert_completeness(
    loan_account_id: int, scenario_id: int, as_of: date
) -> None:
    """Assert every eligible confirmed payment that owes a correction has one.

    Plan 8.3: for each eligible confirmed payment (the split walk's output), a
    non-zero non-principal part (interest + escrow + excess) means the loan's cash
    is partly non-principal, so a correction MUST exist; an all-principal payment
    (a zero-rate, no-escrow, no-overpay payment) legitimately carries none.  Also
    asserts no settled payment is future-dated (a read-switch concern), so such a
    row is flagged rather than silently excluded.
    """
    splits = loan_posting_service.compute_loan_payment_splits(
        loan_account_id, scenario_id, as_of,
    )
    for split in splits:
        non_principal = split.interest + split.escrow + split.excess
        entries = loan_correction_entries(_db.session, split.income_shadow.id)
        if non_principal != Decimal("0"):
            assert entries, (
                f"eligible payment shadow {split.income_shadow.id} has non-"
                f"principal {non_principal} but no correction -- an uncorrected "
                f"Step-2 cash entry"
            )
    assert _settled_income_shadows_after(
        loan_account_id, scenario_id, as_of,
    ) == 0, (
        "a settled loan payment is future-dated -- the confirmed completeness "
        "guarantee does not cover it (a read-switch concern)"
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

            ledger = _ledger_balance(loan.id, scenario_id, _ANCHOR_BALANCE)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == resolver, (
                f"on-schedule ledger {ledger} != resolver {resolver}"
            )
            # Non-vacuity: the loan paid down by the real principal, cash (the
            # scheduled P&I) minus the round(100000 * 0.005) = 500.00 interest.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == scheduled_pi - Decimal("500.00")
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

            ledger = _ledger_balance(loan.id, scenario_id, _ANCHOR_BALANCE)
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

            ledger = _ledger_balance(loan.id, scenario_id, _ANCHOR_BALANCE)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The ledger's real balance, hand-computed.
            assert ledger == Decimal("99500.00")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("500.00")  # real principal
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

            ledger = _ledger_balance(loan.id, scenario_id, _ANCHOR_BALANCE)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == Decimal("98500.00")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("1500.00")  # real principal
            # They DIVERGE, and the ledger (extra paydown) owes LESS.
            assert ledger != resolver
            assert ledger < resolver
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_pre_anchor_payment_is_a_read_switch_boundary_not_an_off_schedule_case(
        self, app, db, seed_user, seed_periods,
    ):
        """A pre-anchor payment's cash sits uncorrected -- the Section-10 boundary.

        The lower-boundary companion to the future-dated guard: Step 2 posts the
        FULL cash of EVERY settled payment, including one that came due before the
        latest anchor; the Step-4 split excludes a pre-anchor payment (the anchor
        already subsumes it, plan 2.5), so its cash sits on the loan's linked
        ledger with NO correction.  With the trueup anchor moved to 2026-02-15, P1
        (due 2026-02-01) is pre-anchor: its $1,000 cash posts, but no correction
        does, and the split walk is empty.  The resolver replays nothing (P1 is
        subsumed), so its balance is the anchor 100,000.00 -- while the NAIVE
        ``anchor - linked_net`` reads 99,000.00, LOW by exactly the pre-anchor cash.

        That divergence is the read switch's "pre-anchor cleanup" (plan Section
        10), a DIFFERENT thing from an off-schedule divergence -- so this pins that
        the parallel-run identity requires a post-anchor-only history, and that the
        reconciliation IDENTITIES (accounting facts) still hold with pre-anchor
        cash present.  A future read switch that sums the linked ledger without
        scoping past the anchor would reintroduce exactly this error.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Anchor AFTER P1's 2026-02-01 due date, so P1 is pre-anchor.
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 15))
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()
            shadow = loan_income_shadow(db.session, xfer.id, loan.id)

            # Step-2 cash posted; no Step-4 correction (pre-anchor, excluded).
            assert loan_correction_entries(db.session, shadow.id) == []
            assert loan_posting_service.compute_loan_payment_splits(
                loan.id, scenario_id, _AS_OF,
            ) == []
            assert _independent_loan_linked_net(
                loan.id, scenario_id,
            ) == Decimal("1000.00")  # the cash only
            assert _per_loan_correction_net(loan.id, scenario_id) == Decimal("0")

            # The resolver subsumes P1 into the anchor -> balance is the anchor.
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert resolver == _ANCHOR_BALANCE
            # The naive parallel-run balance is LOW by exactly the pre-anchor cash
            # -- the Section-10 gap, not an off-schedule divergence.
            naive_ledger = _ledger_balance(loan.id, scenario_id, _ANCHOR_BALANCE)
            assert naive_ledger == _ANCHOR_BALANCE - Decimal("1000.00")
            assert naive_ledger == resolver - Decimal("1000.00")
            # The reconciliation IDENTITIES still hold with pre-anchor cash present.
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)


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
        but the loan nets to the real principal +500 (the correction moved the
        $500 interest off it).  So the cash invariant PROVABLY breaks (+500 !=
        +1000), while the loan-aware superseding invariant
        (``== settled_transfer_effect - non-principal corrections``) holds.
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
            assert linked == Decimal("500.00")
            assert linked != cash_effect + txn_effect  # the cash invariant BREAKS
            # The loan-aware superseding invariant holds.
            assert non_principal == Decimal("500.00")  # the interest moved off
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
        (Expense), refund +895.00 (Asset), and the loan leg -1,000.00 -- the loan
        nets Step-2 cash 2,000 - 1,000 correction = +1,000 == principal.  Three
        per-loan ledgers are minted and the whole sweep ties (this is the only
        fixture exercising interest + escrow + refund together).
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
            # The loan nets to the real principal; the non-principal sums to 1,000.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("1000.00")
            assert _per_loan_correction_net(
                loan.id, scenario_id,
            ) == Decimal("1000.00")
            # The loan is paid off: the ledger balance is zero.
            assert _ledger_balance(
                loan.id, scenario_id, Decimal("1000.00"),
            ) == Decimal("0.00")
            # Three per-loan ledger accounts exist (interest, escrow, refund).
            assert (
                db.session.query(LedgerAccount)
                .filter_by(loan_account_id=loan.id)
                .count()
            ) == 3
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
        the same in both scenarios (interest 500, principal 500).  Scoped to the
        baseline the loan nets +500; scoped to the what-if it also nets +500 -- and
        neither picks up the other (never +1,000).  Each scenario's whole sweep
        ties on its own.
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

            # Each scenario nets +500 in isolation -- never +1,000.
            assert posting_service.account_posting_total(
                loan.id, baseline.id,
            ) == Decimal("500.00")
            assert posting_service.account_posting_total(
                loan.id, whatif.id,
            ) == Decimal("500.00")
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
            assert posting_service.account_posting_total(
                loan1.id, scenario1,
            ) == Decimal("500.00")
            assert posting_service.account_posting_total(
                loan2.id, scenario2,
            ) == Decimal("1500.00")

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
    """A ledger rebuilt by the backfill reconciles identically to the go-forward one."""

    def test_backfilled_ledger_reconciles_identically(
        self, app, db, seed_user, seed_periods,
    ):
        """Clearing then backfilling a payment's correction restores the same ledger.

        Settling posts the correction go-forward (Commit 5); the sweep ties.
        Clearing the corrections with the migration's own teardown reproduces the
        pre-wiring historical state (settled, no correction), and the historical
        backfill (``backfill_all_loan_payment_postings``, reusing the identical
        go-forward sync) re-posts them.  The linked and per-loan nets return to
        their exact go-forward values and the whole sweep reconciles again --
        backfill == go-forward, leg for leg.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            _settle(seed_user, loan, seed_periods[_P2], amount=Decimal("1000.00"))
            db.session.commit()

            forward_linked = _independent_loan_linked_net(loan.id, scenario_id)
            forward_per_loan = _per_loan_correction_net(loan.id, scenario_id)
            assert forward_linked == Decimal("1002.50")  # 500 + 502.50 principal
            assert forward_per_loan == Decimal("997.50")  # 500 + 497.50 interest
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

            # Reproduce the pre-Commit-5 state, then run the historical backfill.
            _BACKFILL_MIGRATION._remove_loan_payment_postings(db.session)
            db.session.commit()
            assert _per_loan_correction_net(loan.id, scenario_id) == Decimal("0")
            loan_posting_service.backfill_all_loan_payment_postings()
            db.session.commit()

            # The backfilled nets equal the go-forward nets, and the sweep ties.
            assert _independent_loan_linked_net(
                loan.id, scenario_id,
            ) == forward_linked
            assert _per_loan_correction_net(
                loan.id, scenario_id,
            ) == forward_per_loan
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)


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
