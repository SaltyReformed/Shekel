"""Build-Order Step 4 wiring: loan-payment splits post at the transfer chokepoints.

Commit 4 built the loan-payment split-posting service (pure, unwired); Commit 5
WIRES it into every chokepoint that changes a loan's confirmed payments -- the
transfer settle / revert / edit / delete / restore paths
(:mod:`app.services.transfer_service`), the balance true-up
(:func:`app.services.anchor_service.apply_loan_anchor_true_up`), the ARM rate
change and origination-rate / params edit routes, and loan-params creation (the
N1 back-post).  These integration tests drive each chokepoint through its REAL
entry point (the service call or the HTTP route), with NO manual
``sync_loan_payment_postings`` call anywhere, and assert the ledger ends in the
right state.

The load-bearing invariant (plan Section 5 / 8.5): a Step-4 correction carries a
NULL ``transfer_id``, so the Step-2 cash reader (``_posted_net``, transfer_id
keyed) never sees it -- proven end-to-end by asserting a revert / delete of a
corrected payment posts the FULL cash reversal (Checking returns to exactly 0),
not a reversal short by the correction.

Loans use a $100,000 anchor at 6%, so the first month accrues exactly $500.00
(``100000 * 0.06 / 12``); every asserted split is hand-computed in the docstring.
"today" is frozen to 2026-05-15 (inside the seed-period range, after every
payment period used) so the wiring's ``date.today()`` as-of is deterministic and
every settled payment is historical.  All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.models.scenario import Scenario
from app.services import (
    anchor_service,
    posting_service,
    transfer_service,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from tests._test_helpers import (
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_transfer,
    find_loan_ledger_account,
    freeze_today,
    ledger_net,
    loan_correction_entries,
    loan_income_shadow,
)

# A 6% loan on a $100,000 anchor accrues exactly $500.00 the first month; the
# round numbers keep every split hand-computable.  The trueup anchor ($100,000)
# is deliberately distinct from origination ($250,000), so a correct interest
# figure proves the walk seeds from the trueup anchor, not origination.
_ANCHOR_BALANCE = Decimal("100000.00")
_RATE = Decimal("0.06000")
_ANCHOR_DATE = date(2026, 1, 10)
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)

# seed_periods indices whose monthly due date (payment_day=1) lands in a
# DISTINCT month after the anchor: P1 start 2026-01-16 -> due 02-01; P2 start
# 2026-02-13 -> due 03-01; P3 start 2026-03-13 -> due 04-01.
_P1, _P2, _P3 = 1, 3, 5


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Freeze today to 2026-05-15 so the wiring's date.today() as-of is fixed.

    Inside the seed-period range (period 9 is 2026-05-08..2026-05-21) and after
    every payment period used (P1/P2/P3 in Jan-Mar), so each settled payment is
    historical (eligible) regardless of the wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 5, 15))


def _make_loan(seed_user, *, anchor_balance=_ANCHOR_BALANCE, rate=_RATE):
    """Create a resolvable amortizing loan with the suite's controlled anchor."""
    return create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=_ANCHOR_DATE, rate=rate,
        origination_date=_ORIGINATION_DATE,
    )


def _settle(seed_user, loan, period, amount=Decimal("1000.00"), scenario=None):
    """Settle a Checking -> loan payment transfer through the service."""
    return create_settled_transfer(
        seed_user, _db.session, seed_user["account"], loan, period,
        amount=amount, scenario=scenario,
    )


def _interest_ledger(loan):
    """Return the loan's per-loan interest ledger account (must exist)."""
    ledger = find_loan_ledger_account(
        _db.session, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
    )
    assert ledger is not None, "interest ledger not minted by the wiring"
    return ledger


class TestSettleWiringAutoPosts:
    """Settling a loan payment auto-posts its split correction (no manual sync)."""

    def test_settle_auto_posts_split_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """A single $1,000 settle posts Loan -500 / Interest +500 with no sync call.

        Arithmetic: interest round(100000 * 0.005) = 500.00; principal 500.00.
        The wiring fires inside update_transfer's settle path, so right after
        create_settled_transfer the loan nets to the real principal (+500) and
        the interest ledger holds +500 -- Checking moved only by the Step-2 cash.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            shadow = loan_income_shadow(db.session, xfer.id, loan.id)
            assert len(loan_correction_entries(db.session, shadow.id)) == 1
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("500.00")
            assert ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            ) == Decimal("500.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("-1000.00")

    def test_settle_with_actual_amount_splits_from_actual_cash(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling with an actual over the estimate splits from the ACTUAL cash.

        A single payoff-overpayment isolates the correction's cash-reading (one
        settle, so no later re-sync can self-heal a wrong read).  On a $300
        anchor, a payment settled with a $100 estimate but a $1,000 ACTUAL in ONE
        update_transfer call pays the loan off -- interest round(300*0.005)=1.50,
        principal capped at 300.00 -- and routes the 1000 - 1.50 - 300 = 698.50
        remainder to the per-loan Refund ledger.  That Refund leg is
        cash-DEPENDENT: a split that read the $100 estimate would under-cover
        (principal 98.50, no cap, NO refund).  Its presence therefore pins both
        that the split reads effective (actual) cash AND that the wiring runs the
        sync AFTER the actual is applied in update_transfer (the actual-after-
        status ordering).  The loan itself nets to the real principal, 300.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("100.00"),
                actual_amount=Decimal("1000.00"),
            )
            db.session.commit()

            refund_ledger = find_loan_ledger_account(
                db.session, loan.id, LedgerAccountKindEnum.LOAN_REFUND,
            )
            assert refund_ledger is not None, (
                "no Refund leg -- the split did not read the overpaying actual"
            )
            assert ledger_net(
                db.session, refund_ledger.id, scenario_id,
            ) == Decimal("698.50")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("300.00")


class TestRevertAndDeletePostFullCashReversal:
    """The CRITICAL 8.5 regression: a correction never shortens the cash reversal."""

    def test_revert_posts_full_cash_reversal_and_reverses_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """Reverting a corrected payment returns Checking to exactly 0.

        The correction (Loan -500 / Interest +500) carries a NULL transfer_id,
        so the Step-2 cash reader sees only the +1,000 cash on the loan ledger
        and posts the FULL -1,000 reversal: Checking returns to 0 (not 500), and
        the loan sync reverses the now-projected payment's correction to zero.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("500.00")

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            # Full cash reversal: Checking back to exactly 0.
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            # Loan + interest back to zero (cash reversed AND correction reversed).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("0.00")
            assert ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            ) == Decimal("0.00")

    def test_hard_delete_strands_nothing_and_full_cash_reversal(
        self, app, db, seed_user, seed_periods,
    ):
        """Hard-deleting a corrected payment leaves every ledger at zero.

        reverse-before-delete zeroes the correction while the shadow id still
        exists (the CASCADE then SET-NULLs the entry's transaction_id), and the
        Step-2 reverse-before posts the FULL cash reversal.  Nothing stranded:
        Checking, the loan, and the interest ledger all net to zero.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("0.00")
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("0.00")

    def test_soft_delete_reverses_correction_and_full_cash(
        self, app, db, seed_user, seed_periods,
    ):
        """Soft-deleting a corrected payment reverses the correction and full cash."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("0.00")

    def test_delete_resplits_downstream_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Deleting an earlier payment re-splits the later one (running-balance coupling).

        P1 (interest 500.00) and P2 (interest round(99500*0.005)=497.50) settle,
        so the interest ledger holds 997.50.  Deleting P1 re-bases P2 onto the
        anchor: P2's interest becomes round(100000*0.005)=500.00, so the ledger
        holds 500.00 after -- P1's reversed, P2's re-split UP from 497.50.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            x1 = _settle(seed_user, loan, seed_periods[_P1])
            _settle(seed_user, loan, seed_periods[_P2])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("997.50")

            transfer_service.delete_transfer(
                x1.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("500.00")


class TestRestoreWiring:
    """Restoring a soft-deleted, settled loan payment re-posts its correction."""

    def test_restore_reposts_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """Delete (reverses to 0) then restore (re-posts principal 500)."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("500.00")

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("0.00")

            transfer_service.restore_transfer(xfer.id, seed_user["user"].id)
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("500.00")


class TestTrueUpWiring:
    """A balance true-up re-bases every scenario's split; Checking is never touched."""

    def test_trueup_reverses_pre_anchor_resplits_downstream_checking_untouched(
        self, app, db, seed_user, seed_periods,
    ):
        """A new anchor reverses now-pre-anchor corrections and re-splits the rest.

        P1 (due 02-01) and P2 (due 03-01) settle against 100000 @ 01-10
        (interest 500.00 + 497.50 = 997.50).  A user-trueup of 90000 @ 02-15
        pushes P1 pre-anchor (due 02-01 <= 02-15) and re-bases P2 from 90000:
        interest round(90000 * 0.005) = 450.00.  After the true-up the interest
        ledger holds ONLY 450.00, and Checking is unchanged from its settled
        -2000.00 (the true-up sync touches only the loan's own ledgers).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            _settle(seed_user, loan, seed_periods[_P2])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("997.50")
            checking_before = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            assert checking_before == Decimal("-2000.00")

            outcome = anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("90000.00"),
                anchor_date=date(2026, 2, 15),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("450.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == checking_before

    def test_trueup_syncs_every_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """One true-up re-splits the payments in every scenario the loan has.

        A P1 payment settles in the baseline AND a second (what-if) scenario,
        each accruing interest 500.00 against 100000 @ 01-10.  A user-trueup of
        90000 @ 01-15 (after the 01-10 anchor, before P1's 02-01 due date)
        re-bases P1 in BOTH scenarios to round(90000 * 0.005) = 450.00 -- proving
        the sync loops every scenario, since the anchor is per-account.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if",
                is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], scenario=baseline)
            _settle(seed_user, loan, seed_periods[_P1], scenario=whatif)
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, baseline.id,
            ) == Decimal("500.00")
            assert ledger_net(
                db.session, interest_ledger_id, whatif.id,
            ) == Decimal("500.00")

            outcome = anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("90000.00"),
                anchor_date=date(2026, 1, 15),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            # BOTH scenarios re-split to 450.00.
            assert ledger_net(
                db.session, interest_ledger_id, baseline.id,
            ) == Decimal("450.00")
            assert ledger_net(
                db.session, interest_ledger_id, whatif.id,
            ) == Decimal("450.00")

    def test_trueup_duplicate_same_day_is_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """A same-(date, balance) true-up re-submit is an idempotent no-op.

        The shared sync-or-duplicate helper flushes the pending event; the
        second identical submit trips the same-day unique index, rolls back, and
        returns DUPLICATE_SAME_DAY, leaving the first commit's correction intact
        (interest still 450.00, not doubled or lost).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            first = anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("90000.00"),
                anchor_date=date(2026, 1, 15),
            )
            assert first is AnchorTrueUpOutcome.COMMITTED
            interest_after_first = ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            )
            assert interest_after_first == Decimal("450.00")

            second = anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("90000.00"),
                anchor_date=date(2026, 1, 15),
            )
            assert second is AnchorTrueUpOutcome.DUPLICATE_SAME_DAY
            assert ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            ) == Decimal("450.00")


class TestNonLoanTransferIgnored:
    """A non-loan transfer settle posts no loan-payment correction (wiring no-op)."""

    def test_savings_transfer_posts_no_loan_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling a Checking -> Savings transfer creates no loan_payment entry.

        classify_account(Savings) is not AMORTIZING, so the loan wiring
        short-circuits: no loan-payment-sourced journal entry is booked under
        the transfer's income shadow.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Wiring Savings",
            )
            db.session.commit()
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_P1], amount=Decimal("250.00"),
            )
            db.session.commit()

            shadow = loan_income_shadow(db.session, xfer.id, savings.id)
            assert loan_correction_entries(db.session, shadow.id) == []
            # And no loan_payment entry exists for this account at all.
            loan_payment_source = ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT,
            )
            count = (
                db.session.query(JournalEntry)
                .filter_by(
                    transaction_id=shadow.id,
                    source_kind_id=loan_payment_source,
                )
                .count()
            )
            assert count == 0


class TestRouteChokepointWiring:
    """The route chokepoints (rate change, params create) fire the re-sync."""

    def test_rate_change_route_resplits_interest(
        self, app, db, seed_user, seed_periods, auth_client,
    ):
        """POSTing an ARM rate change re-splits a later payment's interest.

        P1 (period start 01-16, 6% origination rate) and P3 (period start
        03-13) settle: interest 500.00 + round(99500*0.005)=497.50 = 997.50.  A
        rate change to 12% effective 2026-03-01 governs P3's period: interest
        round(99500 * 0.12 / 12) = 995.00.  The route wiring re-syncs, so the
        interest ledger holds 500.00 + 995.00 = 1495.00 after the POST.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            # Mark ARM so the rate-change surface applies (mirrors the loan
            # route suite); the split itself is rate-feed driven regardless.
            loan_params = loan.loan_params
            loan_params.is_arm = True
            _settle(seed_user, loan, seed_periods[_P1])
            _settle(seed_user, loan, seed_periods[_P3])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("997.50")

            resp = auth_client.post(
                f"/accounts/{loan.id}/loan/rate",
                data={"effective_date": "2026-03-01", "interest_rate": "12.000"},
            )
            assert resp.status_code == 200

            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("1495.00")

    def test_params_update_origination_rate_resplits_interest(
        self, app, db, seed_user, seed_periods, auth_client,
    ):
        """POSTing a new origination rate re-splits every confirmed payment's interest.

        P1 settles at the 6% origination rate: interest round(100000*0.06/12) =
        500.00.  POSTing /loan/params with interest_rate 8.000 upserts the
        origination RateHistory row to 8% (payment_day unchanged), and
        update_params re-syncs UNCONDITIONALLY, so P1's interest re-splits to
        round(100000 * 0.08 / 12) = 666.67.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("500.00")

            resp = auth_client.post(
                f"/accounts/{loan.id}/loan/params",
                data={"interest_rate": "8.000", "payment_day": "1"},
            )
            assert resp.status_code == 302

            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("666.67")

    def test_params_payment_day_change_reverses_now_pre_anchor_payment(
        self, app, db, seed_user, seed_periods, auth_client,
    ):
        """Changing payment_day alone re-splits -- it moves the eligibility boundary.

        Pins the reason update_params syncs UNCONDITIONALLY, not only on a rate
        change.  The loan's anchor is 2026-01-25.  P1 (period start 2026-01-16)
        has monthly due date 2026-02-01 at payment_day=1 -- AFTER the anchor, so
        it is eligible and its correction posts (interest round(100000*0.005) =
        500.00).  POSTing /loan/params with payment_day=20 (rate unchanged) moves
        P1's due date to 2026-01-20 (the first day-20 on or after 01-16) -- now
        BEFORE the 2026-01-25 anchor, so P1 is pre-anchor and its correction
        reverses to zero.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_ORIGINATION_PRINCIPAL,
                anchor_balance=_ANCHOR_BALANCE, anchor_date=date(2026, 1, 25),
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("500.00")

            resp = auth_client.post(
                f"/accounts/{loan.id}/loan/params",
                data={"interest_rate": "6.000", "payment_day": "20"},
            )
            assert resp.status_code == 302

            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("0.00")

    def test_params_created_after_settle_backposts(
        self, app, db, seed_user, seed_periods, auth_client,
    ):
        """A payment settled before loan setup gets its correction on setup (N1).

        A bare amortizing-type account carries no LoanParams, so a payment
        settled into it posts NO correction (the settle-time sync short-circuits
        -- not resolvable).  POSTing /loan/setup configures the loan (origination
        anchor 100000 @ 2026-01-01) and the create_params wiring back-posts: the
        P1 correction now holds interest round(100000 * 0.005) = 500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_account_of_type(
                seed_user, db.session, "Auto Loan", "Unconfigured Loan",
            )
            db.session.commit()
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            shadow = loan_income_shadow(db.session, xfer.id, loan.id)
            # Not resolvable yet -> no correction at settle.
            assert loan_correction_entries(db.session, shadow.id) == []

            resp = auth_client.post(
                f"/accounts/{loan.id}/loan/setup",
                data={
                    "original_principal": "100000.00",
                    "current_principal": "100000.00",
                    "interest_rate": "6.000",
                    # <= the Auto Loan type's 120-month cap; the split's interest
                    # is balance*rate/12, independent of the term.
                    "term_months": "60",
                    "origination_date": "2026-01-01",
                    "payment_day": "1",
                },
            )
            assert resp.status_code == 302

            # Back-posted: the correction now exists with interest 500.00.
            entries = loan_correction_entries(db.session, shadow.id)
            assert len(entries) == 1
            assert ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            ) == Decimal("500.00")
