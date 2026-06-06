"""End-to-end integration test for Symptom #3 -- confirmed transfers reduce loan principal.

Commit 14 of the financial-calculation audit remediation locks the
single most important behavioural promise of E-18: when a user marks
a PITI transfer as paid through the real grid workflow, the loan
resolver's ``current_balance`` drops by exactly the principal portion
of that payment.  Escrow and interest portions never reduce
principal (E-01 split invariant).  Projected transfers never reduce
principal (only ``is_confirmed`` payments are replayed).

The test drives the live ``transfer_service`` / ``transactions.py``
``mark_done`` path -- not a manual ``status_id`` overwrite -- so the
assertions prove the assembled production pipeline behaves correctly:
the income shadow on the loan account moves to a settled status,
:func:`loan_payment_service.get_payment_history` lists it as a
confirmed :class:`PaymentRecord`, :func:`load_loan_context` prepares
it for the engine, and :func:`loan_resolver.resolve_loan` reduces
``current_balance`` by the principal portion only.

Test IDs map to the Commit 14 plan in
``docs/audits/financial_calculations/remediation_plan.md`` Section 9
(C14-1 through C14-4).  C14-5 (the loan-card display reflects the
reduction) is intentionally deferred to Commit 15, when the dashboard
card is routed through the resolver; until then the card renders the
stored ``current_principal`` column unchanged by settles (the
symptom-#3 freeze).

Every monetary expectation carries the arithmetic in a comment so a
future reader can verify the assertion by hand.  Hand-computed
expectations follow the Symptom #3 worked example in
``docs/audits/financial_calculations/05_symptoms.md`` lines 791-817
(adjusted to a $1,798.65 contractual P&I so the same arithmetic ties
into the C13-5 fixed-rate replay test).

A real defect would surface here as either (a) the resolver balance
not dropping, or (b) the drop including the escrow / interest
portions.  Either case is a Commit-14-scope production fix per the
plan's "Note for this commit" (CLAUDE.md rule 4) -- not a
band-aid in the test.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LoanAnchorSourceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_features import EscrowComponent
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.services import (
    account_service,
    loan_payment_service,
    loan_resolver,
    transfer_service,
)


# -- Hand-computed reference values -----------------------------------------
#
# All test scenarios use a single fixed-rate mortgage to keep the
# arithmetic transparent.  The contractual P&I value matches the
# C13-5 resolver unit test in
# ``tests/test_services/test_loan_resolver.py`` so the integration
# layer's hand-computed cumulative reduction ties out byte-for-byte
# with the resolver-level lock.
#
# Loan: $300,000 fixed-rate, 6% annual, 360 months, origination
# 2026-01-01, payment_day=1.
#
#     monthly_rate   = 0.06 / 12 = 0.005
#     contractual_pi = amortize(300000, 0.06, 360) = $1,798.65
#
# Per-payment arithmetic (each starts from the prior balance):
#
#     m1: i = 300000.00 * 0.005   = 1500.00
#         p = 1798.65 - 1500.00   =  298.65
#         bal = 300000.00 - 298.65 = 299,701.35
#     m2: i = 299701.35 * 0.005   = 1498.51 (HALF_UP from 1498.50675)
#         p = 1798.65 - 1498.51   =  300.14
#         bal = 299701.35 - 300.14 = 299,401.21
#     m3: i = 299401.21 * 0.005   = 1497.01 (HALF_UP from 1497.00605)
#         p = 1798.65 - 1497.01   =  301.64
#         bal = 299401.21 - 301.64 = 299,099.57
#
# E-01 invariant: a transfer carrying $400 of escrow per month sends
# a total of $2,198.65 (P&I + escrow) through the checking account,
# but only the $298.65 principal portion reduces the loan balance --
# the escrow $400 never touches principal.
ORIGINATION_DATE = date(2026, 1, 1)
ORIGINAL_PRINCIPAL = Decimal("300000.00")
INTEREST_RATE = Decimal("0.06000")  # Numeric(7,5) storage scale.
TERM_MONTHS = 360
CONTRACTUAL_PI = Decimal("1798.65")
MONTHLY_ESCROW_ANNUAL = Decimal("4800.00")  # $400/mo -> $4,800/yr.
PITI_WITH_ESCROW = Decimal("2198.65")       # 1798.65 P&I + 400.00 escrow.

# Hand-computed balances after each cumulative settle (arithmetic above).
BALANCE_AFTER_1 = Decimal("299701.35")
BALANCE_AFTER_2 = Decimal("299401.21")
BALANCE_AFTER_3 = Decimal("299099.57")

# Principal portions per month (the "reduction" amounts).  Stored as
# constants so the test can assert the diff directly, not just the
# absolute balance.
PRINCIPAL_PORTION_M1 = Decimal("298.65")


# -- Helpers ----------------------------------------------------------------


def _create_mortgage(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    *,
    user_id: int,
    anchor_period_id: int,
    original_principal: Decimal = ORIGINAL_PRINCIPAL,
    current_principal: Decimal | None = None,
    interest_rate: Decimal = INTEREST_RATE,
    term_months: int = TERM_MONTHS,
    origination_date: date = ORIGINATION_DATE,
    payment_day: int = 1,
    escrow_annual: Decimal | None = None,
):
    """Materialise a fixed-rate mortgage account ready for the resolver.

    Creates the :class:`Account` (Mortgage type via the canonical
    factory), the matching :class:`LoanParams`, and the origination
    :class:`LoanAnchorEvent` that Commit 12's migration would have
    inserted for any pre-existing loan.  New-loan-creation paths
    (loan dashboard / E-18 UX) do not yet auto-insert the origination
    event -- see Follow-up F-9 -- so the test creates it explicitly.

    Args:
        user_id: Owner of the loan account.
        anchor_period_id: PayPeriod id to anchor the Account against
            (E-19 / Commit 3 requires every Account to have one).
        original_principal: Loan ``original_principal`` (Numeric(12,2)).
        current_principal: Stored ``current_principal`` for the
            non-authoritative seed column.  Defaults to
            ``original_principal``.
        interest_rate: Annual rate as a decimal fraction (e.g. 0.06).
        term_months: Contractual term in months.
        origination_date: Loan origination date; also the
            ``LoanAnchorEvent.anchor_date`` for the origination event.
        payment_day: Day-of-month the lender expects payment on.
        escrow_annual: If provided, attach a single
            :class:`EscrowComponent` with this annual amount so
            ``calculate_monthly_escrow`` returns a non-zero figure.

    Returns:
        Tuple of (account, loan_params, origination_event) all
        flushed but not committed; the caller commits.
    """
    if current_principal is None:
        current_principal = original_principal

    loan_type = (
        db.session.query(AccountType).filter_by(name="Mortgage").one()
    )
    account = account_service.create_account(
        user_id=user_id,
        account_type_id=loan_type.id,
        name="Principal-Settle Mortgage",
        anchor_balance=original_principal,
        anchor_period_id=anchor_period_id,
        notes="C14 mortgage origination",
    )
    db.session.flush()

    loan_params = LoanParams(
        account_id=account.id,
        original_principal=original_principal,
        current_principal=current_principal,
        interest_rate=interest_rate,
        term_months=term_months,
        origination_date=origination_date,
        payment_day=payment_day,
        is_arm=False,
    )
    db.session.add(loan_params)
    db.session.flush()

    origination_event = LoanAnchorEvent(
        account_id=account.id,
        anchor_date=origination_date,
        anchor_balance=original_principal,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    )
    db.session.add(origination_event)
    db.session.flush()

    if escrow_annual is not None:
        db.session.add(EscrowComponent(
            account_id=account.id,
            name="Property Tax",
            annual_amount=escrow_annual,
        ))
        db.session.flush()

    return account, loan_params, origination_event


def _create_piti_transfer(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    *,
    user_id: int,
    from_account_id: int,
    to_account_id: int,
    pay_period_id: int,
    scenario_id: int,
    amount: Decimal,
    category_id: int,
):
    """Create a PITI transfer in Projected status via the transfer service.

    Routed through :func:`transfer_service.create_transfer` so the
    two-shadow invariant (Transfer Invariant 1) is established the
    same way the production transfer route would.  Status is
    Projected; the caller settles via the live ``mark_done`` route
    when it wants the resolver to count the payment as confirmed.
    """
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    xfer = transfer_service.create_transfer(
        user_id=user_id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        pay_period_id=pay_period_id,
        scenario_id=scenario_id,
        amount=amount,
        status_id=projected_id,
        category_id=category_id,
        notes="C14 PITI transfer",
    )
    return xfer


def _income_shadow(transfer_id: int, loan_account_id: int) -> Transaction:
    """Return the income shadow on the loan account for a given transfer.

    The income shadow is the one whose ``account_id`` matches the
    transfer's destination (the loan); its settle drives the
    :func:`loan_payment_service.get_payment_history` feed.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id == transfer_id,
            Transaction.account_id == loan_account_id,
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
        )
        .one()
    )


def _resolve_balance(
    account_id: int, loan_params, scenario_id: int, as_of: date,
) -> Decimal:
    """Load the resolver inputs and return the current_balance Decimal.

    Mirrors the consumer pattern Commit 15 will route through:
    load anchor events from the database, prepare the payment feed
    via :func:`loan_payment_service.load_loan_context`, then call
    :func:`loan_resolver.resolve_loan`.
    """
    anchor_events = (
        db.session.query(LoanAnchorEvent)
        .filter_by(account_id=account_id)
        .all()
    )
    context = loan_payment_service.load_loan_context(
        account_id, scenario_id, loan_params,
    )
    state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            loan_params, anchor_events, context.payments,
            context.rate_changes,
        ),
        as_of,
    )
    return state.current_balance


# -- Test class -------------------------------------------------------------


class TestLoanPrincipalSettles:
    """Symptom #3 lock: confirmed transfers reduce resolved principal.

    Each test creates a fresh mortgage, drives one or more transfers
    through the real ``mark_done`` route, then calls the resolver
    and asserts the hand-computed balance.  No status is set
    manually -- the production state-machine path is the entire
    point of the test.
    """

    def _setup_loan(self, seed_user, seed_periods, *, escrow=False):
        """Materialise the mortgage and return ids the tests need.

        Helper for the common pre-action setup: create the loan,
        commit, and hand back the ids tests need to drive the
        ``mark_done`` route.  Returns a dict so additions (e.g.
        the escrow components for C14-2) can be returned without
        changing every test's unpacking shape.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        user = seed_user["user"]
        periods = seed_periods

        category = seed_user["categories"]["Car Payment"]

        escrow_annual = MONTHLY_ESCROW_ANNUAL if escrow else None
        mortgage, loan_params, _origination = _create_mortgage(
            user_id=user.id,
            anchor_period_id=periods[0].id,
            escrow_annual=escrow_annual,
        )
        db.session.commit()

        return {
            "user_id": user.id,
            "scenario_id": scenario.id,
            "checking_id": checking.id,
            "mortgage_id": mortgage.id,
            "category_id": category.id,
            "loan_params": loan_params,
            "periods": periods,
        }

    def test_settled_transfer_reduces_principal(  # C14-1
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C14-1: One settled PITI transfer drops the resolved principal by
        exactly the hand-computed principal portion.

        Setup: fresh $300k / 6% / 360mo fixed-rate mortgage with the
        Commit-12-shaped origination anchor at 2026-01-01.  One PITI
        transfer of $1,798.65 in pay period 3 (start 2026-02-13).
        The grid issues a POST to ``/transactions/<id>/mark-done``
        on the loan-side income shadow; the route routes through
        ``transfer_service.update_transfer`` so both shadows reach
        the DONE status (``is_settled = True``) and the
        loan-payment feed picks the transfer up as a confirmed
        :class:`PaymentRecord`.

        Hand-computed expectation:

            interest = 300000.00 * 0.005          = 1500.00
            principal_portion = 1798.65 - 1500.00 =  298.65
            balance = 300000.00 - 298.65          = 299,701.35
        """
        with app.app_context():
            ctx = self._setup_loan(seed_user, seed_periods)

            # Sanity floor: the resolver returns the anchor balance
            # when no confirmed payments are present.  Without this
            # the cumulative-diff assertion could not distinguish a
            # broken setup from a working settle.
            balance_before = _resolve_balance(
                ctx["mortgage_id"], ctx["loan_params"],
                ctx["scenario_id"], date(2026, 3, 1),
            )
            assert balance_before == ORIGINAL_PRINCIPAL, (
                f"Expected anchor balance {ORIGINAL_PRINCIPAL} before "
                f"settle; got {balance_before}.  Likely the origination "
                f"event was not created or the resolver inputs drifted."
            )

            xfer = _create_piti_transfer(
                user_id=ctx["user_id"],
                from_account_id=ctx["checking_id"],
                to_account_id=ctx["mortgage_id"],
                pay_period_id=ctx["periods"][3].id,
                scenario_id=ctx["scenario_id"],
                amount=CONTRACTUAL_PI,
                category_id=ctx["category_id"],
            )
            db.session.commit()

            income_shadow_id = _income_shadow(xfer.id, ctx["mortgage_id"]).id

            resp = auth_client.post(
                f"/transactions/{income_shadow_id}/mark-done",
            )
            assert resp.status_code == 200, (
                f"mark_done returned {resp.status_code}; body={resp.data!r}"
            )

            # Re-fetch the shadow so the test's session sees the
            # commit produced by the request.
            db.session.expire_all()
            settled_shadow = db.session.get(Transaction, income_shadow_id)
            assert settled_shadow.status.is_settled is True, (
                f"Expected income shadow to be settled after mark_done; "
                f"status={settled_shadow.status.name!r}"
            )

            balance_after = _resolve_balance(
                ctx["mortgage_id"], ctx["loan_params"],
                ctx["scenario_id"], date(2026, 3, 1),
            )

            # Primary symptom-#3 assertion: principal drops by exactly
            # the hand-computed portion.
            assert balance_after == BALANCE_AFTER_1, (
                f"Expected balance after one settle = {BALANCE_AFTER_1}; "
                f"got {balance_after}."
            )
            assert (
                balance_before - balance_after == PRINCIPAL_PORTION_M1
            ), (
                f"Expected principal reduction = {PRINCIPAL_PORTION_M1}; "
                f"got {balance_before - balance_after}."
            )

    def test_escrow_interest_do_not_reduce_principal(  # C14-2
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C14-2: E-01 split -- escrow rides through checking but never
        reduces loan principal.

        Setup: same $300k fixed-rate mortgage but with a $400/month
        escrow component attached, so the user's PITI transfer is
        $2,198.65 ($1,798.65 P&I + $400.00 escrow).  The
        ``loan_payment_service.prepare_payments_for_engine`` step
        subtracts the escrow from the above-P&I excess before the
        engine sees the payment, so the resolver replays only the
        $1,798.65 P&I portion and reduces principal by $298.65 --
        not $698.65.

        Hand-computed expectation:

            interest = 300000.00 * 0.005          = 1500.00
            principal_portion = 1798.65 - 1500.00 =  298.65
            balance = 300000.00 - 298.65          = 299,701.35

        Sanity counter-example: if escrow leaked into principal the
        balance would be 300000.00 - 698.65 = 299,301.35.  The test
        asserts the correct value AND the absence of the wrong
        value so a regression that silently swaps the formulae fails
        loudly.
        """
        with app.app_context():
            ctx = self._setup_loan(
                seed_user, seed_periods, escrow=True,
            )

            xfer = _create_piti_transfer(
                user_id=ctx["user_id"],
                from_account_id=ctx["checking_id"],
                to_account_id=ctx["mortgage_id"],
                pay_period_id=ctx["periods"][3].id,
                scenario_id=ctx["scenario_id"],
                amount=PITI_WITH_ESCROW,
                category_id=ctx["category_id"],
            )
            db.session.commit()

            shadow_id = _income_shadow(xfer.id, ctx["mortgage_id"]).id

            resp = auth_client.post(
                f"/transactions/{shadow_id}/mark-done",
            )
            assert resp.status_code == 200, (
                f"mark_done returned {resp.status_code}; body={resp.data!r}"
            )

            db.session.expire_all()

            # E-01 cross-cut: the expense shadow on checking carries
            # the full $2,198.65 PITI -- so when consumers route
            # through the balance resolver (Commits 5-10) checking
            # is debited by the full amount.  We assert the shadow
            # itself here rather than driving the balance resolver
            # because Commit 14 is loan-focused; the checking-side
            # producer assertion belongs to the cross-page lock in
            # Commit 11 (already landed).
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            expense_shadow = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id == xfer.id,
                    Transaction.account_id == ctx["checking_id"],
                    Transaction.transaction_type_id == expense_type_id,
                    Transaction.is_deleted.is_(False),
                )
                .one()
            )
            assert expense_shadow.effective_amount == PITI_WITH_ESCROW, (
                f"Expected checking expense shadow = {PITI_WITH_ESCROW} "
                f"(full PITI per E-01); got "
                f"{expense_shadow.effective_amount}."
            )

            balance_after = _resolve_balance(
                ctx["mortgage_id"], ctx["loan_params"],
                ctx["scenario_id"], date(2026, 3, 1),
            )
            assert balance_after == BALANCE_AFTER_1, (
                f"E-01 violation: expected principal-only reduction to "
                f"{BALANCE_AFTER_1}; got {balance_after}.  If the value "
                f"is 299301.35 escrow leaked into principal; if it is "
                f"300000.00 the settle did not propagate."
            )
            assert balance_after != Decimal("299301.35"), (
                "E-01 violation: balance equals the escrow-included "
                "reduction (300000 - 698.65), meaning escrow was "
                "treated as extra principal."
            )

    def test_unsettled_transfer_no_reduction(  # C14-3
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C14-3: A Projected (unconfirmed) transfer leaves the resolver
        balance unchanged.

        Future commitments are not historical fact.  Only
        ``is_confirmed`` payments (those whose status has
        ``is_settled = True``) reduce the principal.  Without this
        guard the resolver would double-count every projected
        recurrence-engine row alongside the actual settlements.
        The ``auth_client`` fixture is included to keep the test
        symmetric with the settle-driven cases (and to confirm the
        Flask test context is live), but no HTTP POST is issued --
        the transfer remains Projected.
        """
        _ = auth_client  # Fixture kept for context-setup symmetry.
        with app.app_context():
            ctx = self._setup_loan(seed_user, seed_periods)

            xfer = _create_piti_transfer(
                user_id=ctx["user_id"],
                from_account_id=ctx["checking_id"],
                to_account_id=ctx["mortgage_id"],
                pay_period_id=ctx["periods"][3].id,
                scenario_id=ctx["scenario_id"],
                amount=CONTRACTUAL_PI,
                category_id=ctx["category_id"],
            )
            db.session.commit()

            shadow = _income_shadow(xfer.id, ctx["mortgage_id"])
            assert shadow.status.is_settled is False, (
                f"Pre-condition: shadow must start unsettled; "
                f"status={shadow.status.name!r}"
            )

            balance = _resolve_balance(
                ctx["mortgage_id"], ctx["loan_params"],
                ctx["scenario_id"], date(2026, 3, 1),
            )
            assert balance == ORIGINAL_PRINCIPAL, (
                f"Projected transfer must not reduce principal; "
                f"expected {ORIGINAL_PRINCIPAL}, got {balance}."
            )

    def test_multiple_settlements_cumulative(  # C14-4
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C14-4: Three settled transfers reduce the resolved principal by
        the sum of their principal portions.

        Setup: same $300k fixed-rate loan; three PITI transfers in
        pay periods 3 (Feb 13), 5 (Mar 13), and 7 (Apr 10) so each
        lands in a distinct calendar month -- avoids the biweekly
        same-month redistribution path in
        :func:`loan_payment_service.prepare_payments_for_engine`
        and keeps the arithmetic in lockstep with the C13-5
        resolver-level test.

        Hand-computed cumulative reductions (see module docstring
        arithmetic table):

            after 1 settle:  bal = 299,701.35  (-298.65)
            after 2 settles: bal = 299,401.21  (-300.14)
            after 3 settles: bal = 299,099.57  (-301.64)

        Asserting the running balance after each settle (not just
        the final state) confirms the resolver composes correctly:
        a future bug that reduces by the constant first-month
        amount instead of recomputing interest off the new balance
        would pass on the first settle and fail on the second.
        """
        with app.app_context():
            ctx = self._setup_loan(seed_user, seed_periods)

            settle_targets = [
                (ctx["periods"][3].id, BALANCE_AFTER_1),
                (ctx["periods"][5].id, BALANCE_AFTER_2),
                (ctx["periods"][7].id, BALANCE_AFTER_3),
            ]

            for period_id, expected_balance in settle_targets:
                xfer = _create_piti_transfer(
                    user_id=ctx["user_id"],
                    from_account_id=ctx["checking_id"],
                    to_account_id=ctx["mortgage_id"],
                    pay_period_id=period_id,
                    scenario_id=ctx["scenario_id"],
                    amount=CONTRACTUAL_PI,
                    category_id=ctx["category_id"],
                )
                db.session.commit()

                shadow_id = _income_shadow(
                    xfer.id, ctx["mortgage_id"],
                ).id
                resp = auth_client.post(
                    f"/transactions/{shadow_id}/mark-done",
                )
                assert resp.status_code == 200, (
                    f"mark_done returned {resp.status_code}; "
                    f"body={resp.data!r}"
                )
                db.session.expire_all()

                balance = _resolve_balance(
                    ctx["mortgage_id"], ctx["loan_params"],
                    ctx["scenario_id"], date(2026, 5, 1),
                )
                assert balance == expected_balance, (
                    f"Cumulative settle through period {period_id}: "
                    f"expected {expected_balance}, got {balance}.  "
                    f"A constant-reduction bug would yield "
                    f"{ORIGINAL_PRINCIPAL - PRINCIPAL_PORTION_M1 * 3}."
                )


@pytest.mark.parametrize(
    "as_of_offset_months",
    [1, 2, 3, 6],
    ids=["as_of_+1mo", "as_of_+2mo", "as_of_+3mo", "as_of_+6mo"],
)
def test_resolved_balance_stable_across_future_as_of(  # noqa: D401
    app, auth_client, seed_user, seed_periods, db, as_of_offset_months,
):
    """Symptom-#3 corollary: once a single transfer settles in period 3
    (Feb 13), the resolved balance is the same for any ``as_of`` after
    the payment regardless of how far into the future the consumer
    asks.

    Pre-fix the displayed principal moved only when the user
    re-edited the form -- it would have read $300,000.00 on every
    surface for every ``as_of`` value.  Post-fix the balance reflects
    the settle and stays at $299,701.35 for as_of in {Mar, Apr, May,
    Aug} 2026 because no further payments have arrived.  Parametrising
    over multiple ``as_of`` proves the replay does not silently reset
    or drift when the resolver is called multiple times for the same
    underlying state.
    """
    with app.app_context():
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        user = seed_user["user"]
        periods = seed_periods
        category = seed_user["categories"]["Car Payment"]

        mortgage, loan_params, _origination = _create_mortgage(
            user_id=user.id,
            anchor_period_id=periods[0].id,
        )
        db.session.commit()

        xfer = _create_piti_transfer(
            user_id=user.id,
            from_account_id=checking.id,
            to_account_id=mortgage.id,
            pay_period_id=periods[3].id,
            scenario_id=scenario.id,
            amount=CONTRACTUAL_PI,
            category_id=category.id,
        )
        db.session.commit()

        shadow_id = _income_shadow(xfer.id, mortgage.id).id
        resp = auth_client.post(f"/transactions/{shadow_id}/mark-done")
        assert resp.status_code == 200

        db.session.expire_all()
        # Compute the as_of from a fixed base month so the
        # parametrisation does not depend on wall-clock time.
        as_of_month = 2 + as_of_offset_months  # Feb base + offset.
        as_of_year = 2026 + (as_of_month - 1) // 12
        as_of_month = ((as_of_month - 1) % 12) + 1
        as_of = date(as_of_year, as_of_month, 1)

        balance = _resolve_balance(
            mortgage.id, loan_params, scenario.id, as_of,
        )
        assert balance == BALANCE_AFTER_1, (
            f"Resolver drifted for as_of={as_of}: expected "
            f"{BALANCE_AFTER_1}, got {balance}."
        )
