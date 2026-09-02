"""
Shekel Budget App -- Transfer Route Tests

Tests for transfer template CRUD, grid cell endpoints, transfer instance
operations, and ad-hoc transfer creation (§2.3 of the test plan).
"""

import re
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import (
    AcctTypeEnum, SettlementBasisEnum, StatusEnum, TxnTypeEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.recurrence_rule import RecurrenceRule
from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.models.ref import AccountType, Status
from app.services import balance_at, pay_period_write
from app.services.pay_calendar import calendar_for
from app.services.balance_at import BalanceContext
from app.services import transfer_service
from app.services.auth_service import hash_password
from app.services import account_service
from app.utils.dates import display_today
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    all_periods,
    an_asserted_day,
    an_entered_day,
    an_observed_day,
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    field_is_disabled,
    last_covered_day,
    make_every_period_rule,
    make_transfer_template,
    net_posted_by_day,
    open_books_before_the_first_assertion,
    override_anchor,
    settlement_basis_id,
    shadow_amount,
)
from app.services.row_valuation import owned_contribution
from app.services.settle_day import (
    record_settle_day,
    recorded_settle_day,
)


def _create_savings_account(seed_user):
    """Helper: create a second (savings) account for the test user."""
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
    # Its BOOKS open the day before its origination assertion (plan step
    # X-f3c-2b, ruling **R-HG**).  The factory defaults ``observed_on`` to the
    # owner's today and this suite settles transfers on days around it, so
    # leaving the books on today makes every such fixture unrecordable -- an
    # opening equity is the CLOSING balance for its own day.
    open_books_before_the_first_assertion(db.session, acct)
    db.session.commit()
    return acct


def _create_template(seed_user, savings_acct, with_rule=True):
    """Helper: create a transfer template with optional recurrence rule."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_acct.id,
        name="Monthly Savings",
        default_amount=Decimal("200.00"),
    )
    db.session.add(template)
    db.session.flush()
    if with_rule:
        # The definition first, then the cadence onto it (plan step R-F6).
        make_every_period_rule(db.session, template)
    db.session.commit()
    return template


def _create_transfer(
    seed_user, seed_periods_today, savings_acct,
    template=None, amount=Decimal("200.00"), name="Monthly Savings",
):
    """Helper: create a transfer with shadow transactions via the service.

    ``amount`` and ``name`` are parameterised so callers that need
    multiple ad-hoc transfers in the same period can distinguish
    them and avoid the F-050 / C-22 partial unique index
    ``uq_transfers_adhoc_dedupe`` (which legitimately rejects two
    active ad-hoc rows with identical parameters).
    """
    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings_acct.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            amount=amount,
            status_id=projected.id,
            category_id=seed_user["categories"]["Rent"].id,
            transfer_template_id=template.id if template else None,
            name=name,
        ),
    )
    db.session.commit()
    return xfer


def _create_other_user_with_template():
    """Create a second user with their own template and transfer.

    Returns:
        dict with keys: user, account, savings, template, transfer.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()


    # The account_service factory requires the user to have at least one pay
    # period to anchor against.
    # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
    from datetime import date as _date
    from tests._test_helpers import open_owner_calendar as _open_calendar
    _bootstrap = _open_calendar(other_user.id, _date(2024, 1, 5))[0]
    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

    checking = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=savings_type.id,
            name="Other Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add_all([checking, savings])
    # Both sets of BOOKS open the day before their origination -- see
    # ``_create_savings_account`` above (plan step X-f3c-2b, ruling R-HG).
    open_books_before_the_first_assertion(db.session, checking)
    open_books_before_the_first_assertion(db.session, savings)

    scenario = Scenario(
        user_id=other_user.id, name="Baseline", is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    category = Category(
        user_id=other_user.id,
        group_name="Home",
        item_name="Rent",
    )
    db.session.add(category)

    template = TransferTemplate(
        user_id=other_user.id,
        from_account_id=checking.id,
        to_account_id=savings.id,
        name="Other Transfer",
        default_amount=Decimal("100.00"),
    )
    db.session.add(template)
    db.session.flush()

    from datetime import date
    periods = pay_period_write.record_paydays(
        user_id=other_user.id,
        first_payday=date(2026, 1, 2),
        num_periods=3,
        cadence_days=14,
    )
    db.session.flush()

    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=other_user.id,
            from_account_id=checking.id,
            to_account_id=savings.id,
            pay_period_id=periods[0].id,
            scenario_id=scenario.id,
            amount=Decimal("100.00"),
            status_id=projected.id,
            category_id=category.id,
            transfer_template_id=template.id,
            name="Other Transfer",
        ),
    )
    db.session.commit()

    return {
        "user": other_user,
        "template": template,
        "transfer": xfer,
    }


# ── Template Management ───────────────────────────────────────────


class TestTemplateList:
    """Tests for GET /transfers and GET /transfers/new."""

    def test_list_redirects_to_unified_recurring(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /transfers redirects to the unified Recurring surface.

        The standalone transfers list retired when /transfers folded into
        /templates (Loop B); the URL is kept as a redirect for old
        bookmarks and the create/update routes' post-save redirects.
        Following it lands on /templates showing the transfer.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            _create_template(seed_user, savings, with_rule=False)

            response = auth_client.get("/transfers")
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/templates")

            followed = auth_client.get("/transfers", follow_redirects=True)
            assert followed.status_code == 200
            assert b"Monthly Savings" in followed.data

    def test_new_template_form(self, app, auth_client, seed_user):
        """GET /transfers/new renders the creation form."""
        with app.app_context():
            response = auth_client.get("/transfers/new")

            assert response.status_code == 200
            assert b'name="default_amount"' in response.data
            assert b'name="from_account_id"' in response.data
            assert b"New Recurring Transfer" in response.data

    def test_the_create_form_names_the_destinations_that_derive_a_start(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """The CREATE form carries which destinations lock "Starts on".

        Plan step R7c-b.  A loan payment's first occurrence is the loan's, so
        the control stops being the user's to state the moment a loan is
        chosen as the destination -- and the server cannot know at render
        which the user will choose, so it ships the SET and
        ``recurrence_form.js`` applies it.

        What this can see is the attribute; whether the script actually
        disables the control is ``tests/manual/verify_recurrence_form.py``'s,
        because rendered HTML cannot tell a control a script re-enabled from
        one that was never locked -- the defect class this whole affordance
        belongs to.

        NEGATIVE CONTROL: the savings account below must NOT appear, or the
        attribute is "every account" and the lock would fire on all of them.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 4, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Sav",
                anchor_balance=Decimal("100.00"),
            )
            db.session.commit()

            html = auth_client.get("/transfers/new").data.decode()

            assert f'data-loan-account-ids="{loan.id}"' in html
            assert str(savings.id) not in (
                html.split('data-loan-account-ids="')[1].split('"')[0]
            )

    def test_an_edit_form_names_no_such_destinations(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """An EDIT form locks server-side and must not ship a second rule.

        ``recurrence.bounds_are_derived`` already answers "is this template a
        loan payment" from the row itself, so a client-side set would be a
        SECOND answer to the same question -- and two answers is how they come
        to disagree, which is the defect ``owns_validity_window`` was made the
        one predicate to close.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Sav",
                anchor_balance=Decimal("100.00"),
            )
            template = make_transfer_template(
                db.session, seed_user, to_account=savings,
            )
            db.session.commit()

            html = auth_client.get(
                f"/transfers/{template.id}/edit",
            ).data.decode()

            assert "data-loan-account-ids" not in html


class TestTemplatePrefill:
    """Tests for GET /transfers/new with pre-filled account query params."""

    def test_new_transfer_prefills_from_account(self, app, auth_client, seed_user):
        """GET /transfers/new?from_account=<id> pre-selects the source account."""
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.get(f"/transfers/new?from_account={account_id}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'value="{account_id}"' in html

    def test_new_transfer_prefills_to_account(self, app, auth_client, seed_user):
        """GET /transfers/new?to_account=<id> pre-selects the destination account."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            resp = auth_client.get(f"/transfers/new?to_account={savings.id}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'value="{savings.id}"' in html


class TestTheStartPeriodSelectorComesFromTheDerivation:
    """The create form's pay-period ``<select>`` is answered by ONE calendar.

    Plan step **C2-f3a**.  It read ``pay_period_service.get_all_periods`` for
    its options and ``get_current_period`` to preselect one -- two reads of
    ``budget.pay_periods``, the second SQL whose ``.first()`` carried no
    ``ORDER BY`` (ledger row **P19**) resolved against the process clock (row
    **P49**).  Both are now the one derivation, and the day is the OWNER's.

    ``tests/test_arch/test_one_read_pass_per_render`` counts the derivations;
    these grade what the form actually renders, which a count cannot see.
    """

    def test_the_option_values_are_pay_period_ids(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Every option names a row a foreign key can point at.

        The values come off ``DerivedPeriod.period_id`` now, where they came
        off ``PayPeriod.id`` -- the SAME integer, and the case exists because
        the derived type spells it differently: a template rendering ``p.id``
        against a ``DerivedPeriod`` emits an EMPTY value silently, and the
        POST door would then read "no start period" for a form that showed
        one selected.
        """
        with app.app_context():
            expected = [str(period.id) for period in seed_periods_today]

            html = auth_client.get("/transfers/new").data.decode()

        select = html.split('id="start_period_id"', 1)[1].split("</select>", 1)[0]
        rendered = re.findall(r'<option value="([^"]*)"', select)
        assert rendered == expected, (
            "the start-period options do not name this owner's pay periods "
            "in payday order"
        )
        assert "" not in rendered, (
            "an option rendered an empty value, which is what reading ``.id`` "
            "off a DerivedPeriod produces"
        )

    def test_the_preselected_option_is_the_owners_current_paycheck(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The selected option is the period containing the OWNER's day.

        ``seed_periods_today`` puts ``display_today()`` inside period 4 by
        construction, so the assertion names that period rather than
        recomputing the fixture's own rule.
        """
        with app.app_context():
            today = display_today()
            expected = next(
                period for period in seed_periods_today
                if period.start_date <= today <= last_covered_day(period)
            )

            html = auth_client.get("/transfers/new").data.decode()

        select = html.split('id="start_period_id"', 1)[1].split("</select>", 1)[0]
        selected = re.findall(r'<option value="([^"]*)" selected', select)
        assert selected == [str(expected.id)], (
            "the form preselected a paycheck other than the one the owner is "
            "in today"
        )


class TestTemplateCreate:
    """Tests for POST /transfers."""

    def test_create_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers creates a template with recurrence and generates transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            response = auth_client.post("/transfers", data={
                "name": "Weekly Savings",
                "default_amount": "150.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                **cadence_payload(),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"created" in response.data

            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="Weekly Savings")
                .one()
            )
            assert tmpl.default_amount == Decimal("150.00")
            assert tmpl.recurrence_rule is not None

    def test_create_template_validation_error(self, app, auth_client, seed_user):
        """POST /transfers with missing fields shows a validation error."""
        with app.app_context():
            response = auth_client.post("/transfers", data={
                "name": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_template_same_accounts(self, app, auth_client, seed_user):
        """POST /transfers with from == to account shows a validation error."""
        with app.app_context():
            acct_id = seed_user["account"].id

            response = auth_client.post("/transfers", data={
                "name": "Self Transfer",
                "default_amount": "100.00",
                "from_account_id": acct_id,
                "to_account_id": acct_id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_recurring_template_from_loan_flashes_not_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A recurring template with a LOAN source flashes gracefully, not a 500.

        The transfer form offers every active account as a source, so a user can
        pick a Mortgage.  A transfer OUT of an amortizing loan is rejected by
        ``create_transfer`` (review R6); on the RECURRING path the recurrence
        engine fans out through that service, so an unhandled rejection would
        500 (the T-1 defect the guard newly made reachable).  The route must
        instead roll back and flash -- exactly as the one-time path does -- and
        persist no template.
        """
        with app.app_context():
            loan = create_loan_account(
                {"user": seed_user["user"], "scenario": seed_user["scenario"]},
                db.session, name="Mortgage",
                principal=Decimal("250000.00"), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), term=360,
            )
            db.session.commit()
            response = auth_client.post("/transfers", data={
                "name": "Drain The Mortgage",
                "default_amount": "100.00",
                "from_account_id": str(loan.id),        # loan as SOURCE
                "to_account_id": str(seed_user["account"].id),
                **cadence_payload(),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            # Graceful: 200 (not 500), the guard's message flashed, no template.
            assert response.status_code == 200
            assert b"Could not create transfer" in response.data
            assert b"out of a loan" in response.data
            assert (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="Drain The Mortgage")
                .first()
            ) is None

    def test_create_template_double_submit(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers twice with the same name returns a flash warning
        on the second attempt instead of a 500 error, and creates exactly
        one template in the database."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            form_data = {
                "name": "Duplicate Transfer",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                **cadence_payload(),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }

            # -- First submission: succeeds --
            resp1 = auth_client.post("/transfers", data=form_data)
            assert resp1.status_code == 302, (
                f"First submit returned {resp1.status_code}, expected 302"
            )

            # Verify creation via DB.
            template = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Duplicate Transfer",
            ).one()
            assert template.default_amount == Decimal("100.00")

            # Record how many transfers were generated.
            first_submit_transfer_count = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count()
            assert first_submit_transfer_count > 0, (
                "Recurrence should have generated at least one transfer"
            )

            # -- Second submission: duplicate name, handled gracefully --
            resp2 = auth_client.post("/transfers", data=form_data)
            assert resp2.status_code == 302, (
                f"Second submit returned {resp2.status_code}, expected 302 "
                "(not 500 -- IntegrityError should be caught)"
            )

            # Verify redirect target.
            location = resp2.headers.get("Location", "")
            assert "/transfers" in location, (
                f"Redirect went to {location}, expected /transfers list"
            )

            # Follow the redirect chain and verify the flash warning.  The
            # /transfers list URL now forwards to the unified Recurring
            # surface, so the create route's redirect hops once more before
            # rendering; Flask carries the flash across the chain.
            resp3 = auth_client.get(location, follow_redirects=True)
            assert resp3.status_code == 200
            assert b"already exists" in resp3.data, (
                "Flash warning about duplicate name not found in response"
            )

            # -- Verify database state: exactly 1 template, no orphans --
            template_count = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Duplicate Transfer",
            ).count()
            assert template_count == 1, (
                f"Expected exactly 1 template, found {template_count}"
            )

            # Transfer count unchanged (second submit was rolled back).
            final_transfer_count = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count()
            assert final_transfer_count == first_submit_transfer_count, (
                f"Transfer count changed from {first_submit_transfer_count} "
                f"to {final_transfer_count} after rolled-back duplicate"
            )

            # RecurrenceRule count: exactly 1 for this template.
            rule_count = db.session.query(RecurrenceRule).filter_by(
                id=template.recurrence_rule.id,
            ).count()
            assert rule_count == 1, (
                f"Expected 1 recurrence rule, found {rule_count}"
            )

            # Session health check: a subsequent query must not raise
            # InvalidRequestError (proves rollback was effective).
            total_templates = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert total_templates >= 1


class TestTemplateUpdate:
    """Tests for GET/POST /transfers/<id>/edit and /archive and /unarchive."""

    def test_edit_template_form(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/<id>/edit renders the edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            response = auth_client.get(f"/transfers/{template.id}/edit")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data

    def test_update_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/<id> updates the template."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            response = auth_client.post(f"/transfers/{template.id}", data={
                "name": "Updated Savings",
                "default_amount": "300.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                **cadence_payload(),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"updated" in response.data

            db.session.refresh(template)
            assert template.default_amount == Decimal("300.00")

    def test_a_retained_transfer_is_reported_and_the_edit_still_commits(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A row the pass declined to change is TOLD, not put to the owner.

        Plan step R10-b's route half, and the transfer twin of
        ``test_templates.py``'s transaction case.  A retained row has no
        keep-vs-use question -- the pass already took the only safe outcome --
        so ``regenerate_or_conflict_chooser`` must NOT render the chooser over
        it, must flash the notice, and must let the edit commit.  Rendering the
        chooser here would show an empty table and roll the whole edit back,
        which is how an ordinary amount change came to do nothing silently.
        """
        with app.app_context():
            from app.services import transfer_recurrence

            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)  # rule, amount 200
            periods = all_periods(seed_user["user"].id)
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            # A note on a FUTURE row, which the update route's sweep reaches.
            noted = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id)
                .order_by(Transfer.due_date.desc())
                .first()
            )
            transfer_service.update_transfer(
                noted.id, seed_user["user"].id, notes="reconcile this one",
            )
            # Moving the DESTINATION is what retains it: the row's records
            # would be re-filed against an account nobody asserted them on.
            elsewhere = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings.account_type_id,
                    name="Second Savings",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.commit()
            tid, noted_id = template.id, noted.id
            elsewhere_id = elsewhere.id

            # The amount MOVES (200 -> 275), which is what arms the chooser
            # branch at all: it is gated on ``template.default_amount !=
            # before.amount``.  An adversarial review of R10-b found the first
            # version posting the unchanged 200.00, so the "no chooser"
            # assertion below was dead however the branch behaved.
            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Monthly Savings",
                "default_amount": "275.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(elsewhere_id),
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Some upcoming instances were hand-edited" not in resp.data
            assert b"kept the value it already had" in resp.data

            db.session.expire_all()
            reloaded = db.session.get(TransferTemplate, tid)
            assert reloaded.to_account_id == elsewhere_id, (
                "the edit was rolled back by a conflict that asked nothing"
            )
            assert reloaded.default_amount == Decimal("275.00")
            held = db.session.get(Transfer, noted_id)
            assert held.to_account_id == savings.id, (
                "the retained row must be exactly as the pass found it"
            )
            assert held.notes == "reconcile this one"
            assert held.amount == Decimal("200.00"), (
                "a retained row is left ALONE, not re-priced"
            )

    def test_transfer_amount_change_with_override_shows_chooser(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A transfer-template amount change colliding with a hand-edited
        transfer shows the conflict chooser (the shared flow, transfer kind)
        and does not commit the pending edit."""
        with app.app_context():
            from app.services import (
                pay_period_service, transfer_recurrence, transfer_service,
            )
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)  # rule, amount 200
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), scenario.id,
            )
            db.session.flush()
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id)
                .order_by(Transfer.due_date.desc())
                .first()
            )
            # Hand-edit the future transfer, shadow-safe via the service.
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount=Decimal("350.00"), is_override=True,
            )
            db.session.commit()
            tid = template.id
            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Monthly Savings",
                "default_amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                **cadence_payload(),
            })
            assert resp.status_code == 200
            assert b"hand-edited" in resp.data
            assert b"Keep" in resp.data and b"Use" in resp.data
            # Rolled back: the template keeps its pre-edit amount.
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, tid,
            ).default_amount == Decimal("200.00")

    def test_transfer_chooser_apply_use_realigns_transfer_and_shadows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply 'use' realigns the overridden transfer AND both shadow
        transactions to the new amount, preserving transfer invariant 3
        (shadow amounts always equal the parent's)."""
        with app.app_context():
            from app.services import (
                pay_period_service, transfer_recurrence, transfer_service,
            )
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)  # rule, amount 200
            scenario = seed_user["scenario"]
            periods = all_periods(seed_user["user"].id)
            transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), scenario.id,
            )
            db.session.flush()
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id)
                .order_by(Transfer.due_date.desc())
                .first()
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount=Decimal("350.00"), is_override=True,
            )
            db.session.commit()
            tid, xfer_id = template.id, xfer.id
            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Monthly Savings",
                "default_amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                **cadence_payload(),
                "conflict_apply": "1",
                f"conflict_decision_{xfer_id}": "use",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transfer, xfer_id)
            assert reloaded.amount == Decimal("250.00")
            assert reloaded.is_override is False
            # Both shadows mirror the realigned amount (invariant 3).
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .all()
            )
            assert len(shadows) == 2
            assert all(
                shadow_amount(s) == Decimal("250.00") for s in shadows
            )

    def test_archive_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/<id>/archive archives the template and soft-deletes transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            response = auth_client.post(
                f"/transfers/{template.id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"archived" in response.data

            db.session.refresh(template)
            assert template.is_active is False

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

    def test_unarchive_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/<id>/unarchive restores the template and its transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            # Deactivate first.
            template.is_active = False
            xfer.is_deleted = True
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}/unarchive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"unarchived" in response.data

            db.session.refresh(template)
            assert template.is_active is True

            db.session.refresh(xfer)
            assert xfer.is_deleted is False

    def test_update_other_users_template_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /transfers/<id> for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.post(
                f"/transfers/{other['template'].id}",
                data={"name": "Hacked"},
                follow_redirects=True,
            )

            assert response.status_code == 404

    def test_archive_other_users_template_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /transfers/<id>/archive for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.post(
                f"/transfers/{other['template'].id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 404


# ── Grid Cell Routes ───────────────────────────────────────────────


class TestGridCells:
    """Tests for grid cell HTMX partial endpoints."""

    def test_get_cell(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/cell/<id> returns the cell partial."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.get(f"/transfers/cell/{xfer.id}")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data
            assert b"200" in response.data

    def test_get_quick_edit(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/quick-edit/<id> returns the quick-edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.get(f"/transfers/quick-edit/{xfer.id}")

            assert response.status_code == 200
            assert b'name="amount"' in response.data
            assert b"200" in response.data

    def test_get_full_edit(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/<id>/full-edit returns the full-edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.get(f"/transfers/{xfer.id}/full-edit")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data
            assert b'name="amount"' in response.data

    def test_quick_edit_disables_amount_on_finalised_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The transfer inline quick-edit disables the amount input on a
        finalised transfer and shows the revert hint (#26)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            resp = auth_client.get(f"/transfers/quick-edit/{xfer.id}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert field_is_disabled(html, "amount")
            assert "Finalised" in html
            assert "autofocus" not in html

    def test_full_edit_locks_money_fields_on_finalised_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The transfer full-edit disables amount / period / category /
        due-date on a finalised transfer and shows the revert notice, while
        the Status dropdown and Notes stay editable (#26)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "This transfer is finalised" in html
            assert field_is_disabled(html, "amount")
            assert field_is_disabled(html, "pay_period_id")
            assert field_is_disabled(html, "category_id")
            assert field_is_disabled(html, "due_date")
            assert not field_is_disabled(html, "status_id")
            assert not field_is_disabled(html, "notes")

    def test_full_edit_editable_on_projected_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A projected transfer's full-edit money fields stay editable with no
        finalised notice (no regression)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "finalised" not in html.lower()
            assert not field_is_disabled(html, "amount")

    def test_full_edit_renders_due_date_input_for_transfer_shadow(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/<shadow>/full-edit renders an editable due_date field.

        The transfer here has no due date, yet the input renders (empty) so the
        user can add one; get_full_edit detects the shadow and returns the
        transfer edit form, which posts to the transfer update route and
        mirrors the value to both shadows.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .first()
            )

            response = auth_client.get(f"/transactions/{shadow.id}/full-edit")

            assert response.status_code == 200
            assert b'name="due_date"' in response.data
            assert b'type="date"' in response.data

    def test_get_cell_other_users_transfer(self, app, auth_client, seed_user):
        """GET /transfers/cell/<id> for another user's transfer returns 404.

        Read-path IDOR: response must not leak the other user's transfer data.
        """
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.get(f"/transfers/cell/{other['transfer'].id}")

            assert response.status_code == 404
            # Verify no leakage of the other user's transfer data.
            assert b"Other Transfer" not in response.data
            assert b"100.00" not in response.data


# ── Transfer Instance Operations ──────────────────────────────────


class TestTransferInstance:
    """Tests for transfer update, mark-done, cancel, and delete."""

    def test_update_transfer_amount(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transfers/instance/<id> updates the transfer amount."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "250.00"},
            )

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.amount == Decimal("250.00")

    def test_resubmitting_an_unchanged_status_does_not_re_date_the_money(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A notes-only save on a PAID transfer must not move its settle day.

        Finding **N-146**, plan step X-aj1.  On a finalised transfer the
        full-edit form disables the money fields, so they are omitted from the
        POST and the finalised-row lock sees nothing locked; the status
        ``<select>`` is NOT disabled and submits the row's own status, so an
        ordinary notes edit arrives as an identity transition.  The transfer
        service's own status seam re-stamped the settle instant on any entry
        into a settled status -- and since plan step E1a that day IS the
        ``entry_date`` the transfer's postings are filed under, so editing the
        notes moved the money to today.  Measured at ``HEAD`` before the fix:
        a transfer settled 7 days earlier went from ONE ledger entry to three,
        the last dated today.

        The payload here is the exact one the locked form submits.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            done_id = ref_cache.status_id(StatusEnum.DONE)

            settled_at = display_today() - timedelta(days=7)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=done_id, settle_day=an_entered_day(settled_at),
            )
            db.session.commit()

            def _state():
                """Return the shadows' settle instants and the posted days."""
                db.session.expire_all()
                paid = sorted(
                    s.settled_on for s in db.session.query(Transaction)
                    .filter_by(transfer_id=xfer.id).all()
                )
                dated = sorted(
                    e.entry_date for e in db.session.query(JournalEntry)
                    .filter_by(transfer_id=xfer.id).all()
                )
                return paid, dated

            before = _state()
            assert all(p is not None for p in before[0])

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "version_id": str(xfer.version_id),
                    "status_id": str(done_id),
                    "notes": "reconciled against the statement",
                },
            )
            assert response.status_code == 200, response.data[:400]

            paid_after, dated_after = _state()
            assert paid_after == before[0], (
                f"the settle instant moved: {before[0]} -> {paid_after}"
            )
            assert dated_after == before[1], (
                f"the posted entry_date moved: {before[1]} -> {dated_after}"
            )

            # A SECOND re-submit, this one with no other field at all, must
            # not re-date it either: the schema carries no settle day, so the
            # seam decides, and before X-aj1 it stamped now() on any entry
            # into a settled status.
            #
            # This was ``Paid -> Settled`` -- the ARCHIVE -- until plan step
            # **balance:X-am** deleted that status, and it was the stronger
            # case: a real state CHANGE that still had to preserve the day,
            # where an identity re-submit merely has to leave things alone.
            # With the archive gone there is no non-identity move into or
            # inside the settled band from Paid, so what remains is the bare
            # re-submit -- which is the shape the popover actually produces,
            # since it posts the whole row on every Save.
            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "version_id": str(xfer.version_id),
                    "status_id": str(done_id),
                },
            )
            assert response.status_code == 200, response.data[:400]

            paid_archived, dated_archived = _state()
            assert paid_archived == before[0], (
                f"the bare re-submit moved the settle instant: "
                f"{before[0]} -> {paid_archived}"
            )
            assert dated_archived == before[1], (
                f"the bare re-submit moved the posted entry_date: "
                f"{before[1]} -> {dated_archived}"
            )

    def test_update_transfer_clears_category(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The full-edit "-- None --" option (category_id="") clears it.

        ``transfer_service.update_transfer`` was always built to clear
        on ``category_id=None`` (the assignment sits outside the
        ownership probe's not-None guard), but the schema's pre_load
        used to DROP the empty submit so the None never arrived -- the
        nullable-field clear defect.  The parent and BOTH shadows must
        come back NULL (Transfer Invariant 3: shadow fields mirror the
        parent).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            assert xfer.category_id == seed_user["categories"]["Rent"].id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"category_id": ""},
            )

            assert response.status_code == 200
            db.session.refresh(xfer)
            assert xfer.category_id is None
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            assert all(s.category_id is None for s in shadows)

    def test_update_transfer_due_date(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /transfers/instance/<id> with due_date updates parent and shadows."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"due_date": "2026-04-22"},
            )

            assert response.status_code == 200
            db.session.refresh(xfer)
            assert xfer.due_date == date(2026, 4, 22)
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            assert all(s.due_date == date(2026, 4, 22) for s in shadows)

    def test_update_transfer_blank_due_date_clears_it(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A blank due_date on the edit form clears the stored due date.

        Retargeted with the nullable-field clear fix (developer-ratified
        behavior change): the edit form pre-fills due_date with the
        stored value, so an empty submit is the user's deliberate clear
        -- TransferUpdateSchema now loads it as an explicit None and the
        service nulls the parent and both shadows.  The old "blank does
        not clobber" shield this test pinned also blocked legitimate
        clears; the stale-form clobber it guarded against (a form
        rendered before the date was set) is the C-18 ``version_id``
        pin's job -- the real form ships the pin and a stale submit gets
        a 409 before any field is applied.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, due_date=date(2026, 5, 1),
            )
            db.session.commit()

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "300.00", "due_date": ""},
            )

            assert response.status_code == 200
            db.session.refresh(xfer)
            assert xfer.amount == Decimal("300.00")
            assert xfer.due_date is None
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            assert all(s.due_date is None for s in shadows)

    def test_mark_done(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/instance/<id>/mark-done sets status to done."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"

    def test_cancel_transfer(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/instance/<id>/cancel sets status to cancelled."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            assert response.status_code == 200

            db.session.refresh(xfer)
            assert xfer.status.name == "Cancelled"

    def test_finalised_transfer_amount_edit_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH amount on a Paid transfer is refused; amount unchanged (#26).

        A finalised transfer's money fields must not be silently
        rewritten through the inline edit -- the user reverts to
        Projected first.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")
            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "999.99"},
            )
            assert response.status_code == 400
            body = response.data.decode()
            assert "finalised" in body
            assert "transfer" in body

            db.session.refresh(xfer)
            assert xfer.amount == Decimal("200.00")

    def test_finalised_transfer_revert_and_amount_edit_allowed(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Reverting Paid -> Projected AND editing amount in one PATCH is
        allowed -- the escape hatch the lock preserves (#26)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")
            projected_id = (
                db.session.query(Status).filter_by(name="Projected").one().id
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(projected_id), "amount": "250.00"},
            )
            assert response.status_code == 200
            db.session.refresh(xfer)
            assert xfer.status_id == projected_id
            assert xfer.amount == Decimal("250.00")

    def test_finalised_transfer_shadow_amount_edit_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Editing a finalised transfer's amount via its SHADOW transaction
        PATCH is also refused -- the lock covers the transaction-shadow
        entry point, and the parent amount is unchanged (#26)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .first()
            )

            response = auth_client.patch(
                f"/transactions/{shadow.id}",
                data={"estimated_amount": "999.99"},
            )
            assert response.status_code == 400
            assert "finalised" in response.data.decode()

            db.session.refresh(xfer)
            assert xfer.amount == Decimal("200.00")
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert all(shadow_amount(s) == Decimal("200.00") for s in shadows)

    def test_update_transfer_to_credit_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH status_id=Credit on a transfer is refused with 400.

        Credit is a transaction-only status: the credit/auto-payback
        workflow refuses transfers, so a Credit transfer would be
        balance-excluded on both accounts with no compensating payback
        -- it would silently vanish from both projections.  The
        transfer-specific transition map closes the hole the shared
        map left open (``TransferUpdateSchema.status_id`` accepts any
        integer).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            credit_id = (
                db.session.query(Status).filter_by(name="Credit").one().id
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(credit_id)},
            )

            assert response.status_code == 400
            assert "Invalid transfer status transition" in response.data.decode()

            # Parent and both shadows untouched -- still Projected.
            db.session.refresh(xfer)
            assert xfer.status.name == "Projected"
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            assert all(s.status.name == "Projected" for s in shadows)

    def test_update_transfer_to_received_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH status_id=Received on a transfer is refused with 400.

        Received is a display convention for regular income rows; the
        transfer service settles both shadows with Done.  Same
        transfer-map hole as the Credit case.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            received_id = (
                db.session.query(Status).filter_by(name="Received").one().id
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"status_id": str(received_id)},
            )

            assert response.status_code == 400
            db.session.refresh(xfer)
            assert xfer.status.name == "Projected"

    def test_shadow_patch_to_credit_rejected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCHing a transfer SHADOW to Credit is refused with 400.

        The shadow PATCH path forwards any submitted ``status_id`` to
        ``transfer_service.update_transfer``; before the transfer map
        split this set the parent and BOTH shadows to Credit, silently
        removing the whole transfer from both accounts' projections
        with no payback compensation (the mark-credit routes block
        shadows, but this generic path did not).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            credit_id = (
                db.session.query(Status).filter_by(name="Credit").one().id
            )
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .first()
            )

            response = auth_client.patch(
                f"/transactions/{shadow.id}",
                data={"status_id": str(credit_id)},
            )

            assert response.status_code == 400
            assert "Invalid transfer status transition" in response.data.decode()

            # Parent and both shadows untouched -- still Projected.
            db.session.refresh(xfer)
            assert xfer.status.name == "Projected"
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert all(s.status.name == "Projected" for s in shadows)

    def test_delete_ad_hoc_transfer(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transfers/instance/<id> hard-deletes an ad-hoc transfer."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            # Ad-hoc transfer (no template).
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template=None)
            xfer_id = xfer.id

            response = auth_client.delete(f"/transfers/instance/{xfer_id}")

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            # Should be hard-deleted.
            assert db.session.get(Transfer, xfer_id) is None

    def test_delete_template_transfer_soft_deletes(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """DELETE /transfers/instance/<id> soft-deletes a template transfer."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            response = auth_client.delete(f"/transfers/instance/{xfer.id}")

            assert response.status_code == 200

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

    def test_template_transfer_override_on_amount_change(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Updating amount on a template transfer sets is_override=True."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            assert xfer.is_override is False

            auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "999.00"},
            )

            db.session.refresh(xfer)
            assert xfer.is_override is True

    def test_cancelled_transfer_effective_amount_zero(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A cancelled transfer has effective_amount of Decimal('0')."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            db.session.refresh(xfer)
            assert owned_contribution(xfer) == Decimal("0")

    def test_update_other_users_transfer(self, app, auth_client, seed_user):
        """PATCH /transfers/instance/<id> for another user's transfer returns 404.

        IDOR write-path (HIGH priority): must prove the transfer was not modified.
        """
        with app.app_context():
            other = _create_other_user_with_template()
            target = other["transfer"]
            orig_amount = target.amount
            orig_name = target.name

            response = auth_client.patch(
                f"/transfers/instance/{target.id}",
                data={"amount": "9999.00"},
            )

            assert response.status_code == 404

            # Prove no state change occurred.
            db.session.expire_all()
            db.session.refresh(target)
            assert target.amount == orig_amount, (
                "IDOR attack modified victim's transfer amount!"
            )
            assert target.name == orig_name, (
                "IDOR attack modified victim's transfer name!"
            )


# ── Ad-Hoc Creation ───────────────────────────────────────────────


class TestAdHoc:
    """Tests for POST /transfers/ad-hoc."""

    def test_create_ad_hoc_transfer(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/ad-hoc creates a transfer and returns 201."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Quick Transfer",
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            assert response.status_code == 201
            assert response.headers.get("HX-Trigger") == "balanceChanged"

    def test_create_ad_hoc_transfer_with_due_date(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with due_date sets it on the parent and both shadows."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Dated Transfer",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "due_date": "2026-03-15",
            })

            assert response.status_code == 201
            xfer = (
                db.session.query(Transfer)
                .filter_by(name="Dated Transfer")
                .one()
            )
            assert xfer.due_date == date(2026, 3, 15)
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            assert all(s.due_date == date(2026, 3, 15) for s in shadows)

    def test_create_ad_hoc_validation_error(self, app, auth_client, seed_user):
        """POST /transfers/ad-hoc with missing fields returns 422."""
        with app.app_context():
            response = auth_client.post("/transfers/ad-hoc", data={
                "name": "Bad Transfer",
            })

            assert response.status_code == 422
            body = response.get_json()
            assert "errors" in body

    def test_create_ad_hoc_other_users_period(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with another user's period returns 404.

        Create-path IDOR: must verify no transfer was created in the other
        user's period.
        """
        with app.app_context():
            other = _create_other_user_with_template()
            savings = _create_savings_account(seed_user)

            # Use other user's period.
            from app.models.pay_period import PayPeriod
            other_period = (
                db.session.query(PayPeriod)
                .filter_by(user_id=other["user"].id)
                .first()
            )

            # Count transfers in the other user's period before the request.
            count_before = db.session.query(Transfer).filter_by(
                pay_period_id=other_period.id,
            ).count()

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": other_period.id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            assert response.status_code == 404

            # Prove no transfer was created.
            db.session.expire_all()
            count_after = db.session.query(Transfer).filter_by(
                pay_period_id=other_period.id,
            ).count()
            assert count_after == count_before, (
                "IDOR attack created a transfer in victim's period!"
            )

    def test_create_ad_hoc_double_submit(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc twice with identical params returns idempotent success.

        F-050 / C-22: the partial unique index
        ``uq_transfers_adhoc_dedupe`` on (user_id, from_account_id,
        to_account_id, amount, pay_period_id, scenario_id) rejects the
        second active ad-hoc transfer with identical parameters.  The
        route translates the IntegrityError into idempotent 201 +
        cell HTML so the user sees the transfer they intended to
        create regardless of which request reached the database
        first.  After two identical submissions the period must
        contain exactly one active ad-hoc transfer (and exactly two
        active shadow transactions, not four).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            data = {
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Double Transfer",
                "category_id": str(seed_user["categories"]["Rent"].id),
            }

            response1 = auth_client.post("/transfers/ad-hoc", data=data)
            assert response1.status_code == 201

            response2 = auth_client.post("/transfers/ad-hoc", data=data)
            # Idempotent success: the second request returns 201 too,
            # but the body references the SAME transfer the first one
            # produced (no new row was inserted).
            assert response2.status_code == 201
            assert response2.headers.get("HX-Trigger") == "balanceChanged"

            # Verify exactly 1 active ad-hoc transfer exists.
            db.session.expire_all()
            transfers = (
                db.session.query(Transfer)
                .filter_by(
                    pay_period_id=seed_periods_today[0].id,
                    user_id=seed_user["user"].id,
                    is_deleted=False,
                )
                .filter(Transfer.transfer_template_id.is_(None))
                .filter_by(amount=Decimal("50.00"))
                .all()
            )
            assert len(transfers) == 1, (
                f"Expected exactly 1 active ad-hoc transfer after "
                f"double-submit, found {len(transfers)}"
            )
            # Verify exactly 2 active shadow transactions (not 4 --
            # invariant 1 still holds with the new constraint).
            shadow_count = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfers[0].id, is_deleted=False)
                .count()
            )
            assert shadow_count == 2, (
                f"Expected exactly 2 active shadows for the deduped "
                f"transfer, found {shadow_count}"
            )

    def test_create_ad_hoc_different_amount_allowed(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Two ad-hoc transfers with different amounts both succeed.

        F-050 / C-22: the unique constraint includes ``amount`` so
        a $50 transfer and a $100 transfer between the same accounts
        in the same period are treated as different ad-hoc rows --
        the user legitimately split a payment, the constraint must
        not block it.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            base = {
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            }

            r1 = auth_client.post(
                "/transfers/ad-hoc", data={**base, "amount": "50.00"},
            )
            r2 = auth_client.post(
                "/transfers/ad-hoc", data={**base, "amount": "100.00"},
            )

            assert r1.status_code == 201
            assert r2.status_code == 201

            db.session.expire_all()
            count = (
                db.session.query(Transfer)
                .filter_by(
                    pay_period_id=seed_periods_today[0].id,
                    user_id=seed_user["user"].id,
                    is_deleted=False,
                )
                .filter(Transfer.transfer_template_id.is_(None))
                .count()
            )
            assert count == 2, (
                f"Expected 2 distinct ad-hoc transfers, found {count}"
            )

    def test_mark_done_transfer_sets_the_settle_day_on_both_shadows(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/instance/<id>/mark-done dates both shadows.

        F-048 / C-22: parity with ``transactions.mark_done``.
        Settling a transfer must record when it was settled so
        ``Transaction.days_paid_before_due`` analytics, the
        dashboard's "paid on time" indicator, and any downstream
        report that reads the settle day work.  Both shadow
        transactions are checked because the parent transfer has
        no ``settled_on`` column of its own.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done"
            )
            assert response.status_code == 200

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )
            assert len(shadows) == 2
            for shadow in shadows:
                assert shadow.status.name == "Paid"
                assert shadow.settled_on is not None, (
                    f"Shadow {shadow.id} has no settled_on after mark-done; "
                    f"the F-048 parity gap is back."
                )

    def test_re_marking_a_settled_transfer_does_not_re_date_it(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A replayed mark-done leaves a settled transfer's money where it is.

        Finding **N-178**, reproduced before it was fixed: ``done -> done`` is a
        legal transition and neither mark-done route gates on status, so a stale
        page, a second tab or a replayed POST re-submits the settle.  The route
        used to pass ``settled_on=db.func.now()`` explicitly, and
        ``update_transfer``'s explicit-day branch wrote both shadows
        VERBATIM after the status seam has already preserved the existing
        instant -- so the re-submit moved the money to today.  Since plan step
        E1a a settle's civil day IS the posted ``entry_date``, which is why this
        asserts the LEDGER and not only the column: measured at the defective
        commit, a transfer settled 7 days earlier gained a reversal at its real
        settle day plus a fresh posting at today.

        This is finding N-146 through a second door.  N-146's fix was in the
        seam; nothing stopped a caller overriding the seam, and that is what
        both mark-done routes did.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            assert auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done"
            ).status_code == 200

            # Back-date THROUGH the service, so the posted ledger follows the
            # column.  Setting the attribute directly would leave the ledger at
            # today and the assertion below could pass on a stale ledger rather
            # than on a preserved one -- the control has to start from the state
            # a genuinely week-old settle is really in.
            settled_a_week_ago = display_today() - timedelta(days=7)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, settle_day=an_entered_day(settled_a_week_ago),
            )
            db.session.commit()

            def _ledger_days():
                return sorted(
                    entry.entry_date
                    for entry in db.session.query(JournalEntry)
                    .filter(JournalEntry.transfer_id == xfer.id)
                    .all()
                )

            days_before = _ledger_days()
            shadow_days_before = {
                shadow.id: shadow.settled_on
                for shadow in db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            }
            assert shadow_days_before, "fixture produced no shadows"

            # The replay.
            assert auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done"
            ).status_code == 200

            db.session.expire_all()
            for shadow in (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            ):
                moved = (
                    shadow.settled_on - shadow_days_before[shadow.id]
                ).days
                assert moved == 0, (
                    f"Shadow {shadow.id} was re-dated by {moved} days by a "
                    f"replayed mark-done (N-178)."
                )
            assert _ledger_days() == days_before, (
                "The replayed mark-done moved the posted ledger: "
                f"{days_before} -> {_ledger_days()} (N-178)."
            )


class TestTransferSettleDayEditDoor:
    """The transfer PATCH's settle-day correction (rulings R-ED / R-EG).

    **This door is not a convenience twin of the transaction one.**  It is the
    ONLY correction path for the rows finding **N-181** names: all eight settled
    rows whose day the X-f1b backfill had to invent from a pay period's
    ``start_date`` are transfer SHADOWS (four pairs, measured on the 2026-08-03
    production clone), and a shadow's full-edit popover is the TRANSFER form --
    ``routes/transactions/forms.get_full_edit`` redirects a shadow to it rather
    than rendering the transaction popover.  Without this door X-f1c would close
    N-181 in principle and zero rows in fact.
    """

    @staticmethod
    def _settled_transfer(seed_user, seed_periods_today, day):
        """Return a settled transfer whose money moved on *day*, ledger in step.

        Settled and then back-dated THROUGH the service, so the fixture's own
        journal entry carries *day*.  A bare attribute write would leave the
        ledger at today and let a "the ledger followed" assertion pass on a
        stale comparison instead of on the edit under test.
        """
        savings = _create_savings_account(seed_user)
        xfer = _create_transfer(seed_user, seed_periods_today, savings)
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            status_id=ref_cache.status_id(StatusEnum.DONE),
        )
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id, settle_day=an_entered_day(day),
        )
        db.session.commit()
        return xfer

    @staticmethod
    def _net_by_day(xfer_id):
        """Return ``{entry_date: net posted magnitude}`` for one transfer.

        Thin wrapper over the shared
        :func:`tests._test_helpers.net_posted_by_day`; see it for why the NET
        rather than the raw ``entry_date`` list is what grades a correction.
        """
        return net_posted_by_day(JournalEntry.transfer_id == xfer_id)

    @staticmethod
    def _shadow_days(xfer_id):
        """Return both live shadows' settle days, as a set (Invariant 3)."""
        return {
            shadow.settled_on
            for shadow in db.session.query(Transaction)
            .filter_by(transfer_id=xfer_id, is_deleted=False)
            .all()
        }

    def test_a_RE_SUBMITTED_day_does_not_restate_its_basis(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Plan step **X-az**: the ECHO rule at the TRANSFER PATCH.

        This form prefills the settle-day box and posts it on Save, so an
        untouched Save re-submits the day the pair already carries.  Stamping
        that ``entered`` rewrites what the legs knew about their own day -- a
        reconcile-panel BOUND, or a day the bank stated, becomes the owner's own
        typing, with the day unchanged so nothing releases the clearing link.

        A transfer carries neither settle column, so the rule needs the pair off
        the INCOME shadow (``Transfer.settle_day_columns``, ONE read of both).
        Drop the ``recorded`` argument at this route's ``settle_day_for_status``
        call and this fails.
        """
        with app.app_context():
            day = display_today() - timedelta(days=6)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, day,
            )
            for shadow in db.session.query(Transaction).filter_by(
                transfer_id=xfer.id, is_deleted=False,
            ):
                record_settle_day(shadow, an_asserted_day(day))
            db.session.commit()

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_on": day.isoformat()},
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            db.session.expire_all()
            bases = {
                recorded_settle_day(shadow)
                for shadow in db.session.query(Transaction).filter_by(
                    transfer_id=xfer.id, is_deleted=False,
                )
            }
            assert bases == {an_asserted_day(day)}, (
                "an untouched Save laundered the pair's BOUND into the owner's "
                "own day"
            )

    def test_a_transfer_day_the_owner_MOVED_is_their_own(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The firing half of the rule above, so it is not "never restate"."""
        with app.app_context():
            day = display_today() - timedelta(days=6)
            corrected = day + timedelta(days=2)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, day,
            )
            for shadow in db.session.query(Transaction).filter_by(
                transfer_id=xfer.id, is_deleted=False,
            ):
                record_settle_day(shadow, an_asserted_day(day))
            db.session.commit()

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_on": corrected.isoformat()},
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            db.session.expire_all()
            bases = {
                recorded_settle_day(shadow)
                for shadow in db.session.query(Transaction).filter_by(
                    transfer_id=xfer.id, is_deleted=False,
                )
            }
            assert bases == {an_entered_day(corrected)}

    def test_the_SHADOW_branch_of_the_transaction_PATCH_echoes_too(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Plan step **X-az**: the ECHO rule at the THIRD status door.

        A transfer shadow PATCHed through ``/transactions/<id>`` branches into
        ``routes/transactions/_shadow_mutations``, which carries its own call of
        the shared reading -- and a third spelling of one rule is three chances
        for one of them to launder.  It reads the SHADOW's own recorded pair,
        which is the pair for both legs (Transfer Invariant 3).

        Drop the ``recorded`` argument there and this fails while the two doors
        above stay green, which is the point of grading all three.
        """
        with app.app_context():
            day = display_today() - timedelta(days=6)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, day,
            )
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer.id, is_deleted=False,
            ).all()
            for shadow in shadows:
                record_settle_day(shadow, an_observed_day(day))
            db.session.commit()
            shadow_id = shadows[0].id

            response = auth_client.patch(
                f"/transactions/{shadow_id}",
                data={"settled_on": day.isoformat()},
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            db.session.expire_all()
            assert recorded_settle_day(
                db.session.get(Transaction, shadow_id),
            ) == an_observed_day(day), (
                "the shadow branch laundered a bank OBSERVATION into the "
                "owner's own day"
            )

    def test_correcting_the_day_moves_both_shadows_and_the_ledger(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH ``settled_on`` re-dates the pair and its postings (R-ED).

        The gate ruling R-ED names for this half in terms: a test that EDITS a
        settled row's day and asserts the LEDGER followed.  Both shadows take
        the corrected day (Transfer Invariant 3 --
        ``posting_service._entry_date`` reads the income shadow's day for the
        pair), and the reconcile reverses the stale-dated entry and re-posts at
        the corrected day (finding **N-13**).
        """
        with app.app_context():
            original = display_today() - timedelta(days=9)
            corrected = display_today() - timedelta(days=4)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, original,
            )
            assert self._net_by_day(xfer.id) == {original: Decimal("200.00")}, (
                "fixture did not leave its net posted effect at the settle day: "
                f"{self._net_by_day(xfer.id)}"
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_on": corrected.isoformat()},
            )
            assert response.status_code == 200, response.get_data(as_text=True)[:300]

            db.session.expire_all()
            assert self._shadow_days(xfer.id) == {corrected}, (
                "the correction did not reach both shadows equally"
            )
            assert self._net_by_day(xfer.id) == {corrected: Decimal("200.00")}, (
                "the settle day moved but the posted ledger did not follow: "
                f"net effect by day is {self._net_by_day(xfer.id)}, expected "
                f"the whole $200.00 at {corrected} and nothing left at "
                f"{original}"
            )

    def test_reverting_to_projected_ignores_the_submitted_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The unlock path survives the form re-submitting the transfer's day.

        Ruling **R-EG**, the transfer half.  ``apply_settle_day_correction``
        raises ``ValidationError`` for a day supplied on an unsettled transfer,
        and the full-edit form re-submits the day the row already carries when
        the user sets Status to Projected to unlock the amount -- so without
        the route dropping it, the documented unlock path would 400 on every
        settled transfer.  Graded on the 400 NOT happening, on both shadows
        being undated, AND on the ledger being reversed.

        **That last clause was a docstring overclaim until a neutral review
        measured it.**  The body asserted only the status and the shadows, so a
        planted mutant that left the settled effect POSTED through a revert --
        the balance keeping money the user just said never moved -- survived
        this test.  Its transaction-side sibling graded the ledger from the
        start; the asymmetry was an omission, not a design choice.
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=5)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, settled_day,
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_on": settled_day.isoformat(),
                },
            )
            assert response.status_code == 200, (
                "the unlock path (Status -> Projected) was refused because the "
                "form re-submitted the transfer's own settle day: "
                f"{response.get_data(as_text=True)[:300]}"
            )

            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert self._shadow_days(xfer.id) == {None}, (
                "the revert left a settle day on a shadow, breaking the "
                "settled-iff-dated invariant"
            )
            assert self._net_by_day(xfer.id) == {}, (
                "the revert left the settled effect POSTED -- the balance keeps "
                f"money the user just said never moved: {self._net_by_day(xfer.id)}"
            )

    def test_a_future_settle_day_is_refused_and_moves_no_money(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A day that has not happened is refused, and the balance holds.

        Ruling **R-EJ**, the transfer half.  A settled source counts from its
        own ``settled_on``, and ``walk_cash_ledger`` absorbs one into an
        assertion only when the assertion is dated ON OR AFTER it -- so a
        future-dated settle rides on top of every assertion until that day
        arrives, putting already-spent money back in the rendered balance.
        Measured on the live route before the guard existed: a ``$200``
        transfer out of Checking read ``$800`` against a ``$1,000`` anchor, and
        PATCHing its day forward answered **200** with Checking back at
        ``$1,000``.

        The BALANCE is asserted, not just the status code: a 400 that still let
        the write through would pass a status-only check, and the balance is
        the thing the defect actually moved.
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=3)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, settled_day,
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "settled_on": (display_today() + timedelta(days=400)).isoformat(),
                },
            )
            assert response.status_code == 400, (
                "a settle day 400 days out was accepted: "
                f"{response.status_code}"
            )

            db.session.expire_all()
            assert self._shadow_days(xfer.id) == {settled_day}, (
                "the refused day was written anyway"
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            ) == before, (
                "the refused future settle moved the rendered balance -- "
                "already-spent money came back"
            )

    def test_full_edit_offers_the_correction_only_on_a_settled_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The correction input renders for a settled transfer and not before.

        A Projected transfer's money has not moved, so there is no day to
        state.  Asserted from BOTH openers -- the transfers page and a grid
        shadow cell -- because the shadow opener is the one N-181's rows are
        reached through, and it renders this same partial from a different
        blueprint.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, account_id=savings.id)
                .one()
            )

            for url in (
                f"/transfers/{xfer.id}/full-edit",
                f"/transactions/{shadow.id}/full-edit",
            ):
                response = auth_client.get(url)
                assert response.status_code == 200, (
                    f"{url} did not render: a 500 would satisfy the negative "
                    "assertion below without the condition holding"
                )
                body = response.get_data(as_text=True)
                assert 'name="settled_on"' not in body, (
                    f"{url} offered a settle day on a Projected transfer"
                )

            settled_day = display_today() - timedelta(days=2)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, settle_day=an_entered_day(settled_day),
            )
            db.session.commit()

            for url in (
                f"/transfers/{xfer.id}/full-edit",
                f"/transactions/{shadow.id}/full-edit",
            ):
                response = auth_client.get(url)
                assert response.status_code == 200, f"{url} did not render"
                body = response.get_data(as_text=True)
                assert 'name="settled_on"' in body, (
                    f"{url} offered no settle day on a settled transfer"
                )
                assert f'value="{settled_day.isoformat()}"' in body, (
                    f"{url} did not pre-fill the stored settle day"
                )
                # The browser half of ruling R-EJ, and it must be the USER's
                # today: a ``date.today()`` here would refuse, in the evening
                # Eastern, a day the seam accepts.  ``settled_day`` is two days
                # back, so neither assertion can satisfy the other.
                assert f'max="{display_today().isoformat()}"' in body, (
                    f"{url} did not bound the settle day at the user's today"
                )
                assert not field_is_disabled(body, "settled_on"), (
                    f"{url} locked the settle day on a finalised transfer, so "
                    "the correction R-ED exists for is unreachable"
                )

    def test_an_undated_settled_transfer_still_offers_the_repair_box(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A settled transfer carrying NO day still gets a correction input.

        The shape the template's condition is written for, and the reason it
        keys on the STATUS rather than on ``xfer.settled_on``: an undated
        settled transfer is exactly what makes ``posting_service._entry_date``
        raise ``UndatedSettleError`` -- a 500 on the grid -- so it is the row
        that most needs a way to state the day its money really moved.  Keying
        the condition on the day instead would hide the box from precisely that
        row, and a neutral review found nothing grading the difference:
        ``{% if xfer.settled_on %}`` passed the whole suite.

        The row is built by clearing the column directly, which is legal only in
        a fixture -- ``status_seam`` is the single writer and refuses to leave a
        settled row undated -- because production's instances of this shape
        predate the column (finding **N-181**).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, account_id=savings.id)
                .one()
            )
            # The legacy shape, reproduced the only way it can be: straight at
            # the columns, behind the seam's back.  It is a row that pre-dates
            # the settlement record entirely -- settled status, nothing recorded
            # -- so all three columns are cleared together.  That is narrower
            # than what the schema forbids: only the DAY needs a figure beside
            # it (``ck_transactions_settle_day_needs_a_record``), and a record with
            # no day is the legal RETAINED state.
            for row in (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            ):
                record_settle_day(row, None)
                row.settled_amount = None
                row.settled_basis_id = None
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).settled_on is None

            for url in (
                f"/transfers/{xfer.id}/full-edit",
                f"/transactions/{shadow.id}/full-edit",
            ):
                response = auth_client.get(url)
                assert response.status_code == 200, (
                    f"{url} 500'd on an undated settled transfer -- the row "
                    "that most needs the repair box"
                )
                body = response.get_data(as_text=True)
                assert 'name="settled_on"' in body, (
                    f"{url} hid the correction box from an UNDATED settled "
                    "transfer, leaving it no way to state the real day"
                )
                assert 'value=""' in body, (
                    f"{url} pre-filled a day onto a row that carries none"
                )

    def test_the_shadow_PATCH_door_corrects_the_pair_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCHing a SHADOW's own route corrects both shadows and the ledger.

        There are TWO routes onto a transfer's settle day, and this is the one
        no test reached: ``PATCH /transactions/<shadow_id>`` lands in
        ``_shadow_mutations._apply_shadow_update``, which re-expresses the
        submitted fields as ``transfer_service.update_transfer`` kwargs.  A
        neutral review DELETED that mapping block outright and the whole 7,803-
        test suite stayed green -- new, deliberate, defensive code with nothing
        grading either claim its own comment makes.

        No UI submits a day here (a shadow's popover is the TRANSFER form), so
        the reachable callers are a crafted request, a replayed POST or a future
        surface.  That is precisely why it is worth a test: the block exists so
        such a request cannot LOOK like it took while doing nothing, and so the
        two doors onto one rule answer identically.
        """
        with app.app_context():
            original = display_today() - timedelta(days=8)
            corrected = display_today() - timedelta(days=5)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, original,
            )
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, account_id=xfer.from_account_id)
                .one()
            )

            response = auth_client.patch(
                f"/transactions/{shadow.id}",
                data={"settled_on": corrected.isoformat()},
            )
            assert response.status_code == 200, response.get_data(as_text=True)[:300]

            db.session.expire_all()
            assert self._shadow_days(xfer.id) == {corrected}, (
                "the shadow PATCH door dropped the settle day: the request "
                "answered 200 and changed nothing"
            )
            assert self._net_by_day(xfer.id) == {corrected: Decimal("200.00")}, (
                "the shadow PATCH door moved the day without the ledger: "
                f"{self._net_by_day(xfer.id)}"
            )

    def test_the_shadow_PATCH_door_drops_a_day_beside_a_revert(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A revert through the SHADOW route drops the day rather than 400ing.

        Ruling **R-EG** through the second door.  Without the drop this request
        reaches ``apply_settle_day_correction`` with a day for a Projected
        transfer and raises.

        ``_apply_shadow_update`` grades the submitted day against the PARENT
        transfer's status rather than the shadow's -- ``transfer_service`` hands
        it to a function that grades against the parent, so reading the shadow's
        would be a second spelling of one question.  **This test does not prove
        that half and cannot**: Transfer Invariant 3 keeps the two statuses
        equal, so swapping one read for the other is undetectable from outside.
        A neutral review found the claim being made where nothing graded it; what
        is graded here is the drop.
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=6)
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, settled_day,
            )
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, account_id=xfer.from_account_id)
                .one()
            )

            response = auth_client.patch(
                f"/transactions/{shadow.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_on": settled_day.isoformat(),
                },
            )
            assert response.status_code == 200, (
                "the shadow route refused a revert because the payload carried "
                f"the row's own settle day: {response.get_data(as_text=True)[:300]}"
            )

            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert self._shadow_days(xfer.id) == {None}
            assert self._net_by_day(xfer.id) == {}, (
                "the revert through the shadow door left the effect posted"
            )


# ── Helpers for Negative-Path Tests ───────────────────────────────


def _create_second_user_transfer(second_user_data):
    """Create a transfer for the second_user fixture (IDOR testing).

    Creates a savings account, pay periods, and a transfer instance
    for the second user.

    Args:
        second_user_data: Dict from the second_user conftest fixture.

    Returns:
        Transfer: the created transfer.
    """
    from datetime import date as _date  # pylint: disable=import-outside-toplevel

    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=second_user_data["user"].id,
            account_type_id=savings_type.id,
            name="Other Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(savings)
    db.session.flush()

    periods = pay_period_write.record_paydays(
        user_id=second_user_data["user"].id,
        first_payday=_date(2026, 1, 2),
        num_periods=3,
        cadence_days=14,
    )
    db.session.flush()

    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = Transfer(
        user_id=second_user_data["user"].id,
        from_account_id=second_user_data["account"].id,
        to_account_id=savings.id,
        pay_period_id=periods[0].id,
        scenario_id=second_user_data["scenario"].id,
        status_id=projected.id,
        name="Other Transfer",
        amount=Decimal("100.00"),
    )
    db.session.add(xfer)
    db.session.commit()
    return xfer


# ── Negative Paths ────────────────────────────────────────────────


class TestTransferNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR, idempotent ops, validation."""

    def test_update_nonexistent_transfer_instance(self, app, auth_client, seed_user):
        """PATCH /transfers/instance/999999 for a nonexistent transfer returns 404."""
        with app.app_context():
            resp = auth_client.patch(
                "/transfers/instance/999999",
                data={"amount": "100.00"},
            )

            assert resp.status_code == 404

    def test_mark_done_already_done_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/instance/<id>/mark-done on an already-done transfer is idempotent."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            # Set to done first, through the service door so all three rows
            # move together and each shadow records what moved (plan step
            # X-au-c3): a bare status assign on the parent leaves the pair's
            # legs Projected, and the seam then refuses to enter the settled
            # band with no settlement record.
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=done_status.id,
            )
            db.session.commit()

            # Mark done again.
            resp = auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            # Route does not guard against double mark-done; it sets
            # the same status again. This is idempotent behavior.
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"

    def test_cancel_already_cancelled_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/instance/<id>/cancel on an already-cancelled transfer is idempotent."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            # Cancel first.
            cancelled_status = db.session.query(Status).filter_by(name="Cancelled").one()
            xfer.status_id = cancelled_status.id
            db.session.commit()

            # Cancel again.
            resp = auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            # Route does not guard against double cancel; it sets
            # the same status again. This is idempotent behavior.
            assert resp.status_code == 200

            db.session.refresh(xfer)
            assert xfer.status.name == "Cancelled"

    def test_quick_edit_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """GET /transfers/quick-edit/<id> for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)

            resp = auth_client.get(f"/transfers/quick-edit/{other_xfer.id}")

            assert resp.status_code == 404
            # No transfer data should leak.
            assert b"Other Transfer" not in resp.data
            assert b"100.00" not in resp.data

    def test_full_edit_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """GET /transfers/<id>/full-edit for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)

            resp = auth_client.get(f"/transfers/{other_xfer.id}/full-edit")

            assert resp.status_code == 404

    def test_mark_done_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /transfers/instance/<id>/mark-done for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)
            original_status_id = other_xfer.status_id

            resp = auth_client.post(
                f"/transfers/instance/{other_xfer.id}/mark-done"
            )

            assert resp.status_code == 404

            # Verify DB state unchanged.
            db.session.expire_all()
            refreshed = db.session.get(Transfer, other_xfer.id)
            assert refreshed.status_id == original_status_id

    def test_cancel_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /transfers/instance/<id>/cancel for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)
            original_status_id = other_xfer.status_id

            resp = auth_client.post(
                f"/transfers/instance/{other_xfer.id}/cancel"
            )

            assert resp.status_code == 404

            # Verify DB state unchanged.
            db.session.expire_all()
            refreshed = db.session.get(Transfer, other_xfer.id)
            assert refreshed.status_id == original_status_id

    def test_create_template_with_missing_accounts(self, app, auth_client, seed_user):
        """POST /transfers with empty from/to accounts fails schema validation."""
        with app.app_context():
            resp = auth_client.post("/transfers", data={
                "name": "Bad Transfer",
                "default_amount": "100.00",
                "from_account_id": "",
                "to_account_id": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no template was created.
            count = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_ad_hoc_with_zero_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with amount=0.00 fails validation (must be > 0)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            resp = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "0.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            # TransferCreateSchema requires amount > 0 (min_inclusive=False).
            assert resp.status_code == 422
            body = resp.get_json()
            assert "errors" in body

            # Verify no transfer was created.
            count = db.session.query(Transfer).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_ad_hoc_with_negative_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with negative amount fails schema validation."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            resp = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "-100.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            assert resp.status_code == 422
            body = resp.get_json()
            assert "errors" in body

            # Verify no transfer was created.
            count = db.session.query(Transfer).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0


# ── Shadow Context Response Tests (H1 fix) ────────────────────────


def _get_expense_shadow(xfer):
    """Return the expense-side shadow transaction for a transfer."""
    from app.models.ref import TransactionType  # pylint: disable=import-outside-toplevel
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    return (
        db.session.query(Transaction)
        .filter_by(transfer_id=xfer.id, transaction_type_id=expense_type.id)
        .one()
    )


class TestShadowContextResponse:
    """Verify that transfer route handlers render _transaction_cell.html
    (not _transfer_cell.html) when the request includes source_txn_id,
    indicating the form was opened from a shadow transaction cell in the grid.

    Fixes H1, L2, L3 from transfer_rework_verification.md.
    """

    def test_update_from_shadow_renders_transaction_cell(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH with source_txn_id renders _transaction_cell.html content.

        When the transfer full edit popover is opened from a shadow
        transaction cell in the grid, the response must contain the
        transaction cell template (with ``txn-cell-`` IDs and transaction
        HTMX routes) so the cell remains interactive after the update.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            shadow = _get_expense_shadow(xfer)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "300.00", "source_txn_id": str(shadow.id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Must render _transaction_cell.html (has transaction routes).
            assert "transactions.get_quick_edit" in html or f"txn_id={shadow.id}" in html or "txn-cell" in html
            # Must NOT render _transfer_cell.html (has transfer routes).
            assert "xfer-cell-" not in html
            assert "transfers/quick-edit" not in html.replace("transfers/instance", "")

            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            # Verify the transfer amount was actually updated.
            db.session.refresh(xfer)
            assert xfer.amount == Decimal("300.00")

            # Verify the shadow FOLLOWS the parent.  It holds no copy since
            # plan step X-au-g-2c-2; Transfer Invariant 3 is what it READS.
            db.session.refresh(shadow)
            assert shadow_amount(shadow) == Decimal("300.00")

    def test_mark_done_from_shadow_renders_transaction_cell_with_grid_refresh(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST mark-done with source_txn_id renders _transaction_cell.html
        and triggers gridRefresh (not balanceChanged).

        Status changes affect subtotal rows and cell visibility, so the
        transfer route must match the transaction route guard pattern of
        triggering gridRefresh when called from a shadow cell context.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            shadow = _get_expense_shadow(xfer)

            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done",
                data={"source_txn_id": str(shadow.id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Transaction cell, not transfer cell.
            assert "xfer-cell-" not in html

            # Must trigger gridRefresh for status changes.
            assert resp.headers.get("HX-Trigger") == "gridRefresh"

            # Verify the transfer and both shadows are done.
            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert all(s.status.name == "Paid" for s in shadows)

    def test_cancel_from_shadow_renders_transaction_cell_with_grid_refresh(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST cancel with source_txn_id renders _transaction_cell.html
        and triggers gridRefresh.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            shadow = _get_expense_shadow(xfer)

            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/cancel",
                data={"source_txn_id": str(shadow.id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            assert "xfer-cell-" not in html
            assert resp.headers.get("HX-Trigger") == "gridRefresh"

            db.session.refresh(xfer)
            assert xfer.status.name == "Cancelled"

    def test_update_without_source_txn_id_renders_transfer_cell(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH without source_txn_id renders _transfer_cell.html (regression).

        When the transfer management page (not the grid) submits an
        update, there is no source_txn_id.  The response must render
        the transfer cell template with ``xfer-cell-`` IDs as before.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "350.00"},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Must render _transfer_cell.html (management page context).
            assert f"xfer-cell-{xfer.id}" in html or "xfer-cell-" in html

            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.amount == Decimal("350.00")

    def test_invalid_source_txn_id_falls_back_gracefully(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH with nonexistent source_txn_id falls back to transfer cell.

        If source_txn_id is invalid (e.g., tampered or stale), the
        handler must not crash.  It falls back to the transfer cell
        template as a safe default.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "400.00", "source_txn_id": "999999"},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Falls back to transfer cell (safe default).
            assert "xfer-cell-" in html

            # Data still updated correctly.
            db.session.refresh(xfer)
            assert xfer.amount == Decimal("400.00")

    def test_mismatched_source_txn_id_falls_back_gracefully(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH with source_txn_id from a different transfer falls back.

        If source_txn_id points to a shadow of a DIFFERENT transfer,
        the handler must not render the wrong transaction cell.  It
        falls back to the transfer cell template.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            # Distinct amounts/names so the F-050 partial unique
            # index ``uq_transfers_adhoc_dedupe`` does not collapse
            # the two ad-hoc transfers into one (they share user,
            # accounts, period, and scenario).
            xfer_a = _create_transfer(
                seed_user, seed_periods_today, savings,
                amount=Decimal("200.00"), name="Transfer A",
            )
            xfer_b = _create_transfer(
                seed_user, seed_periods_today, savings,
                amount=Decimal("250.00"), name="Transfer B",
            )

            # Get a shadow from transfer B.
            shadow_b = _get_expense_shadow(xfer_b)

            # Send it with transfer A's update.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_a.id}",
                data={"amount": "450.00", "source_txn_id": str(shadow_b.id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Falls back to transfer cell (mismatch detected).
            assert "xfer-cell-" in html

            # Transfer A still updated correctly.
            db.session.refresh(xfer_a)
            assert xfer_a.amount == Decimal("450.00")


# ── Unarchive Service Integration Tests (M1) ─────────────────────


class TestUnarchiveUsesService:
    """Verify that unarchive_transfer_template delegates to
    transfer_service.restore_transfer instead of directly manipulating
    ORM objects, ensuring all transfer mutations flow through the
    service layer.
    """

    def test_unarchive_restores_via_service_with_invariant_correction(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Verify that the unarchive route uses the transfer service to
        restore soft-deleted transfers, including the service's invariant
        correction logic.  An intentionally drifted shadow PERIOD should
        be corrected on unarchive, proving the service was called.

        **The drift was the shadow's AMOUNT until plan step X-au-g-2c-2.**  A
        shadow stores no figure now -- it declares ``PARENT_TRANSFER`` and reads
        its parent -- so ``ck_transactions_amount_ownership`` refuses the write
        the simulation made, and ``restore_transfer``'s amount corrector is
        deleted along with the drift it repaired.  The PERIOD corrector
        (Transfer Invariant 5) is still a hand-written repair and still the
        proof this route reaches the service rather than flipping ``is_deleted``
        itself; the amount half is graded as unconstructible in
        ``test_transfer_service.TestRestoreTransfer``.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            xfer_id = xfer.id

            # Soft-delete the transfer and shadows via the service.
            transfer_service.delete_transfer(xfer_id, seed_user["user"].id, soft=True)
            db.session.commit()

            # Drift one shadow's period while soft-deleted.
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .first()
            )
            drifted_period_id = next(
                p.id for p in seed_periods_today if p.id != xfer.pay_period_id
            )
            shadow.pay_period_id = drifted_period_id
            db.session.commit()

            # Deactivate the template to match the route's expectations.
            template.is_active = False
            db.session.commit()

            # Unarchive via the route.
            response = auth_client.post(
                f"/transfers/{template.id}/unarchive",
                follow_redirects=True,
            )

            assert response.status_code == 200

            # Transfer restored.
            db.session.refresh(xfer)
            assert xfer.is_deleted is False

            # Both shadows restored AND the drifted period corrected.
            # This proves the service's invariant correction ran.
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.is_deleted is False
                assert s.pay_period_id == xfer.pay_period_id
                assert shadow_amount(s) == Decimal("200.00")


# ── One-Time Transfer Tests ────────────────────────────────────────────


class TestOneTimeTransfer:
    """Creating a transfer that does NOT repeat, via the template form.

    Its shape is ``recurrence_rule_id IS NULL`` -- the same one a
    non-repeating transaction template has always used -- since plan step
    R2e-3 retired the ``Once`` PATTERN that was the second spelling.  It is
    also the create form's DEFAULT selection, so this is the ordinary path,
    not an edge.  Unlike a rule-less TRANSACTION template, which generates
    nothing, this materialises exactly one Transfer with its two shadow
    transactions in the chosen pay period.

    **Every case here 500'd before R2e-3** (defect **D13**): the route
    dereferenced ``rule.id`` with no null branch, on a comment claiming the
    schema made the recurrence field required.  It does not -- the field is
    ``allow_none`` -- so both the empty and the absent spelling raised
    ``AttributeError``.  ``test_absent_cadence_is_the_same_path`` covers the
    second spelling, which no form emits but a client may.

    The field is ``recurrence_unit`` since plan step R7b-2, which renamed what
    "does not repeat" is spelled as without changing what it MEANS: an empty
    unit, kept as a present ``None`` by ``_normalize_empty_inputs``.
    """

    def test_non_repeating_creates_one_transfer_and_two_shadows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers with an EMPTY pattern creates a rule-less template
        AND a single Transfer with exactly two shadow transactions.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers", data={
                "name": "Once Payment",
                "default_amount": "500.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "recurrence_unit": "",
                "start_period_id": str(seed_periods_today[1].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"created" in response.data.lower()

            # Template was created with NO recurrence rule.
            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(
                    user_id=seed_user["user"].id,
                    name="Once Payment",
                )
                .one()
            )
            assert tmpl.recurrence_rule is None
            assert tmpl.recurrence_rule is None

            # Transfer was created via the service.
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id)
                .one()
            )
            assert xfer.amount == Decimal("500.00")
            assert xfer.pay_period_id == seed_periods_today[1].id
            # The due date is the period's own start.  A ``Once`` rule used to
            # supply it through ``compute_due_date``, which returned exactly
            # this for a day-less rule -- verified against both live ``Once``
            # transfers on production.
            assert xfer.due_date == seed_periods_today[1].start_date

            # Exactly two shadow transactions exist.
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )
            assert len(shadows) == 2

            types = {s.transaction_type.name for s in shadows}
            assert types == {"Expense", "Income"}

    def test_non_repeating_shadow_accounts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Non-repeating transfer shadows are linked to the correct accounts:
        expense shadow on from_account, income shadow on to_account.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            checking_id = seed_user["account"].id

            auth_client.post("/transfers", data={
                "name": "Account Check",
                "default_amount": "300.00",
                "from_account_id": str(checking_id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "start_period_id": str(seed_periods_today[0].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(name="Account Check")
                .one()
            )
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id)
                .one()
            )
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )

            expense_shadow = [
                s for s in shadows if s.transaction_type.name == "Expense"
            ][0]
            income_shadow = [
                s for s in shadows if s.transaction_type.name == "Income"
            ][0]

            # Expense drains the from_account (checking).
            assert expense_shadow.account_id == checking_id
            # Income fills the to_account (savings).
            assert income_shadow.account_id == savings.id

    def test_non_repeating_balance_impact(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Non-repeating transfer shadows affect balance calculations.

        The checking balance should decrease and savings balance should
        increase by the transfer amount.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            # Asserted rather than assigned (ruling R-EH deleted the column).
            override_anchor(
                db.session, savings, seed_periods_today[0], Decimal("0.00"),
            )
            db.session.commit()

            auth_client.post("/transfers", data={
                "name": "Balance Test",
                "default_amount": "250.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "start_period_id": str(seed_periods_today[1].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            # Get shadow transactions for checking account.
            checking_shadows = (
                db.session.query(Transaction)
                .filter(
                    Transaction.account_id == seed_user["account"].id,
                    Transaction.transfer_id.isnot(None),
                    Transaction.is_deleted.is_(False),
                )
                .all()
            )
            assert checking_shadows
            # ``as_of`` is pinned inside period 0: the transfer lands in period
            # 1, which is in the PAST relative to this fixture's today, and
            # ruling R-G clamps a still-Projected row forward past a reader's
            # own now rather than letting it sit in a period that has gone by.
            ctx = BalanceContext.build(
                seed_user["user"].id,
                as_of=seed_periods_today[0].start_date,
            )
            checking_balances = balance_at.cash_balance_map(
                seed_user["account"], ctx,
            )
            # Checking decreased by 250 in period 2.
            assert checking_balances[seed_periods_today[1].id] == Decimal("750.00")

            # Get shadow transactions for savings account.
            savings_shadows = (
                db.session.query(Transaction)
                .filter(
                    Transaction.account_id == savings.id,
                    Transaction.transfer_id.isnot(None),
                    Transaction.is_deleted.is_(False),
                )
                .all()
            )
            assert savings_shadows
            savings_balances = balance_at.cash_balance_map(
                savings, ctx,
            )
            # Savings increased by 250 in period 2.
            assert savings_balances[seed_periods_today[1].id] == Decimal("250.00")

    def test_one_time_transfer_idor_period(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """POST /transfers with another user's period is rejected.

        The cross-user start_period is caught by the F-24 builder's universal
        ownership probe (deep-quality-hunt #21), which plan step R2e-3 moved
        AHEAD of that helper's no-pattern early return.  It had sat below it,
        so a submission naming no pattern skipped the probe entirely -- which
        was harmless while no-pattern meant "generate nothing", and is not
        once no-pattern is what materialises a Transfer into exactly the
        submitted period.  ``_materialize_one_time_transfer`` re-checks as
        defence in depth ("Invalid pay period for one-time transfer."); this
        asserts the FIRST guard fires, before any row is written.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers", data={
                "name": "IDOR Attempt",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_unit": "",
                # Use second user's period.
                "start_period_id": str(seed_second_periods[0].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid start period" in response.data

            # Neither a template nor a transfer was persisted.
            assert (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0
            assert (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0

    def test_absent_cadence_is_the_same_path(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A POST that OMITS ``recurrence_unit`` behaves identically.

        The form always submits the key (empty for "Does not repeat"), but a
        client need not.  Both spellings reached the same unguarded
        ``rule.id`` before plan step R2e-3 and both 500'd; both must now
        produce the same rule-less template and its single Transfer.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers", data={
                "name": "No Pattern Key",
                "default_amount": "425.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "start_period_id": str(seed_periods_today[1].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="No Pattern Key")
                .one()
            )
            assert tmpl.recurrence_rule is None
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id)
                .one()
            )
            assert xfer.amount == Decimal("425.00")
            assert xfer.pay_period_id == seed_periods_today[1].id

    def test_non_repeating_without_a_period_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A non-repeating transfer naming no pay period writes nothing.

        It has nowhere to land, and reporting success would state that money
        moved when no Transfer and no shadow pair exist.  Before plan step
        R2e-3 the ``Once`` path took exactly that branch silently: the
        materialization required BOTH the pattern and a period, so without one
        it created the template, no transfer, and flashed "created."
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers", data={
                "name": "Nowhere To Land",
                "default_amount": "75.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_unit": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"has to land in one pay period" in response.data
            assert (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0
            assert (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0

    def _create_non_repeating(
        self, auth_client, seed_user, savings, period, *, name, amount="500.00",
    ):
        """POST the create form's default (non-repeating) selection."""
        auth_client.post("/transfers", data={
            "name": name,
            "default_amount": amount,
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(savings.id),
            "recurrence_unit": "",
            "start_period_id": str(period.id),
            "category_id": str(seed_user["categories"]["Rent"].id),
        }, follow_redirects=True)
        return (
            db.session.query(TransferTemplate)
            .filter_by(user_id=seed_user["user"].id, name=name)
            .one()
        )

    def test_changing_accounts_on_a_non_repeating_transfer_moves_the_pair(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An account change reaches the Transfer and BOTH of its shadows.

        **RE-RULED at plan step R10-b** (developer decision, 2026-08-20).  This
        case asserted a REFUSAL -- "cannot be moved between accounts" -- and
        that refusal existed for one reason: ``transfer_service.update_transfer``
        could not move a transfer between accounts, so the propagation door
        carried amount, name and category and nothing else.  It was a limit of
        the door, not a rule about transfers: a RECURRING template with the
        identical edit had it applied, by a regeneration that destroyed and
        rebuilt every generated row to do it.  The door moves a pair now
        (``transfer_service._endpoints``), so the edit applies here too and one
        edit stops meaning two different things.

        Asserts all three rows, because moving the parent alone would leave the
        shadows on the accounts the money no longer moves between -- and each
        shadow's display NAME is derived from the endpoints, so a moved leg
        keeping its old name is a row that contradicts itself.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = Account(
                user_id=seed_user["user"].id, name="Other Savings",
                account_type_id=savings.account_type_id, is_active=True,
            )
            db.session.add(other)
            db.session.commit()
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Fixed Accounts",
            )

            resp = auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Fixed Accounts",
                "default_amount": "500.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(other.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            tmpl = db.session.get(TransferTemplate, tmpl.id)
            assert tmpl.to_account_id == other.id
            xfer = db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id).one()
            assert xfer.to_account_id == other.id
            assert xfer.from_account_id == seed_user["account"].id
            shadows = {
                shadow.transaction_type_id: shadow
                for shadow in db.session.query(Transaction).filter_by(
                    transfer_id=xfer.id, is_deleted=False,
                )
            }
            expense = shadows[ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)]
            income = shadows[ref_cache.txn_type_id(TxnTypeEnum.INCOME)]
            assert expense.account_id == seed_user["account"].id
            assert income.account_id == other.id
            assert expense.name == f"Transfer to {other.name}"
            assert income.name == f"Transfer from {seed_user['account'].name}"

    def test_a_non_repeating_transfer_holding_a_record_is_retained_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The rule-less path asks the same question the recurring one does.

        **Found by an adversarial review of plan step R10-b.**  Removing the
        "cannot be moved between accounts" refusal made the two account columns
        propagate here -- and this door applied the definition unconditionally,
        so a non-repeating transfer carrying a settlement record retained
        through a revert had its pair moved in SILENCE, while the identical edit
        on a RECURRING template was retained and reported.  That is the very
        inconsistency the step removed, re-created in the opposite direction.

        The record is the one ``status_seam`` keeps when the owner reverts in
        order to edit: a figure read off the OLD destination's statement, which
        re-pointing the pair would re-file against an account nobody asserted it
        on.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = Account(
                user_id=seed_user["user"].id, name="Other Savings",
                account_type_id=savings.account_type_id, is_active=True,
            )
            db.session.add(other)
            db.session.commit()
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Has A Record",
            )
            xfer = db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id).one()
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id, submitted=Decimal("412.90"),
                settle_day=an_entered_day(display_today()),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            tmpl_id, xfer_id, other_id = tmpl.id, xfer.id, other.id
            savings_id = savings.id

            resp = auth_client.post(f"/transfers/{tmpl_id}", data={
                "name": "Has A Record",
                "default_amount": "500.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(other_id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"kept the value it already had" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, tmpl_id,
            ).to_account_id == other_id, "the definition itself still moves"
            held = db.session.get(Transfer, xfer_id)
            assert held.to_account_id == savings_id, (
                "the row carrying a recorded figure was re-filed anyway"
            )
            legs = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id, is_deleted=False,
            ).all()
            assert [leg.account_id for leg in legs].count(savings_id) == 1
            assert all(
                leg.settled_amount == Decimal("412.90") for leg in legs
            )

    def test_changing_accounts_is_allowed_with_no_live_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A rule-less template with NO live Transfer re-points cleanly.

        The propagation loop has nothing to iterate here -- a template whose
        recurrence was CLEARED keeps no upcoming Transfer -- so the edit must
        land on the template alone and raise nothing.  It is the empty half of
        the case above, and it is the half that would break if the propagation
        ever assumed a row was there to write.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = Account(
                user_id=seed_user["user"].id, name="Other Savings",
                account_type_id=savings.account_type_id, is_active=True,
            )
            db.session.add(other)
            db.session.commit()
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Repointable",
            )
            # Remove the Transfer, leaving the rule-less template with none.
            for xfer in db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id,
            ).all():
                transfer_service.delete_transfer(
                    xfer.id, seed_user["user"].id, soft=False,
                )
            db.session.commit()

            resp = auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Repointable",
                "default_amount": "500.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(other.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, tmpl.id,
            ).to_account_id == other.id
            # The propagation had nothing to write and said nothing about it:
            # no retained notice, and no Transfer came back.
            assert b"kept the value it already had" not in resp.data
            assert db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id,
            ).count() == 0

    def test_a_settled_transfer_does_not_follow_the_definition(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Propagation never rewrites immutable history.

        A Paid transfer carries posting-ledger entries and real money that
        already moved; the same rule
        ``_recurrence_common.classify_maintain_work`` applies to every
        recurring template's regeneration applies here.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Already Paid",
            )
            xfer = db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id).one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            xfer_id = xfer.id

            auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Already Paid",
                "default_amount": "900.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, tmpl.id,
            ).default_amount == Decimal("900.00")
            assert db.session.get(Transfer, xfer_id).amount == Decimal("500.00")

    def test_a_hand_edited_transfer_does_not_follow_the_definition(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An override is a deliberate per-instance choice and is preserved.

        The same partition the regeneration sweep uses: an overridden row is
        the user's, not the template's.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            future = [
                p for p in seed_periods_today if p.start_date > display_today()
            ]
            tmpl = self._create_non_repeating(
                auth_client, seed_user, savings, future[0], name="Hand Edited",
            )
            xfer = db.session.query(Transfer).filter_by(
                transfer_template_id=tmpl.id).one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount=Decimal("123.45"), is_override=True,
            )
            db.session.commit()
            xfer_id = xfer.id

            auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Hand Edited",
                "default_amount": "900.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            db.session.expire_all()
            assert db.session.get(Transfer, xfer_id).amount == Decimal("123.45")

    def test_renaming_a_non_repeating_transfer_keeps_its_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Defect **D16**: a rename must not destroy the single Transfer.

        A ``Once``-ruled transfer template DID reach the regeneration sweep on
        any edit, because it named a rule: the sweep hard-deleted the
        projected row and the pattern's own suppression guard then generated
        nothing back.  Measured at HEAD before plan step R2e-3, with the
        transfer in a FUTURE period so it fell inside the sweep window:
        1 transfer + 2 shadows -> 0 + 0.  A rule-less template is skipped by
        ``regenerate_or_conflict_chooser``'s "neither has nor had a rule"
        gate, which is what closes it.

        The period must be in the future: ``query_rows_from_effective_date``
        bounds the sweep at ``PayPeriod.end_date >= effective_from``, so a
        past-period transfer survives a rename for an unrelated reason and
        would pass this test against the broken code.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            future = [
                p for p in seed_periods_today
                if p.start_date > display_today()
            ]
            assert future, "fixture must materialise a future pay period"

            auth_client.post("/transfers", data={
                "name": "Rename Me",
                "default_amount": "500.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "start_period_id": str(future[0].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="Rename Me")
                .one()
            )
            before = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id).one()
            )
            assert db.session.query(Transaction).filter_by(
                transfer_id=before.id, is_deleted=False,
            ).count() == 2

            auth_client.post(f"/transfers/{tmpl.id}", data={
                "name": "Renamed",
                "default_amount": "650.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_unit": "",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "version_id": str(tmpl.version_id),
            }, follow_redirects=True)

            db.session.expire_all()
            after = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id).all()
            )
            assert len(after) == 1, "the rename destroyed the transfer (D16)"
            assert after[0].id == before.id
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=after[0].id, is_deleted=False,
            ).all()
            assert len(shadows) == 2

            # SURVIVING is only half of it: the edit must also REACH the row.
            # Asserting only that the id still exists passes against a route
            # that silently ignores the edit -- measured before
            # ``propagate_to_non_repeating_transfers``, this same edit left the
            # template at $650.00 while the Transfer and both shadows stayed at
            # $500.00, under a plain "updated." flash.
            assert after[0].name == "Renamed"
            assert after[0].amount == Decimal("650.00")
            assert {shadow_amount(s) for s in shadows} == {Decimal("650.00")}

    def test_recurring_transfer_idor_period(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """POST /transfers rejects a foreign start_period on a RECURRING pattern.

        deep-quality-hunt #21/#24: before the universal probe, the
        start_period ownership check ran ONLY for EVERY_N_PERIODS, so a
        recurring pattern (here "Every Period") persisted a foreign
        ``start_period_id`` unchecked -- and ``recurrence_engine`` then
        read that victim period's ``start_date`` as the generation
        boundary.

        **The probe MOVED at plan step R7b-4 and this test is now its
        primary coverage.**  It sat in the kind-agnostic F-24 builder
        (``recurrence_spec_from_form``) because that ``<select>`` was
        also the recurrence's "First paycheck"; the recurrence takes a DATE
        now, so the field has one job -- which period a NON-repeating transfer
        lands in -- and ``create_transfer_template`` owner-checks it before
        anything is written.  The transaction-template twin of this case is
        gone with the surface: that schema no longer declares the field at all
        (``test_templates.py::test_create_recurring_template_ignores_a_foreign_start_period``).

        This POST names a RECURRING cadence, which is the shape that matters:
        the recurrence has no use for the period, so the check must still run
        rather than being skipped as irrelevant.  A regression that only
        checked the non-repeating branch would reopen the IDOR here.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            response = auth_client.post("/transfers", data={
                "name": "Recurring IDOR Attempt",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                **cadence_payload(),
                # Second user's period on a recurring (non-EVERY_N) pattern.
                "start_period_id": str(seed_second_periods[0].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid start period" in response.data

            # Neither a template nor a transfer was persisted.
            assert (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0
            assert (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            ) == 0


# ── Hard Delete Tests (5A.5-3) ─────────────────────────────────────


class TestTransferTemplateHardDelete:
    """Tests for POST /transfers/<id>/hard-delete (permanent deletion).

    These tests verify transfer invariant compliance: shadow transactions
    must never be orphaned, and all deletions must flow through the
    transfer service.
    """

    def test_hard_delete_transfer_template_no_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-17: Template with only Projected transfers is permanently deleted."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            template_id = template.id
            xfer_id = xfer.id

            # Verify shadows exist before deletion.
            shadow_count_before = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).count()
            assert shadow_count_before == 2

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template is gone.
            assert db.session.get(TransferTemplate, template_id) is None

            # Transfer is gone.
            assert db.session.get(Transfer, xfer_id) is None

            # Shadow transactions are gone.
            shadow_count_after = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).count()
            assert shadow_count_after == 0

    def test_hard_delete_transfer_template_no_transfers(
        self, app, auth_client, seed_user,
    ):
        """Template with zero transfers ever generated is permanently deleted."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            template_id = template.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(TransferTemplate, template_id) is None

    def test_hard_delete_transfer_template_with_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-18: Template with Paid transfer is blocked and archived instead."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            # Create two transfers: one Projected, one Paid.
            xfer_projected = _create_transfer(
                seed_user, seed_periods_today, savings, template,
            )

            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            xfer_paid = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[1].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("200.00"),
                    status_id=paid_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Monthly Savings",
                ),
            )
            db.session.commit()

            resp = auth_client.post(
                f"/transfers/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data
            assert b"archived instead" in resp.data

            # Template still exists but is archived.
            db.session.refresh(template)
            assert template.is_active is False

            # Paid transfer and its shadows are untouched.
            db.session.refresh(xfer_paid)
            assert xfer_paid.is_deleted is False
            paid_shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_paid.id,
            ).all()
            assert len(paid_shadows) == 2
            for shadow in paid_shadows:
                assert shadow.is_deleted is False

            # Projected transfer is soft-deleted.
            db.session.refresh(xfer_projected)
            assert xfer_projected.is_deleted is True

    def test_hard_delete_transfer_template_with_history_already_archived(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Already-archived template with Paid history stays archived without re-archiving."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("200.00"),
                    status_id=paid_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Monthly Savings",
                ),
            )

            # Pre-archive.
            template.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/transfers/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data

            db.session.refresh(template)
            assert template.is_active is False

    def test_hard_delete_transfer_template_received_blocked(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C21-6: A transfer template with a RECEIVED transfer is archived, not deleted.

        Mirror of the transaction-template CRIT-05 fix proof.  The
        pre-fix predicate enumerated ``[DONE, SETTLED]`` (SETTLED being the
        terminal archive plan step **balance:X-am** has since deleted) and silently
        omitted ``RECEIVED``; ``RECEIVED`` carries ``is_settled=True``
        in ``ref_seeds.py`` so the post-fix
        ``transfer_template_has_paid_history`` -- now filtering on
        ``Status.is_settled`` -- correctly returns True for a
        RECEIVED transfer and the route archives instead of
        physically destroying the transfer plus its shadow pair.
        Verifies the predicate fix end-to-end at the route layer.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            received_status = db.session.query(Status).filter_by(name="Received").one()
            xfer_received = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=received_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Monthly Savings",
                ),
            )
            db.session.commit()

            template_id = template.id
            xfer_id = xfer_received.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived instead" in resp.data
            # The archive-fallback flash contains "cannot be permanently
            # deleted" so the broad substring check is unsafe; assert
            # the literal success-flash text never fired instead.
            assert (
                b"Recurring transfer 'Monthly Savings' permanently deleted"
                not in resp.data
            )

            # Template archived, not deleted.
            db.session.refresh(template)
            assert template.is_active is False
            assert db.session.get(TransferTemplate, template_id) is not None

            # RECEIVED transfer preserved with original amount.  Hand-
            # verified: $250.00 stays exactly $250.00 (Decimal from
            # string per coding standards).
            surviving = db.session.get(Transfer, xfer_id)
            assert surviving is not None
            assert surviving.status_id == received_status.id
            assert surviving.is_deleted is False
            assert surviving.amount == Decimal("250.00")

            # Both shadows survive untouched (transfer invariant 1: a
            # transfer always has exactly two linked shadows).
            shadow_count = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).count()
            assert shadow_count == 2

    def test_hard_delete_transfer_template_bulk_delete_skips_settled_rows(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """C8-1: Even if the predicate is bypassed, the bulk delete spares settled transfers.

        Defense in depth (F-14, mirror of CRIT-05 / E-22): commit C-21
        of the main remediation already fixed
        ``transfer_template_has_paid_history`` to filter on
        ``Status.is_settled`` so the guard at the route's entry
        catches every settled status.  This commit adds the second
        layer: the bulk-delete loop itself filters on
        ``Transaction.status_id.notin_(settled_status_ids)`` so a
        future regression of the predicate, a race window between
        the guard and the delete, or a different caller that bypasses
        the guard cannot physically destroy settled transfers plus
        their shadow pairs.  This test forces the bypass scenario by
        monkey-patching ``transfer_template_has_paid_history`` to
        return False even when a RECEIVED transfer exists, then
        asserts the post-conditions: the settled transfer plus its
        two shadows survive intact while the Projected transfer is
        deleted as intended.

        ``Transfer.transfer_template_id`` is a FK with ``ON DELETE
        SET NULL`` (``app/models/transfer.py``) so the surviving
        Received transfer has its ``transfer_template_id`` cleared
        but its financial data -- amount, status, period -- is
        intact.  Both linked shadow transactions ride along: they
        reference ``transfer_id`` (NOT NULL), so the transfer's
        survival guarantees their survival.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            received_status = db.session.query(Status).filter_by(name="Received").one()
            projected_status = db.session.query(Status).filter_by(name="Projected").one()

            # RECEIVED transfer in period 0 (must survive).
            xfer_received = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=received_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Past Transfer",
                ),
            )
            # PROJECTED transfer in period 1 (must be deleted).
            xfer_projected = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[1].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=projected_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Future Transfer",
                ),
            )
            db.session.commit()

            template_id = template.id
            received_id = xfer_received.id
            projected_id = xfer_projected.id
            received_shadow_ids = [
                row.id for row in db.session.query(Transaction).filter_by(
                    transfer_id=received_id,
                ).all()
            ]
            assert len(received_shadow_ids) == 2

            # Force the bypass: predicate lies and says "no history."
            # The defense-in-depth filter inside the route is what must
            # save the Received transfer plus its two shadows.
            monkeypatch.setattr(
                "app.routes.transfers.templates.archive_helpers.transfer_template_has_paid_history",
                lambda _template_id: False,
            )

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200

            # Settled (Received) transfer SURVIVES with original
            # amount and status; FK SET NULL clears transfer_template_id.
            surviving = db.session.get(Transfer, received_id)
            assert surviving is not None
            assert surviving.status_id == received_status.id
            assert surviving.is_deleted is False
            # Hand-verified: original $250.00 stays exactly $250.00
            # (Decimal from string per coding standards).
            assert surviving.amount == Decimal("250.00")
            assert surviving.transfer_template_id is None

            # Both shadows of the Received transfer survive untouched
            # (transfer invariant 1: a transfer always has exactly two
            # linked shadows).
            surviving_shadows = db.session.query(Transaction).filter_by(
                transfer_id=received_id,
            ).all()
            assert len(surviving_shadows) == 2
            for shadow in surviving_shadows:
                assert shadow.is_deleted is False
                assert shadow.status_id == received_status.id
                assert shadow_amount(shadow) == Decimal("250.00")
            assert {s.id for s in surviving_shadows} == set(received_shadow_ids)

            # Non-settled (Projected) transfer was deleted by the
            # bulk loop, as intended -- the defense-in-depth filter is
            # additive, not a wholesale block.
            assert db.session.get(Transfer, projected_id) is None
            orphaned_projected_shadows = db.session.query(Transaction).filter_by(
                transfer_id=projected_id,
            ).count()
            assert orphaned_projected_shadows == 0

            # Template itself was deleted (the bypass path completed).
            assert db.session.get(TransferTemplate, template_id) is None

    def test_hard_delete_preserves_shadow_invariant(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-19: No orphaned shadows remain after hard-deleting a template's transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            # Create multiple transfers via the service.
            xfer_ids = []
            for i in range(3):
                xfer = transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=seed_user["user"].id,
                        from_account_id=seed_user["account"].id,
                        to_account_id=savings.id,
                        pay_period_id=seed_periods_today[i].id,
                        scenario_id=seed_user["scenario"].id,
                        amount=Decimal("200.00"),
                        status_id=db.session.query(Status).filter_by(
                        name="Projected"
                    ).one().id,
                        category_id=seed_user["categories"]["Rent"].id,
                        transfer_template_id=template.id,
                        name="Monthly Savings",
                    ),
                )
                xfer_ids.append(xfer.id)
            db.session.commit()

            # Verify 3 transfers, 6 shadows (2 per transfer) before deletion.
            total_shadows_before = 0
            for xfer_id in xfer_ids:
                count = db.session.query(Transaction).filter_by(
                    transfer_id=xfer_id,
                ).count()
                assert count == 2, (
                    f"Transfer {xfer_id} should have exactly 2 shadows, "
                    f"found {count}"
                )
                total_shadows_before += count
            assert total_shadows_before == 6

            template_id = template.id

            # Hard-delete the template.
            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template and all transfers are gone.
            assert db.session.get(TransferTemplate, template_id) is None
            for xfer_id in xfer_ids:
                assert db.session.get(Transfer, xfer_id) is None

            # No orphaned shadows: query for any Transaction with a
            # transfer_id that was just deleted.
            orphaned_shadows = db.session.query(Transaction).filter(
                Transaction.transfer_id.in_(xfer_ids),
            ).count()
            assert orphaned_shadows == 0, (
                f"Found {orphaned_shadows} orphaned shadow transactions "
                f"after hard-deleting template {template_id}"
            )

    def test_hard_delete_transfer_template_idor(
        self, app, auth_client, seed_user,
    ):
        """C-5A.5-20: Hard-deleting another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()
            other_id = other["template"].id

            resp = auth_client.post(
                f"/transfers/{other_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Other user's template still exists.
            assert db.session.get(TransferTemplate, other_id) is not None

    def test_list_separates_active_and_archived_transfers(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-21: The unified Recurring surface shows active transfers in
        the Transfers section and archived transfers under the collapsed
        Archived section (reached by following the /transfers redirect)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            active = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Active Transfer",
                default_amount=Decimal("100.00"),
            )
            archived = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Archived Transfer",
                default_amount=Decimal("50.00"),
                is_active=False,
            )
            db.session.add_all([active, archived])
            db.session.commit()

            resp = auth_client.get("/transfers", follow_redirects=True)
            assert resp.status_code == 200
            html = resp.data.decode()

            # Active transfer in the Transfers section.
            assert "Active Transfer" in html

            # Archived section with count indicator (both kinds share it).
            assert "Archived (1)" in html
            assert "Archived Transfer" in html

    def test_archive_label_in_flash_transfers(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Archive flash message says 'archived' not 'deactivated'."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            _create_transfer(seed_user, seed_periods_today, savings, template)

            resp = auth_client.post(
                f"/transfers/{template.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data
            assert b"deactivated" not in resp.data

    def test_hard_delete_transfer_template_soft_deleted_transfers_cleaned(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Soft-deleted transfers and their shadows are permanently removed on hard-delete."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            xfer_id = xfer.id

            # Soft-delete the transfer via the service.
            transfer_service.delete_transfer(xfer.id, seed_user["user"].id, soft=True)
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

            # Shadows are also soft-deleted.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).all()
            assert all(s.is_deleted for s in shadows)

            template_id = template.id

            # Hard-delete the template.
            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Everything is gone -- no ghost data.
            assert db.session.get(TransferTemplate, template_id) is None
            assert db.session.get(Transfer, xfer_id) is None
            orphans = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).count()
            assert orphans == 0


# ── Period move (transfer-period-move follow-up) ──────────────────


class TestTransferPeriodMove:
    """Moving a transfer's pay period from the full-edit popover.

    The transfer service already relocates the parent transfer and both
    shadow transactions together (Transfer Invariant 3); these tests
    cover the UI wiring: the filtered period selector, the override flag
    on a template move, the gridRefresh trigger, and route-boundary
    ownership of the submitted period id.
    """

    def _shadows(self, xfer_id):
        """Return the two shadow transactions for a transfer."""
        return (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer_id)
            .all()
        )

    def test_full_edit_renders_filtered_period_selector(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Popover lists current+future periods plus the transfer's own.

        seed_periods_today places today in index 4, so index 0 is past
        (the transfer's own -- included and selected), index 5 is future
        (offered), and index 2 is past and not the transfer's own
        (excluded).  The pay-period <select> is isolated so option-value
        assertions cannot collide with the status select.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            own = seed_periods_today[0]
            future = seed_periods_today[5]
            excluded_past = seed_periods_today[2]

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'name="pay_period_id"' in html
            start = html.index('name="pay_period_id"')
            period_select = html[start:html.index("</select>", start)]
            # Labels off the CALENDAR since pay-calendar plan step C4-a-5,
            # which deleted ``PayPeriod.label``: the ``<option>`` renders
            # ``DerivedPeriod.label``.
            calendar = calendar_for(seed_user["user"].id)
            assert calendar.period_by_id(own.id).label in period_select
            assert f'value="{own.id}" selected' in period_select
            assert calendar.period_by_id(future.id).label in period_select
            assert calendar.period_by_id(
                excluded_past.id,
            ).label not in period_select

    def test_move_relocates_transfer_and_both_shadows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A period move relocates the transfer and both shadows; gridRefresh.

        Verifies Transfer Invariant 3 (shadow periods equal the parent's)
        is preserved through the move and that the response asks for a
        full grid refresh so the relocated rows appear under the new period.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            target = seed_periods_today[5]

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"pay_period_id": target.id, "version_id": xfer.version_id},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "gridRefresh"

            db.session.refresh(xfer)
            assert xfer.pay_period_id == target.id
            shadows = self._shadows(xfer.id)
            assert len(shadows) == 2
            assert all(s.pay_period_id == target.id for s in shadows)

    def test_template_transfer_move_sets_override(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Moving a template-generated transfer flags it is_override.

        Without the flag the recurrence engine would regenerate the
        transfer in its original period, duplicating it.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(
                seed_user, seed_periods_today, savings, template=template,
            )
            assert xfer.is_override is False
            target = seed_periods_today[5]

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"pay_period_id": target.id, "version_id": xfer.version_id},
            )
            assert resp.status_code == 200
            db.session.refresh(xfer)
            assert xfer.pay_period_id == target.id
            assert xfer.is_override is True

    def test_move_to_cross_user_period_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Submitting another user's period id returns 404 and moves nothing."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            original_period_id = xfer.pay_period_id
            other = _create_other_user_with_template()
            foreign_period_id = other["transfer"].pay_period_id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "pay_period_id": foreign_period_id,
                    "version_id": xfer.version_id,
                },
            )
            assert resp.status_code == 404
            db.session.refresh(xfer)
            assert xfer.pay_period_id == original_period_id
            assert all(
                s.pay_period_id == original_period_id
                for s in self._shadows(xfer.id)
            )

    def test_inplace_edit_keeps_balancechanged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An edit that does not change the period keeps balanceChanged."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "250.00", "version_id": xfer.version_id},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"
            db.session.refresh(xfer)
            assert xfer.amount == Decimal("250.00")



def _THREE_DAYS_AGO():
    """A settle day that is NOT the user's today.

    Every "a figure correction did not move the settle day" assertion needs a
    fixture day the seam could not have re-stamped, or it grades nothing: a
    settle stamps ``display_today()``, so on a today-dated row "preserved" and
    "re-stamped" read the same.  Three days back is inside any generated
    schedule, so it clears ruling **R-EL**'s floor, and it is in the past, so it
    clears ruling **R-EJ**'s future refusal.
    """
    return display_today() - timedelta(days=3)


class TestTransferActualBox:
    """The transfer PATCH's FIGURE correction -- the Actual box.

    **The settle-day door's sibling, and it exists because the card was
    inconsistent with itself** (developer ruling, 2026-08-17): a settled
    transfer's DAY was correctable in place while its FIGURE was not, so the
    only way to restate what the bank moved was to revert, edit and settle
    again -- and a revert RETAINS the recorded figure, so the re-settle silently
    re-booked the old number over the re-planned one.  The lock produced a wrong
    figure, not friction.

    The rule the whole class grades: **a lock protects a BUDGET DECISION from
    being rewritten; an OBSERVED FACT gets corrected when the statement
    disagrees.**  The estimate, the period, the category and the due date are
    locked on a finalised transfer.  What the bank moved, and the day it moved,
    are not.
    """

    @staticmethod
    def _settled_transfer(seed_user, seed_periods_today, day):
        """Return a settled transfer dated *day*, ledger in step.

        Callers pass a PAST day deliberately.  Settling stamps the user's today,
        so a fixture dated today makes every "a figure correction did not move
        the settle day" assertion blind: the mutation those assertions exist to
        catch is the seam re-stamping today, which on a today-dated row is
        indistinguishable from preserving.  That is finding **N-146**'s shape,
        which this arc has already paid for once.
        """
        savings = _create_savings_account(seed_user)
        xfer = _create_transfer(seed_user, seed_periods_today, savings)
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            status_id=ref_cache.status_id(StatusEnum.DONE),
        )
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id, settle_day=an_entered_day(day),
        )
        db.session.commit()
        return xfer

    @staticmethod
    def _legs(xfer_id):
        """Return both live shadows of *xfer_id*, expense leg first."""
        rows = (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer_id, is_deleted=False)
            .order_by(Transaction.id)
            .all()
        )
        assert len(rows) == 2, f"expected a pair, got {len(rows)}"
        return rows

    def test_the_box_renders_on_a_settled_transfer_and_is_not_disabled(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A finalised transfer offers the Actual box, ENABLED.

        The whole ruling in one assertion.  A draft of this step drew the
        transaction twin of this box gated on ``locked`` -- and every
        ``is_settled`` status is also ``is_immutable``, so it rendered
        ``disabled`` on 100% of the rows it appeared on and was deleted as
        unreachable.  Being disabled WAS the defect, so the test asserts the
        box is present AND live, on both doors onto this popover.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            expense, _income = self._legs(xfer.id)

            for url in (
                f"/transfers/{xfer.id}/full-edit",
                f"/transactions/{expense.id}/full-edit",
            ):
                body = auth_client.get(url).get_data(as_text=True)
                assert 'name="settled_amount"' in body, (
                    f"{url} hid the Actual box from a settled transfer"
                )
                assert not field_is_disabled(body, "settled_amount"), (
                    f"{url} rendered the Actual box disabled -- that is the "
                    "defect, not the guard: a lock protects a decision and "
                    "what the bank moved is an observation"
                )
                # The BUDGET decision beside it stays locked, which is what
                # makes the contrast the ruling draws visible on one screen.
                assert field_is_disabled(body, "amount"), (
                    f"{url} left the plan editable on a finalised transfer"
                )

    def test_the_box_is_absent_on_a_projected_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No settle, no figure: there is nothing to correct.

        The firing control for the render condition.  A box offered on a
        Projected transfer would take a figure the service refuses, which is
        the shape ruling **R-FF** removed from the reconcile panel.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            db.session.commit()

            body = auth_client.get(
                f"/transfers/{xfer.id}/full-edit"
            ).get_data(as_text=True)
            assert 'name="settled_amount"' not in body

    def test_a_correction_lands_on_BOTH_legs_and_moves_the_ledger(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """$200.00 settled, the statement says $214.37: both legs record it.

        Transfer Invariant 3 for the settlement record: a transfer's money moves
        on its two legs and the two record the same figure, exactly as they
        carry the same day.  The ledger follows in the same request, because the
        figure IS the pair's confirmed cash effect -- a correction that changed
        the record without moving the books would leave the two disagreeing.

        Hand arithmetic: the settle books $200.00 out of Checking and into
        Savings.  The correction re-books $214.37, so the net posted magnitude
        for this transfer is $214.37, not $200.00 + $14.37 twice-counted.
        """
        with app.app_context():
            day = _THREE_DAYS_AGO()
            xfer = self._settled_transfer(seed_user, seed_periods_today, day)
            version = db.session.get(Transfer, xfer.id).version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "settled_amount": "214.37",
                    "version_id": str(version),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            for leg in self._legs(xfer.id):
                assert leg.settled_amount == Decimal("214.37"), (
                    f"leg {leg.id} did not record the correction"
                )
                assert leg.settled_basis_id == settlement_basis_id(
                    SettlementBasisEnum.CORRECTED,
                ), "a figure a human typed is a CORRECTION, not a derivation"
                assert leg.settled_on == day, (
                    "a figure correction moved the settle day"
                )
            assert net_posted_by_day(
                JournalEntry.transfer_id == xfer.id,
            ) == {day: Decimal("214.37")}

    def test_an_ECHO_of_the_recorded_figure_records_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Saving the popover untouched must not manufacture a correction.

        The box is PREFILLED with what the pair records, and this form submits
        every input it renders -- so an untouched Save posts the same figure
        back.  Writing it would restamp a ``derived`` record as ``corrected``
        and destroy the only stored signal that says a human read a number off a
        statement, which is what ruling **R-FB**'s production measurement is
        made of.

        Shown to FIRE: deleting ``correction_record``'s equality test leaves
        both legs stamped ``corrected``.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            derived = settlement_basis_id(SettlementBasisEnum.DERIVED)
            assert all(
                leg.settled_basis_id == derived for leg in self._legs(xfer.id)
            ), "fixture precondition: a plain settle records a DERIVED figure"
            version = db.session.get(Transfer, xfer.id).version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": "200.00", "version_id": str(version)},
            )

            assert response.status_code == 200
            db.session.expire_all()
            for leg in self._legs(xfer.id):
                assert leg.settled_basis_id == derived, (
                    "an echoed prefill was recorded as a human's correction"
                )

    def test_a_REVERT_carrying_the_box_drops_it_and_still_reverts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling R-EG's argument applied to the figure.

        The documented way to unlock a finalised transfer is to set Status to
        Projected in this same form -- which submits the Actual box's current
        contents alongside it.  That figure is a stale ECHO of the state being
        left, not an assertion that this much moved: the user picked Projected,
        which says it did not move at all.  The route DROPS it, so the unlock
        path keeps working; a service caller asserting both on purpose is still
        refused (see the service tests).

        What moved is RETAINED, because a revert releases the ASSERTION and
        keeps the fact -- the two have different lifetimes.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            version = db.session.get(Transfer, xfer.id).version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_amount": "200.00",
                    "version_id": str(version),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            ), "the unlock path was broken by the echoed figure"
            for leg in self._legs(xfer.id):
                assert leg.settled_on is None, "a revert keeps the assertion"
                assert leg.settled_amount == Decimal("200.00"), (
                    "a revert destroyed what moved"
                )

    def test_a_figure_on_a_PROJECTED_transfer_is_REFUSED(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An amount states what MOVED, and a Projected pair's money has not.

        A Projected transfer records NOTHING, so a submitted figure cannot be an
        echo of its record -- there is no record to echo.  It is therefore a
        real assertion about money that has not moved, and it is refused rather
        than dropped: ``TransferUpdateSchema.Meta.unknown`` is ``EXCLUDE``, and a
        silently discarded money field is how a user's typed number disappears.

        The ECHO half of the same rule -- an untouched box riding a revert, which
        must still drop so the unlock path works -- is graded by
        ``test_a_REVERT_carrying_the_box_drops_it_and_still_reverts``.  The two
        cases together are the whole of ``status_seam.figure_for_status``, and
        an earlier version of that rule read the STATUS alone and so could not
        tell them apart.

        Reached by a crafted submission rather than by the form: the box is not
        rendered on a Projected transfer at all.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            db.session.commit()
            version = xfer.version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": "50.00", "version_id": str(version)},
            )

            assert response.status_code == 400, response.status_code
            assert "has nothing to record" in response.get_data(as_text=True)
            db.session.expire_all()
            for leg in self._legs(xfer.id):
                assert leg.settled_amount is None, (
                    "a refused figure was written anyway"
                )
                assert leg.settled_basis_id is None

    def test_a_status_IN_HAND_carrying_a_correction_does_BOTH(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A status IN HAND with a corrected Actual records on BOTH legs.

        The transfer half of the defect this step measured on the transaction
        door: the correction and the status change were treated as
        alternatives, so one of them was silently dropped and the response
        still said 200.

        It was ``Paid -> Settled`` -- an ARCHIVE, a real status MOVE -- until
        plan step **balance:X-am** deleted that status.  A settled transfer's
        only remaining status move is the revert, and a revert carrying a
        changed figure is refused rather than composed, so the surviving legal
        instance is the identity re-submit the popover actually produces: it
        posts the whole row, so the status box arrives beside the Actual box on
        every Save.  The reduction is recorded on the transaction twin's class
        docstring, ``TestTheDoorAppliesTheStatusANDTheCorrection``.

        What this still grades that its transaction sibling cannot: the
        correction reaches BOTH SHADOWS and the pair stays mirrored (transfer
        invariant 3).
        """
        with app.app_context():
            day = _THREE_DAYS_AGO()
            xfer = self._settled_transfer(seed_user, seed_periods_today, day)
            stored = db.session.get(Transfer, xfer.id)
            version = stored.version_id
            paid_id = stored.status_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(paid_id),
                    "settled_amount": "187.65",
                    "version_id": str(version),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).status_id == paid_id, (
                "the status was dropped while the figure was recorded"
            )
            for leg in self._legs(xfer.id):
                assert leg.status_id == paid_id
                assert leg.settled_amount == Decimal("187.65")
            assert net_posted_by_day(
                JournalEntry.transfer_id == xfer.id,
            ) == {day: Decimal("187.65")}

    def test_a_recordless_settled_pair_repairs_with_the_day_AND_the_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The legacy shape's repair, and it needs BOTH halves in one save.

        A settled row carrying no settlement record predates the record
        entirely (finding **N-181**).  ``ck_transactions_settle_day_needs_a_record``
        pairs the day with the record, so stating the DAY alone violates it --
        measured: the day-only save returns a designed 400 rather than
        repairing anything.  Stating both is the repair, and the Actual box is
        what makes it expressible.

        The popover must therefore RENDER for such a pair, with both boxes
        empty: a surface that refuses to draw cannot repair the row it is the
        only repair path for.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            # The legacy shape, reproduced the only way it can be: straight at
            # the columns, behind the seam's back.
            for leg in self._legs(xfer.id):
                record_settle_day(leg, None)
                leg.settled_amount = None
                leg.settled_basis_id = None
            db.session.commit()
            db.session.expire_all()

            body = auth_client.get(
                f"/transfers/{xfer.id}/full-edit"
            ).get_data(as_text=True)
            assert 'name="settled_amount"' in body, (
                "the popover hid the Actual box from the pair that needs it"
            )
            # Sliced to the INPUT TAG, because ``value=""`` appears
            # unconditionally elsewhere in this body -- the Category select's
            # "-- None --" option, and the empty notes / due-date / settle-day
            # inputs.  A bare membership test on the whole body is a constant
            # ``True`` and grades nothing, which is what a neutral review
            # measured this assertion doing (2026-08-18).
            box = body[body.index('name="settled_amount"'):]
            box = box[:box.index(">")]
            assert 'value=""' in box, (
                "a figure was pre-filled onto a pair that records none: " + box
            )

            version = db.session.get(Transfer, xfer.id).version_id
            day_only = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "settled_on": display_today().isoformat(),
                    "version_id": str(version),
                },
            )
            assert day_only.status_code == 400, (
                "stating the day alone must be refused -- the CHECK pairs it "
                "with the record, so it cannot succeed"
            )
            assert "records nothing that moved" in day_only.get_data(
                as_text=True,
            ), (
                "the refusal must name the repair, not surface as the "
                "constraint violation it used to be"
            )

            db.session.expire_all()
            version = db.session.get(Transfer, xfer.id).version_id
            repair = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "settled_on": display_today().isoformat(),
                    "settled_amount": "200.00",
                    "version_id": str(version),
                },
            )

            assert repair.status_code == 200, repair.get_data(as_text=True)
            db.session.expire_all()
            for leg in self._legs(xfer.id):
                assert leg.settled_on == display_today()
                assert leg.settled_amount == Decimal("200.00")
                assert leg.settled_basis_id == settlement_basis_id(
                    SettlementBasisEnum.CORRECTED,
                )

    def test_the_rebook_notice_shows_what_a_re_settle_will_book(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A reverted transfer's card names the figure a re-settle re-books.

        This card TELLS the user to set Status to Projected in order to edit the
        amount, so the transfer they are most likely looking at here is exactly
        the one carrying a retained correction -- and its plan ($200.00) is not
        what a tick will book ($214.37).  Two numbers about one transfer, and
        the second was visible on no surface but the reconcile panel.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, settled_amount=Decimal("214.37"),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            body = auth_client.get(
                f"/transfers/{xfer.id}/full-edit"
            ).get_data(as_text=True)
            assert "$214.37" in body, (
                "the card showed a $200.00 plan for a transfer that will book "
                "$214.37, with the real figure on no surface"
            )

    def test_the_notice_is_absent_when_the_plan_IS_what_a_tick_books(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The notice's firing control.

        A transfer holding no ``corrected`` record re-settles at its plan, which
        is already on screen in the Amount box -- so drawing the notice would
        state a difference that does not exist.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            body = auth_client.get(
                f"/transfers/{xfer.id}/full-edit"
            ).get_data(as_text=True)
            assert "Marking this paid will record" not in body


class TestTheTransferLockCoversTheShadowOnlyEdits:
    """A stale popover cannot silently overwrite a figure or a day.

    **A transfer and its two shadows are ONE thing** (developer ruling,
    2026-08-18).  Both full-edit popovers pin ``Transfer.version_id``, but the
    two facts they can correct in place -- what the bank moved and the day it
    moved -- live on the SHADOWS, and SQLAlchemy bumps a version counter only
    for a row it actually UPDATEs.  So the pin protected everything on the form
    except the two money-adjacent fields the form exists to correct.

    Measured before the fix: parent ``version_id = 2``; tab A corrects the
    figure to `$214.37`, 200 OK, parent still `2`; stale tab B saves its
    prefilled `$200.00` against the same pin, 200 OK, both legs now record
    `$200.00`.  The statement figure gone, reported as success.
    """

    @staticmethod
    def _settled(seed_user, seed_periods_today):
        savings = _create_savings_account(seed_user)
        xfer = _create_transfer(seed_user, seed_periods_today, savings)
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            status_id=ref_cache.status_id(StatusEnum.DONE),
        )
        db.session.commit()
        db.session.expire_all()
        return xfer

    @staticmethod
    def _legs(xfer_id):
        return (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer_id, is_deleted=False)
            .order_by(Transaction.id).all()
        )

    def test_a_stale_tab_cannot_overwrite_a_figure_correction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two tabs, one pin: the second save is a 409, not a lost update."""
        with app.app_context():
            xfer = self._settled(seed_user, seed_periods_today)
            shared_pin = db.session.get(Transfer, xfer.id).version_id

            first = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": "214.37",
                      "version_id": str(shared_pin)},
            )
            assert first.status_code == 200, first.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).version_id > shared_pin, (
                "a shadow-only write left the aggregate's counter behind"
            )

            second = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": "200.00",
                      "version_id": str(shared_pin)},
            )

            assert second.status_code == 409, (
                "a stale tab overwrote a figure correction and reported success"
            )
            db.session.expire_all()
            for leg in self._legs(xfer.id):
                assert leg.settled_amount == Decimal("214.37"), (
                    "the stale save landed anyway"
                )

    def test_a_settle_day_correction_moves_the_counter_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The SAME hole one field over, closed by the same statement.

        The settle-day box (ruling **R-ED**) shipped before the Actual box and
        writes only the shadows for the identical reason, so it carried the
        identical defect.  Fixing the figure alone would have left it standing
        on the same form.
        """
        with app.app_context():
            xfer = self._settled(seed_user, seed_periods_today)
            shared_pin = db.session.get(Transfer, xfer.id).version_id

            assert auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_on": _THREE_DAYS_AGO().isoformat(),
                      "version_id": str(shared_pin)},
            ).status_code == 200
            db.session.expire_all()

            assert db.session.get(Transfer, xfer.id).version_id > shared_pin

    def test_an_ECHO_does_not_move_the_counter(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The firing control: a write that changes nothing is not a change.

        The popover re-submits every input on every Save, so an untouched
        re-save must not bump the pin -- a counter that moved when nothing did
        would turn a second open tab into a spurious 409 on a form nobody
        edited.
        """
        with app.app_context():
            xfer = self._settled(seed_user, seed_periods_today)
            recorded = self._legs(xfer.id)[0].settled_amount
            pin = db.session.get(Transfer, xfer.id).version_id

            assert auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": str(recorded),
                      "version_id": str(pin)},
            ).status_code == 200

            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).version_id == pin, (
                "an echoed prefill bumped the aggregate's counter"
            )

    def test_a_CHANGED_figure_beside_a_revert_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer twin of the silent-discard defect.

        The submission rule read the STATUS alone, so a figure the user had just
        retyped was dropped exactly like an untouched prefill -- and because a
        revert RETAINS what moved, the stale record then governed the re-settle.
        An echo still drops (the unlock path must keep working); a change is
        refused, naming both acts.
        """
        with app.app_context():
            xfer = self._settled(seed_user, seed_periods_today)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                settled_amount=Decimal("214.37"),
            )
            db.session.commit()
            db.session.expire_all()
            pin = db.session.get(Transfer, xfer.id).version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_amount": "111.11",
                    "version_id": str(pin),
                },
            )

            assert response.status_code == 400, (
                "a figure the user CHANGED was swallowed by the revert"
            )
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            ), "a refused request reverted the transfer anyway"
            for leg in self._legs(xfer.id):
                assert leg.settled_amount == Decimal("214.37")
