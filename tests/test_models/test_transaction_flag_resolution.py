"""
Shekel Budget App -- Transaction flag-resolution property tests

Unit tests for ``Transaction.tracks_purchases`` and
``Transaction.visible_to_companion``.  Resolution rule: a
template-generated row defers to its template's flag (the template is
the single source of truth for every instance it generates), while an
ad-hoc row (template_id IS NULL) uses its own column.  These properties
are the load-bearing abstraction behind F2 (companion visibility) and
F3 (purchase tracking) for ad-hoc transactions.
"""
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate


def _adhoc(seed_user, period, *, is_envelope, companion_visible):
    """Create and commit an ad-hoc (template_id IS NULL) transaction."""
    txn = Transaction(
        name="Ad-hoc",
        estimated_amount=Decimal("100.00"),
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        pay_period_id=period.id,
        account_id=seed_user["account"].id,
        category_id=list(seed_user["categories"].values())[0].id,
        scenario_id=seed_user["scenario"].id,
        template_id=None,
        is_envelope=is_envelope,
        companion_visible=companion_visible,
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _templated(seed_user, period, *, tpl_envelope, tpl_visible,
               own_envelope, own_visible):
    """Create a template (with tpl_* flags) plus a row linked to it.

    The row's OWN flags are set to the opposite of the template's so the
    test can prove the resolved property reads the template, not the row.
    """
    category = list(seed_user["categories"].values())[0]
    tpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Templated",
        default_amount=Decimal("100.00"),
        is_envelope=tpl_envelope,
        companion_visible=tpl_visible,
    )
    db.session.add(tpl)
    db.session.flush()
    txn = Transaction(
        name="Templated",
        estimated_amount=Decimal("100.00"),
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        pay_period_id=period.id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        scenario_id=seed_user["scenario"].id,
        template_id=tpl.id,
        is_envelope=own_envelope,
        companion_visible=own_visible,
    )
    db.session.add(txn)
    db.session.commit()
    return txn


class TestTracksPurchasesResolution:
    """Resolution of Transaction.tracks_purchases."""

    def test_adhoc_uses_own_flag_true(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ad-hoc row with is_envelope=True tracks purchases."""
        with app.app_context():
            txn = _adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=False,
            )
            assert txn.tracks_purchases is True

    def test_adhoc_uses_own_flag_false(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ad-hoc row with is_envelope=False does not track purchases."""
        with app.app_context():
            txn = _adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=False, companion_visible=False,
            )
            assert txn.tracks_purchases is False

    def test_template_row_defers_to_template(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A template row reads the template flag, ignoring its own column.

        Both rows set the OWN flag to the opposite of the template flag,
        so a passing assertion proves the template wins.
        """
        with app.app_context():
            # Template ON, row's own flag OFF -> resolves ON.
            txn_on = _templated(
                seed_user, seed_periods_today[0],
                tpl_envelope=True, tpl_visible=False,
                own_envelope=False, own_visible=False,
            )
            assert txn_on.tracks_purchases is True

            # Template OFF, row's own flag ON -> resolves OFF.
            txn_off = _templated(
                seed_user, seed_periods_today[1],
                tpl_envelope=False, tpl_visible=False,
                own_envelope=True, own_visible=False,
            )
            assert txn_off.tracks_purchases is False


class TestVisibleToCompanionResolution:
    """Resolution of Transaction.visible_to_companion."""

    def test_adhoc_uses_own_flag_true(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ad-hoc row with companion_visible=True is visible."""
        with app.app_context():
            txn = _adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=False, companion_visible=True,
            )
            assert txn.visible_to_companion is True

    def test_adhoc_uses_own_flag_false(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ad-hoc row with companion_visible=False is not visible."""
        with app.app_context():
            txn = _adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=False, companion_visible=False,
            )
            assert txn.visible_to_companion is False

    def test_template_row_defers_to_template(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A template row reads the template's companion_visible flag."""
        with app.app_context():
            txn_on = _templated(
                seed_user, seed_periods_today[0],
                tpl_envelope=False, tpl_visible=True,
                own_envelope=False, own_visible=False,
            )
            assert txn_on.visible_to_companion is True

            txn_off = _templated(
                seed_user, seed_periods_today[1],
                tpl_envelope=False, tpl_visible=False,
                own_envelope=False, own_visible=True,
            )
            assert txn_off.visible_to_companion is False
