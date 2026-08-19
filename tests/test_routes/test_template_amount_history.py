"""
Shekel Budget App -- Amount-history route tests (plan step X-au-a)

The write DOOR as the two recurring-definition forms drive it, and the panel
that makes what it wrote visible and correctable.

The date the edit form asks for used to be "Regenerate effective from": it
bounded one delete-and-recreate sweep and was then discarded, so the app could
never say what a definition cost last March.  It is now stored as a version of
the amount, and it still bounds the same sweep -- so what the series says a bill
is worth and which bills an edit rebuilds are read off ONE value.  These tests
assert the stored consequence of each form action, not just its status code.

Both kinds are covered here rather than in the two per-kind route suites,
because the door and the panel are ONE implementation over two forms
(:mod:`app.services.template_amount_service`,
:mod:`app.routes._amount_version_actions`) and a per-kind split would test it
twice and its shared rules nowhere.
"""

from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser

from app.extensions import db
from app.utils.dates import display_today
from app.models.ref import TransactionType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service as tas
from tests._test_helpers import create_savings_account, make_every_period_rule
from tests.oracles.recurrence_baseline import EVERY_PERIOD


# ── Helpers ──────────────────────────────────────────────────────────


class _FormNesting(HTMLParser):
    """Track the deepest ``<form>`` nesting a rendered page reaches."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.deepest = 0

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.depth += 1
            self.deepest = max(self.deepest, self.depth)

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth -= 1


def _max_form_nesting(html: str) -> int:
    """Return the deepest ``<form>`` nesting in *html* (1 = never nested)."""
    parser = _FormNesting()
    parser.feed(html)
    return parser.deepest


def _template_with_history(seed_user, amounts, name="Geico"):
    """Create a rule-less expense template carrying a stated price history.

    ``amounts`` is ``[(effective_date, "amount"), ...]`` ascending, written
    through the real write door so the stored shape is the one production
    would hold.
    """
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name=name,
        default_amount=Decimal(amounts[-1][1]),
    )
    db.session.add(template)
    db.session.flush()
    for effective_on, amount in amounts:
        tas.set_amount(template, Decimal(amount), effective_on=effective_on)
    db.session.commit()
    return template


def _transfer_template(seed_user, savings_acct, amount="250.00"):
    """Create a recurring transfer template with an Every-Period rule."""
    # Authored through the write door (plan step R7c-b): the two-axis columns
    # are NOT NULL, so a rule naming only a pattern cannot be stored.
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_acct.id,
        name="Money Market Contribution",
        default_amount=Decimal(amount),
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db.session, template)
    tas.set_amount(template, Decimal(amount), effective_on=date(2020, 4, 9))
    db.session.commit()
    return template


# ── The create doors ─────────────────────────────────────────────────


class TestCreateOpensTheSeries:
    """A definition is born with its price recorded, not just stored."""

    def test_creating_a_recurring_transaction_opens_its_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates records the stated amount as of today.

        Without this the definition would have a column and no history, and the
        resolver plan step X-au-b builds would have nothing to answer from.
        """
        with app.app_context():
            resp = auth_client.post("/templates", data={
                "name": "Geico",
                "default_amount": "165.30",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": db.session.query(TransactionType)
                    .filter_by(name="Expense").one().id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)
            assert resp.status_code == 200

            template = db.session.query(TransactionTemplate).filter_by(
                name="Geico",
            ).one()
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].amount == Decimal("165.30")
            assert versions[0].effective_date == display_today()

    def test_creating_a_recurring_transfer_opens_its_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers records the stated amount as of today."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()

            resp = auth_client.post("/transfers", data={
                "name": "Money Market Contribution",
                "default_amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "category_id": seed_user["categories"]["Rent"].id,
                "start_period_id": seed_periods_today[0].id,
            }, follow_redirects=True)
            assert resp.status_code == 200

            template = db.session.query(TransferTemplate).filter_by(
                name="Money Market Contribution",
            ).one()
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].amount == Decimal("250.00")
            assert versions[0].effective_date == display_today()


class TestEligibilityCanBeGAINED:
    """A definition that becomes stated-amount must not be left with no series."""

    def test_archiving_a_salary_profile_opens_its_template_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The paycheck stops pricing the rows, so the column becomes the price.

        With no ACTIVE profile the recurrence engine falls back to
        ``default_amount`` (``recurrence_engine._get_transaction_amount``), so
        the template starts owning its amount at exactly that moment.  Found by
        adversarial review: before this, archiving the profile left an eligible
        template holding ZERO versions -- 58 rows on production's one salary
        template -- which is the empty-series gap plan step X-au-b's resolver is
        specified to refuse rather than fall back on.
        """
        with app.app_context():
            from tests._test_helpers import make_salary_profile
            income = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=income.id,
                name="Data Manager",
                default_amount=Decimal("2473.38"),
            )
            db.session.add(template)
            db.session.flush()
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.commit()
            tid, pid = template.id, profile.id

            assert tas.owns_its_amount(template) is False
            assert tas.amount_versions(template) == []

            resp = auth_client.post(
                f"/salary/{pid}/delete", follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert tas.owns_its_amount(template) is True
            versions = tas.amount_versions(template)
            assert len(versions) == 1
            assert versions[0].amount == Decimal("2473.38")
            assert tas.amount_as_of(template, date(2026, 4, 1)) == Decimal("2473.38")


# ── The edit door ────────────────────────────────────────────────────


class TestEditRecordsThePrice:
    """The form's date is a stored fact, not a transient sweep bound."""

    def test_a_new_amount_at_a_date_appends_a_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Raising the premium from September leaves August on the old price.

        The defect ruling R-FI names is the opposite: with a bare scalar, this
        edit reprices every past projection too.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2026, 4, 1), "178.00")],
            )

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Geico",
                "default_amount": "165.30",
                "effective_from": "2026-09-01",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, template.id)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2026, 4, 1), Decimal("178.00")),
                (date(2026, 9, 1), Decimal("165.30")),
            ]
            assert tas.amount_as_of(template, date(2026, 8, 1)) == Decimal("178.00")

    def test_a_rename_records_no_price(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-submitting the same amount states no change, so nothing is written.

        Every edit form posts the amount back, so gating on "the form carried a
        figure" would litter the history with entries that say nothing.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2026, 4, 1), "178.00")],
            )

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Geico Auto",
                "default_amount": "178.00",
                "effective_from": "2026-09-01",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, template.id)
            assert template.name == "Geico Auto"
            assert len(tas.amount_versions(template)) == 1

    def test_a_transfer_amount_change_appends_a_version(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer form drives the same door: $500.00 -> $250.00.

        Production's ``Checking -> Fidelity Money Market`` contribution is
        exactly this shape, and it is the only transfer template whose rows
        record two prices.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()
            template = _transfer_template(seed_user, savings, amount="500.00")

            resp = auth_client.post(f"/transfers/{template.id}", data={
                "name": "Money Market Contribution",
                "default_amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "category_id": seed_user["categories"]["Rent"].id,
                "effective_from": "2026-05-21",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransferTemplate, template.id)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2020, 4, 9), Decimal("500.00")),
                (date(2026, 5, 21), Decimal("250.00")),
            ]


class TestTheConflictChooserRoundTrip:
    """The chooser rolls the pending edit back, so the price must roll back too.

    A collision with a hand-edited upcoming row renders a full-page chooser and
    then ``db.session.rollback()``s the whole pending edit; Apply re-submits the
    identical form, ``effective_from`` included, and the edit runs again.  The
    price recorded on that first pass therefore has to VANISH with it, and the
    Apply pass has to record it exactly once -- otherwise a user who thinks
    better of a change leaves a version behind, or one who confirms it gets two.
    """

    def _template_with_conflict(self, seed_user):
        """A recurring template whose latest upcoming instance is hand-edited."""
        from tests.test_routes.test_templates import (
            _create_template, _future_override_txn,
        )
        template = _create_template(
            seed_user, cadence=EVERY_PERIOD, amount="1200.00",
        )
        tas.set_amount(
            template, Decimal("1200.00"), effective_on=date(2026, 1, 1),
        )
        db.session.commit()
        txn = _future_override_txn(seed_user, template, amount="1500.00")
        return template, txn

    def test_the_chooser_leaves_no_price_behind(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The first submit renders the chooser and records nothing."""
        with app.app_context():
            template, _txn = self._template_with_conflict(seed_user)
            tid = template.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "default_amount": "1400.00",
                "effective_from": "2026-06-01",
            })
            assert resp.status_code == 200
            assert b"hand-edited" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert template.default_amount == Decimal("1200.00")
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [(date(2026, 1, 1), Decimal("1200.00"))]

    def test_apply_records_the_price_exactly_once(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply re-runs the same edit, so the price lands once at its date."""
        with app.app_context():
            template, txn = self._template_with_conflict(seed_user)
            tid, txn_id = template.id, txn.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "default_amount": "1400.00",
                "effective_from": "2026-06-01",
                "conflict_apply": "1",
                f"conflict_decision_{txn_id}": "keep",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2026, 1, 1), Decimal("1200.00")),
                (date(2026, 6, 1), Decimal("1400.00")),
            ]


    def test_a_scheduled_rise_survives_a_rename_end_to_end(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Schedule a December rise, then rename: December stays December.

        The full path an adversarial review walked through the real form: the
        edit page prefills the AMOUNT it shows as "Current", so a rename posts
        today's price with a blank date and states nothing new.  Before the fix
        the page offered the December figure instead, and the rename recorded
        that rise as having happened today -- while the same page's history
        panel still labelled it "Scheduled".
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2020, 4, 1), "178.00")],
            )
            tid = template.id
            future = display_today() + timedelta(days=120)

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico",
                "default_amount": "200.00",
                "effective_from": future.isoformat(),
            }, follow_redirects=True)
            assert resp.status_code == 200

            # The form now offers TODAY's price, not the queued one.
            body = auth_client.get(f"/templates/{tid}/edit").get_data(as_text=True)
            assert 'name="default_amount"' in body
            assert 'value="178.00"' in body

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico Auto",
                "default_amount": "178.00",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert template.name == "Geico Auto"
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2020, 4, 1), Decimal("178.00")),
                (future, Decimal("200.00")),
            ]
            # The column still carries the queued price, so the rows that edit
            # rebuilt are regenerated at it.
            assert template.default_amount == Decimal("200.00")


class TestTheOptimisticLockCounter:
    """One edit bumps the template's version counter exactly once."""

    def test_a_rename_plus_amount_submit_bumps_the_counter_once(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The stale-form guard rests on this, and nothing else pins it.

        The amount door suppresses autoflush, and the update route calls it
        BEFORE the field loop, precisely because a rename issues a bulk UPDATE
        that autoflushes whatever is dirty -- so stating the amount afterwards
        left a second dirty write for the commit and took ``version_id`` from 1
        to 3 on one submit.  An adversarial review noted that removing either
        guard would silently break the stale-form check with a green suite.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2020, 4, 1), "178.00")],
            )
            tid = template.id
            before = template.version_id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico Auto",
                "default_amount": "165.30",
                "version_id": str(before),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert template.version_id == before + 1
            assert template.name == "Geico Auto"
            assert template.default_amount == Decimal("165.30")

    def test_a_transfer_rename_plus_amount_submit_bumps_once_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer route calls the same door before its own field loop."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()
            template = _transfer_template(seed_user, savings, amount="500.00")
            tid = template.id
            before = template.version_id

            resp = auth_client.post(f"/transfers/{tid}", data={
                "name": "Money Market Savings",
                "default_amount": "250.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "category_id": seed_user["categories"]["Rent"].id,
                "version_id": str(before),
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransferTemplate, tid)
            assert template.version_id == before + 1
            assert template.default_amount == Decimal("250.00")


    def test_a_wild_effective_date_is_rejected_by_the_form(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A four-digit-year typo never reaches the series.

        Measured by an adversarial review before the bound existed: ``0202``
        was accepted, became the earliest version, and could not be withdrawn.
        The schema now refuses it and the edit is redirected back unapplied.
        """
        with app.app_context():
            template = _template_with_history(
                seed_user, [(date(2020, 4, 1), "178.00")],
            )
            tid = template.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Geico",
                "default_amount": "165.30",
                "effective_from": "0202-08-11",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [(date(2020, 4, 1), Decimal("178.00"))]
            assert template.default_amount == Decimal("178.00")


# ── The panel ────────────────────────────────────────────────────────


class TestHistoryPanel:
    """What was recorded is visible on the form that recorded it."""

    def test_the_edit_form_renders_the_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Each recorded price and its date appear, newest first.

        A stored fact nobody can see is a fact nobody can find wrong, and until
        plan step X-au-e prices rows from this series a mistake would surface
        only once it started moving money.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2026, 4, 1), "178.00"),
                (date(2026, 6, 1), "178.32"),
                (date(2026, 9, 1), "165.30"),
            ])

            resp = auth_client.get(f"/templates/{template.id}/edit")
            body = resp.get_data(as_text=True)

            assert resp.status_code == 200
            assert "Amount history" in body
            assert "Amount effective from" in body
            # Each Remove control is its own POST form carrying a CSRF token,
            # and the panel sits OUTSIDE the edit form -- nested forms are not
            # valid HTML and the browser would drop the inner one.
            assert 'name="csrf_token"' in body
            assert _max_form_nesting(body) == 1
            for figure in ("$178.00", "$178.32", "$165.30"):
                assert figure in body
            assert body.index("$165.30") < body.index("$178.00")

    def test_a_definition_with_no_history_renders_no_panel(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A salary-linked template has no stated price, so there is none to show."""
        with app.app_context():
            from tests._test_helpers import make_salary_profile
            expense = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense.id,
                name="Data Manager",
                default_amount=Decimal("2473.38"),
            )
            db.session.add(template)
            db.session.flush()
            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.commit()

            resp = auth_client.get(f"/templates/{template.id}/edit")
            body = resp.get_data(as_text=True)

            assert resp.status_code == 200
            assert "Amount history" not in body


# ── The withdrawal control ───────────────────────────────────────────


class TestWithdrawAmountVersion:
    """Removing a mis-dated entry, and the one the route refuses."""

    def test_withdrawing_an_interior_entry(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The June entry goes and June falls back on the April price.

        Interior, so the newest price the series states is unchanged and
        nothing generation writes moves.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2020, 4, 1), "178.00"),
                (date(2020, 6, 1), "178.32"),
                (date(2020, 9, 1), "165.30"),
            ])
            tid = template.id
            june_id = tas.amount_versions(template)[1].id

            resp = auth_client.post(
                f"/templates/{tid}/amount-versions/{june_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Amount history entry removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert len(tas.amount_versions(template)) == 2
            assert tas.amount_as_of(template, date(2020, 7, 1)) == Decimal("178.00")
            assert template.default_amount == Decimal("165.30")

    def test_withdrawing_the_earliest_is_refused_with_the_repair(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Every date before the series is priced from it, so it stays.

        The refusal names the repair -- record the amount at the date you want
        first -- rather than only stating the rule.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2026, 4, 1), "178.00"),
                (date(2026, 9, 1), "165.30"),
            ])
            earliest_id = tas.amount_versions(template)[0].id

            resp = auth_client.post(
                f"/templates/{template.id}/amount-versions/{earliest_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data
            assert b"record the amount at the date you want first" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, template.id)
            assert len(tas.amount_versions(template)) == 2

    def test_a_withdrawal_that_would_move_the_price_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """This door corrects the RECORD; it never moves what is budgeted.

        It runs no regeneration and offers no conflict chooser, so a withdrawal
        that changed ``default_amount`` would leave the rows already generated
        disagreeing with the definition, silently.  Refusing keeps the panel a
        safe surface and sends a real price change to the Amount field.
        """
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2020, 1, 1), "178.00"),
                (date(2020, 6, 1), "165.30"),
            ])
            tid = template.id
            newest_id = tas.amount_versions(template)[1].id

            resp = auth_client.post(
                f"/templates/{tid}/amount-versions/{newest_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransactionTemplate, tid)
            assert template.default_amount == Decimal("165.30")
            assert len(tas.amount_versions(template)) == 2

    def test_another_users_template_is_404(
        self, app, second_auth_client, seed_user, seed_second_user,
        seed_periods_today,
    ):
        """The security response rule: not-yours and not-found are one answer."""
        with app.app_context():
            template = _template_with_history(seed_user, [
                (date(2026, 4, 1), "178.00"),
                (date(2026, 9, 1), "165.30"),
            ])
            version_id = tas.amount_versions(template)[1].id

            resp = second_auth_client.post(
                f"/templates/{template.id}/amount-versions/{version_id}/delete",
            )
            assert resp.status_code == 404

    def test_a_version_id_from_another_template_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Your own template, someone else's entry id: the same refusal.

        The service looks the id up inside THIS template's collection, so a
        version belonging elsewhere is indistinguishable from one that does not
        exist -- no existence oracle either way.
        """
        with app.app_context():
            mine = _template_with_history(seed_user, [
                (date(2020, 4, 1), "178.00"),
                (date(2020, 9, 1), "165.30"),
            ])
            other = _template_with_history(seed_user, [
                (date(2020, 4, 1), "18.14"),
                (date(2020, 9, 1), "19.99"),
            ], name="Apple Music")
            mine_id = mine.id
            foreign_id = tas.amount_versions(other)[1].id

            resp = auth_client.post(
                f"/templates/{mine_id}/amount-versions/{foreign_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"could not be removed" in resp.data

            db.session.expire_all()
            assert len(tas.amount_versions(
                db.session.get(TransactionTemplate, mine_id),
            )) == 2

    def test_a_transfer_entry_is_withdrawn_through_its_own_route(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer form's control drives the same shared action."""
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            db.session.commit()
            template = _transfer_template(seed_user, savings, amount="500.00")
            tas.set_amount(
                template, Decimal("250.00"), effective_on=date(2020, 5, 21),
            )
            tas.set_amount(
                template, Decimal("300.00"), effective_on=date(2020, 6, 4),
            )
            db.session.commit()
            tid = template.id
            # The MIDDLE entry: removing it leaves $300.00 the newest price, so
            # nothing generation writes moves.
            middle_id = tas.amount_versions(template)[1].id

            resp = auth_client.post(
                f"/transfers/{tid}/amount-versions/{middle_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Amount history entry removed" in resp.data

            db.session.expire_all()
            template = db.session.get(TransferTemplate, tid)
            assert [
                (v.effective_date, v.amount) for v in tas.amount_versions(template)
            ] == [
                (date(2020, 4, 9), Decimal("500.00")),
                (date(2020, 6, 4), Decimal("300.00")),
            ]
            assert template.default_amount == Decimal("300.00")
