"""Integration: a transfer's settle books ONE figure, whichever door asked.

Plan step **X-f2-c3**, ruling **R-FA** applied to the transfer table.  FOUR
doors can move a transfer into the settled band and exactly ONE of them froze
an auto-derived loan payment's live payment-date cash:

* the grid's shadow "Mark Paid" (``routes/transactions/_shadow_mutations``) --
  it called ``loan_payment_service.live_loan_payment_amount`` itself;
* the transfers page's "Mark Done" (``routes/transfers/mutations.mark_done``);
* the transfer full-edit Status dropdown (``_execute_transfer_update``);
* a transaction PATCH landing on a shadow (``_apply_shadow_update``).

The other three booked the stored estimate -- the creation-time escrow -- so
the same payment recorded a different figure depending on which control the
operator pressed.  That is finding **N-219**'s shape on this table, and a ROUTE
holding a money rule is this arc's own root cause 1.
``transfer_service.settle_transfer`` is the rule's name now and
``update_transfer`` dispatches to it, so these grade the FIGURE at the service
and then prove each door reaches it.

**WHICH COLUMN the freeze writes stays ``actual_amount``, and that is a
developer ruling (2026-08-12) rather than an oversight.**  Ruling **R-FH** says
a derivation belongs in the row's OWN amount and finding **N-241** records that
it does not go there; this branch BUILT that move and withdrew it, because two
adversarial reviews measured it unsafe on today's schema -- a manual payment's
base is ``estimated_amount``, so writing the freeze there compounds the standing
extra across settle/revert cycles (`$1,599.10` -> `$1,699.10` -> `$1,799.10`),
and a leftover ``actual_amount`` from a reverted settle would OUTRANK the frozen
figure through ``COALESCE``.  Plan step **X-au-c**'s ``amount_source`` column is
what makes the write idempotent and authoritative, so the move is that step's.
These cases therefore assert ``actual_amount`` AND ``effective_amount``: the
first is where the freeze lands today, the second is what the ledger books, and
the second is the one that must never change.

**The arithmetic is hand-computed and shown.**  Loan $200,000 / 6% / 360mo:
    P&I    = amortize(200000, 0.06, 360)  = 1,199.10
    escrow = 3,600.00 / 12                =   300.00
    PITI   = 1,199.10 + 300.00            = 1,499.10
The template's stored ``default_amount`` is a deliberately stale ``$1.00``, so
a settle that books ``$1.00`` is a settle that missed the freeze and a settle
that books ``$1,499.10`` is one that took it.

**On production this changes `$0.00` today**: ``budget.loan_payment_settings``
holds ZERO rows, so no live transfer is an auto-derived loan payment and all 17
settled transfer shadows on Checking carry ``actual_amount = NULL``.  The route
that writes that settings row (``routes/loan/payment_transfer.py``) is live, so
the split opens on the next loan payment transfer created through the loan page.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import SettlementBasisEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    account_service,
    posting_service,
    transfer_service,
    transfer_recurrence,
)
from app.services.generation_schedule import GenerationSchedule
from app.utils.dates import attribution_date, display_today
from tests._test_helpers import create_transfer, settlement_basis_id
from tests.test_integration.test_loan_transfer_live_amount import (
    _build_derived_loan_transfer,
)
from app.services.row_valuation import owned_contribution, settled_figure
from app.services.pay_calendar import calendar_for

#: P&I 1,199.10 + escrow 300.00, the figure the freeze captures.
_LIVE_PITI = Decimal("1499.10")
#: The template's deliberately stale stored amount.
_STALE = Decimal("1.00")


def _derived_loan_transfer(seed_user, seed_periods):
    """Return one projected loan-payment transfer and its expense shadow.

    Built on the derive-from-loan fixture the live-override integration test
    already owns, so the two grade the same loan with the same arithmetic: a
    $200k / 6% / 360mo mortgage with $3,600/yr escrow behind a recurring
    transfer whose stored amount is stale.
    """
    loan, _escrow, scenario_id, template, _rule, _periods = (
        _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
    )
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            calendar_for(template.user_id), {p.id for p in seed_periods},
        ),
        scenario_id,
    )
    db.session.commit()

    xfer = (
        db.session.query(Transfer)
        .filter_by(to_account_id=loan.id)
        .order_by(Transfer.id)
        .first()
    )
    assert xfer is not None, "expected a generated loan-payment transfer"
    expense_shadow = (
        db.session.query(Transaction)
        .filter_by(transfer_id=xfer.id, account_id=xfer.from_account_id)
        .one()
    )
    return xfer, expense_shadow


def _plain_transfer(
    seed_user, seed_periods, amount="250.00", destination="Savings",
):
    """Return a projected checking-to-savings transfer -- NO loan behind it.

    The control shape: its template carries no ``loan_payment_settings`` row,
    so the freeze resolves to ``None`` and the row books its own estimate.  It
    is what every live transfer on production is today.

    *destination* names the receiving account because ``uq_accounts_user_name``
    is unique per owner, so a case wanting two independent transfers has to ask
    for two destinations.
    """
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            name=destination,
            account_type_id=seed_user["account"].account_type_id,
            anchor_balance=Decimal("100.00"),
        ),
    )
    db.session.flush()
    return create_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal(amount),
    )


def _shadows(xfer_id):
    """Return both legs of *xfer_id*, expense side first."""
    rows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=xfer_id)
        .order_by(Transaction.id)
        .all()
    )
    assert len(rows) == 2, "Transfer Invariant 1: exactly two shadows"
    return rows


class TestTheSettleFreezeIsTheSERVICEs:
    """The amount rule lives at the one chokepoint, so no door can miss it."""

    def test_a_plain_settle_freezes_the_live_payment_date_cash(
        self, app, db, seed_user, seed_periods,
    ):
        """One-click Paid books `$1,499.10`, not the stale `$1.00`.

        The rule's whole point: an auto-derived loan payment's stored estimate
        is the creation-time escrow, and what actually leaves checking is the
        live P&I + escrow-as-of on the shadow's own DUE date -- the same figure
        the genesis split subtracts, so ``cash == split`` holds by construction.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            for shadow in _shadows(xfer.id):
                # What it BOOKS -- the ledger figure, and the whole point.
                assert owned_contribution(shadow) == _LIVE_PITI
                # Where the freeze lands today (N-241 is the open question
                # about which column that should be; X-au-c owns it).
                assert shadow.settled_amount == _LIVE_PITI
                # The stored estimate is UNTOUCHED, which is what keeps the
                # freeze idempotent: ``_manual_shadow_amount`` reads this
                # column as its base, so a settle that wrote it would make a
                # later settle derive from its own output.
                assert shadow.estimated_amount == _STALE

    def test_a_typed_figure_BEATS_the_freeze(
        self, app, db, seed_user, seed_periods,
    ):
        """A human read `$1,512.44` off the statement; the derivation yields.

        The precedence half of the rule.  A figure somebody typed is a FACT
        about money that moved; the freeze is a derivation, and a derivation
        never overwrites a fact.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_amount=Decimal("1512.44"),
            )
            db.session.commit()

            db.session.expire_all()
            for shadow in _shadows(xfer.id):
                assert shadow.settled_amount == Decimal("1512.44")
                assert owned_contribution(shadow) == Decimal("1512.44")

    def test_an_ECHOED_prefill_is_not_written(
        self, app, db, seed_user, seed_periods,
    ):
        """Submitting exactly what the row would book leaves the column NULL.

        The reconcile panel PREFILLS its amount box, so every correctable row
        on the form posts a figure whether the user touched it or not.  Writing
        an untouched echo would populate a column that is NULL on all 17 settled
        transfer shadows in production and destroy the only signal that says a
        human read one off a statement.

        Graded on a NON-loan transfer, where the row books its own estimate:
        the echo is the estimate, and the column must stay NULL.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)
            db.session.commit()

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_amount=Decimal("250.00"),
            )
            db.session.commit()

            db.session.expire_all()
            for shadow in _shadows(xfer.id):
                # An uncorrected settle RECORDS what it booked on the
                # ``derived`` basis (plan step X-au-c3).  This asserted a NULL
                # figure until that step, because a NULL was the only signal
                # that no human had typed one; the basis carries that now, so
                # the record can state the figure AND stay distinguishable.
                assert shadow.settled_basis_id == settlement_basis_id(
                    SettlementBasisEnum.DERIVED,
                )
                assert settled_figure(shadow) == Decimal("250.00")
                assert owned_contribution(shadow) == Decimal("250.00")

    # ``test_an_explicit_None_still_CLEARS_a_typed_actual`` lived here until
    # plan step X-au-c3, and BOTH halves of its premise are gone.  It wrote
    # ``actual_amount = 310.00`` onto an UNSETTLED transfer -- a figure now
    # RECORDS a settle, so the service refuses that outright -- and then had an
    # explicit ``None`` clear it, which was a distinct act only while a settled
    # transfer carrying no figure was a legal state.  A settled row always
    # records what moved, so a ``None`` means what an empty box means (nobody
    # typed one) and there is no clearing act for it to request;
    # ``_update._fields_the_settle_left`` and ``_apply_remaining_fields``
    # document the same removal on the app side.  The act that DOES release a
    # record is leaving the settled band, graded by
    # ``test_transfer_service.TestUpdateTransfer.
    # test_a_REVERT_is_what_clears_a_settled_transfers_figure``.

    def test_is_override_in_the_SAME_call_suppresses_the_freeze(
        self, app, db, seed_user, seed_periods,
    ):
        """Retyping the amount and marking Paid keeps the typed figure.

        The transfer edit route auto-sets ``is_override`` whenever a
        template-linked transfer's amount moves, so this is exactly what a
        "correct the payment and mark it Paid" save sends.  The flag says the
        OPERATOR owns this amount, and reading its PRE-edit value would freeze
        a derived `$1,499.10` straight over the `$1,325.00` the user had just
        typed -- which is why the dispatch runs after the caller-stated facts
        rather than before them.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount=Decimal("1325.00"),
                is_override=True,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            for shadow in _shadows(xfer.id):
                assert shadow.estimated_amount == Decimal("1325.00")
                # An uncorrected settle RECORDS what it booked on the
                # ``derived`` basis (plan step X-au-c3).  This asserted a NULL
                # figure until that step, because a NULL was the only signal
                # that no human had typed one; the basis carries that now, so
                # the record can state the figure AND stay distinguishable.
                assert settled_figure(shadow) == Decimal("1325.00")
                assert owned_contribution(shadow) == Decimal("1325.00")

    def test_a_re_settle_does_not_rewrite_the_frozen_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The freeze is ONE-SHOT, and a stale tab must not move money.

        ``done -> done`` is a legal identity transition, so a replayed POST from
        a page left open reaches the service again.  The capture is gated on the
        shadow still being Projected, and the dispatch runs BEFORE the status is
        applied so a genuine first settle still sees that -- but a re-settle
        resolves to nothing and the recorded cash stands, even after the loan's
        escrow moves underneath it.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            done_id = ref_cache.status_id(StatusEnum.DONE)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=done_id,
            )
            db.session.commit()
            db.session.expire_all()
            assert _shadows(xfer.id)[0].settled_amount == _LIVE_PITI

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=done_id,
            )
            db.session.commit()

            db.session.expire_all()
            for shadow in _shadows(xfer.id):
                assert shadow.settled_amount == _LIVE_PITI
                assert owned_contribution(shadow) == _LIVE_PITI

    def test_settle_amount_publishes_what_a_tick_WILL_book(
        self, app, db, seed_user, seed_periods,
    ):
        """The panel's figure and the booked figure come from one expression.

        ``settle_amount`` is what the reconcile panel renders; the dispatch
        resolves its own figure through the same two functions.  A panel showing
        one number beside a verb that books another is this arc's own root cause
        1 applied to a screen.
        """
        with app.app_context():
            xfer, shadow = _derived_loan_transfer(seed_user, seed_periods)

            offered = transfer_service.settle_amount(shadow)
            assert offered == _LIVE_PITI

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            assert owned_contribution(_shadows(xfer.id)[0]) == offered
            assert _shadows(xfer.id)[0].settled_amount == offered


class TestEveryDoorReachesTheSameFigure:
    """The census, from the doors themselves -- three of four used to differ."""

    def test_the_transfers_page_mark_done_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """``POST /transfers/instance/<id>/mark-done``, which did NOT freeze.

        The door that made this a defect rather than a design: it sent
        ``status_id`` alone, so an auto-derived loan payment settled at its
        creation-time escrow here and at the live figure from the grid.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id

        response = auth_client.post(f"/transfers/instance/{xfer_id}/mark-done")
        assert response.status_code == 200

        with app.app_context():
            for shadow in _shadows(xfer_id):
                assert owned_contribution(shadow) == _LIVE_PITI
                assert shadow.settled_amount == _LIVE_PITI

    def test_the_grid_shadow_mark_done_still_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """``POST /transactions/<id>/mark-done``, the door that always froze.

        The control for the move: the rule left this route for the service, so
        the figure it books must be unchanged.
        """
        with app.app_context():
            xfer, shadow = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id, shadow_id = xfer.id, shadow.id

        response = auth_client.post(f"/transactions/{shadow_id}/mark-done")
        assert response.status_code == 200

        with app.app_context():
            for row in _shadows(xfer_id):
                assert owned_contribution(row) == _LIVE_PITI
                assert row.settled_amount == _LIVE_PITI

    def test_the_transfer_full_edit_status_dropdown_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """``PATCH /transfers/instance/<id>`` with the payload the FORM sends.

        The third door, and the one whose transaction twin is finding
        **N-219**: it flipped the status through the service without ever
        asking what the payment was worth.

        **It posts ``amount`` because the real form always does**, and that is
        the whole point of this case rather than a detail.  An earlier version
        submitted ``status_id`` alone, which no browser can produce:
        ``_transfer_full_edit.html`` renders the Amount input on every editable
        row and an HTML form posts every input it renders.  Against that
        realistic payload the door did NOT freeze -- the route set
        ``is_override`` on the mere PRESENCE of the field, which tells the
        settle "the operator owns this amount" and suppresses the derivation.
        Measured before the fix: `$1.00` of cash booked against a `$1,000.00`
        interest + `$300.00` escrow split.  The route now tests whether the
        amount actually MOVED, which is what its own comment always claimed.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            version = xfer.version_id
            # Exactly what the rendered form carries back: the value already in
            # the box, unchanged, beside the status the user did change.
            rendered_amount = str(xfer.amount)
            period_id = xfer.pay_period_id
            due = xfer.due_date

        response = auth_client.patch(
            f"/transfers/instance/{xfer_id}",
            data={
                "amount": rendered_amount,
                "pay_period_id": period_id,
                "due_date": due.isoformat() if due else "",
                "status_id": ref_cache.status_id(StatusEnum.DONE),
                "version_id": version,
            },
        )
        assert response.status_code == 200, response.data

        with app.app_context():
            for row in _shadows(xfer_id):
                assert owned_contribution(row) == _LIVE_PITI
                assert row.settled_amount == _LIVE_PITI

    def test_a_transaction_PATCH_landing_on_a_shadow_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """``PATCH /transactions/<shadow id>`` -- the FOURTH door.

        The one the census names and no test reached.  A shadow's PATCH is
        branched to ``_shadow_mutations._apply_shadow_update``, which maps the
        submitted transaction fields onto transfer-service kwargs -- so a
        settling ``status_id`` arriving here is a settle that must freeze like
        any other.  Without this case the census is a claim in a docstring:
        three doors graded, one asserted.

        No UI reaches it today -- the quick-edit cell PATCHes this route but
        renders an amount only, and a shadow's full edit redirects to the
        TRANSFER form -- so this grades the service dispatch behind a public
        door rather than a live control.  That is the same ground
        ``transaction_service.settle_transaction`` gives for owning its own
        shadow refusal: a door with no caller today is a door the next feature
        writes against.
        """
        with app.app_context():
            xfer, shadow = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id, shadow_id = xfer.id, shadow.id
            version = shadow.version_id

        response = auth_client.patch(
            f"/transactions/{shadow_id}",
            data={
                "status_id": ref_cache.status_id(StatusEnum.DONE),
                "version_id": version,
            },
        )
        assert response.status_code == 200, response.data

        with app.app_context():
            for row in _shadows(xfer_id):
                assert owned_contribution(row) == _LIVE_PITI
                assert row.settled_amount == _LIVE_PITI

    def test_the_reconcile_panels_tick_freezes_and_dates_by_the_STATEMENT(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The FIFTH door, which plan step X-f2-c3 opens.

        It is the only one that knows a day, so it grades the two rules
        together: the freeze books the live figure, and both legs record the
        money as having moved on the day the STATEMENT covers rather than the
        day the operator got round to reconciling.
        """
        with app.app_context():
            xfer, shadow = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id, shadow_id = xfer.id, shadow.id
            account_id = xfer.from_account_id
            # The day the panel measures the offer against is the row's own
            # LANDING day, not its period's start: a loan payment carries a
            # due date, so the two differ and a statement asserted before the
            # landing day would (correctly) offer nothing.
            period = shadow.pay_period
            observed = attribution_date(
                shadow.due_date, period.start_date, period.end_date,
            )

        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={
                "anchor_balance": "4537.66",
                "observed_on": observed.isoformat(),
            },
        )
        assert response.status_code == 200, response.data

        response = auth_client.post(
            f"/accounts/{account_id}/reconcile",
            data={"transaction_ids": [str(shadow_id)]},
        )
        assert response.status_code == 200, response.data

        with app.app_context():
            for row in _shadows(xfer_id):
                assert owned_contribution(row) == _LIVE_PITI
                assert row.settled_amount == _LIVE_PITI
                assert row.settled_on == observed


class TestTheNamedVerbItself:
    """``settle_transfer``: the surface every settle-only door now calls.

    Its behaviour was reachable only THROUGH those doors until an adversarial
    review pointed out that a public verb with no direct case is a verb whose
    own contract is ungraded -- and then found a real defect in exactly the
    branch nothing exercised.
    """

    def test_it_returns_whether_a_HUMAN_s_figure_was_booked(
        self, app, db, seed_user, seed_periods,
    ):
        """The return value is what the reconcile writer counts (**N-231**).

        Three shapes in one case, because the answer is a three-way decision
        and grading one arm would leave the other two free to invert: nobody
        typed a figure, somebody typed the panel's own prefill back, and
        somebody typed a different one.
        """
        with app.app_context():
            nothing_typed = _plain_transfer(
                seed_user, seed_periods, destination="Savings",
            )
            echoed = _plain_transfer(
                seed_user, seed_periods, amount="120.00",
                destination="Vacation",
            )
            corrected = _plain_transfer(
                seed_user, seed_periods, amount="80.00",
                destination="Emergency",
            )
            db.session.commit()
            owner = seed_user["user"].id

            assert transfer_service.settle_transfer(
                nothing_typed.id, owner,
            ) is False
            assert transfer_service.settle_transfer(
                echoed.id, owner, submitted=Decimal("120.00"),
            ) is False
            assert transfer_service.settle_transfer(
                corrected.id, owner, submitted=Decimal("95.50"),
            ) is True

            db.session.commit()
            db.session.expire_all()
            # And the RECORD follows the same three-way decision: the two
            # uncorrected settles book what they resolved on the ``derived``
            # basis, the corrected one books the human's figure and says so.
            derived_id = settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert _shadows(nothing_typed.id)[0].settled_basis_id == derived_id
            assert _shadows(echoed.id)[0].settled_basis_id == derived_id
            assert _shadows(echoed.id)[0].settled_amount == Decimal("120.00")
            assert _shadows(corrected.id)[0].settled_basis_id == (
                settlement_basis_id(SettlementBasisEnum.CORRECTED)
            )
            assert _shadows(corrected.id)[0].settled_amount == Decimal("95.50")

    def test_settling_an_ALREADY_settled_transfer_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A settle is idempotent, and the defect this grades was measured.

        ``enters_settled_band`` is False for ``done -> done``, so no settle
        runs -- and before plan step X-f2-c3's review the kwargs fell through
        to the ordinary field arms anyway: ``actual_amount`` was written
        VERBATIM past the echo rule, and ``settled_on`` re-dated both legs
        through ruling **R-ED**'s correction door, moving the posted
        ``entry_date`` (findings **N-146** / **N-178**).  The verb returned
        ``False`` throughout, so the reconcile writer's count under-reported a
        write it had just made.

        Shown to FIRE: deleting the ``settle_only`` arm in
        ``_apply_transfer_updates`` fails every assertion below.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)
            db.session.commit()
            owner = seed_user["user"].id
            first_day = display_today() - timedelta(days=3)

            transfer_service.settle_transfer(
                xfer.id, owner, settled_on=first_day,
            )
            db.session.commit()
            db.session.expire_all()
            assert _shadows(xfer.id)[0].settled_on == first_day

            # A stale tab replays the settle, carrying a figure and a later day.
            assert transfer_service.settle_transfer(
                xfer.id, owner,
                submitted=Decimal("999.99"),
                settled_on=display_today(),
            ) is False
            db.session.commit()

            db.session.expire_all()
            for shadow in _shadows(xfer.id):
                # The echoed-past-the-rule write did not happen: the record
                # still says ``derived`` at what the FIRST settle booked, not
                # ``corrected`` at the replayed $999.99.
                assert shadow.settled_basis_id == settlement_basis_id(
                    SettlementBasisEnum.DERIVED,
                )
                assert settled_figure(shadow) == Decimal("250.00")
                assert owned_contribution(shadow) == Decimal("250.00")
                # ... and the day the money moved was not moved.
                assert shadow.settled_on == first_day

    def test_a_derived_freeze_emits_its_own_event(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """The one money write no operator asked for must be on the record.

        ``EVT_TRANSFER_AMOUNT_FROZEN`` exists because the figure booked differs
        from the one the operator saw when the transfer was generated, and
        nothing else records that it moved.  Without this case, deleting the
        ``log_event`` call leaves the suite green.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            with caplog.at_level(logging.INFO):
                transfer_service.settle_transfer(
                    xfer.id, seed_user["user"].id,
                )
                db.session.commit()

        frozen = [
            record for record in caplog.records
            if getattr(record, "event", None) == "transfer_amount_frozen"
        ]
        assert len(frozen) == 1, "the freeze reports itself exactly once"
        assert frozen[0].frozen_amount == str(_LIVE_PITI)

    def test_a_settle_carrying_a_CORRECTION_is_not_reported_as_a_freeze(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """The control: the event means the DERIVATION decided the figure.

        A human's correction beats the freeze, so the figure booked is theirs
        and no freeze happened.  Without this the event could be emitted on
        every settle of a loan payment and still pass its sibling above.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            with caplog.at_level(logging.INFO):
                transfer_service.settle_transfer(
                    xfer.id, seed_user["user"].id,
                    submitted=Decimal("1512.44"),
                )
                db.session.commit()

        assert not [
            record for record in caplog.records
            if getattr(record, "event", None) == "transfer_amount_frozen"
        ]


class TestATransfersOfferIsWhatItsReSettleBOOKS:
    """The TRANSFER half of finding **C1**, which nothing graded.

    ``transaction_service.settle_amount`` and ``transfer_service.settle_amount``
    both answer a retained ``corrected`` record before they price anything, so
    the reconcile panel's PREFILL equals what a tick BOOKS on either kind of
    row.  The transaction half is pinned by
    ``test_full_edit_settle_door.TestWhatAReSettleBooksIsWhatTheOfferSHOWED``;
    the transfer half was pinned by nothing.

    **Measured 2026-08-17 by an adversarial mutation pass**: deleting the
    ``honoured_correction`` arm from ``transfer_service._settle.settle_amount``
    left the whole 9,626-test suite GREEN.  ``settle()`` reads the retained
    correction independently, so the mutant separates the two -- the panel
    offers the row's derived figure and the tick books the retained one, which
    is exactly the defect the transaction side documents and refuses.
    """

    def test_the_offer_equals_the_booking_on_a_reverted_transfer(
        self, app, db, seed_user, seed_periods,
    ):
        """A reverted transfer offers the figure it will re-book, not its plan.

        Shown to FIRE: dropping ``settle_amount``'s ``honoured_correction`` arm
        makes the offer ``$250.00`` while the settle still books ``$95.50``.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods, amount="250.00")
            db.session.commit()
            owner = seed_user["user"].id
            xfer_id = xfer.id

            # Settle at a HUMAN's figure, then revert -- the round trip the
            # full-edit card instructs.
            assert transfer_service.settle_transfer(
                xfer_id, owner, submitted=Decimal("95.50"),
            ) is True
            db.session.commit()
            transfer_service.update_transfer(
                xfer_id, owner,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            db.session.expire_all()

            expense = _shadows(xfer_id)[0]
            # The record SURVIVES the revert and the assertion does not.
            assert expense.settled_amount == Decimal("95.50")
            assert expense.settled_on is None

            # THE CLAIM: what the panel would offer is what a tick will book.
            offered = transfer_service.settle_amount(expense)
            assert offered == Decimal("95.50"), (
                f"the panel would offer {offered} for a transfer whose "
                "re-settle books its retained correction"
            )

            transfer_service.settle_transfer(xfer_id, owner)
            db.session.commit()
            db.session.expire_all()
            assert _shadows(xfer_id)[0].settled_amount == offered


class TestOneBrokenPairCannotStopTheDeployResync:
    """``resync_all_cash_postings`` skips a drifted pair instead of aborting.

    **The refusal that made this necessary is correct, and its blast radius was
    not** (developer ruling, 2026-08-17).  ``posting_service._settle_effective``
    gained a settled-status predicate at plan step X-au-c3 and raises
    ``PostingError`` when the income shadow is not settled -- right for a single
    write path, where a caller asking what one transfer settled at must not be
    handed a fabricated figure.

    But this batch selects transfers by the PARENT's status and runs at
    container start (``scripts/init_database.py``) with no isolation, so one
    Transfer-Invariant-4 drift -- the exact corruption ``restore_transfer``
    exists to repair -- made the app unbootable for every user, and the operator
    could not reach the screen that would name the row.  Before the predicate it
    would have posted a figure and carried on.
    """

    def test_it_skips_the_broken_pair_and_still_posts_the_healthy_one(
        self, app, db, seed_user, seed_periods,
    ):
        """One drifted pair beside one sound pair: the sound one still syncs.

        Shown to FIRE: without the per-transfer isolation this raises
        ``PostingError`` out of the batch and the healthy transfer is never
        reached, which is the container-start failure.
        """
        with app.app_context():
            broken = _plain_transfer(
                seed_user, seed_periods, amount="60.00",
                destination="Savings",
            )
            healthy = _plain_transfer(
                seed_user, seed_periods, amount="45.00",
                destination="Vacation",
            )
            db.session.commit()
            owner = seed_user["user"].id
            broken_id, healthy_id = broken.id, healthy.id

            transfer_service.settle_transfer(broken_id, owner)
            transfer_service.settle_transfer(healthy_id, owner)
            db.session.commit()

            # Drift the INCOME leg out of the settled band, behind the seam's
            # back -- which is how the real corruption arises (a bulk UPDATE, a
            # half-applied migration).  The PARENT stays settled, so the batch
            # still selects it and still asks for its settled effect.
            income = [
                s for s in _shadows(broken_id) if not s.is_expense
            ][0]
            income.status_id = ref_cache.status_id(StatusEnum.PROJECTED)
            db.session.commit()

            # The batch completes rather than raising, which is the claim.
            transactions_changed, transfers_changed = (
                posting_service.resync_all_cash_postings()
            )
            db.session.commit()

            assert isinstance(transfers_changed, int)
            assert isinstance(transactions_changed, int)

            # And the sound pair was still reached: it is at target, so a
            # further pass reports nothing to do for it.
            assert posting_service.resync_all_cash_postings()[1] == 0
