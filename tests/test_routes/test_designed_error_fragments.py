"""
Shekel Budget App -- Grid designed error-fragment tests.

The marker-header convention (closeout plan session 4, ruled
2026-07-11): grid mutation rejections that used to return raw strings
or JSON -- bodies the app-wide htmx config silently drops -- now
re-render the requesting surface (desktop cell, mobile card, entry
list) with CURRENT data plus the rejection message, stamped with the
``Shekel-Designed-Fragment`` header so the one global listener in
``app.js`` swaps them.  These tests pin the response contract per
surface; the state-machine legality itself is covered by the C-21
suites.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER


def _create_expense(seed_user, seed_periods_today, status_name="Projected"):
    """Insert an expense in the given status for a rejection attack.

    Mirrors the ``test_c21_state_machine_routes`` helper so this suite
    shares the auth_client / seed_user wiring.  The status is written
    directly (bypassing the route layer) because the point is to START
    from a state the attacked action is illegal from.
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    txn = Transaction(
        pay_period_id=seed_periods_today[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=status.id,
        name="Fragment Test Expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("55.00"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


class TestDesktopCellErrorFragment:
    """Cell-targeted rejections return the cell re-rendered + marker."""

    def test_illegal_patch_transition_renders_cell_with_names(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An illegal PATCH returns the marked cell naming both statuses.

        Projected -> Settled is unreachable (a row must pass through
        Done/Received).  The 400 body must be the cell fragment (not a
        raw string), carry the marker header, and name the two statuses
        in words -- the id-only message the state machine used to emit
        reads as noise in a user-facing hint.
        """
        with app.app_context():
            txn = _create_expense(seed_user, seed_periods_today)
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(settled_id)},
            )
            assert resp.status_code == 400
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "txn-chip" in body
            assert "bi-exclamation-octagon" in body
            # Status NAMES lead the message; the ids stay in parens.
            assert "Projected" in body
            assert "Settled" in body

            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)

    def test_mark_done_on_cancelled_renders_cell_error(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Mark Paid on a Cancelled row: designed 400, row untouched.

        The stale-surface race D2 names: another device cancels, then a
        still-open surface posts mark-done.  Cancelled -> Paid is
        illegal; before the convention the rejection body was silently
        dropped and the click did nothing visible.
        """
        with app.app_context():
            txn = _create_expense(
                seed_user, seed_periods_today, status_name="Cancelled",
            )
            cancelled_id = txn.status_id

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "txn-chip" in body
            assert "Cancelled" in body

            db.session.refresh(txn)
            assert txn.status_id == cancelled_id

    def test_patch_schema_422_renders_cell_error(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A malformed PATCH field returns the marked cell at 422.

        The flattened Marshmallow message replaces the old JSON body
        the client dropped; the stored amount survives.
        """
        with app.app_context():
            txn = _create_expense(seed_user, seed_periods_today)

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "abc"},
            )
            assert resp.status_code == 422
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "txn-chip" in body
            assert "estimated_amount" in body

            db.session.refresh(txn)
            assert txn.estimated_amount == Decimal("55.00")


class TestMobileCardErrorFragment:
    """Card-targeted rejections return the card + banner, not the cell."""

    def test_mark_done_on_cancelled_renders_card_banner(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A mobile Mark Paid rejection re-renders THAT card with a banner.

        The request carries ``render=mobile_card`` + ``card_prefix``,
        so the 400 body must be the ``#card-tp-<id>`` wrapper (a cell
        fragment would not resolve against the card's hx-target) with
        the danger banner inside the wrapper -- outside it, the next
        successful swap would orphan the banner.
        """
        with app.app_context():
            txn = _create_expense(
                seed_user, seed_periods_today, status_name="Cancelled",
            )

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={
                    "render": "mobile_card",
                    "card_prefix": "tp",
                    "can_edit": "1",
                },
            )
            assert resp.status_code == 400
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert f'id="card-tp-{txn.id}"' in body
            assert "alert-danger" in body
            assert "Cancelled" in body


class TestEntryListErrorFragment:
    """Entry-list rejections return the list + banner at the host id."""

    def test_create_entry_schema_422_renders_list_banner(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A bad entry amount re-renders the entry list with the reason.

        The 422 body must be the ``#entry-list-<id>`` fragment (the
        request's own hx-target) with a danger banner carrying the
        flattened field message, replacing the old raw ``str(errors)``
        dict repr the client dropped.
        """
        with app.app_context():
            txn = _create_expense(seed_user, seed_periods_today)

            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "abc",
                    "description": "Bad amount",
                    "entry_date": "2026-07-11",
                },
            )
            assert resp.status_code == 422
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert f'id="entry-list-{txn.id}"' in body
            assert "alert-danger" in body
            assert "amount" in body
