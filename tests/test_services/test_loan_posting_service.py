"""Tests for the genesis loan posting service (Build-Order Step 4 + read switch).

:mod:`app.services.loan_posting_service` posts a loan's confirmed history into the
double-entry ledger: the per-payment principal / interest / escrow / refund split
(layered on the Step-2 cash entry), and the once-per-loan OPENING plus every user
TRUE-UP as balanced anchor corrections.  Both derive from ONE chronological
running-balance walk that seeds at origination and RESETS at each anchor.  The
payment split is wired into the transfer chokepoints (auto-posted on settle via
``transfer_service``); the anchor corrections (``sync_loan_anchor_corrections``)
and the confirmed-balance reader (``confirmed_loan_balance_at`` /
``confirmed_loan_balance_map``) are inert -- these tests drive them directly.  The
reader tests freeze ``date.today()`` because the scalar reader guards its domain
(``as_of <= today``); the ledger it reads is the same genesis ledger the split +
anchor tests above build.

The split fixtures are SYNTHETIC with HAND-COMPUTED literals: a $100,000 balance
at 6% gives a clean $500.00 first-month interest (``100000 * 0.06 / 12``), so
every expected interest / principal / escrow / refund is computed by hand and
shown in the docstring's arithmetic.  The loan's user-trueup anchor ($100,000)
deliberately differs from its origination principal ($250,000): under genesis the
walk seeds from origination and resets at the trueup, so an asserted $500 (vs the
$1,250 the origination balance would give) PROVES the reset, while a payment
placed BEFORE the trueup asserting $1,250 proves origination seeding (built-in
non-vacuity checks).

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.loan_features import EscrowComponent, RateHistory
from app.models.loan_params import LoanParams
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import (
    loan_loaders,
    loan_payment_service,
    loan_posting_service,
    loan_resolver,
    posting_service,
    transfer_service,
)
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    find_loan_ledger_account,
    freeze_today,
    insert_tracking_start_event,
    insert_trueup_event,
    ledger_accounts_for_account,
    ledger_net,
    loan_correction_entries,
    loan_income_shadow,
    SPLIT_LOAN,
)

# The shared synthetic split-loan fixture ($250,000 @ 6%, trued up to $100,000 --
# distinct so a correct interest figure proves the walk's anchor reset); see
# SPLIT_LOAN in tests/_test_helpers.py.  _P1/_P2/_P3 are the seed_periods indices
# (payment_day=1) due 02-01 / 03-01 / 04-01, in distinct months after the anchor.
(_ORIGINATION_PRINCIPAL, _ORIGINATION_DATE, _RATE, _ANCHOR_BALANCE,
 _ANCHOR_DATE, _P1, _P2, _P3) = SPLIT_LOAN
_AS_OF = date(2026, 12, 31)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _loan_params(loan):
    """Return the loan account's :class:`LoanParams` row."""
    return _db.session.query(LoanParams).filter_by(account_id=loan.id).one()


def _make_loan(
    seed_user,
    *,
    anchor_balance=_ANCHOR_BALANCE,
    anchor_date=_ANCHOR_DATE,
    rate=_RATE,
    escrow_annual=None,
    name="Split Loan",
):
    """Create an amortizing loan with a controlled user-trueup anchor.

    Delegates to the shared ``create_loan_with_trueup`` factory, pinning this
    suite's fixed origination principal / date (distinct from the anchor, so a
    correct interest figure proves the walk seeds from the trueup anchor).  A
    distinct *name* lets one owner carry more than one loan (the account name is
    unique per user).
    """
    return create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=_ORIGINATION_DATE, escrow_annual=escrow_annual,
        name=name,
    )


def _add_rate_change(loan, effective_date, rate):
    """Append a :class:`RateHistory` rate change (recasting the period)."""
    _db.session.add(RateHistory(
        account_id=loan.id, effective_date=effective_date, interest_rate=rate,
    ))
    _db.session.commit()


def _settle_payment(seed_user, loan, period, cash, actual=None):
    """Settle a Checking -> loan payment transfer; return its income shadow.

    Creates and settles the transfer through ``transfer_service`` (so the
    Step-2 cash entry auto-posts), then returns the loan-side income shadow the
    Step-4 correction books under.
    """
    xfer = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], loan, period,
        amount=cash, actual_amount=actual,
    )
    return xfer, _income_shadow(xfer.id, loan.id)


def _income_shadow(transfer_id, loan_id):
    """Return the loan-side income shadow of a transfer (shared query helper)."""
    return loan_income_shadow(_db.session, transfer_id, loan_id)


def _linked_ledger_id(account):
    """Return the linked (Asset/Liability) ledger account id for *account*."""
    return ledger_accounts_for_account(_db.session, account.id)[0].id


def _find_loan_ledger(loan_id, kind):
    """Return the per-loan ledger account of *kind*, or None (shared helper)."""
    return find_loan_ledger_account(_db.session, loan_id, kind)


def _ledger_net(ledger_id, scenario_id):
    """Return the net of a ledger account's posting legs (shared query helper)."""
    return ledger_net(_db.session, ledger_id, scenario_id)


def _genesis_entry_count(user_id):
    """Count a user's loan opening + true-up (genesis) journal entries."""
    opening = ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING)
    trueup = ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP)
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.source_kind_id.in_([opening, trueup]),
        )
        .count()
    )


def _transfer_filtered_loan_net(transfer_id, ledger_id):
    """Sum a transfer's postings on one ledger -- replicating ``_posted_net``.

    Mirrors the Step-2 cash reader ``posting_service._posted_net`` exactly
    (``JournalEntry.transfer_id == transfer_id`` on one ledger), WITHOUT calling
    the private helper, so a test can prove a Step-4 correction (which carries a
    NULL ``transfer_id``) is invisible to that reader.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.transfer_id == transfer_id,
            Posting.ledger_account_id == ledger_id,
        )
        .scalar()
    )


def _correction_entries(shadow_id):
    """Return the loan_payment corrections under a shadow (shared query helper)."""
    return loan_correction_entries(_db.session, shadow_id)


def _entry_legs(entry_id):
    """Return ``{ledger_account_id: (amount, posting_kind_id)}`` for an entry."""
    return {
        leg.ledger_account_id: (leg.amount, leg.posting_kind_id)
        for leg in _db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


# ---------------------------------------------------------------------------
# compute_loan_payment_splits -- hand-computed splits (the core)
# ---------------------------------------------------------------------------


class TestComputeLoanPaymentSplits:
    """The real split is computed from the actual cash, hand-computed literals."""

    def test_on_schedule_single_payment(self, app, db, seed_user, seed_periods):
        """One $1,000 payment: interest 500.00, principal 500.00, no escrow.

        Arithmetic: interest = round(100000 * 0.06 / 12) = 500.00; principal =
        cash - interest - escrow = 1000 - 500 - 0 = 500.00; excess = 0.  The
        500.00 interest also proves the walk seeds from the $100,000 trueup
        anchor (origination principal is $250,000, which would give $1,250).
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert len(splits) == 1
            split = splits[0]
            assert split.interest == Decimal("500.00")
            assert split.escrow == Decimal("0.00")
            assert split.principal == Decimal("500.00")
            assert split.excess == Decimal("0.00")

    def test_running_balance_across_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Three $1,000 payments accrue interest on the shrinking real balance.

        Arithmetic (6%/12 = 0.5% monthly):
          P1: interest round(100000.00*0.005)=500.00, principal 500.00,
              balance 99500.00
          P2: interest round(99500.00*0.005)=497.50, principal 502.50,
              balance 98997.50
          P3: interest round(98997.50*0.005)=494.99, principal 505.01,
              balance 98492.49
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2], seed_periods[_P3]):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("500.00"), Decimal("500.00")),
                (Decimal("497.50"), Decimal("502.50")),
                (Decimal("494.99"), Decimal("505.01")),
            ]

    def test_extra_principal_lands_in_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,500 payment pays $1,000 principal -- the extra is captured.

        Arithmetic: interest 500.00; principal = 1500 - 500 - 0 = 1000.00.  The
        resolver's contractual replay would book only the scheduled principal
        and need a true-up; the ledger captures the extra automatically.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1500.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].principal == Decimal("1000.00")
            assert splits[0].excess == Decimal("0.00")

    def test_short_payment_gives_negative_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $400 payment under-covers interest: principal -100.00, balance rises.

        Arithmetic: interest 500.00; principal = 400 - 500 - 0 = -100.00
        (negative amortization, surfaced not clamped); the next-period balance
        would be 100000 - (-100) = 100100.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("400.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].interest == Decimal("500.00")
            assert splits[0].principal == Decimal("-100.00")
            assert splits[0].excess == Decimal("0.00")

    def test_configured_escrow_is_subtracted(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,200/yr escrow component subtracts $100.00/mo from principal.

        Arithmetic: monthly escrow = round(1200 / 12) = 100.00; interest 500.00;
        principal = 1000 - 500 - 100 = 400.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user, escrow_annual=Decimal("1200.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].escrow == Decimal("100.00")
            assert splits[0].principal == Decimal("400.00")

    def test_escrow_change_is_effective_dated_not_retroactive(
        self, app, db, seed_user, seed_periods,
    ):
        """Each payment's escrow is the version in effect ON its date, and a
        LATER escrow change never re-splits an already-past payment.

        Two escrow versions: $1,200/yr ($100.00/mo) from origination until
        2026-03-01, then $2,400/yr ($200.00/mo).  P1's pay-period start
        (2026-01-16) is in the first version -> escrow 100.00; the later
        payment's start (2026-03-13) is in the second -> escrow 200.00.  Then a
        THIRD version ($3,600/yr) effective 2026-06-01 (the second closed there)
        must leave BOTH earlier splits unchanged -- proving the split is
        immutable for a past date, the whole point of effective-dating escrow
        (the pre-fix code recomputed every payment at the current escrow, so the
        third change would have retroactively moved both to $300.00).
        """
        with app.app_context():
            loan = _make_loan(seed_user)  # no escrow via the helper
            # V1: $100/mo from origination, removed 2026-03-01.
            db.session.add(EscrowComponent(
                account_id=loan.id, name="Escrow",
                annual_amount=Decimal("1200.00"),
                effective_date=_ORIGINATION_DATE, end_date=date(2026, 3, 1),
            ))
            # V2: $200/mo from 2026-03-01 (open).
            db.session.add(EscrowComponent(
                account_id=loan.id, name="Escrow",
                annual_amount=Decimal("2400.00"),
                effective_date=date(2026, 3, 1),
            ))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P3], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            # Chronological: P1 start 2026-01-16 (V1 $100); P_late start
            # 2026-03-13 (V2 $200).  Distinct escrow proves the as-of keying.
            assert [s.escrow for s in splits] == [
                Decimal("100.00"), Decimal("200.00"),
            ]

            # A future escrow change: close V2 at 2026-06-01 (flush first so the
            # active-name partial unique frees), then add a $300/mo V3.  Neither
            # past payment's date falls in V3's range, so both splits must hold.
            v2 = (
                db.session.query(EscrowComponent)
                .filter_by(account_id=loan.id, annual_amount=Decimal("2400.00"))
                .one()
            )
            v2.end_date = date(2026, 6, 1)
            db.session.flush()
            db.session.add(EscrowComponent(
                account_id=loan.id, name="Escrow",
                annual_amount=Decimal("3600.00"),
                effective_date=date(2026, 6, 1),
            ))
            db.session.commit()

            resplits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert [s.escrow for s in resplits] == [
                Decimal("100.00"), Decimal("200.00"),
            ]

    def test_payoff_overpayment_routes_excess_to_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """Overpaying a $300 balance caps principal and refunds the rest.

        Arithmetic (anchor balance 300.00 @ 6%): interest round(300*0.005)=1.50;
        principal0 = 1000 - 1.50 - 0 = 998.50 > 300 -> principal capped at
        300.00, excess = 998.50 - 300.00 = 698.50; balance closes at 0.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].interest == Decimal("1.50")
            assert splits[0].principal == Decimal("300.00")
            assert splits[0].excess == Decimal("698.50")

    def test_payment_after_payoff_is_all_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment on an already-closed loan is entirely a refund.

        Arithmetic: P1 (anchor 300 @ 6%) closes the loan (balance 0); P2's
        $500 cash accrues no interest and no escrow on the closed loan, so
        principal 0.00 and the whole 500.00 routes to refund.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("500.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert len(splits) == 2
            assert splits[1].interest == Decimal("0.00")
            assert splits[1].escrow == Decimal("0.00")
            assert splits[1].principal == Decimal("0.00")
            assert splits[1].excess == Decimal("500.00")

    def test_arm_rate_step_changes_interest(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-history rate step to 12% changes the later payment's interest.

        Arithmetic: P1 (period start 2026-01-16, governed by the 6% origination
        rate) interest 500.00, principal 500.00, balance 99500.00.  A rate
        change effective 2026-03-01 to 12% governs P2 (period start 2026-03-13):
        interest = round(99500 * 0.12 / 12) = 995.00, principal = 1000 - 995 =
        5.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _add_rate_change(loan, date(2026, 3, 1), Decimal("0.12000"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P3], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("500.00"), Decimal("500.00")),
                (Decimal("995.00"), Decimal("5.00")),
            ]

    def test_actual_amount_drives_the_split(
        self, app, db, seed_user, seed_periods,
    ):
        """The split uses effective (actual) cash -- the adversarial non-vacuity proof.

        Settling with a $1,300 ACTUAL over a $1,000 estimate must move principal
        to 1300 - 500 = 800.00 (not the estimate's 500.00); a split that read
        ``transfers.amount`` instead of the shadow's effective amount would
        fail here.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                actual=Decimal("1300.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].principal == Decimal("800.00")

    def test_pre_trueup_payment_is_split_from_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due before the trueup is split from the ORIGINATION reset.

        Genesis walks EVERY confirmed payment from origination and applies each
        anchor as a running-balance reset, so a payment due BEFORE the trueup is
        NOT excluded (the Step-4 behavior) -- it is split on the origination
        balance the walk carries until the trueup resets it.  With the trueup
        moved to 2026-04-15, period 1's payment (pay-period start 2026-01-16, due
        2026-02-01) precedes it and accrues on the $250,000 origination principal:
        interest = round(250000 * 0.06 / 12) = 1250.00;
        principal = 1000 - 1250 - 0 = -250.00 (negative amortization, surfaced).
        The 1250.00 (vs the trueup's 500.00) is what proves the origination reset.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_date=date(2026, 4, 15))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")
            assert splits[0].principal == Decimal("-250.00")

    def test_mid_life_loan_resets_at_trueup(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment before the trueup splits from origination; one after, from the trueup.

        The genesis reset proven with a payment on each side of a trueup dated
        2026-02-15 (origination $250,000 @ 6%, trueup $100,000):
          P1 (period 1, due 2026-02-01, PRE-trueup): interest
            round(250000 * 0.005) = 1250.00, principal 1000 - 1250 = -250.00.
          Reset to 100000 at the 2026-02-15 trueup.
          P2 (period 3, due 2026-03-01, POST-trueup): interest
            round(100000 * 0.005) = 500.00, principal 1000 - 500 = 500.00.
        A from-origination walk with NO reset would accrue P2 on ~250000, not
        100000 -- so the distinct 500.00 is the reset's signature.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 15))
            _settle_payment(seed_user, loan, seed_periods[_P1], Decimal("1000.00"))
            _settle_payment(seed_user, loan, seed_periods[_P2], Decimal("1000.00"))
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("1250.00"), Decimal("-250.00")),
                (Decimal("500.00"), Decimal("500.00")),
            ]

    def test_projected_payment_is_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """An unsettled (Projected) payment is a future commitment, not history."""
        with app.app_context():
            loan = _make_loan(seed_user)
            # Settle one, then un-settle it back to Projected directly.
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            db.session.query(Transaction).filter(
                Transaction.transfer_id == shadow.transfer_id,
            ).update({"status_id": ref_cache.status_id(StatusEnum.PROJECTED)})
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits == []

    def test_no_loan_params_returns_empty(self, app, db, seed_user):
        """An account with no LoanParams is not yet resolvable -- no splits."""
        with app.app_context():
            checking = seed_user["account"]  # a plain Checking, no LoanParams
            splits = loan_posting_service.compute_loan_payment_splits(
                checking.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits == []


# ---------------------------------------------------------------------------
# tracking-start opening -- a mid-life loan opens at the recent balance
# ---------------------------------------------------------------------------


class TestTrackingStartOpening:
    """A tracking-start event seeds the walk at the recent balance, not origination."""

    def test_split_and_balance_open_from_tracking_start(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A mid-life loan opens at its tracking-start balance; the payment accrues on it.

        Origination is $250,000 @ 6% (2025-01-01), but the operator started
        tracking with a $100,000 balance as of 2026-01-05.  The single $1,000
        payment therefore accrues interest on $100,000:

          * interest = round(100000 * 0.06 / 12) = 500.00 (NOT origination's
            250000 -> 1250.00, which is the pre-fix bug this pins against)
          * principal = 1000 - 500 - 0 = 500.00
          * confirmed balance opens at 100000 and amortizes to 100000 - 500 =
            99500.00 (origination's 250000 never enters the ledger)
          * Schedule-A interest for 2026 is the same 500.00.
        """
        # ``confirmed_loan_balance_at`` answers only ``as_of <= today``; freeze
        # today after the payment period so the confirmed read is in range and
        # the wiring's sync-as-of is deterministic across CI clocks.
        as_of = date(2026, 6, 30)
        freeze_today(monkeypatch, as_of)
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, name="Mid-life Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                _loan_params(loan), Decimal("100000.00"), date(2026, 1, 5),
            )
            db.session.commit()

            # Explicit paid_at in 2026 so the Schedule-A year attribution does
            # not depend on the wall clock (CI runs in any year).
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                paid_at=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, scenario_id, as_of,
            )
            assert len(splits) == 1
            split = splits[0]
            assert split.interest == Decimal("500.00")
            assert split.principal == Decimal("500.00")
            assert split.escrow == Decimal("0.00")

            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, as_of,
            ) == Decimal("99500.00")

            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("500.00")

    def test_drift_scorecard_labels_the_tracking_start_opening(
        self, app, db, seed_user,
    ):
        """The anchor drift scorecard marks the opening as a tracking-start.

        A configured mid-life loan (no payments) shows exactly one drift row: the
        tracking-start opening at its recorded balance, flagged
        ``is_tracking_start`` so the display labels it "Tracking start" rather
        than "Origination".
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, name="Mid-life Loan 2",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                _loan_params(loan), Decimal("100000.00"), date(2026, 1, 5),
            )
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            rows = loan_posting_service.loan_balance_anchor_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert len(rows) == 1
            assert rows[0].is_opening is True
            assert rows[0].is_tracking_start is True
            assert rows[0].recorded == Decimal("100000.00")
            assert rows[0].anchor_date == date(2026, 1, 5)


# ---------------------------------------------------------------------------
# sync_loan_payment_postings -- posts the balanced correction
# ---------------------------------------------------------------------------


class TestSyncLoanPaymentPostings:
    """Syncing posts one balanced correction per payment.

    The setup settles through the transfer service, which fires the go-forward
    wiring and posts the loan's opening + true-up corrections alongside the
    payment splits, so ``account_posting_total(loan)`` is the FULL genesis balance
    ``-(current balance)``.  The payment split itself is pinned by the correction
    entry and the per-loan interest / escrow / refund legs, which the opening /
    true-up corrections never touch.
    """

    def test_sync_posts_one_balanced_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """The correction is Loan -500 / Interest +500, summing to zero.

        Arithmetic: cash 1000, interest 500, principal 500.  The correction's
        loan leg (-500) nets against the Step-2 cash (+1000) to +500 of principal;
        the loan_interest ledger nets +500.00.  With the wiring's opening (-250000)
        and true-up (+150000) also posted, the loan-linked ledger nets to
        -(current balance 99500) = -99500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            entries = _correction_entries(shadow.id)
            assert len(entries) == 1
            entry = entries[0]
            assert entry.transfer_id is None
            assert entry.transaction_id == shadow.id

            loan_ledger = _linked_ledger_id(loan)
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(entry.id)
            assert legs[loan_ledger] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            # Genesis: opening (-250000) + true-up (+150000) + principal (+500)
            # = -(current balance 99500); interest ledger holds the +500.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("500.00")

    def test_sync_posts_each_payment_in_a_multi_payment_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """Three payments each get a correction; the loan nets to -(final balance).

        Arithmetic (the running-balance walk from
        ``test_running_balance_across_payments``): principals 500.00 + 502.50 +
        505.01 = 1507.51, so the three Step-2 cash legs (+3000) plus the three
        correction loan legs (-1492.49) contribute +1507.51 of principal.  With
        the opening (-250000) and true-up (+150000) the loan-linked ledger nets
        -250000 + 150000 + 1507.51 = -98492.49 == -(anchor 100000 - 1507.51) =
        -(final balance 98492.49).  A second whole-loan sync writes nothing
        (idempotent across every payment).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            shadows = []
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _, shadow = _settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                )
                shadows.append(shadow)
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert all(len(_correction_entries(s.id)) == 1 for s in shadows)
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-98492.49")

            # Idempotent across the whole loan: a re-sync adds no entries.
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert all(len(_correction_entries(s.id)) == 1 for s in shadows)

    def test_resync_is_idempotent(self, app, db, seed_user, seed_periods):
        """A second sync at the same target writes no new entry."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert len(_correction_entries(shadow.id)) == 1

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert len(_correction_entries(shadow.id)) == 1

    def test_no_escrow_loan_drops_the_escrow_leg(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan with no escrow components creates no loan_escrow ledger."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # no escrow component
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            assert _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_ESCROW,
            ) is None

    def test_payoff_posts_a_refund_leg(self, app, db, seed_user, seed_periods):
        """A payoff overpayment books a refund-receivable leg.

        Arithmetic (anchor 300 @ 6%, cash 1000): interest 1.50, principal
        300.00, excess 698.50.  The loan_refund ledger nets +698.50; the payment
        contributes +300.00 of principal (cash 1000 - correction 700).  With the
        opening (-250000) and true-up (+249700 = 250000 - 300) the loan-linked
        ledger nets -300 + 300 = 0.00 -- the loan is paid off, -(balance 0).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            refund_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_REFUND,
            )
            assert refund_ledger is not None
            assert _ledger_net(refund_ledger.id, scenario_id) == Decimal("698.50")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("0.00")

    def test_sync_never_touches_checking(
        self, app, db, seed_user, seed_periods,
    ):
        """The loan sync moves only loan ledgers -- Checking is unchanged.

        The Step-2 cash entry already moved Checking (its $1000.00 Step-5
        opening - 1000 = 0.00); the loan correction must not move it
        further, so Checking's posted total is identical before and after
        the sync.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            checking_before = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            checking_after = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            assert checking_before == Decimal("0.00")
            assert checking_after == checking_before

    def test_correction_is_invisible_to_transfer_id_reader(
        self, app, db, seed_user, seed_periods,
    ):
        """The Step-2 cash reader (transfer_id-keyed) never sees the correction.

        The CRITICAL invariant (plan Section 5): the correction carries a NULL
        ``transfer_id``, so a reader filtering ``transfer_id == xfer.id`` on the
        loan ledger sums only the Step-2 cash (+1000), NOT the correction
        (-500) -- which is what keeps the cash path's reversals correct.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer, _ = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_ledger = _linked_ledger_id(loan)
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            # The transfer-id-keyed reader sees only the cash leg -- neither the
            # payment correction nor the opening / true-up (all NULL transfer_id).
            assert _transfer_filtered_loan_net(
                xfer.id, loan_ledger,
            ) == Decimal("1000.00")
            # But the full ledger (opening -250000 + true-up +150000 + cash 1000
            # + correction -500) nets to -(current balance 99500) = -99500.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")

    def test_new_trueup_resplits_later_payments_and_keeps_earlier(
        self, app, db, seed_user, seed_periods,
    ):
        """A new trueup re-splits payments after it and KEEPS those before it.

        Genesis retires the Step-4 "pushed behind anchor" reversal: a payment
        before a new trueup is NOT reversed -- it keeps its split from the prior
        anchor, and only LATER payments re-split from the new value.

        P1 (period 1, due 02-01) and P2 (period 3, due 03-01) settle and sync
        against the 100000 @ 01-10 trueup: interest 500.00 and
        round(99500 * 0.005) = 497.50 = 997.50 total.  A NEW trueup of 90000 @
        02-15 falls BETWEEN them, so after re-sync P1 keeps its 500.00 (it is due
        before the new trueup) and P2 re-splits from 90000 to
        round(90000 * 0.005) = 450.00 -- the loan_interest ledger holds
        500 + 450 = 950.00, and P1's correction is NOT reversed (one entry, where
        Step 4 left an original-plus-reversal pair).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, p1_shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert _ledger_net(
                interest_ledger.id, scenario_id,
            ) == Decimal("997.50")

            # New trueup at 90000 on 02-15 falls between P1 (due 02-01) and P2
            # (due 03-01): it re-bases P2 but leaves P1 untouched.
            insert_trueup_event(
                _loan_params(loan), Decimal("90000.00"), date(2026, 2, 15),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            # P1's 500 kept, P2 re-split 497.50 -> 450: interest ledger 950.
            assert _ledger_net(
                interest_ledger.id, scenario_id,
            ) == Decimal("950.00")
            # P1's correction is NOT reversed -- genesis keeps pre-trueup splits.
            assert len(_correction_entries(p1_shadow.id)) == 1

    def test_correction_is_disjoint_from_the_transaction_path(
        self, app, db, seed_user, seed_periods,
    ):
        """The Step-3 transaction poster refuses a loan income shadow (no-op).

        The Step-3 reader ``_posted_net_by_account`` is source-kind-agnostic, so
        the correction's ``transaction_id`` is safe only because the Step-3 PATH
        guards ``if txn.transfer_id is not None: return None`` -- and a loan
        income shadow always has a ``transfer_id``.  This pins that guard:
        after a loan payment is settled and synced, driving the income shadow
        through ``sync_transaction_postings`` posts nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            entries_before = len(_correction_entries(shadow.id))

            result = posting_service.sync_transaction_postings(
                shadow, settled=True,
            )
            db.session.commit()

            assert result == []
            assert len(_correction_entries(shadow.id)) == entries_before

    def test_early_settled_payment_splits_at_settle(
        self, app, db, monkeypatch, seed_user, seed_periods,
    ):
        """A payment settled before its period begins posts its split immediately.

        Settlement is the confirming event (the 2026-07-02 adversarial review's
        R1, fixing H2): the Step-2 cash entry posts the moment a payment
        settles, so the split correction must post in the SAME moment or the
        loan-linked ledger holds raw cash with no interest backout from the
        payment's period start until the next loan write.  Both entries carry
        the payment's OWN pay period, so the readers' period bound still keeps
        the early payment out of every balance displayed before its period
        begins.

        Frozen today 2026-02-10: the P1 payment's period has begun, the P3
        payment's (due 04-01) has not.  Arithmetic: P1 splits interest
        100000 * 0.005 = 500.00 -> balance 99500; the early P3 payment splits
        NEXT on that running balance -- interest round(99500 * 0.005) = 497.50,
        principal 1000 - 497.50 = 502.50 -> balance 98997.50.  So:

        * the P3 correction exists AT SETTLE (no manual sync), legs
          Loan -497.50 / Interest +497.50, attributed to P3's period;
        * the scalar reader at today still reads 99500.00 (period not begun);
        * the map at P3's period reads the REAL 98997.50 -- NOT the raw-cash
          98500.00 (= 99500 - 1000) the pre-fix ledger showed (H2's
          demonstrated mis-statement, the assertion that fails on the old
          code).
        """
        with app.app_context():
            frozen = date(2026, 2, 10)
            freeze_today(monkeypatch, frozen)
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _, early_shadow = _settle_payment(
                seed_user, loan, seed_periods[_P3], Decimal("1000.00"),
            )
            db.session.commit()
            # The premise: P1's period has begun by the frozen today, P3's has
            # not (its due 04-01 payment is an EARLY settle).
            assert seed_periods[_P1].start_date <= frozen
            assert seed_periods[_P3].start_date > frozen

            # The correction posted at settle, through the transfer wiring --
            # no manual sync call -- attributed to the payment's own period.
            entries = _correction_entries(early_shadow.id)
            assert len(entries) == 1
            assert entries[0].pay_period_id == seed_periods[_P3].id
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(entries[0].id)
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("-497.50"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("497.50"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )

            # Display is untouched today: the scalar excludes the not-yet-begun
            # period (100000 - 500 = 99500.00, the P1-only balance).
            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, frozen,
            ) == Decimal("99500.00")

            # At P3's period the map shows the REAL principal drop: 99500 -
            # 502.50 = 98997.50 -- never the raw cash 99500 - 1000 = 98500.00
            # the unsplit ledger showed (H2).
            balance_map = loan_posting_service.confirmed_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            assert balance_map[seed_periods[_P3].id] == Decimal("98997.50")


# ---------------------------------------------------------------------------
# reverse + stale-correction reversal
# ---------------------------------------------------------------------------


class TestReverseLoanPaymentPostings:
    """A correction reverses cleanly before a delete, and stale ones self-heal."""

    def test_revert_and_move_reverses_into_the_original_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A reverted-and-moved payment's correction reverses into its old period.

        The loan twin of the R2 attribution rule (the 2026-07-02 review's H1
        class): one ``update_transfer`` call reverts the payment to Projected
        AND moves it to a later period -- the shadows carry the NEW period by
        the time the loan wiring reconciles.  The stale correction must
        reverse into the period it was POSTED in (P1's), never the shadow's
        new one, so the correction pair nets P1's period to zero and the new
        period holds no loan_payment entries at all.  Arithmetic: the P1
        split was interest 500.00 / principal 500.00, so the reversal legs
        are Loan +500.00 / Interest -500.00 in P1's period, and the interest
        ledger nets back to zero.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            original_period_id = seed_periods[_P1].id
            moved_to = seed_periods[_P3]

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                pay_period_id=moved_to.id,
            )
            db.session.commit()

            entries = _correction_entries(shadow.id)
            assert len(entries) == 2
            reversal = entries[-1]
            # The R2 rule: the reversal carries the ORIGINAL period -- the
            # pre-fix code stamped it with the shadow's NEW period.
            assert reversal.pay_period_id == original_period_id
            assert all(
                entry.pay_period_id == original_period_id for entry in entries
            )
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(reversal.id)
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            assert _ledger_net(interest_ledger.id, scenario_id) == (
                Decimal("0.00")
            )

    def test_reverse_zeroes_the_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """Reversing a payment's correction drops the loan ledger to opening+cash.

        After the reverse, the per-shadow loan_payment net is zero on every
        ledger, so the interest ledger nets to 0.00 and the loan-linked ledger
        holds the Step-2 cash (+1000) plus the still-posted opening + true-up
        (-100000) -- the reverse touches only the payment correction, never the
        anchor corrections -- for a net of -99000.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )

            loan_posting_service.reverse_loan_payment_postings_for_shadow(shadow)
            db.session.commit()

            # The payment correction net (cash leg + reversal) is zero on the
            # per-loan ledgers; the linked ledger keeps cash (+1000) + the
            # opening / true-up (-100000) = -99000.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99000.00")
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("0")

    def test_reverse_is_a_noop_for_an_unposted_shadow(
        self, app, db, seed_user, seed_periods,
    ):
        """Reversing a payment that carries no posted correction writes nothing.

        Commit 5 wires the split-posting into the transfer chokepoints, so a
        SETTLED loan payment auto-posts its correction; a genuinely unposted
        shadow is therefore a PROJECTED payment (never settled -> never
        synced).  Reversing it must be an idempotent no-op -- no entry written
        -- which is what the delete path relies on for a never-settled payment.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=loan.id,
                    pay_period_id=seed_periods[_P1].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("1000.00"),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                ),
            )
            db.session.commit()
            shadow = _income_shadow(xfer.id, loan.id)

            loan_posting_service.reverse_loan_payment_postings_for_shadow(shadow)
            db.session.commit()
            assert _correction_entries(shadow.id) == []

    def test_resync_reverses_an_unsettled_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment that leaves the eligible set is reversed by the next sync.

        Settle + sync (one correction; loan-linked -99500 = opening -250000 +
        true-up +150000 + principal 500), then un-settle the payment (directly,
        standing in for the revert wiring) and re-sync: the now-stale correction
        is reversed, so the loan-linked ledger drops to the opening + true-up +
        the un-reversed Step-2 cash (1000) = -99000.  (The raw un-settle leaves
        the cash entry in place; the payment-only re-sync touches neither the
        cash nor the anchor corrections.)
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")

            # Un-settle (revert) the payment directly, then re-sync.
            db.session.query(Transaction).filter(
                Transaction.transfer_id == shadow.transfer_id,
            ).update({"status_id": ref_cache.status_id(StatusEnum.PROJECTED)})
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            # The correction is reversed; the loan holds the un-reversed Step-2
            # cash (1000) plus the still-posted opening + true-up (-100000).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99000.00")


# ---------------------------------------------------------------------------
# sync_loan_anchor_corrections -- opening + true-up (the genesis read switch)
# ---------------------------------------------------------------------------


def _anchor_correction_entries(loan_id, scenario_id, source_enum):
    """Return a loan's anchor-correction entries of *source_enum* (linked-scoped).

    Finds the ``loan_opening`` / ``loan_trueup`` journal entries in *scenario_id*
    that touch the loan's linked ledger -- the way the reconcile scopes them to
    one loan (the linked ledger is per-account).
    """
    linked_id = ledger_accounts_for_account(_db.session, loan_id)[0].id
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                source_enum,
            ),
            JournalEntry.id.in_(
                _db.session.query(Posting.journal_entry_id)
                .filter(Posting.ledger_account_id == linked_id)
            ),
        )
        .all()
    )


class TestSyncLoanAnchorCorrections:
    """The opening + true-up corrections drive the linked ledger to -(owed)."""

    def test_opening_posts_original_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An origination-only loan posts the opening: linked -P0, equity +P0.

        A loan with only its origination anchor ($250,000) and no payments posts
        ONE opening entry: owed_before is 0 at origination, so the loan-linked
        ledger gets 0 - 250000 = -250000.00 and the per-loan opening-equity ledger
        its negative, +250000.00.  The linked net is then -250000, so the reader
        (which negates it) reports the full $250,000 owed.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            equity = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _ledger_net(equity.id, scenario_id) == Decimal("250000.00")
            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) == 1
            assert _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_and_trueup_drive_linked_to_negative_verified(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + true-up land the linked net on -(verified balance).

        Origination $250,000 then a trueup to $100,000 (no payments):
          opening (owed_before 0): linked 0 - 250000 = -250000.00.
          trueup (owed_before 250000, the origination balance carried to the
            trueup date): linked 250000 - 100000 = +150000.00.
        Linked net -250000 + 150000 = -100000.00 -- exactly -(the $100,000 the
        user verified), the opening + trueup reproducing the resolver's anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # origination 250000, trueup 100000
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            linked_id = ledger_accounts_for_account(db.session, loan.id)[0].id
            equity = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _ledger_net(equity.id, scenario_id) == Decimal("100000.00")

            opening = _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )
            trueup = _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )
            assert len(opening) == 1 and len(trueup) == 1
            assert _entry_legs(opening[0].id)[linked_id][0] == Decimal("-250000.00")
            assert _entry_legs(trueup[0].id)[linked_id][0] == Decimal("150000.00")

    def test_anchor_corrections_are_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """A second sync at the same state writes no new anchor entry."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            before = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) + len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            assert before == 2

            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            after = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) + len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            assert after == 2

    def test_trueup_matching_the_walk_books_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A trueup equal to the walked balance posts no true-up entry.

        Origination $250,000 and a trueup ALSO of $250,000 (no payments): at the
        trueup date owed_before is 250000, so the correction 250000 - 250000 = 0
        books nothing.  Only the opening is posted; the linked net is -250000.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("250000.00"))
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_payments_trueup_reproduce_resolver_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + payment corrections + true-up net to -(resolver balance).

        The genesis payoff: three post-trueup $1,000 payments split to principal
        500.00 + 502.50 + 505.01 = 1507.51 (the running-balance walk from the
        $100,000 trueup), so the resolver's balance is 100000 - 1507.51 =
        98492.49.  With the opening (-250000) and true-up (+150000) added, the
        loan-linked net is (1507.51 payment nets) - 100000 = -98492.49 -- exactly
        -(the resolver balance), proving the from-origination sum-of-postings
        reproduces the resolver on a trued-up loan.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-98492.49")

    def test_trueup_self_heals_when_a_pre_trueup_payment_is_added(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling a pre-trueup payment re-bases the true-up in the SAME reconcile.

        The true-up correction is owed_before - verified, and owed_before moves
        when a pre-trueup payment changes -- so the reconcile must self-heal
        (reconcile-to-target), not leave a stale snapshot.  Origination $250,000,
        trueup $100,000 @ 2026-02-15: with no payments the opening (-250000) and
        true-up (250000 - 100000 = +150000) net the linked ledger to -100000.00,
        and the per-loan opening-equity ledger holds their negatives, +100000.00.
        Settling P1 (due 02-01, PRE-trueup, cash 1000) splits it on the origination
        balance (interest round(250000 * 0.005) = 1250, principal -250) and grows
        owed_before to 250250 -- and because the settle fires the UNIFIED sync
        (:func:`sync_loan_postings`), the true-up re-bases to 250250 - 100000 =
        +150250 (a +250 linked delta) in the SAME transaction, so the linked ledger
        STAYS -100000.00 == -(verified $100,000), the pre-trueup payment fully
        absorbed; the equity ledger re-bases to 250000 - 150250 = +99750.00.  No
        stale window is ever observable through the go-forward path.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 15))
            # The opening + true-up (and their per-loan equity ledger) are posted
            # lazily on this first sync.
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            equity_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            # Opening (+250000) + true-up (-150000) on the equity ledger.
            assert _ledger_net(
                equity_ledger.id, scenario_id,
            ) == Decimal("100000.00")

            # Settle a pre-trueup payment; the unified wiring re-bases the true-up
            # in the same transaction (no manual anchor re-sync needed).
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            # Self-healed automatically: linked still -(verified 100000); the
            # equity ledger re-based to opening (+250000) + true-up (-150250).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _ledger_net(
                equity_ledger.id, scenario_id,
            ) == Decimal("99750.00")

            # A further unified sync is a no-op: no new true-up entry, balance holds.
            trueups_after_heal = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )) == trueups_after_heal
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")

    def test_payment_due_on_anchor_date_is_subsumed_by_the_reset(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due EXACTLY on the anchor date is walked before the reset, then subsumed.

        The strict ``anchor_date < monthly_due_date`` boundary means a payment due
        ON an anchor's date is PRE-anchor (subsumed).  The walk realises this by
        sorting a payment (tag 0) before an anchor (tag 1) on an equal date, so P1
        (period 1, due 2026-02-01 at payment_day=1) is split on the $250,000
        origination balance BEFORE the 2026-02-01 trueup resets to $100,000:
        interest round(250000 * 0.005) = 1250.00 lands in the interest ledger, but
        its principal is wiped by the reset -- the reader ends at the verified
        $100,000, exactly as the resolver (which does not replay P1).  A SWAPPED
        tie-break (reset first) would instead give interest 500.00 and a balance of
        99500.00, so this pins the ordering the whole trued-up-balance design rests
        on (no other fixture places a due date on an anchor date).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Trueup date EQUALS P1's 2026-02-01 due date (payment_day=1).
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 1))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            # P1 is split BEFORE the reset -> interest on the origination balance.
            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, scenario_id, _AS_OF,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")

            # With opening + true-up the reader lands on the verified 100000 --
            # P1's principal subsumed by the reset (a swapped tie-break -> -99500).
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")


# ---------------------------------------------------------------------------
# sync_loan_postings_all_scenarios -- the unified loan-global entry point
# ---------------------------------------------------------------------------


class TestUnifiedAllScenariosSync:
    """The all-scenarios sync posts a payment-less loan's opening in the baseline."""

    def test_posts_opening_for_a_payment_less_loan_in_the_baseline(
        self, app, db, seed_user,
    ):
        """A brand-new loan with no payments gets its opening posted in the baseline.

        A payment-less loan is in NO scenario's payment set
        (``_scenarios_with_loan_payments`` is empty), so the all-scenarios sync
        must add the owner's baseline -- otherwise ``create_params`` would post no
        opening for a fresh loan.  Origination $250,000, trueup $100,000: the
        opening (-250000) + true-up (+150000) net the baseline linked ledger to
        -100000.00 == -(anchor 100000), and the per-loan opening-equity ledger
        holds +100000; no payment-correction ledger is minted (there is no
        payment).  This is the ``create_params`` N1 path the chokepoint relies on.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id  # the owner's baseline
            loan = _make_loan(seed_user)  # no payments settled

            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            # Opening (-250000) + true-up (+150000), no payment.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            equity_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert equity_ledger is not None
            assert _ledger_net(
                equity_ledger.id, scenario_id,
            ) == Decimal("100000.00")
            # No payment-correction ledger is minted without a payment.
            assert _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            ) is None


# ---------------------------------------------------------------------------
# confirmed_loan_balance_at / _map -- the genesis reader (the read switch)
# ---------------------------------------------------------------------------

# The frozen "today" for the after-window reader class (see its _frozen_today
# fixture): after the Jan-May 2026 seed periods and the 2026-12-31 _AS_OF, so an
# in-domain read never trips the scalar's guard while a 2027 as_of exercises it.
_FROZEN_TODAY = date(2027, 1, 1)


class TestConfirmedLoanBalanceReader:
    """The reader reports a loan's balance as -(sum of its linked postings).

    Every posting the write side books onto the loan's linked ledger -- the
    opening, each payment's Step-2 cash and Step-4 split, each true-up -- is
    summed and negated, bounded by pay-period start and scenario.  The literals
    are the same hand-computed figures the split tests above prove
    (100000 - 500 - 502.50 - 505.01 = 98492.49 over three $1,000 payments), read
    back through the ledger.
    """

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so an in-domain read is stable.

        The scalar reader guards ``as_of <= today``; 2027-01-01 sits after the
        Jan-May 2026 seed periods and the 2026-12-31 ``_AS_OF``, so every in-domain
        read is deterministic (not wall-clock dependent) and a 2027 ``as_of``
        exercises the guard.  Class-scoped, so only the reader tests freeze -- the
        split / anchor tests above keep the real clock.
        """
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_opening_only_loan_reads_the_original_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An origination-only loan reads back its opening balance.

        A $250,000 loan with only its origination anchor (no true-up, no
        payments): the opening books linked -250000, so the reader negates it to
        the full $250,000 owed -- the ledger, not ``LoanParams``, is the source.
        With no payments the balance is flat, so every period's map value is the
        same $250,000.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("250000.00")
            # No payments -> the opening balance carries flat across every period.
            result = loan_posting_service.confirmed_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            assert set(result.values()) == {Decimal("250000.00")}

    def test_reads_the_balance_after_a_single_confirmed_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """One $1,000 payment drops the balance by its real principal.

        Trued up to $100,000, one post-anchor $1,000 payment splits to interest
        round(100000 * 0.005) = 500.00 and principal 500.00, so the reader reports
        100000 - 500 = 99500.00 (opening -250000 + true-up +150000 + Step-2 cash
        +1000 + split -500, negated).  The settle wiring auto-posts all of it.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("99500.00")

    def test_extra_payment_is_captured_without_a_trueup(
        self, app, db, seed_user, seed_periods,
    ):
        """An off-schedule extra payment lowers the read balance -- no true-up.

        This is the arc's headline.  A $1,500 payment on the $100,000 balance
        splits to interest round(100000 * 0.005) = 500.00 and principal
        1500 - 500 = 1000.00, so the reader reports 100000 - 1000 = 99000.00.  The
        resolver's contractual replay would book only the scheduled ~$500
        principal and need a balance true-up to reconcile the extra $500; the
        ledger captures the real principal from the actual cash, so the reader
        already reflects it with no anchor edit.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1500.00"),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("99000.00")

    def test_running_balance_reproduces_the_resolver_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Three $1,000 payments read back the resolver's balance, 98492.49.

        The running-balance walk (interest 500.00 / 497.50 / 494.99 on the
        shrinking real balance; principal 500.00 / 502.50 / 505.01) leaves
        100000 - 1507.51 = 98492.49 -- the resolver's confirmed balance
        (``test_opening_payments_trueup_reproduce_resolver_balance`` proves the
        equality against ``account_posting_total``); the reader negates the same
        linked net to the same figure.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("98492.49")

    def test_paid_off_loan_reads_zero_not_none(
        self, app, db, seed_user, seed_periods,
    ):
        """A fully-paid-off loan reads 0.00, distinct from an unconfigured None.

        One $100,500 payment on the $100,000 verified balance splits to interest
        round(100000 * 0.005) = 500.00 and principal 100000.00 (cash - interest),
        exactly closing the loan, so the linked net is 0 and the reader reports
        Decimal("0.00") -- a real paid-off balance, NOT the ``None`` an
        unconfigured loan returns.  The zero is a clean +0.00 (the reader negates
        via ``0 - net``), never ``-0.00``.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("100500.00"),
            )
            db.session.commit()

            result = loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            )
            # == 0.00 (not None): None == Decimal fails, so this proves both.
            assert result == Decimal("0.00")
            # A clean positive zero, never -0.00 (the 0 - net negation).
            assert not result.is_signed()

    def test_payoff_overpayment_reads_zero_with_excess_on_the_refund_ledger(
        self, app, db, seed_user, seed_periods,
    ):
        """An overpayment closes the loan at 0.00; the excess is not on the linked ledger.

        A $150,000 payment on the $100,000 balance: interest 500.00, principal
        capped at the 100000.00 that closes the loan, and the 49500.00 surplus
        routed to the per-loan Refund (Asset) ledger, NOT the linked ledger.  The
        reader sums only the linked ledger, so it reports 0.00 (paid off, never a
        negative balance), while the refund ledger separately holds the 49500.00
        the lender owes back -- proving the excess cannot corrupt the balance the
        reader returns.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("150000.00"),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("0.00")
            # The surplus lives on the Refund ledger, invisible to the reader.
            refund = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_REFUND,
            )
            assert _ledger_net(refund.id, scenario_id) == Decimal("49500.00")

    def test_as_of_bounds_the_sum_by_pay_period_start(
        self, app, db, seed_user, seed_periods,
    ):
        """A historical as_of counts only the payments whose period has begun.

        Over the SAME fully-posted ledger (three $1,000 payments in periods 1/3/5,
        due 02-01 / 03-01 / 04-01), the reader is a point-in-time sum bounded by
        ``pay_period.start_date <= as_of``:

          * before P1's period (period-0 end): opening + true-up only -> 100000.00
          * P1's period start .. its end:       + P1 -> 99500.00 (stable within the
                                                period -- start and end agree, since
                                                a posting's period start IS a
                                                boundary)
          * P2's period start:                  + P2 -> 98997.50
          * P3's period start .. _AS_OF:         + P3 -> 98492.49

        No re-walk and no boundary rule -- just which periods have begun.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            def read(as_of):
                return loan_posting_service.confirmed_loan_balance_at(
                    loan.id, scenario_id, as_of,
                )

            # P1 is in period 1; period 0 ends the day before it begins.
            assert read(seed_periods[0].end_date) == Decimal("100000.00")
            assert read(seed_periods[_P1].start_date) == Decimal("99500.00")
            # Anywhere within P1's period gives P1's balance (start == end bound).
            assert read(seed_periods[_P1].end_date) == Decimal("99500.00")
            assert read(seed_periods[_P2].start_date) == Decimal("98997.50")
            assert read(seed_periods[_P3].start_date) == Decimal("98492.49")
            assert read(_AS_OF) == Decimal("98492.49")

    def test_unconfigured_loan_returns_none(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan with no opening posting reads None, never $0.

        A loan whose ledger carries no OPENING (never synced) is unconfigured:
        both the scalar and the map return ``None`` so the caller routes to its
        needs-setup path, distinct from a real $0 (a paid-off) balance.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Built but never synced -> the linked ledger exists (the create hook
            # pairs it) but carries no opening posting.
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )

            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) is None
            assert loan_posting_service.confirmed_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            ) is None

    def test_future_as_of_raises(
        self, app, db, seed_user, seed_periods,
    ):
        """The scalar reader refuses a date after today, but reads in-domain fine.

        A future ``as_of`` is a forward projection, not a confirmed sum; the
        reader raises rather than silently returning today's balance, forcing the
        caller to route a future date to the projection.  The guard does not break
        an ordinary in-domain read: the same configured loan reads its 100000.00
        verified balance at an in-window date.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            # In domain (<= the frozen 2027-01-01 today): reads the opening +
            # true-up balance.
            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, seed_periods[0].start_date,
            ) == Decimal("100000.00")
            # After today: refused, not clamped to today's balance.
            with pytest.raises(ValueError, match="as_of <= today"):
                loan_posting_service.confirmed_loan_balance_at(
                    loan.id, scenario_id, date(2027, 1, 2),
                )

    def test_reader_is_scenario_scoped_and_none_off_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """The reader isolates scenarios; a scenario with no opening reads None.

        A $1,000 payment settled in the baseline auto-posts the opening, true-up,
        and split there only (a what-if with no payment is neither in the payment
        set nor the baseline).  The baseline reads 99500.00; the what-if -- which
        holds no opening -- reads ``None`` (the M2 what-if fallback the read
        switch closes later), never a misleading $0 or the baseline's balance.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, baseline.id, _AS_OF,
            ) == Decimal("99500.00")
            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, whatif.id, _AS_OF,
            ) is None
            assert loan_posting_service.confirmed_loan_balance_map(
                loan.id, whatif.id, seed_periods,
            ) is None

    def test_per_period_map_runs_the_balance_and_carries_flat(
        self, app, db, seed_user, seed_periods,
    ):
        """The map gives each period the cumulative balance, flat between payments.

        Three $1,000 payments land in periods 1 / 3 / 5 (the opening + true-up in
        period 0).  Each period holds the balance AFTER the payment attributed to
        it, carried flat across payment-less periods:

          period 0: 100000.00 (opening + true-up, pre-payment)
          period 1: 99500.00  (+ P1)      period 2: 99500.00  (flat)
          period 3: 98997.50  (+ P2)      period 4: 98997.50  (flat)
          period 5: 98492.49  (+ P3)      periods 6-9: 98492.49 (flat)

        The map keys by ``period.id`` in the passed order.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            result = loan_posting_service.confirmed_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            expected = {
                seed_periods[0].id: Decimal("100000.00"),
                seed_periods[1].id: Decimal("99500.00"),
                seed_periods[2].id: Decimal("99500.00"),
                seed_periods[3].id: Decimal("98997.50"),
                seed_periods[4].id: Decimal("98997.50"),
                seed_periods[5].id: Decimal("98492.49"),
                seed_periods[6].id: Decimal("98492.49"),
                seed_periods[7].id: Decimal("98492.49"),
                seed_periods[8].id: Decimal("98492.49"),
                seed_periods[9].id: Decimal("98492.49"),
            }
            assert dict(result) == expected
            # Keyed in the passed period order.
            assert list(result.keys()) == [p.id for p in seed_periods]

class TestConfirmedLoanBalanceReaderFuturePeriods:
    """The map carries future periods flat (no domain guard); the scalar raises.

    A separate class because it freezes today MID seed-window (so some seed
    periods are genuinely after today), where ``TestConfirmedLoanBalanceReader``
    freezes after the window.
    """

    @pytest.fixture(autouse=True)
    def _frozen_mid_window(self, monkeypatch):
        """Freeze today inside the seed window so periods 4-9 are in the future.

        2026-02-20 sits in period 3 of the Jan-May seed window, leaving periods
        4-9 starting after today -- the future tail the map must answer (carried
        flat) while the scalar refuses it.
        """
        freeze_today(monkeypatch, date(2026, 2, 20))

    def test_map_carries_flat_for_future_periods_while_scalar_raises(
        self, app, db, seed_user, seed_periods,
    ):
        """The map answers post-today periods (carried flat); the scalar raises.

        A payment-less trued-up loan (opening -250000 + true-up +150000 =
        -100000) posts its opening + true-up as of the frozen 2026-02-20.  Every
        period -- including periods 4-9, which start after today -- carries the
        flat 100000.00 confirmed balance in the map WITHOUT raising, so the read
        switch can pass its whole display window and overlay the projection on the
        future tail.  The scalar reader, a single ambiguous point, still refuses a
        future date.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # opening + true-up, no payments
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            # period 3 contains 2026-02-20; period 4 starts 2026-02-27 (future).
            assert seed_periods[3].start_date <= date(2026, 2, 20)
            assert seed_periods[4].start_date > date(2026, 2, 20)

            result = loan_posting_service.confirmed_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            # Every period -- historical AND future -- carries the flat balance.
            assert set(result.values()) == {Decimal("100000.00")}
            assert len(result) == len(seed_periods)

            # A historical point is answered; a future point is refused.
            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, seed_periods[3].start_date,
            ) == Decimal("100000.00")
            with pytest.raises(ValueError, match="as_of <= today"):
                loan_posting_service.confirmed_loan_balance_at(
                    loan.id, scenario_id, seed_periods[4].start_date,
                )


def _paid_on(year: int, month: int, day: int) -> datetime:
    """Return a noon-UTC settle instant, so its civil date is unambiguous."""
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


class TestConfirmedLoanInterestReader:
    """The reader reports a loan's ACTUAL interest paid in a year, by paid date.

    ``confirmed_loan_interest_in_year`` sums the per-loan ``loan_interest``
    legs -- the same $500.00-on-$100,000 splits the split tests above prove --
    and attributes each payment's NET interest to its CURRENT civil paid date,
    so a reverted payment nets to zero rather than stranding across a year
    boundary.  Every payment is settled with an EXPLICIT ``paid_at`` so the
    attribution year is deterministic (not wall-clock dependent).
    """

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so the settle walk is stable.

        The settle wiring walks confirmed payments with pay-period start
        ``<= date.today()``; 2027-01-01 sits after the Jan-May 2026 seed periods,
        so every settled payment is confirmed regardless of the wall clock.  The
        interest reader itself takes a YEAR (no ``as_of <= today`` guard), so the
        attribution comes solely from each payment's explicit ``paid_at``.
        """
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_single_payment_interest_by_paid_year(
        self, app, db, seed_user, seed_periods,
    ):
        """One $1,000 payment paid in 2026 reports $500.00 interest for 2026 only.

        Trued up to $100,000, one post-anchor $1,000 payment accrues interest
        round(100000 * 0.005) = 500.00.  Paid 2026-03-15, so 2026 sees the full
        500.00 and the adjacent years see nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                paid_at=_paid_on(2026, 3, 15),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("500.00")
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2025,
            ) == Decimal("0.00")
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2027,
            ) == Decimal("0.00")

    def test_running_interest_across_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Three 2026 payments sum their real interest: 500 + 497.50 + 494.99.

        The running-balance walk accrues on the shrinking real balance
        (100000 -> 99500 -> 98997.50): interest 500.00 / 497.50 / 494.99, so the
        year's paid interest is 1492.49 -- the ACTUAL figure, from the ledger.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], loan, period,
                    amount=Decimal("1000.00"), paid_at=_paid_on(2026, 6, 1),
                )
            db.session.commit()

            # 500.00 + 497.50 + 494.99 = 1492.49.
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("1492.49")

    def test_interest_follows_the_paid_date_not_the_pay_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A 2026-period payment PAID in 2025 reports its interest in 2025.

        Mortgage interest is deductible in the year PAID, not the year the
        payment was scheduled.  A period-``_P1`` payment (a 2026 pay period)
        settled with ``paid_at`` 2025-12-20 attributes its 500.00 interest to
        2025, and 2026 sees nothing -- proving the reader keys on the civil paid
        date, not the pay period.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                paid_at=_paid_on(2025, 12, 20),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2025,
            ) == Decimal("500.00")
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("0.00")

    def test_new_years_eve_evening_settle_deducts_in_the_display_year(
        self, app, db, seed_user, seed_periods,
    ):
        """THE L9 CASE: a settle at 8:05 PM EST Dec 31 deducts in the Dec 31 year.

        A payment settled 2025-12-31 20:05 Eastern is stored as
        2026-01-01 01:05 UTC, so its journal ``entry_date`` books 2026 (the
        UTC storage rule).  Schedule-A attribution follows the user's
        wall-clock day (L9, decided 2026-07-03): the 500.00 interest deducts
        in 2025, and 2026 sees nothing.  The pre-L9 UTC attribution reported
        the reverse.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                paid_at=datetime(2026, 1, 1, 1, 5, tzinfo=timezone.utc),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2025,
            ) == Decimal("500.00")
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("0.00")

    def test_reverted_payment_nets_to_zero_across_the_year_boundary(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment reverted across a year boundary strands NO interest.

        The headline of the by-paid-date design.  A ``_P1`` payment (a 2026 pay
        period) is settled with ``paid_at`` 2025-12-20, so its 500.00 interest
        first lands in 2025.  Reverting it clears ``paid_at``, so the reversal
        leg is dated at the pay-period START (2026) -- a DIFFERENT year than the
        original.  Because the reader groups the legs by their payment and
        attributes the NET (here zero), the payment drops out of BOTH years,
        instead of the +500 / -500 that summing each leg by its own entry date
        would strand.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                paid_at=_paid_on(2025, 12, 20),
            )
            db.session.commit()
            # Pre-revert: the 500.00 is attributed to the 2025 paid date.
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2025,
            ) == Decimal("500.00")

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            # Post-revert: the payment's net interest is zero -- it strands
            # nothing in EITHER year (the reversal at the 2026 period start does
            # not leave a spurious -500 in 2026, nor the +500 in 2025).
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2025,
            ) == Decimal("0.00")
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("0.00")

    def test_unconfigured_loan_returns_none(
        self, app, db, seed_user,
    ):
        """A loan with no OPENING posting reads None, so the caller falls back.

        ``create_loan_account`` alone posts no genesis opening (the sync is not
        run), so the loan is unconfigured in the ledger: the reader returns
        ``None`` (route to the schedule), never a misleading $0.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) is None

    def test_interest_is_scenario_scoped(
        self, app, db, seed_user, seed_periods,
    ):
        """Each scenario reports only its own paid interest, never the other's.

        The baseline settles TWO payments (interest 500.00 + 497.50 = 997.50); a
        what-if settles ONE (interest 500.00).  Each scenario's reader returns
        only its own total -- a leak would sum them (1497.50) -- so the genesis
        interest read is scenario-scoped.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], loan, period,
                    amount=Decimal("1000.00"), paid_at=_paid_on(2026, 6, 1),
                )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                paid_at=_paid_on(2026, 6, 1), scenario=whatif,
            )
            db.session.commit()

            # 500.00 + 497.50 = 997.50 in the baseline; 500.00 in the what-if.
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, baseline.id, 2026,
            ) == Decimal("997.50")
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, whatif.id, 2026,
            ) == Decimal("500.00")

    def test_escrow_is_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """Only ``loan_interest`` legs count; escrow is not deductible interest.

        A $1,200/yr ($100/mo) escrow loan settles a $1,000 payment: the split is
        interest 500.00, escrow 100.00, principal 400.00.  The reader reports the
        500.00 interest ONLY -- the $100.00 escrow (posted to its own ledger) is
        excluded, so the figure is not the 600.00 an all-per-loan-legs sum gives.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, escrow_annual=Decimal("1200.00"))
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                paid_at=_paid_on(2026, 3, 15),
            )
            db.session.commit()

            # Escrow WAS posted (proving the exclusion is real, not vacuous).
            escrow_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_ESCROW,
            )
            assert _ledger_net(escrow_ledger.id, scenario_id) == Decimal("100.00")
            # ...but only the 500.00 interest is reported.
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("500.00")

    def test_hard_deleted_payment_is_not_deducted(
        self, app, db, seed_user, seed_periods,
    ):
        """A hard-deleted payment contributes no interest (its legs are SET NULL).

        Settling P1 ($1,000, interest 500.00) then P2 ($1,000, interest 497.50 on
        the 99,500 balance) totals 997.50.  HARD-deleting P2 reverses its
        correction to zero BEFORE the delete SET-NULLs the entry's
        ``transaction_id``; the reader's ``transaction_id IS NOT NULL`` filter
        drops the dead legs, leaving only P1's surviving 500.00 -- a deleted
        payment is not a deduction.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                paid_at=_paid_on(2026, 3, 15),
            )
            deleted = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P2], amount=Decimal("1000.00"),
                paid_at=_paid_on(2026, 4, 15),
            )
            db.session.commit()
            # Both count before the delete: 500.00 + 497.50 = 997.50.
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("997.50")

            transfer_service.delete_transfer(
                deleted.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            # Only the surviving P1's 500.00 remains.
            assert loan_posting_service.confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) == Decimal("500.00")


class TestConfirmedLoanHistoryRows:
    """The history reader rebuilds the confirmed schedule from posted legs.

    ``confirmed_loan_history_rows`` is the ledger-derived amortization HISTORY
    adapter: one row per confirmed payment, its interest from the posted
    ``loan_interest`` legs, its principal from the payment's net on the linked
    ledger, and its running balance from the cumulative linked net -- never the
    resolver's contractual replay.  On-schedule the rows are byte-identical to
    the replay's (pinned against the resolver directly); off-schedule they show
    the actual economics the replay cannot.  The loan is the shared SPLIT_LOAN
    fixture: contractual P&I round_money(250000 * 0.005 * 1.005^360 /
    (1.005^360 - 1)) = 1498.88, trued up to $100,000 (monthly rate 0.005).
    """

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window (same rationale as the reader)."""
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_on_schedule_rows_are_byte_identical_to_the_replay_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """Two contractual payments read back the replay's exact rows.

        Two settled payments of exactly the contractual P&I (1498.88):

          row 1 (due 02-01): interest round(100000 * 0.005) = 500.00,
            principal 1498.88 - 500.00 = 998.88, balance 99001.12
          row 2 (due 03-01): interest round(99001.12 * 0.005) = 495.01,
            principal 1003.87, balance 97997.25

        The resolver's replay computes the identical figures for an on-schedule
        loan (same accrual formula on the same running balance), so the
        ledger-derived rows must equal the resolver's confirmed schedule rows
        FIELD BY FIELD -- month, date, payment, principal, interest, extra,
        balance, flag, and rate.  This is the compatibility pin that lets the
        read switch replace the replay's history without moving any
        on-schedule number.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle_payment(
                    seed_user, loan, period, Decimal("1498.88"),
                )
            db.session.commit()

            rows = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            # Hand-computed spot pins (independent of the resolver).
            assert [
                (r.payment_date, r.interest, r.principal, r.remaining_balance)
                for r in rows
            ] == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("998.88"), Decimal("99001.12")),
                (date(2026, 3, 1), Decimal("495.01"),
                 Decimal("1003.87"), Decimal("97997.25")),
            ]

            # Field-by-field equality with the resolver's replayed history.
            params = _loan_params(loan)
            ctx = loan_payment_service.load_loan_context(
                loan.id, scenario_id, params,
            )
            state = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    params,
                    loan_loaders.load_loan_anchor_facts(params),
                    ctx.payments,
                    ctx.rate_changes,
                ),
                _AS_OF,
            )
            replay_rows = [r for r in state.schedule if r.is_confirmed]
            assert rows == replay_rows

    def test_extra_payment_row_shows_the_actual_split_and_extra(
        self, app, db, seed_user, seed_periods,
    ):
        """An off-schedule extra payment's row carries its real economics.

        A $2,000 payment on the $100,000 balance: interest round(100000 *
        0.005) = 500.00, principal 2000 - 500 = 1500.00, balance 98500.00.
        Against the 1498.88 contractual P&I the actual P&I of 2000.00 carries
        extra 2000.00 - 1498.88 = 501.12, leaving payment 1498.88 -- the
        schedule-row invariant ``principal + interest == payment + extra``
        (1500.00 + 500.00 == 1498.88 + 501.12), the same algebra a projected
        row with extra uses, so the table's totals add up unchanged.  The
        replay would show only the scheduled 998.88 principal; the ledger row
        shows what the cash actually did.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("2000.00"),
            )
            db.session.commit()

            (row,) = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("1500.00")
            assert row.extra_payment == Decimal("501.12")
            assert row.payment == Decimal("1498.88")
            assert row.remaining_balance == Decimal("98500.00")
            assert row.is_confirmed is True

    def test_short_payment_row_shows_negative_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An underpayment's row surfaces the real negative principal.

        A $400 payment against 500.00 accrued interest: principal
        400 - 500 = -100.00 (the balance GROWS to 100100.00), payment 400.00
        (the actual P&I, under contractual so extra is 0.00).  Surfaced,
        never clamped -- the same D5 honesty the split walk pins.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("400.00"),
            )
            db.session.commit()

            (row,) = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("-100.00")
            assert row.extra_payment == Decimal("0.00")
            assert row.payment == Decimal("400.00")
            assert row.remaining_balance == Decimal("100100.00")

    def test_payoff_overpayment_row_caps_principal_and_excludes_the_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """A payoff overpayment's row ends at 0.00 with the refund excluded.

        A $150,000 payment on the $100,000 balance: interest 500.00, principal
        capped at the 100000.00 that closes the loan, and the 49500.00 surplus
        on the Refund ledger -- NOT in the row (it is not P&I).  The actual P&I
        is 100500.00, so extra = 100500.00 - 1498.88 = 99001.12 and payment
        stays the contractual-shaped 1498.88 (principal + interest == payment +
        extra holds: 100500.00 == 1498.88 + 99001.12).  The balance reads a
        clean 0.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("150000.00"),
            )
            db.session.commit()

            (row,) = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("100000.00")
            assert row.extra_payment == Decimal("99001.12")
            assert row.payment == Decimal("1498.88")
            assert row.remaining_balance == Decimal("0.00")
            assert not row.remaining_balance.is_signed()

    def test_trueup_between_payments_moves_the_next_row_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-history true-up resets the balance the NEXT row reads.

        P1 ($1,000, due 02-01) splits 500.00 / 500.00 leaving 99500.00; a
        true-up then asserts $95,000 on 02-15; P2 ($1,000, due 03-01) accrues
        on the VERIFIED balance: interest round(95000 * 0.005) = 475.00,
        principal 525.00, balance 94475.00.  The true-up itself emits no row
        (it is not a payment) but its correction posting moves the running
        balance between the rows -- the pre-true-up row keeps its actual
        interest (the arc's from-origination history), and the post-true-up
        row lands on the asserted trajectory.

        The pre-true-up row is also the NEW display surface: the resolver's
        replay starts at the LATEST anchor (02-15), so its confirmed schedule
        hides the 02-01 payment forever; the ledger history shows the real
        recorded payment and still lands on the asserted trajectory -- pinned
        below against the un-seeded resolver directly.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            insert_trueup_event(
                _loan_params(loan), Decimal("95000.00"), date(2026, 2, 15),
            )
            db.session.commit()
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()

            rows = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            assert [
                (r.payment_date, r.interest, r.principal, r.remaining_balance)
                for r in rows
            ] == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("475.00"),
                 Decimal("525.00"), Decimal("94475.00")),
            ]

            # The un-seeded resolver replays from the LATEST anchor (02-15),
            # so its confirmed schedule hides the real 02-01 payment; the
            # ledger history is the only surface that shows it.
            params = _loan_params(loan)
            ctx = loan_payment_service.load_loan_context(
                loan.id, scenario_id, params,
            )
            state = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    params,
                    loan_loaders.load_loan_anchor_facts(params),
                    ctx.payments,
                    ctx.rate_changes,
                ),
                _AS_OF,
            )
            replay_dates = [
                r.payment_date for r in state.schedule if r.is_confirmed
            ]
            assert replay_dates == [date(2026, 3, 1)]

    def test_reverted_payment_does_not_wobble_row_balances(
        self, app, db, seed_user, seed_periods,
    ):
        """A reverted payment's two-dated residue never pollutes a row balance.

        A reverted payment's ledger residue nets to zero but at TWO dates:
        its original entries at the civil paid date (02-20) and its reversal
        entries at the pay-period start (01-30, the cleared-``paid_at``
        fallback).  A confirmed sibling due BETWEEN them (02-01) would absorb
        half the pair if the residue were treated as dated balance events.
        The classifier instead recognises the whole transfer as payment
        LINEAGE and drops it, so the confirmed $1,100 payment's row reads its
        exact economics -- interest 500.00, principal 600.00, balance
        100000 - 600 = 99400.00 -- and the final row still equals the scalar
        reader to the penny.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1100.00"),
            )
            reverted = create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[2], amount=Decimal("1000.00"),
                paid_at=_paid_on(2026, 2, 20),
            )
            db.session.commit()
            transfer_service.update_transfer(
                reverted.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            rows = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            assert [
                (r.payment_date, r.interest, r.principal, r.remaining_balance)
                for r in rows
            ] == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("600.00"), Decimal("99400.00")),
            ]
            assert rows[-1].remaining_balance == (
                loan_posting_service.confirmed_loan_balance_at(
                    loan.id, scenario_id, _AS_OF,
                )
            )

    def test_settled_payment_in_a_not_yet_begun_period_is_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment settled ahead of its period stays out until the period begins.

        With payments in periods 1 and 5, a read at 2026-02-10 -- after period
        1 began (its 02-01 due row counts) but before period 5 begins on
        03-27 -- must exclude the early-settled payment ENTIRELY, exactly as
        the scalar reader's period bound does: one row, balance 99500.00,
        equal to the scalar at the same as_of.  Treating the future payment's
        postings as dated balance events would desync the schedule table from
        the loan card by the full cash.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P3], Decimal("1000.00"),
            )
            db.session.commit()

            mid_window = date(2026, 2, 10)
            rows = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, mid_window,
            )
            assert [
                (r.payment_date, r.remaining_balance) for r in rows
            ] == [
                (date(2026, 2, 1), Decimal("99500.00")),
            ]
            assert rows[-1].remaining_balance == (
                loan_posting_service.confirmed_loan_balance_at(
                    loan.id, scenario_id, mid_window,
                )
            )

    def test_trueup_dated_on_a_due_date_applies_after_that_days_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A true-up dated exactly on a due date subsumes that day's payment.

        The write walk's tie-break (a payment sorts BEFORE a same-date anchor)
        must be mirrored by the history walk or the row would accrue on the
        wrong balance.  P1 leaves 99500.00; a true-up asserts $95,000 dated
        exactly on P2's 03-01 due date; P2's row is walked FIRST -- interest
        round(99500 * 0.005) = 497.50, principal 502.50, row balance
        98997.50 -- and the reset applies after it, so the scalar reads the
        asserted 95000.00.  (An anchor-first order would show 475.00 interest
        on the reset balance -- the mutation this test kills.)
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            insert_trueup_event(
                _loan_params(loan), Decimal("95000.00"), date(2026, 3, 1),
            )
            db.session.commit()
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()

            rows = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            assert [
                (r.payment_date, r.interest, r.principal, r.remaining_balance)
                for r in rows
            ] == [
                (date(2026, 2, 1), Decimal("500.00"),
                 Decimal("500.00"), Decimal("99500.00")),
                (date(2026, 3, 1), Decimal("497.50"),
                 Decimal("502.50"), Decimal("98997.50")),
            ]
            assert loan_posting_service.confirmed_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("95000.00")

    def test_final_row_balance_matches_the_scalar_reader(
        self, app, db, seed_user, seed_periods,
    ):
        """The last row's balance IS the confirmed-balance scalar (one truth).

        Three $1,000 payments (500.00 / 502.50 / 505.01 principal on the
        shrinking real balance) leave 100000 - 1507.51 = 98492.49.  The history
        walk and the scalar reader sum the SAME linked postings, so the final
        row's ``remaining_balance`` must equal ``confirmed_loan_balance_at`` to
        the penny -- the transitivity pin that keeps the schedule table's last
        confirmed row equal to the loan card.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            rows = loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )
            assert rows[-1].remaining_balance == Decimal("98492.49")
            assert rows[-1].remaining_balance == (
                loan_posting_service.confirmed_loan_balance_at(
                    loan.id, scenario_id, _AS_OF,
                )
            )

    def test_configured_loan_with_no_payments_returns_an_empty_list(
        self, app, db, seed_user, seed_periods,
    ):
        """An opened loan with no confirmed payment reads [] -- not None.

        The opening posting alone means the ledger CAN answer (the loan is
        configured); there is simply no history yet.  ``[]`` routes the caller
        to an empty confirmed slice, where ``None`` would wrongly fall back to
        the replay.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            assert loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            ) == []

    def test_unconfigured_loan_returns_none(self, app, db, seed_user):
        """A loan with no opening posting reads None (keep the replay rows).

        Mirrors the balance reader's sentinel: no OPENING posting means the
        ledger cannot answer for this loan / scenario, so the caller keeps the
        resolver's replay -- never an empty history that would blank a real
        schedule.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            # Deliberately NO sync: params exist but nothing is posted.
            assert loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            ) is None

    def test_history_is_scenario_scoped(
        self, app, db, seed_user, seed_periods,
    ):
        """A what-if scenario the opening was never posted into reads None.

        The baseline carries the full history; a second scenario carries no
        postings at all, so the reader reports None there (the M2 what-if
        fallback) while the baseline's rows are untouched.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            what_if = Scenario(
                user_id=seed_user["user"].id, name="What-if",
                is_baseline=False,
            )
            db.session.add(what_if)
            db.session.commit()

            assert loan_posting_service.confirmed_loan_history_rows(
                loan.id, what_if.id, _AS_OF,
            ) is None
            assert len(loan_posting_service.confirmed_loan_history_rows(
                loan.id, scenario_id, _AS_OF,
            )) == 1

    def test_future_as_of_raises(self, app, db, seed_user, seed_periods):
        """A future as_of is out of the confirmed domain and raises.

        Mirrors the scalar reader's guard: 2027-06-01 is after the frozen
        2027-01-01 today, so the reader refuses rather than answering a
        projection question with a history sum.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            db.session.commit()

            with pytest.raises(ValueError, match="as_of <= today"):
                loan_posting_service.confirmed_loan_history_rows(
                    loan.id, scenario_id, date(2027, 6, 1),
                )


class TestUserScopedResync:
    """``resync_user_loan_postings`` + its enumerator reconcile ONE owner's loans.

    The per-user counterpart to the deploy-wide ``backfill_all_loan_postings``,
    called by ``pay_period_admin.reset_pay_periods`` to re-post a reset user's
    loan genesis entries the period wipe cascades -- scoped to that one user so a
    single-user reset never reconciles another owner's loans inside its
    transaction (review M2 / R7).
    """

    @pytest.fixture(autouse=True)
    def _freeze(self, monkeypatch):
        """Pin today after both anchor dates so the genesis walk is eligible."""
        freeze_today(monkeypatch, date(2026, 6, 15))

    def test_enumerator_returns_only_the_users_loans(
        self, app, db, seed_user, seed_second_user,
    ):
        """load_loan_account_ids_for_user filters LoanParams by owner, ascending.

        Two loans on the first user and one on the second: each owner's scoped
        enumeration returns exactly its own loan ids, and the all-owners sweep
        returns their union -- proving the join is owner-scoped, not global.
        """
        with app.app_context():
            loan_a = _make_loan(seed_user, name="Loan A")
            loan_b = _make_loan(seed_user, name="Loan B")
            loan_c = _make_loan(seed_second_user, name="Loan C")
            db.session.commit()

            assert loan_loaders.load_loan_account_ids_for_user(
                seed_user["user"].id,
            ) == sorted([loan_a.id, loan_b.id])
            assert loan_loaders.load_loan_account_ids_for_user(
                seed_second_user["user"].id,
            ) == [loan_c.id]
            # The scoped sets partition the global sweep.
            assert loan_loaders.load_all_loan_account_ids() == sorted(
                [loan_a.id, loan_b.id, loan_c.id],
            )

    def test_resync_posts_only_the_users_genesis(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Re-syncing user 1 posts its genesis and leaves user 2's loan untouched.

        Both users own a configured loan (genesis net -100000 = opening -250000 +
        true-up +150000).  Re-syncing only the first user returns just its loan
        id, posts its opening + true-up (the loan-linked ledger nets -100000), and
        posts NOTHING for the second user (zero genesis entries) -- the scoping
        that keeps a single-user reset from reconciling another owner's loans.
        """
        with app.app_context():
            loan1 = _make_loan(seed_user)
            loan2 = _make_loan(seed_second_user)
            db.session.commit()
            u1 = seed_user["user"].id
            u2 = seed_second_user["user"].id

            posted = loan_posting_service.resync_user_loan_postings(u1)
            db.session.commit()

            assert posted == [loan1.id]
            assert posting_service.account_posting_total(
                loan1.id, seed_user["scenario"].id,
            ) == Decimal("-100000.00")
            # User 2's loan was never visited: no genesis entries posted.
            assert _genesis_entry_count(u2) == 0
            assert loan2.id not in posted
