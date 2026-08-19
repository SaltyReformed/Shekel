"""
Shekel Budget App -- Clearing a recurring definition (plan step R2e-1)

Both edit forms offer "Does not repeat" as a recurrence pattern.
Choosing it must mean what it says: the template stops naming a rule, the rule
row ceases to exist, and the instances that rule already generated stop
occupying future pay periods.

**It meant none of those things.**  Measured on a real edit of an
every-paycheck template before this step::

    rule_id before: 1   rows: 10
    rule_id after:  1   rows: 10
    (log) deleted_count=6  created_count=6

The builder returned ``None`` for an unselected pattern, the resolver assigned
nothing, and the route then regenerated from the rule the user had just asked
it to stop using -- so the option was not merely inert, it re-materialised the
recurrence it was supposed to end.

The properties pinned here, because getting one right at the cost of another is
the failure mode:

1. the rule is DETACHED and DELETED (a detached rule is finding **F-6**'s leak);
2. the future auto-generated rows are swept from the edit's effective date,
   while settled, soft-deleted and hand-edited rows inside that same window
   survive;
3. a template that NEVER recurred is untouched -- a RULE-LESS transfer
   template's single Transfer is an ordinary auto-generated row, so a sweep
   gated only on "has no rule now" would delete it on a rename;
4. the amount chooser is not offered, because it would ask an amount question
   about rows the request is deleting;
5. a LOAN PAYMENT refuses to be cleared at all -- its cadence is what the
   balance seam projects the loan against;
6. only the owner can reach the path (it DELETEs a row, so 404 is asserted
   directly rather than inferred from the shared ``get_or_404``).

The transfer half is covered too: the same helpers serve both kinds, and plan
step R2e-3 points the transfer form's null option at this exact path when it
retires the ``Once`` pattern.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.generation_schedule import GenerationSchedule
from app.services import (
    account_service,
    pay_period_service,
    recurrence_engine,
    recurring_transfer_query,
    transfer_recurrence,
    transfer_service,
)
from tests._test_helpers import create_loan_account, make_cadence_rule
from tests.oracles.recurrence_baseline import EVERY_PERIOD


# ── Helpers ──────────────────────────────────────────────────────────


def _projected_id():
    """Return the Projected status id."""
    return ref_cache.status_id(StatusEnum.PROJECTED)


def _every_period_rule(template):
    """Author and flush an every-paycheck rule ONTO *template*.

    It takes the owning definition since plan step R-F6: the owning FK is on
    ``budget.recurrence_rules``, so a rule cannot be written before there is a
    definition for it to belong to.
    """
    return make_cadence_rule(template, EVERY_PERIOD)


def _recurring_txn_template(seed_user, recurs=True):
    """Create an expense template, optionally recurring, and generate its rows.

    It AUTHORS the cadence rather than taking a pre-built rule (plan step
    R-F6); a caller that needs the rule reads ``template.recurrence_rule``.
    """
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name="Streaming",
        default_amount=Decimal("15.99"),
    )
    db.session.add(template)
    db.session.flush()
    rule = _every_period_rule(template) if recurs else None
    if rule is not None:
        recurrence_engine.generate_for_template(
            template,
            GenerationSchedule.for_periods(template.user_id, pay_period_service.get_all_periods(seed_user["user"].id)),
            seed_user["scenario"].id,
        )
    db.session.commit()
    return template


def _savings_account(seed_user):
    """Create a Savings destination for the transfer templates below."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _recurring_transfer_template(seed_user, savings, recurs=True):
    """Create a transfer template, optionally recurring, and generate its rows.

    Authors the cadence itself, for the reason
    :func:`_recurring_txn_template` gives.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="Sweep to savings",
        default_amount=Decimal("50.00"),
    )
    db.session.add(template)
    db.session.flush()
    rule = _every_period_rule(template) if recurs else None
    if rule is not None:
        transfer_recurrence.generate_for_template(
            template,
            GenerationSchedule.for_periods(template.user_id, pay_period_service.get_all_periods(seed_user["user"].id)),
            seed_user["scenario"].id,
        )
    db.session.commit()
    return template


def _period_indices(rows, periods):
    """Return the sorted period indices the given rows occupy."""
    by_id = {p.id: p.period_index for p in periods}
    return sorted(by_id[row.pay_period_id] for row in rows)


# ── Transaction templates ────────────────────────────────────────────


class TestClearingATransactionTemplatesRecurrence:
    """POST /templates/<id> with an empty recurrence pattern."""

    def test_the_rule_is_deleted_and_the_future_rows_are_swept(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The rule row is gone and only rows before the cut survive.

        Ten every-paycheck rows occupy period indices 0-9.  The edit states
        ``effective_from`` as period 4's start date, and regeneration collects
        rows whose period ENDS on or after that date -- so periods 4-9 are
        swept and 0-3 survive.  Nothing is regenerated, because the rule the
        rows came from no longer exists.
        """
        template = _recurring_txn_template(seed_user)
        rule_id = template.recurrence_rule.id
        rows = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert _period_indices(rows, seed_periods) == list(range(10))

        resp = auth_client.post(f"/templates/{template.id}", data={
            "recurrence_unit": "",
            "effective_from": seed_periods[4].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        template = db.session.get(TransactionTemplate, template.id)
        assert template.recurrence_rule is None
        # Detached is not enough -- the row itself must be gone, or the edit
        # form becomes a second producer of finding F-6's orphaned rules.
        assert db.session.get(RecurrenceRule, rule_id) is None

        survivors = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert _period_indices(survivors, seed_periods) == [0, 1, 2, 3]

    def test_a_hand_edited_future_row_survives_the_sweep(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An override inside the swept window is preserved, not deleted.

        Clearing a recurrence is an ordinary regeneration, so it runs the same
        override protection every other edit runs: the row the user changed by
        hand stays at its own amount while the untouched auto-generated rows
        around it are removed.
        """
        template = _recurring_txn_template(seed_user)
        overridden = (
            db.session.query(Transaction)
            .filter_by(template_id=template.id, pay_period_id=seed_periods[6].id)
            .one()
        )
        overridden.is_override = True
        overridden.estimated_amount = Decimal("17.99")
        overridden_id = overridden.id
        db.session.commit()

        resp = auth_client.post(f"/templates/{template.id}", data={
            "recurrence_unit": "",
            "effective_from": seed_periods[4].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        kept = db.session.get(Transaction, overridden_id)
        assert kept is not None
        assert kept.estimated_amount == Decimal("17.99")

        survivors = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert _period_indices(survivors, seed_periods) == [0, 1, 2, 3, 6]

    def test_settled_and_soft_deleted_rows_inside_the_window_survive(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The two irrecoverable cases, both inside the swept window.

        A settled row is immutable and carries ledger postings; a soft-deleted
        one records a removal the user made on purpose.  Neither may be
        destroyed by an edit that only says "stop repeating".  Both sit in
        periods 5 and 7 -- past ``effective_from`` -- so the sweep has to
        decline them rather than merely not reach them.
        """
        template = _recurring_txn_template(seed_user)
        settled = (
            db.session.query(Transaction)
            .filter_by(template_id=template.id, pay_period_id=seed_periods[5].id)
            .one()
        )
        settled.status_id = ref_cache.status_id(StatusEnum.DONE)
        removed = (
            db.session.query(Transaction)
            .filter_by(template_id=template.id, pay_period_id=seed_periods[7].id)
            .one()
        )
        removed.is_deleted = True
        settled_id, removed_id = settled.id, removed.id
        db.session.commit()

        resp = auth_client.post(f"/templates/{template.id}", data={
            "recurrence_unit": "",
            "effective_from": seed_periods[4].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        kept_settled = db.session.get(Transaction, settled_id)
        assert kept_settled is not None
        assert kept_settled.status_id == ref_cache.status_id(StatusEnum.DONE)
        kept_removed = db.session.get(Transaction, removed_id)
        assert kept_removed is not None
        assert kept_removed.is_deleted is True

        survivors = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert _period_indices(survivors, seed_periods) == [0, 1, 2, 3, 5, 7]

    def test_clearing_and_changing_the_amount_does_not_offer_the_chooser(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The amount chooser must not front a delete.

        The chooser asks "should your hand-edited instances move to the new
        amount?", which presumes future instances will be regenerated at that
        amount.  On a cleared recurrence none will be, so before the guard a
        clear-plus-amount edit rendered "Your other upcoming instances move to
        $99.99" over rows the same request was deleting.  The edit must instead
        complete, sweeping the untouched rows and leaving the override alone.
        """
        template = _recurring_txn_template(seed_user)
        overridden = (
            db.session.query(Transaction)
            .filter_by(template_id=template.id, pay_period_id=seed_periods[6].id)
            .one()
        )
        overridden.is_override = True
        overridden.estimated_amount = Decimal("17.99")
        overridden_id = overridden.id
        db.session.commit()

        resp = auth_client.post(f"/templates/{template.id}", data={
            "recurrence_unit": "",
            "default_amount": "99.99",
            "effective_from": seed_periods[4].start_date.isoformat(),
            "version_id": str(template.version_id),
        })
        assert resp.status_code == 302
        assert b"conflict_apply" not in resp.data

        db.session.expire_all()
        template = db.session.get(TransactionTemplate, template.id)
        assert template.recurrence_rule is None
        kept = db.session.get(Transaction, overridden_id)
        assert kept.estimated_amount == Decimal("17.99")
        assert kept.is_override is True
        survivors = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert _period_indices(survivors, seed_periods) == [0, 1, 2, 3, 6]

    def test_another_users_template_is_not_reachable(
        self, app, second_auth_client, seed_user, seed_periods,
    ):
        """A cross-user clear 404s and leaves the rule row standing.

        The clear path DELETES a row, so the security-response rule is checked
        on it directly rather than assumed from the shared ``get_or_404``.
        """
        template = _recurring_txn_template(seed_user)
        rule_id = template.recurrence_rule.id

        resp = second_auth_client.post(f"/templates/{template.id}", data={
            "recurrence_unit": "",
            "version_id": str(template.version_id),
        })
        assert resp.status_code == 404

        db.session.expire_all()
        assert db.session.get(RecurrenceRule, rule_id) is not None
        assert db.session.get(
            TransactionTemplate, template.id,
        ).recurrence_rule.id == rule_id

    def test_an_edit_that_submits_no_pattern_at_all_leaves_the_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An absent recurrence key is "unchanged", not "cleared".

        The update schemas are partial: a caller that submits only an amount
        is asking for an amount change.  Only the form's explicit
        "Does not repeat" -- which posts an EMPTY value, surviving as
        a present ``None`` -- means "stop recurring".  Collapsing the two would
        make every partial update silently delete the template's cadence.
        """
        template = _recurring_txn_template(seed_user)
        rule_id = template.recurrence_rule.id

        resp = auth_client.post(f"/templates/{template.id}", data={
            "default_amount": "19.99",
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        template = db.session.get(TransactionTemplate, template.id)
        assert template.default_amount == Decimal("19.99")
        assert template.recurrence_rule.id == rule_id
        assert db.session.get(RecurrenceRule, rule_id) is not None
        rows = db.session.query(Transaction).filter_by(
            template_id=template.id,
        ).all()
        assert _period_indices(rows, seed_periods) == list(range(10))


class TestATemplateThatNeverRecurredIsNotSwept:
    """The sweep is gated on the recurrence having PARTICIPATED in the edit."""

    def test_renaming_a_rule_less_template_keeps_its_rows(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A hand-placed row on a never-recurring template survives a rename.

        The regeneration gate cannot be "the template has no rule -> sweep":
        a template that never recurred can still own generated rows -- a
        one-time transfer's single Transfer is exactly that shape -- and an
        unrelated edit must not touch them.
        """
        template = _recurring_txn_template(seed_user, recurs=False)
        assert template.recurrence_rule is None
        manual = Transaction(
            account_id=seed_user["account"].id,
            template_id=template.id,
            pay_period_id=seed_periods[6].id,
            scenario_id=seed_user["scenario"].id,
            status_id=_projected_id(),
            name="Streaming",
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=template.transaction_type_id,
            estimated_amount=Decimal("15.99"),
            is_override=False,
            is_deleted=False,
            due_date=seed_periods[6].start_date,
        )
        db.session.add(manual)
        db.session.commit()
        manual_id = manual.id

        resp = auth_client.post(f"/templates/{template.id}", data={
            "name": "Streaming (renamed)",
            "effective_from": seed_periods[0].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        assert db.session.get(Transaction, manual_id) is not None


# ── Transfer templates ───────────────────────────────────────────────


class TestClearingATransferTemplatesRecurrence:
    """POST /transfers/<id> with an empty recurrence pattern.

    The transfer form has no null option until plan step R2e-3 retires the
    ``Once`` pattern, but the route path it will use is shared with the
    transaction form and is live now -- so it is pinned here rather than
    discovered by R2e-3.
    """

    def test_the_rule_is_deleted_and_the_future_transfers_are_swept(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The rule row is gone and only transfers before the cut survive.

        Same contract as the transaction half, through the transfer service so
        each removed Transfer takes its two shadow transactions with it
        (transfer invariant 1).
        """
        savings = _savings_account(seed_user)
        template = _recurring_transfer_template(seed_user, savings)
        rule_id = template.recurrence_rule.id
        rows = db.session.query(Transfer).filter_by(
            transfer_template_id=template.id,
        ).all()
        assert _period_indices(rows, seed_periods) == list(range(10))

        resp = auth_client.post(f"/transfers/{template.id}", data={
            "recurrence_unit": "",
            "effective_from": seed_periods[4].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        template = db.session.get(TransferTemplate, template.id)
        assert template.recurrence_rule is None
        assert db.session.get(RecurrenceRule, rule_id) is None

        survivors = db.session.query(Transfer).filter_by(
            transfer_template_id=template.id,
        ).all()
        assert _period_indices(survivors, seed_periods) == [0, 1, 2, 3]

        # Transfer invariants 1-3, asserted positively.  A subset check would
        # pass against a sweep that destroyed EVERY shadow, so each surviving
        # transfer is checked to still carry exactly its pair, one leg each
        # way, at the parent's amount and in the parent's period.
        income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
        for xfer in survivors:
            pair = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id,
            ).all()
            assert len(pair) == 2
            assert {t.transaction_type_id for t in pair} == {income_id, expense_id}
            assert all(t.estimated_amount == xfer.amount for t in pair)
            assert all(t.pay_period_id == xfer.pay_period_id for t in pair)
        # 4 survivors x 2 legs; the 6 swept transfers took 12 shadows with them.
        assert db.session.query(Transaction).filter(
            Transaction.transfer_id.isnot(None),
        ).count() == 8

    def test_a_loan_payment_refuses_to_become_one_time(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A loan payment's cadence cannot be cleared, and the reason is money.

        ``recurring_transfer_query.active_recurring_transfer_template`` finds a
        loan's payment by whether a rule names it, and the balance
        seam threads that template's ``extra_principal`` into every projected
        loan trajectory.  Clearing the rule nulls the column, so the standing
        overpayment silently drops to zero and the projected payoff moves --
        while the ``loan_payment_settings`` row goes on asserting it.  Measured
        before the refusal: 250.00 -> 0.00 with the settings row unchanged.
        """
        loan = create_loan_account(seed_user, db.session)
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan.id,
            category_id=seed_user["categories"]["Rent"].id,
            name="Mortgage payment",
            default_amount=Decimal("1000.00"),
        )
        template.settings = LoanPaymentSettings(
            extra_principal=Decimal("250.00"),
        )
        db.session.add(template)
        db.session.flush()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule_id = _every_period_rule(template).id
        db.session.commit()
        extra_before = recurring_transfer_query.loan_standing_extra(
            loan.id, seed_user["user"].id,
        )
        assert extra_before == Decimal("250.00")

        resp = auth_client.post(f"/transfers/{template.id}", data={
            "recurrence_unit": "",
            "effective_from": seed_periods[0].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"cannot be made" in resp.data

        db.session.expire_all()
        assert db.session.get(
            TransferTemplate, template.id,
        ).recurrence_rule.id == rule_id
        assert db.session.get(RecurrenceRule, rule_id) is not None
        assert recurring_transfer_query.loan_standing_extra(
            loan.id, seed_user["user"].id,
        ) == Decimal("250.00")

    def test_a_rule_less_transfer_templates_single_transfer_survives_a_rename(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The one-time transfer shape plan step R2e-3 SHIPPED is safe.

        A transfer template with no rule and exactly one materialised Transfer
        is what "one-time transfer" became when ``Once`` was retired.  An
        unrelated edit must leave that Transfer alone.  Built here through the
        service; ``test_transfers.py`` covers the same property end-to-end
        through the create form, which is what now produces this shape.
        """
        savings = _savings_account(seed_user)
        template = _recurring_transfer_template(seed_user, savings, recurs=False)
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=seed_user["user"].id,
                from_account_id=template.from_account_id,
                to_account_id=template.to_account_id,
                pay_period_id=seed_periods[6].id,
                scenario_id=seed_user["scenario"].id,
                amount=template.default_amount,
                status_id=_projected_id(),
                category_id=template.category_id,
                name=template.name,
                transfer_template_id=template.id,
                due_date=seed_periods[6].start_date,
            ),
        )
        db.session.commit()
        xfer_id = xfer.id

        resp = auth_client.post(f"/transfers/{template.id}", data={
            "name": "Sweep to savings (renamed)",
            "effective_from": seed_periods[0].start_date.isoformat(),
            "version_id": str(template.version_id),
        }, follow_redirects=True)
        assert resp.status_code == 200

        db.session.expire_all()
        assert db.session.get(Transfer, xfer_id) is not None
