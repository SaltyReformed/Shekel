"""
Shekel Budget App -- Transaction flag-resolution property tests

Unit tests for ``Transaction.tracks_purchases`` and
``Transaction.visible_to_companion``.  Resolution rule: a
template-generated row defers to its template's flag (the template is
the single source of truth for every instance it generates), while an
ad-hoc row (template_id IS NULL) uses its own column.  These properties
are the load-bearing abstraction behind F2 (companion visibility) and
F3 (purchase tracking) for ad-hoc transactions.

**The ``is_envelope`` cell is SEALED since plan step ``balance:X-bi-1``**
(ruling **R-JQ**; the seal over a pin, developer 2026-09-11):
:class:`TestTheDeadCellHasNoPublicName` pins what that means.  On a
template-generated row the row's own cell is dead, and keying on it read
4 envelopes where there are 238 -- so the public name ``is_envelope``
now READS the one accessor and only WRITES the cell, and every derived
flag's class-level name refuses to key a query rather than silently
matching nothing.
"""
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import generate_row_of, make_expense_template


def _adhoc(seed_user, period, *, is_envelope, companion_visible):
    """Create and commit an ad-hoc (template_id IS NULL) transaction."""
    txn = Transaction(
        name="Ad-hoc",
        amount_ownership=AmountOwnership.own(Decimal("100.00")),
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        user_id=period.user_id,
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
    """Create a template (with tpl_* flags) plus the engine's row of it.

    The row is the definition's own, generated through the engine
    (:func:`generate_row_of`, plan step balance:X-cf) rather than restated
    here.  The engine writes neither flag on a row -- both are the row's own
    cells, at their defaults -- so the row's OWN flags are then set to the
    opposite of the template's, which is the state that proves the resolved
    property reads the template and not the row.  The ``is_envelope`` write
    goes through the sealed cell's public setter, the same door the ad-hoc
    constructors use; on a generated row it is the inert write plan step
    X-bi-5 deletes.
    """
    tpl = make_expense_template(
        db.session, seed_user, amount="100.00", name="Templated",
        is_envelope=tpl_envelope, companion_visible=tpl_visible,
    )
    txn = generate_row_of(tpl, period)
    txn.is_envelope = own_envelope
    txn.companion_visible = own_visible
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


class TestTheDeadCellHasNoPublicName:
    """Plan step ``balance:X-bi-1``: ``is_envelope`` is sealed on the row.

    Six claims, and they fail for different reasons.  The public name
    READS the accessor (a reader reaching for the column name gets the
    template's answer on a generated row); it still WRITES the cell (the
    ad-hoc doors keep their kwarg); the guessed single-underscore name
    reaches no column; the SQL name is unchanged; a class-level name
    refuses to key a query on any spelling (the silent-empty-result trap
    measured 2026-09-11); and the two read-only flags refuse assignment.
    """

    def test_the_public_name_reads_the_template_on_a_generated_row(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``txn.is_envelope`` on a generated row is the TEMPLATE's answer.

        Both rows set the own cell to the opposite of the template flag,
        so a passing assertion proves the column name no longer reaches the
        dead cell -- the 4-where-there-are-238 misread, unrepresentable.
        """
        with app.app_context():
            txn_on = _templated(
                seed_user, seed_periods_today[0],
                tpl_envelope=True, tpl_visible=False,
                own_envelope=False, own_visible=False,
            )
            assert txn_on.is_envelope is True
            assert txn_on.is_envelope is txn_on.tracks_purchases

            txn_off = _templated(
                seed_user, seed_periods_today[1],
                tpl_envelope=False, tpl_visible=False,
                own_envelope=True, own_visible=False,
            )
            assert txn_off.is_envelope is False
            assert txn_off.is_envelope is txn_off.tracks_purchases

    def test_the_public_name_reads_the_own_cell_on_an_adhoc_row(
        self, app, db, seed_user, seed_periods_today,
    ):
        """On an ad-hoc row the cell IS the answer, through the same accessor."""
        with app.app_context():
            txn = _adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=False,
            )
            assert txn.is_envelope is True
            txn.is_envelope = False
            db.session.commit()
            db.session.expire(txn)
            assert txn.is_envelope is False
            assert txn.tracks_purchases is False

    def test_a_setattr_over_a_variable_name_still_writes_the_cell(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The PATCH route's generic ``setattr`` loop keeps working.

        ``routes/transactions/mutations._apply_field_updates`` writes every
        schema field by name, so the seal must not turn that write into a
        plain instance attribute the next load forgets.  Written, committed,
        EXPIRED and re-read: the cell holds it.
        """
        with app.app_context():
            txn = _adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=False, companion_visible=False,
            )
            field = "is_envelope"
            setattr(txn, field, True)
            db.session.commit()
            db.session.expire(txn)
            assert txn.tracks_purchases is True

    def test_a_guessed_single_underscore_reaches_no_column(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``row._is_envelope`` binds a plain attribute, never the cell.

        The seal's own claim about itself, from the column comment: the
        double underscore mangles the mapped name, so the spelling a reader
        would guess is a no-op the next read exposes.
        """
        with app.app_context():
            txn = _adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=False, companion_visible=False,
            )
            txn._is_envelope = True  # pylint: disable=protected-access
            db.session.commit()
            db.session.expire(txn)
            assert txn.tracks_purchases is False
            assert txn.is_envelope is False

    def test_the_sql_name_is_unchanged(self, app):
        """The seal renames the ATTRIBUTE and leaves the column where it was.

        No migration rides with X-bi-1; this is the half of that claim a
        model test can grade.  The other half -- an empty autogenerate
        diff -- was measured on 2026-09-11 against an untouched
        ``origin/dev`` tree: the same six pre-existing ``system.*`` items
        on both, nothing on either flag.
        """
        with app.app_context():
            column = Transaction.__table__.c.is_envelope
            assert column.name == "is_envelope"
            assert column.nullable is False
            assert str(column.server_default.arg) == "false"

    @pytest.mark.parametrize("flag", [
        "is_envelope", "tracks_purchases", "visible_to_companion",
    ])
    def test_a_query_keyed_on_a_derived_flag_refuses_to_build(
        self, app, flag,
    ):
        """Every spelling of a query over the flag raises at BUILD time.

        A plain ``property`` at class level compares ``False`` to
        everything, so ``filter_by(flag=True)`` and ``Transaction.flag ==
        True`` returned NO ROWS with no error (measured 2026-09-11 on the
        first cut of this step) -- a silent wrong answer, worse than the
        wrong column it replaced.  Three spellings, one refusal each.
        """
        with app.app_context():
            with pytest.raises(TypeError, match="cannot key a query"):
                db.session.query(Transaction).filter_by(**{flag: True})
            with pytest.raises(TypeError, match="cannot key a query"):
                getattr(Transaction, flag) == True  # noqa: E712  # pylint: disable=singleton-comparison,expression-not-assigned
            with pytest.raises(TypeError, match="cannot key a query"):
                getattr(Transaction, flag).is_(True)
            # The truth-test arm on its own: the three above reach the
            # refusal through ``__eq__`` and ``__getattr__``, so deleting
            # ``__bool__`` would leave them green while
            # ``if Transaction.flag:`` silently read True.
            with pytest.raises(TypeError, match="cannot key a query"):
                bool(getattr(Transaction, flag))
            with pytest.raises(TypeError, match="cannot key a query"):
                db.session.query(Transaction).filter(getattr(Transaction, flag))

    def test_the_read_only_flags_refuse_assignment(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``tracks_purchases`` and ``visible_to_companion`` have no setter.

        A derivation that accepted a write would bind a plain instance
        attribute shadowing the descriptor for that one row -- the same
        silent shape the single-underscore test above pins from the other
        side.
        """
        with app.app_context():
            txn = _adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=False, companion_visible=False,
            )
            with pytest.raises(AttributeError):
                txn.tracks_purchases = True
            with pytest.raises(AttributeError):
                txn.visible_to_companion = True
            assert txn.tracks_purchases is False
            assert txn.visible_to_companion is False
