"""Integration: a derive-from-loan transfer's cash amount is DERIVED.

Commit 5 of the loan rate-period work.  A recurring loan-payment transfer
flagged ``derive_from_loan`` is worth the loan's current monthly payment (P&I +
escrow), and an escrow change reflows that amount WITHOUT regenerating the
transfer -- the stored ``Transfer.amount`` stays put.

**The MECHANISM changed at plan step X-au-g-2c-2 and the CLAIM did not**, which
is why the figures below are untouched.  A read-time OVERRIDE
(``LoanPricing.live_cash``) used to supersede each shadow's stored figure; a
shadow stores no figure at all now -- it declares ``PARENT_TRANSFER`` and is
priced by amount rule 4 -- so the same arithmetic arrives through the amount
model and there is no stale copy left for an override to beat.  Every case here
asks :func:`_derived_cash`, which is what a SCREEN reads
(``cash_ledger.amounts_by_id``).

Every monetary expectation is hand-computed with the arithmetic shown.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.models.loan_features import RateHistory
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    cash_ledger,
    loan_ledger,
    loan_posting_service,
    transfer_recurrence,
)
from app.services import escrow_calculator
from app.services.loan_loaders import (
    load_escrow_lines,
    loan_payment_due_date,
)
from app.services.rate_period_engine import monthly_due_date
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    add_escrow_line,
    create_loan_account,
    loan_params_for,
    make_cadence_rule,
)
from tests.oracles.recurrence_baseline import MONTHLY
from app.services.row_valuation import owned_contribution


def _derived_cash(seed_user, rows):
    """What a SCREEN shows for *rows* -- the figure the amount model resolves.

    Plan step X-au-c2b split ``live_loan_transfer_amounts`` into an owner-scoped
    DERIVATION and a per-row lookup, and collapsed the settle-time twin
    ``live_loan_payment_amount`` into the same rule; plan step X-au-g-2c-2 then
    deleted that rule outright, because a transfer shadow stores no figure for a
    read-time override to supersede.  So the ``{transaction_id: Decimal}`` map
    this file grades is now the amount model's own
    (``cash_ledger.amounts_by_id``), and the assertions below keep their
    original figures.

    **It covers EVERY row it is given, where the override map covered only the
    rows that had one.**  That is the substantive difference, and two cases turn
    on it: a manual payment and a non-derive transfer used to be ABSENT from the
    map (``assert overrides == {}``) and are now PRESENT with their definition's
    own price, which is a sharper claim -- absence could not distinguish "no
    override" from "not priced at all".

    A FRESH basis per call, deliberately: the cases that mutate an escrow line
    and re-read must see the new figure, and a basis memoizes each loan for the
    length of the read pass it was built for.

    Args:
        seed_user: The owner fixture; its user and scenario pin the basis.
        rows: The shadows to price.

    Returns:
        ``{transaction_id: Decimal}`` covering every row.
    """
    basis = cash_ledger.amount_basis(
        seed_user["user"].id, seed_user["scenario"].id,
    )
    return cash_ledger.amounts_by_id(rows, basis)

def _build_derived_loan_transfer(seed_user, escrow_annual):
    """Create a $200k/6%/360 mortgage + a derive_from_loan recurring transfer.

    Returns ``(loan_account, escrow_version, scenario_id)``.  The
    transfer's stored default amount is intentionally a stale value so
    the test can prove the live override, not the stored amount, drives
    the result.

    The loan is built by the shared :func:`create_loan_account` factory, so it
    carries the genesis posting ledger every production loan-write path opens in
    the same transaction as the ``LoanParams`` insert (``routes/loan/params.py``)
    -- a hand-rolled ``LoanParams`` insert would leave it on the no-ledger
    fallback production never takes.
    """
    user = seed_user["user"]
    scenario_id = seed_user["scenario"].id
    checking = seed_user["account"]

    loan = create_loan_account(
        seed_user, db.session, name="Live Mortgage",
        principal=Decimal("200000.00"), rate=Decimal("0.06000"),
        term=360, origination_date=date(2026, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    params = loan_params_for(db.session, loan.id)

    escrow = add_escrow_line(
        db.session, loan.id, "Property Tax", escrow_annual,
        effective_date=params.origination_date,
    )

    # Authored through the write door (plan step R7c-b): the day a rule fires
    # on is its first occurrence's own day, so "the 1st" is a DATE the fixture
    # schedule reaches rather than a separate column.
    template = TransferTemplate(
        user_id=user.id,
        from_account_id=checking.id,
        to_account_id=loan.id,
        name="Live Mortgage Payment",
        # Deliberately stale stored amount -- the live override must win.
        default_amount=Decimal("1.00"),
    )
    # derive_from_loan moved off transfer_templates into the 1:1
    # loan_payment_settings row (decision B); attach it via the relationship.
    template.settings = LoanPaymentSettings(derive_from_loan=True)
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_cadence_rule(
        template, MONTHLY, fires_on_day=1,
    )

    periods = seed_user["periods"] if "periods" in seed_user else None
    return loan, escrow, scenario_id, template, rule, periods


def _loan_transfer_shadows(loan_id, scenario_id):
    """Return the projected shadow transactions of the loan's transfers."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id.isnot(None),
            Transaction.scenario_id == scenario_id,
        )
        .all()
    )


def test_derived_transfer_amount_tracks_escrow_without_regeneration(
    app, db, seed_user, seed_periods,
):
    """The transfer's live cash amount = P&I + escrow, and reflows on escrow change.

    Loan $200,000 / 6% / 360mo, escrow $3,600/yr:
        P&I    = amortize(200000, 0.06, 360) = 1,199.10
        escrow = 3600 / 12 = 300.00
        PITI   = 1,199.10 + 300.00 = 1,499.10
    After escrow rises to $4,800/yr (400.00/mo):
        PITI   = 1,199.10 + 400.00 = 1,599.10
    The stored Transfer.amount never changes (no regeneration); only the
    live override reflects the new escrow.
    """
    with app.app_context():
        loan, escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        assert shadows, "expected generated shadow transactions"

        overrides = _derived_cash(seed_user, shadows)
        # Every shadow of this loan's transfer gets the live PITI.
        assert overrides, "expected live overrides for the derive_from_loan transfer"
        assert all(v == Decimal("1499.10") for v in overrides.values())

        # The stored transfer amounts are untouched (the stale $1.00),
        # proving the amount is live-derived, not regenerated.
        stored_amounts = {
            xfer.amount
            for xfer in db.session.query(Transfer)
            .filter_by(scenario_id=scenario_id)
            .all()
        }
        assert stored_amounts == {Decimal("1.00")}

        # Raise escrow; the live override reflows without regeneration.
        escrow.annual_amount = Decimal("4800.00")
        db.session.commit()

        overrides_after = _derived_cash(seed_user, shadows)
        assert all(v == Decimal("1599.10") for v in overrides_after.values())
        # Still no regeneration: stored transfer amounts unchanged.
        stored_after = {
            xfer.amount
            for xfer in db.session.query(Transfer)
            .filter_by(scenario_id=scenario_id)
            .all()
        }
        assert stored_after == {Decimal("1.00")}


def test_non_derived_transfer_has_no_live_override(
    app, db, seed_user, seed_periods,
):
    """A transfer whose template is NOT derive_from_loan is worth its DEFINITION's price.

    Confirms the loan derivation is dormant unless explicitly enabled (the
    "only new transfers" choice: every pre-existing template is False).  The
    fixture's stored base is a deliberately stale ``$1.00``, so a shadow worth
    ``$1.00`` is one the loan did not price and a shadow worth ``$1,499.10``
    is one it did.

    **It asserted an EMPTY override map until plan step X-au-g-2c-2**, which
    could not tell "the loan did not price this" from "nothing priced this at
    all".  Naming the figure is the stronger claim, and it is the one that
    would catch a shadow that had stopped being priced.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.derive_from_loan = False
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        assert shadows, "expected generated shadow transactions"
        resolved = _derived_cash(seed_user, shadows)
        assert all(v == Decimal("1.00") for v in resolved.values())


def test_derived_transfer_due_date_matches_loan_due_date(
    app, db, seed_user, seed_periods,
):
    """A derive_from_loan transfer is due on the loan's true monthly due date.

    The loan card derives its due dates from LoanParams.payment_day via
    rate_period_engine.monthly_due_date.  The transfer recurrence now uses the
    shared compute_due_date, and the loan template's rule carries
    day_of_month = payment_day (1), so the transfer's parent + both shadows
    land on the 1st of each month -- matching the loan card -- rather than the
    pay-period start (~2 weeks early) they used before.  Over seed_periods
    (biweekly from 2026-01-02), day 1 falls in P2/P4/P6/P8, giving due dates
    2026-02-01, 03-01, 04-01, 05-01.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        created = transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        assert sorted(x.due_date for x in created) == [
            date(2026, 2, 1),
            date(2026, 3, 1),
            date(2026, 4, 1),
            date(2026, 5, 1),
        ]
        for xfer in created:
            # Parent due date equals the loan's contractual monthly due date.
            assert xfer.due_date == monthly_due_date(
                xfer.pay_period.start_date, 1,
            )
            assert xfer.due_date.day == 1
            # Both shadows mirror the parent (Transfer Invariant 3).
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.due_date == xfer.due_date


def test_derived_override_is_per_shadow_date_aware(
    app, db, seed_user, seed_periods,
):
    """A future-dated escrow version changes only the shadows due on/after its date.

    Loan $200k / 6% / 360mo, P&I 1,199.10.  Escrow $3,600/yr (300/mo) from
    origination (2026-01-01), then a NEW version $4,800/yr (400/mo) effective
    2026-03-15 on the SAME line.  The live override resolves escrow per shadow
    INSTALLMENT: a shadow due before 2026-03-15 keeps PITI 1,499.10; one due on
    or after picks up 1,599.10.  A single figure per loan (today's escrow for
    every shadow) would wrongly give them all 1,599.10 -- the bug this
    per-shadow resolution fixes, and the cash side of the cash==split invariant
    for future-dated escrow.

    The partition is by DUE date (ruling D5's contract time, finding N-34); this
    fixture's shadows are due 02-01 / 03-01 / 04-01 / 05-01 against pay periods
    starting 01-30 / 02-27 / 03-27 / 04-24, so the two keyings happen to group
    them identically here.  The test that DISCRIMINATES them is
    :func:`test_live_cash_and_split_agree_on_a_mid_window_escrow_change`, which
    puts the version inside a single payment's period-start-to-due-date window.
    """
    with app.app_context():
        loan, escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        # Append a second version on the SAME line: 400/mo effective 2026-03-15.
        db.session.add(EscrowComponentVersion(
            line_id=escrow.line_id,
            effective_date=date(2026, 3, 15),
            annual_amount=Decimal("4800.00"),
        ))
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        overrides = _derived_cash(seed_user, shadows)
        cutoff = date(2026, 3, 15)
        before = [s for s in shadows if s.due_date < cutoff]
        after = [s for s in shadows if s.due_date >= cutoff]
        assert before and after, (
            "seed_periods must place installments on both sides of 2026-03-15"
        )
        # Old escrow ($300) for pre-effective shadows: 1199.10 + 300 = 1499.10.
        assert all(overrides[s.id] == Decimal("1499.10") for s in before)
        # New escrow ($400) for on/after shadows: 1199.10 + 400 = 1599.10.
        assert all(overrides[s.id] == Decimal("1599.10") for s in after)


def test_live_cash_and_split_agree_on_a_mid_window_escrow_change(
    app, db, auth_client, seed_user, seed_periods,
):
    """An escrow version inside the period-to-due window moves BOTH cash and split.

    The discriminating control for finding N-34's cash==split half.  The
    installment due 2026-03-01 is paid from the pay period starting 2026-02-27,
    so a version effective **2026-02-28** falls STRICTLY inside that window --
    after the period start, before the due date.  The two keyings disagree there,
    and they must not be allowed to disagree with EACH OTHER:

      * DUE-date keying (ruling D5, as built): escrow 500.00, so the live cash is
        P&I 1,199.10 + 500.00 = **1,699.10**, and the split subtracts the same
        500.00 -- interest 200,000 x 0.06 / 12 = 1,000.00, principal
        1,699.10 - 1,000.00 - 500.00 = 199.10 (exactly P&I - interest).
      * PERIOD-START keying (the N-34 defect): escrow 300.00 -> cash 1,499.10.

    Reverting EITHER end alone re-opens the gap this test exists to close: the
    cash would carry one escrow figure while the split backed out the other, and
    the $200.00 difference would land silently in PRINCIPAL, moving the recorded
    balance.  Asserting ``principal == P&I - interest`` is what pins that -- it
    holds only when both ends read the same date.
    """
    with app.app_context():
        loan, escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.add(EscrowComponentVersion(
            line_id=escrow.line_id,
            effective_date=date(2026, 2, 28),
            annual_amount=Decimal("6000.00"),
        ))
        db.session.commit()

        income_shadow = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id.isnot(None),
                Transaction.account_id == loan.id,
                Transaction.scenario_id == scenario_id,
                Transaction.due_date == date(2026, 3, 1),
            )
            .one()
        )
        # The version really is inside the window: after the period start,
        # before the installment it governs.
        assert (
            income_shadow.pay_period.start_date
            < date(2026, 2, 28)
            < income_shadow.due_date
        )

        overrides = _derived_cash(seed_user, [income_shadow])
        assert overrides[income_shadow.id] == Decimal("1699.10")

        resp = auth_client.post(f"/transactions/{income_shadow.id}/mark-done")
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        settled = db.session.get(Transaction, income_shadow.id)
        assert owned_contribution(settled) == Decimal("1699.10")

        (split,) = loan_ledger.compute_loan_payment_splits(loan.id, scenario_id)
        assert split.due_date == date(2026, 3, 1)
        assert split.escrow == Decimal("500.00")
        assert split.interest == Decimal("1000.00")
        # The cash==split invariant: every cent of escrow built into the cash is
        # backed out again, so principal is exactly P&I - interest.
        assert split.principal == Decimal("199.10")
        assert split.excess == Decimal("0.00")
        assert (
            split.interest + split.escrow + split.principal + split.excess
            == owned_contribution(settled)
        )


def test_settling_derived_loan_payment_captures_live_amount(
    app, db, auth_client, seed_user, seed_periods,
):
    """A one-click settle freezes the LIVE payment-date amount, not the estimate.

    Capture-on-settle (escrow redesign, Option A): the transfer's stored
    default is a deliberately stale $1.00, and the operator settles via the
    ``mark_done`` route WITHOUT typing an actual.  The frozen ``actual_amount``
    must be the live PITI (P&I 1,199.10 + escrow 300.00 = 1,499.10), NOT the
    $1.00 estimate -- so the settled cash carries exactly the escrow the genesis
    split subtracts (cash == split).  The split then divides 1,499.10 into
    interest 1,000.00 (200,000 * 0.06 / 12), escrow 300.00, and principal
    199.10 (= P&I 1,199.10 - interest 1,000.00).

    **The shadow's own ``estimated_amount`` is NULL and that is the point of
    the cutover** (plan step X-au-g-2c-2).  It held the generated ``$1.00`` and
    was deliberately left alone, because it was the base the deleted
    ``_manual_shadow_amount`` derived from -- so a settle that wrote it would
    have made the next settle derive from its own output (finding N-259's
    shape).  The column is empty on a derived row, so that hazard has no state
    to occur in, and what a PROJECTED shadow is worth is asked of the amount
    model rather than of the column.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        income_shadow = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id.isnot(None),
                Transaction.account_id == loan.id,
                Transaction.scenario_id == scenario_id,
            )
            .order_by(Transaction.id)
            .first()
        )
        assert income_shadow is not None
        # Pre-settle the shadow is worth the LIVE PITI, and the parent's stale
        # $1.00 is what it would have shown before the cutover.  Asked of the
        # amount model, because ``owned_contribution`` REFUSES a derived row --
        # its name is the assertion, and a projected shadow no longer owns
        # anything.
        assert _derived_cash(seed_user, [income_shadow])[
            income_shadow.id
        ] == Decimal("1499.10")
        assert income_shadow.estimated_amount is None
        assert income_shadow.transfer.amount == Decimal("1.00")
        income_shadow_id = income_shadow.id
        transfer_id = income_shadow.transfer_id

        resp = auth_client.post(
            f"/transactions/{income_shadow_id}/mark-done",
        )
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        settled = db.session.get(Transaction, income_shadow_id)
        assert settled.status.is_settled is True
        # Capture-on-settle froze the LIVE PITI, not the $1.00 estimate.
        assert settled.settled_amount == Decimal("1499.10")
        assert owned_contribution(settled) == Decimal("1499.10")
        # The PLAN column stays empty: a settle RECORDS what moved beside the
        # plan (plan step X-au-c3) and never writes into it.
        assert settled.estimated_amount is None
        # Both legs mirror the captured actual (Transfer Invariant 3).
        expense = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id == transfer_id,
                Transaction.id != income_shadow_id,
            )
            .one()
        )
        assert expense.settled_amount == Decimal("1499.10")

        # cash == split: the genesis split reads the frozen cash and subtracts
        # the same escrow, leaving principal = P&I.
        splits = loan_ledger.compute_loan_payment_splits(
            loan.id, scenario_id,
        )
        assert len(splits) == 1
        split = splits[0]
        assert split.interest == Decimal("1000.00")
        assert split.escrow == Decimal("300.00")
        assert split.principal == Decimal("199.10")
        assert split.excess == Decimal("0.00")


def test_settled_loan_payment_freeze_is_one_shot(
    app, db, auth_client, seed_user, seed_periods,
):
    """A re-settle never rewrites an already-frozen loan payment's actual cash.

    Capture-on-settle is ONE-SHOT.  A stale-tab re-POST of ``mark_done`` --
    admitted by the ``done -> done`` identity transition on the still-present
    mark-paid button -- leaves the frozen figure untouched.

    **The two guards holding that are BOTH structural since plan step
    X-au-g-2c-2**, where one was a producer's ``is_projected`` gate.  The
    settle act runs only on the way INTO the settled band, so a re-settle never
    reaches a derivation at all; and what a settled row is worth is read from
    its own settlement RECORD (``row_valuation.fixed_contribution``) before any
    producer is asked, so even a direct ask answers the frozen figure rather
    than a fresh one.  The second is asserted here in place of the deleted
    ``live_cash(settled) is None``: a ``None`` proved the producer would not
    fire, and what matters is the stronger statement that the recorded cash is
    what the row is worth.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        income_shadow_id = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id.isnot(None),
                Transaction.account_id == loan.id,
                Transaction.scenario_id == scenario_id,
            )
            .order_by(Transaction.id)
            .first()
            .id
        )

        resp = auth_client.post(
            f"/transactions/{income_shadow_id}/mark-done",
        )
        assert resp.status_code == 200, resp.data
        db.session.expire_all()
        settled = db.session.get(Transaction, income_shadow_id)
        assert settled.status.is_settled is True
        assert settled.settled_amount == Decimal("1499.10")

        # The freeze is one-shot: a settled row answers from its own RECORD,
        # so even asking the model directly cannot produce a fresher figure to
        # overwrite it with.
        assert _derived_cash(seed_user, [settled])[settled.id] == Decimal(
            "1499.10",
        )
        assert owned_contribution(settled) == Decimal("1499.10")

        # A stale-tab re-settle leaves the frozen figure untouched.
        resp2 = auth_client.post(
            f"/transactions/{income_shadow_id}/mark-done",
        )
        assert resp2.status_code == 200, resp2.data
        db.session.expire_all()
        replayed = db.session.get(Transaction, income_shadow_id)
        assert replayed.settled_amount == Decimal("1499.10")
        assert owned_contribution(replayed) == Decimal("1499.10")


def test_loan_standing_extra_reads_the_recurring_payment_setting(
    app, db, seed_user, seed_periods,
):
    """loan_standing_extra returns the active recurring payment's extra (else 0).

    The single loan-level figure the payoff projection threads (step 5): 0.00
    before an extra is set, the settings value after, and 0.00 for an account
    with no recurring payment (the checking source).
    """
    from app.services.recurring_transfer_query import (  # pylint: disable=import-outside-toplevel
        loan_standing_extra,
    )

    with app.app_context():
        loan, _escrow, _scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        db.session.commit()
        user_id = seed_user["user"].id

        assert loan_standing_extra(loan.id, user_id) == Decimal("0.00")

        template.settings.extra_principal = Decimal("250.00")
        db.session.commit()
        assert loan_standing_extra(loan.id, user_id) == Decimal("250.00")

        # An account with no recurring payment into it resolves to 0.00.
        assert loan_standing_extra(
            seed_user["account"].id, user_id,
        ) == Decimal("0.00")


# ── Overpayment (step 5): the standing extra rides both modes' cash ──────────


def test_derived_override_includes_standing_extra(
    app, db, seed_user, seed_periods,
):
    """A derive-mode override is P&I + escrow + the standing extra_principal.

    Loan $200,000 / 6% / 360mo, escrow $3,600/yr; extra_principal $100.00:
        P&I    = amortize(200000, 0.06, 360) = 1,199.10
        escrow = 3600 / 12                   = 300.00
        cash   = 1,199.10 + 300.00 + 100.00  = 1,599.10
    The extra is a LIVE parameter added on top; it is NOT baked into the stale
    stored $1.00, which stays untouched.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.extra_principal = Decimal("100.00")
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        overrides = _derived_cash(seed_user, shadows)
        assert overrides
        assert all(v == Decimal("1599.10") for v in overrides.values())


def test_manual_payment_with_extra_gets_base_plus_extra(
    app, db, seed_user, seed_periods,
):
    """A MANUAL payment (not derive) with a standing extra overrides to base + extra.

    Manual base (stored default) $1,499.10 + extra $100.00 = $1,599.10.  The
    base is operator-owned (not re-derived); only the extra is added live.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        # Flip to manual mode with a realistic typed base + a standing extra.
        template.settings.derive_from_loan = False
        template.settings.extra_principal = Decimal("100.00")
        template.default_amount = Decimal("1499.10")
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        overrides = _derived_cash(seed_user, shadows)
        assert overrides
        assert all(v == Decimal("1599.10") for v in overrides.values())


# ``test_manual_extra_keys_to_recurring_base_not_a_typed_actual`` lived here
# until plan step X-au-c3, and the DISCRIMINATOR it needed no longer exists.
# It put an operator-typed ``actual_amount`` on a PROJECTED, non-override loan
# shadow -- the one state where ``estimated_amount`` and the row's CONTRIBUTION
# gave different answers -- and proved ``_manual_shadow_amount`` keys the
# standing extra to the recurring base rather than to the per-instance figure.
#
# A figure RECORDS a settle now, so ``ck_transactions_settled_amount_needs_basis``
# makes that row unconstructible, and ``LoanPricing.live_cash`` gates on
# ``is_projected`` -- so no row this producer can see carries a settled figure at
# all.  The two expressions therefore answer the same number for every
# constructible input, and a test written against the difference cannot fail,
# which is not a test (finding **N-184**'s rule).  What survives is the extra
# riding the recurring base, which
# ``test_manual_payment_with_extra_gets_base_plus_extra`` above grades on the
# row shape that is still reachable.


def test_manual_payment_without_extra_gets_no_override(
    app, db, seed_user, seed_periods,
):
    """A MANUAL payment with no extra is worth its DEFINITION's base.

    Nothing to re-derive and no extra to add, so the base the operator owns IS
    the cash.  ``$1,499.10`` here, and the loan's own contractual installment
    happens to be the same figure -- which is what makes the SECOND assertion
    load-bearing: the escrow is raised afterwards, and a manual payment must NOT
    follow it.  Without that, this case would pass just as well on a rule that
    had quietly started deriving from the loan.

    **It asserted an EMPTY map until plan step X-au-g-2c-2** -- the read-time
    override was dormant for this shape, so the row simply did not appear.
    Absence could not tell "no override" from "not priced at all"; the row is
    priced now, by rule 4's manual arm, and the figure is stated.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.derive_from_loan = False
        template.settings.extra_principal = Decimal("0.00")
        template.default_amount = Decimal("1499.10")
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        assert shadows, "expected generated shadow transactions"
        resolved = _derived_cash(seed_user, shadows)
        assert all(v == Decimal("1499.10") for v in resolved.values())

        # Raise the escrow: a DERIVE payment would move to $1,599.10 and a
        # manual one must not, which is what says the base is the operator's.
        # The VERSION carries the figure -- ``EscrowLine`` has no
        # ``annual_amount`` at all, so writing one there sets an unmapped
        # attribute and this case would "pass" without the escrow moving.
        version = (
            db.session.query(EscrowComponentVersion)
            .join(EscrowLine, EscrowLine.id == EscrowComponentVersion.line_id)
            .filter(EscrowLine.account_id == loan.id)
            .one()
        )
        version.annual_amount = Decimal("4800.00")
        db.session.commit()

        # The control on the control: the raise really took effect, so the
        # assertion below is about the RULE and not about a write that never
        # happened.  Measured over the axis the RULE reads -- each shadow's own
        # installment date (``_shadow_live_amount`` resolves the escrow on
        # ``loan_payment_due_date``) -- and not a date chosen here.  A hard-coded
        # date coincides only while the write mutates the standing version in
        # place; converting it to "append a later version", which is the shape
        # ``add_escrow_line`` itself produces, would leave the probe green while
        # the shadows read the old figure and this case went back to proving
        # nothing.
        lines = load_escrow_lines(loan.id)
        assert {
            escrow_calculator.escrow_monthly_as_of(
                lines, loan_payment_due_date(shadow, 1),
            )
            for shadow in shadows
        } == {Decimal("400.00")}

        after = _derived_cash(seed_user, shadows)
        assert all(v == Decimal("1499.10") for v in after.values())


def test_a_DERIVE_payment_on_the_same_fixture_DOES_follow_the_escrow(
    app, db, seed_user, seed_periods,
):
    """The other half of the manual case's discrimination, asserted not argued.

    ``test_manual_payment_without_extra_gets_no_override`` above proves a manual
    payment does not follow an escrow raise, and its probe proves the raise took
    effect -- but "a DERIVE payment WOULD have moved to $1,599.10" was left as an
    inference from ``_shadow_live_amount``'s formula rather than measured.  An
    adversarial review of this step asked for the pair, and it is cheap: the same
    fixture, the same raise, ``derive_from_loan`` left ON.

    P&I 1,199.10 + the raised escrow 400.00 = 1,599.10.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.default_amount = Decimal("1499.10")
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        assert all(
            v == Decimal("1499.10")
            for v in _derived_cash(seed_user, shadows).values()
        )

        version = (
            db.session.query(EscrowComponentVersion)
            .join(EscrowLine, EscrowLine.id == EscrowComponentVersion.line_id)
            .filter(EscrowLine.account_id == loan.id)
            .one()
        )
        version.annual_amount = Decimal("4800.00")
        db.session.commit()

        after = _derived_cash(seed_user, shadows)
        assert all(v == Decimal("1599.10") for v in after.values())


def test_settling_with_extra_lands_the_extra_in_principal(
    app, db, auth_client, seed_user, seed_periods,
):
    """cash == split with a standing extra: the extra flows into principal.

    The Sec. 9 invariant with an overpayment.  Derive payment, escrow $3,600/yr,
    extra $100.00.  A one-click settle freezes cash = 1,199.10 P&I + 300.00
    escrow + 100.00 extra = 1,599.10.  The genesis split then divides it:
        interest  = 200,000 * 0.06 / 12 = 1,000.00
        escrow    = 300.00
        principal = 1,599.10 - 1,000.00 - 300.00 = 299.10
    which is the scheduled principal 199.10 (P&I 1,199.10 - interest 1,000.00)
    PLUS the 100.00 extra -- the residual split routes it to principal by
    construction, with no excess.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.extra_principal = Decimal("100.00")
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        income_shadow = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id.isnot(None),
                Transaction.account_id == loan.id,
                Transaction.scenario_id == scenario_id,
            )
            .order_by(Transaction.id)
            .first()
        )
        assert income_shadow is not None
        income_shadow_id = income_shadow.id

        resp = auth_client.post(f"/transactions/{income_shadow_id}/mark-done")
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        settled = db.session.get(Transaction, income_shadow_id)
        # Frozen cash carries P&I + escrow + extra.
        assert settled.settled_amount == Decimal("1599.10")

        # The genesis split routes the extra into principal (cash == split).
        splits = loan_ledger.compute_loan_payment_splits(
            loan.id, scenario_id,
        )
        assert len(splits) == 1
        split = splits[0]
        assert split.interest == Decimal("1000.00")
        assert split.escrow == Decimal("300.00")
        assert split.principal == Decimal("299.10")
        assert split.excess == Decimal("0.00")


def test_settling_manual_payment_with_extra_captures_base_plus_extra(
    app, db, auth_client, seed_user, seed_periods,
):
    """Capture-on-settle fires for a MANUAL payment carrying a standing extra.

    Manual base $1,499.10 + extra $100.00 -> the settle freezes $1,599.10, so
    the split routes the extra into principal exactly as in derive mode.  A
    manual payment with NO extra would keep its estimate (covered separately).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.derive_from_loan = False
        template.settings.extra_principal = Decimal("100.00")
        template.default_amount = Decimal("1499.10")
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        income_shadow = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id.isnot(None),
                Transaction.account_id == loan.id,
                Transaction.scenario_id == scenario_id,
            )
            .order_by(Transaction.id)
            .first()
        )
        assert income_shadow is not None
        income_shadow_id = income_shadow.id

        resp = auth_client.post(f"/transactions/{income_shadow_id}/mark-done")
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        settled = db.session.get(Transaction, income_shadow_id)
        assert settled.settled_amount == Decimal("1599.10")
        # The manual BASE is untouched, so a second settle would freeze the
        # same 1,599.10 rather than 1,699.10: the derivation must never read
        # its own output.  **That base is on a DIFFERENT ROW since plan step
        # X-au-g-2c-2**, which is what makes the compounding cycle structurally
        # impossible rather than merely not-currently-written: the manual arm
        # reads the PARENT TRANSFER's figure, and a settle writes neither that
        # nor the shadow's plan column -- the shadow has no plan column to
        # write, which is the pairing CHECK saying so.
        assert settled.estimated_amount is None
        assert settled.transfer.amount == Decimal("1499.10")


def test_each_shadows_cash_is_its_own_installments_pi(
    app, db, seed_user, seed_periods,
):
    """A shadow's P&I is its INSTALLMENT's, not the read date's (ruling R-IJ).

    Finding **N-40**, closed structurally at plan step X-au-g-2b.  The
    derivation resolved ONE ``compute_monthly_payment_baseline(..., as_of)``
    per loan per read pass and added it to every shadow alike, while the escrow
    in the same sum already resolved on each shadow's own due date.  So a loan
    whose payment recasts mid-horizon priced every projected installment at the
    payment in force on the day the page was rendered.

    The loan: $200,000 / 6% / 360 from 2026-01-01, escrow $3,600/yr, plus a
    recorded recast effective **2026-04-01** stating a $1,500.00 P&I.

        period 0 P&I = amortize(200000, 0.06, 360) = 1,199.10
        period 1 P&I = 1,500.00 (the lender's recorded recast)
        escrow       = 3600 / 12 = 300.00

        installment before 2026-04-01 -> 1,199.10 + 300.00 = 1,499.10
        installment on/after it       -> 1,500.00 + 300.00 = 1,800.00

    Both figures must appear, and the split must fall on the recast date.  A
    fixture whose shadows all landed on one side of the boundary would assert
    the same thing about a producer that read one clock, which is why the
    boundary itself is asserted rather than the figures alone.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        # The lender's recorded recast: a rate change carrying its own P&I, so
        # period 1's level payment is stated rather than derived (and the
        # expectation below is a quoted contract term, not an amortization).
        db.session.add(RateHistory(
            account_id=loan.id,
            effective_date=date(2026, 4, 1),
            interest_rate=Decimal("0.07000"),
            monthly_pi=Decimal("1500.00"),
        ))
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        assert shadows, "expected generated shadow transactions"

        resolved = _derived_cash(seed_user, shadows)
        priced = {
            loan_payment_due_date(shadow, 1): resolved[shadow.id]
            for shadow in shadows
        }

        assert priced, "expected derived cash for the derive_from_loan transfer"
        # The fixture really does straddle the recast -- otherwise a producer
        # reading one clock would satisfy the mapping below.
        assert set(priced.values()) == {Decimal("1499.10"), Decimal("1800.00")}, (
            f"the fixture did not straddle the recast: {sorted(priced.items())}"
        )
        for due, cash in priced.items():
            expected = (
                Decimal("1800.00") if due >= date(2026, 4, 1)
                else Decimal("1499.10")
            )
            assert cash == expected, (
                f"installment due {due} priced {cash}, expected {expected}"
            )
