"""E1c: the walk-based confirmed view is proven equal to the posting-based one.

Plan step E1c (``docs/audits/balance_architecture/README.md``).  Parallel-runs
the walk-based confirmed view (:func:`app.services.balance_at.confirmed_view`,
built from the event fold) against the shipping posting-based view
(:func:`app.services.loan_payment_service.confirmed_loan_view`, read from the
posted ledger) on EVERY DAY of each shape's domain.  The two must agree byte for
byte -- the same ``balance`` and the same ``history_rows`` (every field of every
:class:`~app.services.amortization_engine.AmortizationRow`) -- so E1d's cutover of
the resolver's confirmed slice onto the walk moves nothing.

**What this proves.**  The walk view's ``balance`` is the fold, already proven
equal to the posting reader on every day (step B2, ``test_loan_fold_oracle.py``);
this file adds the ROWS.  Each row's ``principal`` / ``interest`` equals the
posted linked / interest net the writer books from the SAME split (the checked
projection step E1a asserts at write time), and its ``remaining_balance`` is the
same contract-order running sum over the same visible event set the posting
reader's ``_replay_history_events`` walks.  Equal on every day of every shape is
the equivalence proof E1d rests on.

**Two DELIBERATE divergences, demonstrated not hidden.**  The fold is TOTAL, so
it answers where the partial posting reader returns ``None`` -- a BROKEN loan
(originated, no opening posting: the fold reads source facts, the reader has no
opening to sum, finding B-12) -- and it is blind to a raw transaction typed onto
a loan (finding N-11, forbidden at source by BG) where the reader is not.  Both
are pinned below as divergences the equivalence shapes route AROUND, never
through.

**Sampling is forbidden** (plan Section 7.2): every equivalence test walks EVERY
day of its domain and guards the loop so a vacuous domain cannot pass.
"""

import dataclasses
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.loan_features import RateHistory
from app.services import balance_at, loan_ledger, loan_loaders
from app.services.balance_at import BalanceContext
from app.services.loan_payment_service import confirmed_loan_view
from tests._test_helpers import (
    clear_loan_ledger,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
    insert_tracking_start_event,
    settle_instant_on,
)

# The controlled loan terms, shared with B1/B2 (``test_loan_ledger.py`` /
# ``test_loan_fold_oracle.py``) so the whole arc reads one shape from many angles:
# $250,000 originated 2025-01-01 at 6%, trued up to $100,000 as of 2026-01-05.
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)
_ANCHOR_BALANCE = Decimal("100000.00")
_ANCHOR_DATE = date(2026, 1, 5)
_RATE = Decimal("0.06")

# A date before every event (origination included): both views return ``None``
# there (nothing has happened, the fold's 0.00 must not seed a projection), so
# the domain also pins that agreement.
_BEFORE_ALL = date(2024, 12, 31)


def _make_loan(seed_user, db, **kwargs):
    """Build the controlled trued-up loan (origination + a user true-up)."""
    return create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
        rate=_RATE, origination_date=_ORIGINATION_DATE, name="View Oracle Loan",
        **kwargs,
    )


def _settle(seed_user, db, loan, period, amount=Decimal("1000.00"), paid_at=None):
    """Settle a Checking -> loan payment transfer through the sole writer.

    Pins ``paid_at`` to the period's start by default (C2 keys visibility on the
    settled date); pass an explicit ``paid_at`` for a late-settled payment whose
    visible date lands in a later period than its due date.
    """
    return create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, period,
        amount=amount,
        paid_at=(
            settle_instant_on(period.start_date) if paid_at is None else paid_at
        ),
    )


def _describe(walk, posting):
    """Return a short description of the FIRST difference between two views."""
    if walk is None or posting is None:
        walk_r = "None" if walk is None else f"view({walk.balance})"
        post_r = "None" if posting is None else f"view({posting.balance})"
        return f"walk={walk_r} posting={post_r}"
    if walk.balance != posting.balance:
        return f"balance {walk.balance} != {posting.balance}"
    if len(walk.history_rows) != len(posting.history_rows):
        return (
            f"row count {len(walk.history_rows)} != "
            f"{len(posting.history_rows)}"
        )
    for i, (a, b) in enumerate(zip(walk.history_rows, posting.history_rows)):
        if a != b:
            return f"row[{i}] {a} != {b}"
    return ""


def _assert_views_agree_every_day(loan, seed_user, start, end, *, min_days):
    """Assert walk view == posting view on EVERY day of ``[start, end]`` inclusive.

    Builds a fresh :class:`BalanceContext` per day (its ``as_of`` is that day) and
    compares the walk-based :func:`app.services.balance_at.confirmed_view` against
    the posting-based
    :func:`app.services.loan_payment_service.confirmed_loan_view` -- whole
    ``ConfirmedLedgerView`` equality, so the balance AND every history row must
    match (both ``None`` before origination).  Guards the loop so a vacuous or
    tiny domain -- which would pass without proving anything -- fails instead.

    Args:
        loan: The loan :class:`~app.models.account.Account`.
        seed_user: The ``seed_user`` fixture dict.
        start: First day of the domain (inclusive).
        end: Last day of the domain (inclusive); the caller freezes today past it
            so the posting view's ``as_of <= today`` domain holds.
        min_days: The floor on the domain length (anti-sampling).
    """
    user_id = seed_user["user"].id
    scenario_id = seed_user["scenario"].id
    params = loan_loaders.load_loan_params(loan.id)
    days: list[date] = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    mismatches = []
    for as_of in days:
        ctx = BalanceContext.build(user_id, as_of=as_of)
        walk = balance_at.confirmed_view(ctx, loan)
        posting = confirmed_loan_view(params, scenario_id, as_of)
        if walk != posting:
            mismatches.append((as_of.isoformat(), _describe(walk, posting)))
    assert not mismatches, (
        f"walk view vs posting view disagree on {len(mismatches)}/{len(days)} "
        f"days (first 5): {mismatches[:5]}"
    )
    # Guard the loop: a vacuous or tiny domain proves nothing (anti-sampling).
    assert days[0] == start and days[-1] == end
    assert len(days) >= min_days


class TestConfirmedViewMatchesPostingViewAcrossTheShapeMatrix:
    """Every required shape (plan Section 7.4 + E1c), every day: walk == posting."""

    def test_trued_up_loan_with_payments(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A2's paid shape: origination + true-up + several settled payments.

        The compounding-balance history the amortization table shows, walked day
        by day: each payment's row (real principal / interest / running balance)
        and the headline balance must match the posting reader's.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for period in (seed_periods[1], seed_periods[3], seed_periods[5]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            _assert_views_agree_every_day(
                loan, seed_user, _BEFORE_ALL, end, min_days=400,
            )

    def test_tracking_start_import(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A mid-life import: origination opening + a tracking-start assertion (C1).

        Both views open at ORIGINATION, hold the principal flat across the
        pre-tracking plateau, then reset at the tracking-start -- their rows and
        balance must agree across the reset, day by day.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Import View Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                loan_loaders.load_loan_params(loan.id),
                _ANCHOR_BALANCE, _ANCHOR_DATE,
            )
            db.session.commit()
            for period in (seed_periods[1], seed_periods[3]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            end = seed_periods[5].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            _assert_views_agree_every_day(
                loan, seed_user, _BEFORE_ALL, end, min_days=400,
            )

    def test_arm_rate_step(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """An ARM recast: a rate change lifts later payments' interest and rate.

        A row's ``interest`` and ``interest_rate`` come from the payment's
        governing period; the change (effective 2026-02-01) must move both views'
        post-change rows to 9% identically.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            db.session.add(RateHistory(
                account_id=loan.id, effective_date=date(2026, 2, 1),
                interest_rate=Decimal("0.09"),
            ))
            db.session.flush()
            for period in (seed_periods[1], seed_periods[3], seed_periods[5]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            # Prove the ARM step is LIVE in the rows both views build (not a shared
            # no-op): a post-change payment's row carries the 9% rate.
            ctx = BalanceContext.build(seed_user["user"].id, as_of=end)
            view = balance_at.confirmed_view(ctx, loan)
            assert any(
                row.interest_rate == Decimal("0.09")
                for row in view.history_rows
            )
            _assert_views_agree_every_day(
                loan, seed_user, _BEFORE_ALL, end, min_days=400,
            )

    def test_escrow(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Each payment carries an escrow slice; the escrow leaves the row's principal.

        A $3,600/yr escrow line means each $1,000 payment splits interest + $300
        escrow + principal, and the escrow posts OFF the liability ledger -- so
        both views' rows move by principal only and must agree every day.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db, escrow_annual=Decimal("3600.00"))
            for period in (seed_periods[1], seed_periods[3], seed_periods[5]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            _assert_views_agree_every_day(
                loan, seed_user, _BEFORE_ALL, end, min_days=400,
            )

    def test_payoff_overpayment(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A payment overpays; the surplus is a Refund, the row shows extra, not debt.

        Trued to $1,000, a $1,500 payment pays off the loan and routes the
        surplus to a Refund (Asset) -- neither view's balance falls below 0, and
        the paid-off row's ``extra_payment`` carries the surplus above the
        contractual P&I identically.
        """
        with app.app_context():
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_ORIGINATION_PRINCIPAL,
                anchor_balance=Decimal("1000.00"), anchor_date=_ANCHOR_DATE,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
                name="Payoff View Loan",
            )
            _settle(
                seed_user, db, loan, seed_periods[1], amount=Decimal("1500.00"),
            )
            _settle(
                seed_user, db, loan, seed_periods[3], amount=Decimal("1500.00"),
            )
            db.session.commit()
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            _assert_views_agree_every_day(
                loan, seed_user, _BEFORE_ALL, end, min_days=400,
            )

    def test_underpayment_grows_the_balance(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A payment below the interest due leaves NEGATIVE principal (D5).

        Trued to $100,000 at 6% the monthly interest is ~$500; a $200 payment
        pays down NEGATIVE principal (the balance grows), which the row surfaces
        rather than clamps.  Both views must carry the same negative principal and
        rising balance, day by day.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(
                seed_user, db, loan, seed_periods[1], amount=Decimal("200.00"),
            )
            _settle(
                seed_user, db, loan, seed_periods[3], amount=Decimal("200.00"),
            )
            db.session.commit()
            # Prove the shape is REALIZED: at least one row's principal is < 0.
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            ctx = BalanceContext.build(seed_user["user"].id, as_of=end)
            view = balance_at.confirmed_view(ctx, loan)
            assert any(row.principal < Decimal("0.00") for row in view.history_rows)
            _assert_views_agree_every_day(
                loan, seed_user, _BEFORE_ALL, end, min_days=400,
            )

    def test_late_settled_payment(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A payment settled in a LATER period than its due date (R-A / C2).

        The payment is budgeted to period 1 (its due date) but SETTLED in
        period 3, so it is visible only from period 3 while its row stays dated at
        the period-1 installment.  Between the due date and the settled date the
        row is ABSENT from both views; from the settled date on it is present and
        dated back at the installment -- the visible-filter case the walk builder
        re-accumulates over, and it must match the posting reader every day.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            # Due in period 1, but settled (paid_at) in period 3 -- visible late.
            _settle(
                seed_user, db, loan, seed_periods[1],
                paid_at=settle_instant_on(seed_periods[3].start_date),
            )
            _settle(seed_user, db, loan, seed_periods[2])
            db.session.commit()
            # Prove the shape: the period-1 payment (shadows[0], sorted by
            # pay-period start) is settled LATE -- visible in period 3, but its
            # installment (due date) precedes period 3.
            params = loan_loaders.load_loan_params(loan.id)
            shadows = loan_loaders.settled_income_shadows(
                loan.id, seed_user["scenario"].id,
            )
            late = shadows[0]
            assert loan_ledger.payment_visible_on(
                late,
            ) == seed_periods[3].start_date
            assert loan_loaders.loan_payment_due_date(
                late, params.payment_day,
            ) < seed_periods[3].start_date
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            _assert_views_agree_every_day(
                loan, seed_user, _BEFORE_ALL, end, min_days=400,
            )

    def test_biweekly_due_month_collision(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Two payments in one due month show as TWO rows dated the same month.

        Periods 3 (02-13..02-26) and 4 (02-27..03-12) both start in February, so
        payments budgeted to them satisfy the SAME monthly installment (no stored
        due_date -> the shared fallback dates both at that month's payment day).
        The ledger keeps the true due date -- two rows in one month -- where the
        resolver's DISPLAY redistribution would split them; both LEDGER views keep
        both rows, byte-equal.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[3])
            _settle(seed_user, db, loan, seed_periods[4])
            db.session.commit()
            # Prove the COLLISION: both payments resolve to the SAME installment.
            params = loan_loaders.load_loan_params(loan.id)
            shadows = loan_loaders.settled_income_shadows(
                loan.id, seed_user["scenario"].id,
            )
            dues = {
                loan_loaders.loan_payment_due_date(s, params.payment_day)
                for s in shadows
            }
            assert len(shadows) == 2 and len(dues) == 1
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            _assert_views_agree_every_day(
                loan, seed_user, _BEFORE_ALL, end, min_days=400,
            )

    def test_pre_anchor_payment(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A payment whose due date PRECEDES the latest anchor (read-switch boundary).

        The true-up is dated 2026-03-15; a payment due 2026-02-01 precedes it, so
        the walk resets the balance at the true-up and the pre-anchor payment's
        principal is subsumed -- the posting reader sums the same correction, and
        both views' rows and balances must agree every day.
        """
        with app.app_context():
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_ORIGINATION_PRINCIPAL,
                anchor_balance=_ANCHOR_BALANCE, anchor_date=date(2026, 3, 15),
                rate=_RATE, origination_date=_ORIGINATION_DATE,
                name="Pre-Anchor View Loan",
            )
            _settle(
                seed_user, db, loan, seed_periods[1], amount=Decimal("2000.00"),
            )
            db.session.commit()
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            _assert_views_agree_every_day(
                loan, seed_user, _BEFORE_ALL, end, min_days=400,
            )


class TestConfirmedViewOracleHasTeeth:
    """The harness FAILS on a ROW divergence -- it does not pass vacuously."""

    def test_a_forced_row_divergence_makes_the_harness_fail(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Bump ONE history row's principal by $1.00 and the harness raises.

        The balance is left untouched, so this proves the harness compares the
        ROWS (not just the balance) -- the negative control the row matrix rests
        on (plan Section 7.3).
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1])
            db.session.commit()
            end = seed_periods[3].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))

            real_view = confirmed_loan_view

            def bump_first_row(params, scenario_id, as_of):
                view = real_view(params, scenario_id, as_of)
                if view is None or not view.history_rows:
                    return view
                rows = list(view.history_rows)
                rows[0] = dataclasses.replace(
                    rows[0], principal=rows[0].principal + Decimal("1.00"),
                )
                return dataclasses.replace(view, history_rows=rows)

            monkeypatch.setattr(
                "tests.test_services.test_confirmed_view_oracle."
                "confirmed_loan_view",
                bump_first_row,
            )
            with pytest.raises(AssertionError, match="disagree"):
                _assert_views_agree_every_day(
                    loan, seed_user, _BEFORE_ALL, end, min_days=1,
                )


class TestBrokenLoanFoldsWherePostingViewIsNone:
    """B-12: a broken loan folds where the partial posting reader gives up."""

    def test_broken_loan_folds_where_posting_view_returns_none(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Clearing the genesis postings makes the posting view None; the fold answers.

        A configured, originated loan whose opening / correction postings are
        deleted (a cold cache) is exactly the state the posting reader returns
        ``None`` for (no opening to sum).  The walk reads SOURCE facts, so the
        walk view is UNCHANGED by the deletion and still answers -- the finding
        B-12 repairable-cache decision the scalar and map already took (steps
        C3b1 / C3b3).  Pinned as a DELIBERATE divergence, not folded into the
        every-day equality.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1])
            _settle(seed_user, db, loan, seed_periods[3])
            db.session.commit()
            as_of = seed_periods[4].start_date
            freeze_today(monkeypatch, as_of + timedelta(days=1))
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            params = loan_loaders.load_loan_params(loan.id)

            ctx = BalanceContext.build(user_id, as_of=as_of)
            before = balance_at.confirmed_view(ctx, loan)
            assert before is not None
            # They agree BEFORE the cache is cleared.
            assert before == confirmed_loan_view(params, scenario_id, as_of)

            clear_loan_ledger(loan.id)

            # The posting reader can no longer answer; the fold is unchanged.
            assert confirmed_loan_view(params, scenario_id, as_of) is None
            after = balance_at.confirmed_view(
                BalanceContext.build(user_id, as_of=as_of), loan,
            )
            assert after == before


class TestRawLoanTransactionDivergesTheViews:
    """N-11: a raw transaction typed onto a loan diverges the two views."""

    def test_a_forced_raw_loan_transaction_diverges_the_views(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A direct raw transaction on the loan moves the posting view, not the fold.

        Bypassing ruling D4's create guard with a direct settled-cash insert (the
        shape a legacy row would be), a $300 transaction posts onto the loan's
        linked ledger.  The posting view counts it (its balance moves and it gains
        a non-payment event); the walk view -- transfer shadows only -- cannot see
        it.  This is why the shape is forbidden at source (BG), not folded; the
        guard's own home is ``test_transaction_guards.py``.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1])
            db.session.commit()
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            params = loan_loaders.load_loan_params(loan.id)
            as_of = seed_periods[3].start_date
            freeze_today(monkeypatch, as_of + timedelta(days=1))

            ctx = BalanceContext.build(user_id, as_of=as_of)
            before = balance_at.confirmed_view(ctx, loan)
            assert before == confirmed_loan_view(params, scenario_id, as_of)

            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[1], Decimal("300.00"),
                account=loan, name="Typed On Loan",
                paid_at=settle_instant_on(seed_periods[1].start_date),
            )
            db.session.commit()

            after_walk = balance_at.confirmed_view(
                BalanceContext.build(user_id, as_of=as_of), loan,
            )
            after_posting = confirmed_loan_view(params, scenario_id, as_of)
            # The fold is blind to the raw transaction; the posting view is not.
            assert after_walk == before
            assert after_posting.balance != after_walk.balance
            assert abs(
                after_posting.balance - after_walk.balance
            ) == Decimal("300.00")
