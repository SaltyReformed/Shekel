"""The recurrence preview bounds by the books a save would (plan step pay_calendar:C18-a).

Ruling **R-PC85**: a recurring definition's occurrences start above the books
of every account it moves money in.  The form's live preview promises "what
saving would produce", so it takes the same floor through the same composition
(``BalanceContext.resolved_for``): the transaction form's ``account_id`` and
the transfer form's ``from_account_id`` ride on the request beside
``to_account_id``, each through the ownership gate -- 404 for a missing or a
foreign account, the house rule.
"""
from decimal import Decimal

from app.enums import RecurrenceUnitEnum
from app.services import account_service
from tests._test_helpers import cadence_payload


def _preview(auth_client, seed_periods, **accounts):
    """GET the preview of an every-paycheck cadence from the first period."""
    return auth_client.get(
        "/templates/preview-recurrence",
        query_string={
            **cadence_payload(
                unit=RecurrenceUnitEnum.PERIOD,
                starts_on=seed_periods[0].start_date,
            ),
            **accounts,
        },
    )


def _listed(period):
    """Return how the preview renders *period*'s payday."""
    return period.start_date.strftime("%b %d, %Y").encode()


class TestThePreviewTakesTheBooks:
    """The source accounts bound the preview; foreign ones are refused."""

    def test_a_LATER_opened_account_drops_the_paychecks_on_or_before_its_books(
        self, app, db, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Opened on the third payday: the first three paychecks are not listed.

        The same request without the account lists the first paycheck, so the
        difference is the floor and not the cadence.
        """
        with app.app_context():
            opened = seed_periods[2].start_date
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Later books",
                    anchor_balance=Decimal("0.00"),
                    observed_on=opened,
                ),
            )
            db.session.commit()

            unbounded = _preview(auth_client, seed_periods)
            bounded = _preview(auth_client, seed_periods, account_id=account.id)

            assert unbounded.status_code == 200
            assert _listed(seed_periods[0]) in unbounded.data
            assert bounded.status_code == 200
            for period in seed_periods[:3]:
                assert _listed(period) not in bounded.data
            assert _listed(seed_periods[3]) in bounded.data

    def test_a_transfer_SOURCE_opened_later_bounds_it_too(
        self, app, db, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Money leaves the source; its books bind as the destination's do."""
        with app.app_context():
            opened = seed_periods[2].start_date
            source = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Later source",
                    anchor_balance=Decimal("0.00"),
                    observed_on=opened,
                ),
            )
            db.session.commit()

            resp = _preview(
                auth_client, seed_periods,
                from_account_id=source.id,
                to_account_id=seed_user["account"].id,
            )

            assert resp.status_code == 200
            assert _listed(seed_periods[0]) not in resp.data
            assert _listed(seed_periods[3]) in resp.data

    def test_another_owners_account_is_404_on_every_account_control(
        self, app, db, auth_client, seed_user, seed_periods,
        seed_second_user,
    ):  # pylint: disable=unused-argument
        """Each id names a row, so each goes through the ownership gate."""
        with app.app_context():
            foreign = seed_second_user["account"].id
            for field in ("account_id", "from_account_id", "to_account_id"):
                resp = _preview(auth_client, seed_periods, **{field: foreign})
                assert resp.status_code == 404, field
