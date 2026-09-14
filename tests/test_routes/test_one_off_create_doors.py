"""
Shekel Budget App -- the grid's two create doors mint a ONE-OFF (balance:X-bi-7b)

``POST /transactions/inline`` (a grid cell) and ``POST /transactions`` (the
Add Transaction modal) wrote a bare link-less ``Transaction`` carrying its own
flags and figure until this step.  Both now hand the form to the one producer
(``one_off.place_one_off``), so what a browser's Create leaves behind is a
RULE-LESS DEFINITION plus its placed row (ruling **R-BAL20**).  These cases
grade the doors as the wire reaches them -- every field the forms render,
posted -- and read back through the app's own readers:

* the row names a definition with no rule, and that definition carries the
  name, category, type and flags the form posted;
* the typed figure is the definition's ONE version dated on the row's due
  date (**R-BAL21**); the row states no figure and RESOLVES to it;
* the row is due on its paycheck's start where the form offers no date
  (**R-BAL22**) and on the stated day where it does, and ``occurs_on``
  records it (**R-BAL25**);
* the inline door's defaulted name reaches the definition;
* the IDOR and loan refusals fire BEFORE anything is minted -- a refused
  create leaves no definition behind;
* the cell the door renders shows the resolved figure, so the grid a
  browser swaps in reads the same number the balance does.
"""
import re
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services.template_amount_service import amount_versions
from tests._test_helpers import resolved_amount


def _inline_payload(seed_user, seed_periods_today, **overrides):
    """Return what the quick-create form posts, with *overrides*."""
    data = {
        "estimated_amount": "162.25",
        "account_id": seed_user["account"].id,
        "category_id": seed_user["categories"]["Groceries"].id,
        "pay_period_id": seed_periods_today[4].id,
        "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        "scenario_id": seed_user["scenario"].id,
    }
    data.update(overrides)
    return data


def _modal_payload(seed_user, seed_periods_today, **overrides):
    """Return what the Add Transaction modal posts, with *overrides*."""
    data = {
        "name": "Kayla's Kindle",
        "estimated_amount": "162.25",
        "account_id": seed_user["account"].id,
        "category_id": seed_user["categories"]["Groceries"].id,
        "pay_period_id": seed_periods_today[4].id,
        "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        "scenario_id": seed_user["scenario"].id,
    }
    data.update(overrides)
    return data


def _the_row_named(name):
    """Return the one row called *name*."""
    return db.session.query(Transaction).filter_by(name=name).one()


def _definition_count():
    """Return how many definitions the database holds."""
    return db.session.query(TransactionTemplate).count()


class TestTheInlineDoor:
    """``POST /transactions/inline`` -- the grid cell's quick create."""

    def test_the_row_names_a_rule_less_definition_carrying_the_form(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Name, category, type, both flags on the DEFINITION; no rule."""
        with app.app_context():
            resp = auth_client.post("/transactions/inline", data=_inline_payload(
                seed_user, seed_periods_today,
                name="Kayla's Kindle", is_envelope="true",
                companion_visible="true",
            ))
            assert resp.status_code == 201, resp.data[:400]

            row = _the_row_named("Kayla's Kindle")
            assert row.template_id is not None
            definition = row.template
            assert definition.recurrence_rule is None
            assert row.recurs is False
            assert definition.name == "Kayla's Kindle"
            assert definition.category_id == seed_user["categories"]["Groceries"].id
            assert definition.transaction_type_id == ref_cache.txn_type_id(
                TxnTypeEnum.EXPENSE,
            )
            assert definition.account_id == seed_user["account"].id
            assert definition.user_id == seed_user["user"].id
            assert definition.is_envelope is True
            assert definition.companion_visible is True
            assert row.tracks_purchases is True
            assert row.visible_to_companion is True

    def test_the_typed_figure_is_the_definitions_one_version_and_prices_the_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL21: the row states nothing and resolves to what was typed."""
        with app.app_context():
            resp = auth_client.post("/transactions/inline", data=_inline_payload(
                seed_user, seed_periods_today, name="Kayla's Kindle",
            ))
            assert resp.status_code == 201, resp.data[:400]

            row = _the_row_named("Kayla's Kindle")
            assert row.estimated_amount is None
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert resolved_amount(row) == Decimal("162.25")
            versions = amount_versions(row.template)
            assert len(versions) == 1
            assert versions[0].effective_date == row.due_date
            assert Decimal(str(versions[0].amount)) == Decimal("162.25")

    def test_the_row_is_due_on_its_paychecks_start_and_answers_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL22 / R-BAL25: the cell's forms offer no date."""
        with app.app_context():
            resp = auth_client.post("/transactions/inline", data=_inline_payload(
                seed_user, seed_periods_today, name="Kayla's Kindle",
            ))
            assert resp.status_code == 201, resp.data[:400]

            row = _the_row_named("Kayla's Kindle")
            assert row.due_date == seed_periods_today[4].start_date
            assert row.occurs_on == row.due_date
            assert row.pay_period_id == seed_periods_today[4].id
            assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert row.is_override is False

    def test_a_blank_name_defaults_to_the_category_on_the_definition(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Grid audit A5's default reaches the DEFINITION, and the row reads it."""
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            resp = auth_client.post("/transactions/inline", data=_inline_payload(
                seed_user, seed_periods_today, name="",
            ))
            assert resp.status_code == 201, resp.data[:400]

            row = _the_row_named(category.display_name)
            assert row.template.name == category.display_name

    def test_the_rendered_cell_shows_the_resolved_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The cell a browser swaps in reads the number the balance does."""
        with app.app_context():
            resp = auth_client.post("/transactions/inline", data=_inline_payload(
                seed_user, seed_periods_today, name="Kayla's Kindle",
                estimated_amount="162.25",
            ))
            assert resp.status_code == 201, resp.data[:400]
            html = resp.data.decode()
            assert "162.25" in html
            assert "None" not in re.sub(r"<[^>]+>", " ", html)

    def test_a_foreign_category_is_refused_before_anything_is_minted(
        self, app, auth_client, seed_user, seed_second_user, seed_periods_today,
    ):
        """The IDOR probe fires first; no definition is left behind."""
        with app.app_context():
            before = _definition_count()
            foreign = next(iter(seed_second_user["categories"].values()))
            resp = auth_client.post("/transactions/inline", data=_inline_payload(
                seed_user, seed_periods_today, category_id=foreign.id,
            ))
            assert resp.status_code == 404
            assert _definition_count() == before

    def test_a_foreign_paycheck_is_refused_before_anything_is_minted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An id the owner's calendar does not carry is absent, not minted into."""
        with app.app_context():
            before = _definition_count()
            resp = auth_client.post("/transactions/inline", data=_inline_payload(
                seed_user, seed_periods_today, pay_period_id=999999,
            ))
            assert resp.status_code == 404
            assert _definition_count() == before


class TestTheModalDoor:
    """``POST /transactions`` -- the Add Transaction modal's full create."""

    def test_the_row_names_a_rule_less_definition_priced_by_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same shape the inline door mints, from the other form."""
        with app.app_context():
            resp = auth_client.post("/transactions", data=_modal_payload(
                seed_user, seed_periods_today, companion_visible="true",
            ))
            assert resp.status_code == 201, resp.data[:400]

            row = _the_row_named("Kayla's Kindle")
            assert row.template_id is not None
            assert row.recurs is False
            assert row.template.companion_visible is True
            assert row.template.is_envelope is False
            assert row.estimated_amount is None
            assert resolved_amount(row) == Decimal("162.25")
            assert len(amount_versions(row.template)) == 1

    def test_a_stated_due_date_lands_on_the_row_and_dates_the_series(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL22's other arm: the owner's day, on the row and the version."""
        with app.app_context():
            stated = seed_periods_today[4].start_date + timedelta(days=5)
            resp = auth_client.post("/transactions", data=_modal_payload(
                seed_user, seed_periods_today, due_date=stated.isoformat(),
            ))
            assert resp.status_code == 201, resp.data[:400]

            row = _the_row_named("Kayla's Kindle")
            assert row.due_date == stated
            assert row.occurs_on == stated
            assert amount_versions(row.template)[0].effective_date == stated

    def test_a_note_is_the_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The one field the definition does not state lands on the row."""
        with app.app_context():
            resp = auth_client.post("/transactions", data=_modal_payload(
                seed_user, seed_periods_today, notes="ordered 5/2",
            ))
            assert resp.status_code == 201, resp.data[:400]
            assert _the_row_named("Kayla's Kindle").notes == "ordered 5/2"

    def test_an_income_one_off_takes_the_income_type_on_its_definition(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Type is the definition's; the row reads it (``is_income``)."""
        with app.app_context():
            income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            resp = auth_client.post("/transactions", data=_modal_payload(
                seed_user, seed_periods_today, name="Rebate",
                transaction_type_id=income_id,
                category_id=seed_user["categories"]["Salary"].id,
            ))
            assert resp.status_code == 201, resp.data[:400]

            row = _the_row_named("Rebate")
            assert row.template.transaction_type_id == income_id
            assert row.is_income is True

    def test_a_foreign_category_is_refused_before_anything_is_minted(
        self, app, auth_client, seed_user, seed_second_user, seed_periods_today,
    ):
        """The IDOR probe fires first on this door too."""
        with app.app_context():
            before = _definition_count()
            foreign = next(iter(seed_second_user["categories"].values()))
            resp = auth_client.post("/transactions", data=_modal_payload(
                seed_user, seed_periods_today, category_id=foreign.id,
            ))
            assert resp.status_code == 404
            assert _definition_count() == before


class TestTwoOneOffsAreTwoDefinitions:
    """Every plan item has exactly ONE definition, and each one-off is one."""

    def test_two_creates_of_the_same_name_mint_two_definitions(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two $4 coffees are two plan items (the create door's own rule)."""
        with app.app_context():
            before = _definition_count()
            for _ in range(2):
                resp = auth_client.post(
                    "/transactions/inline",
                    data=_inline_payload(
                        seed_user, seed_periods_today, name="Coffee",
                        estimated_amount="4.00",
                    ),
                )
                assert resp.status_code == 201, resp.data[:400]

            rows = db.session.query(Transaction).filter_by(name="Coffee").all()
            assert len(rows) == 2
            assert rows[0].template_id != rows[1].template_id
            assert _definition_count() == before + 2
