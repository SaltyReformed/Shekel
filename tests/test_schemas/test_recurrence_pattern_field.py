"""
Shekel Budget App -- ``RecurrencePatternField`` (plan step R2e-2)

``ref.recurrence_patterns`` is a TABLE; ``RecurrencePatternEnum`` is the set
``app.services.recurrence.resolve`` can read back, and the two are deliberately
allowed to diverge (plan step R2e-3 deletes the ``Once`` member while its row
survives to R9, so the auto-rollback image can still boot).  An id in the gap is
a well-formed row id that no route catches: it passes ``RowId``, passes a
``db.session.get`` existence probe, and then raises inside the write door.

Refusing it is a property of the SUBMISSION, so it belongs to the submission's
validator (developer ruling 2026-08-07).  The check used to live in the two
route-layer form readers -- the same rule written twice, which a third caller
would have had neither copy of; ``tests/test_routes/test_recurrence_form_helpers
.py::test_invalid_pattern_returns_redirect_response`` pinned that older shape
and moved here when the guarantee did.

What these tests pin, at the layer that now owns it:

1. an unmodelled id is refused on ALL FOUR schemas that accept a pattern;
2. the refusal message is the one the routes flash verbatim, so the user still
   reads "Invalid recurrence pattern." rather than a generic prompt;
3. a modelled id and an explicit ``None`` both still pass -- without which a
   validator that refused everything would satisfy (1).
"""
import pytest
from marshmallow import ValidationError

from app import ref_cache
from app.enums import RecurrencePatternEnum
from app.extensions import db
from app.models.ref import RecurrencePattern
from app.schemas.validation import TemplateCreateSchema, TemplateUpdateSchema
from app.schemas.validation.transfers import (
    TransferTemplateCreateSchema,
    TransferTemplateUpdateSchema,
)

#: Verbatim copy the routes flash; pinned so the message cannot drift silently.
_REFUSAL = "Invalid recurrence pattern."

#: The four schemas that accept a recurrence pattern.  Swept rather than
#: sampled: the field is inherited by the update schemas, and an override that
#: dropped it on one of them would otherwise pass unnoticed.
_SCHEMAS = (
    ("TemplateCreateSchema", TemplateCreateSchema),
    ("TemplateUpdateSchema", TemplateUpdateSchema),
    ("TransferTemplateCreateSchema", TransferTemplateCreateSchema),
    ("TransferTemplateUpdateSchema", TransferTemplateUpdateSchema),
)


def _unmodelled_pattern_id(name="Every Blue Moon"):
    """Insert a ``ref.recurrence_patterns`` row that no enum member names.

    The post-R2e-3 shape: ``ref_cache.init`` requires every ENUM member to have
    a row and says nothing about the reverse, so a surplus row is a state the
    schema permits.

    Returns:
        int: The new row's primary key.
    """
    assert name not in {member.value for member in RecurrencePatternEnum}
    row = RecurrencePattern(name=name)
    db.session.add(row)
    db.session.flush()
    return row.id


class TestAnUnmodelledPatternIsRefused:
    """A real ``ref`` row the enum does not name never reaches a route."""

    @pytest.mark.parametrize("label,schema_cls", _SCHEMAS)
    def test_every_schema_refuses_it(self, app, label, schema_cls):
        """All four schemas attach the refusal to ``recurrence_pattern``."""
        with app.app_context():
            unmodelled_id = _unmodelled_pattern_id()

            with pytest.raises(ValidationError) as exc:
                schema_cls().load(
                    {"recurrence_pattern": str(unmodelled_id)}, partial=True,
                )

            assert "recurrence_pattern" in exc.value.messages, label
            assert _REFUSAL in exc.value.messages["recurrence_pattern"], label

    def test_the_row_really_exists(self, app):
        """The refusal is about MEMBERSHIP, not about a missing row.

        Without this the test above would also pass against the old
        existence-probe implementation, which is the thing being replaced.
        """
        with app.app_context():
            unmodelled_id = _unmodelled_pattern_id()

            assert db.session.get(RecurrencePattern, unmodelled_id) is not None

    def test_validate_reports_it_without_raising(self, app):
        """``schema.validate`` surfaces it, which is what the routes call.

        The routes call ``validate`` first and flash from the returned dict;
        a rule enforced only on ``load`` would 500 instead of flashing.
        """
        with app.app_context():
            unmodelled_id = _unmodelled_pattern_id()

            errors = TemplateCreateSchema().validate(
                {"recurrence_pattern": str(unmodelled_id)}, partial=True,
            )

            assert errors["recurrence_pattern"] == [_REFUSAL]


class TestTheModelledCasesStillPass:
    """Negative controls -- a validator that refused everything would fail here."""

    @pytest.mark.parametrize("label,schema_cls", _SCHEMAS)
    def test_every_modelled_pattern_loads(self, app, label, schema_cls):
        """Each of the eight patterns deserializes to its integer id."""
        with app.app_context():
            for member in RecurrencePatternEnum:
                pattern_id = ref_cache.recurrence_pattern_id(member)

                loaded = schema_cls().load(
                    {"recurrence_pattern": str(pattern_id)}, partial=True,
                )

                assert loaded["recurrence_pattern"] == pattern_id, (
                    f"{label} / {member.name}"
                )

    @pytest.mark.parametrize("label,schema_cls", _SCHEMAS)
    def test_an_explicit_none_still_passes(self, app, label, schema_cls):
        """``None`` is the "one-time / manual" choice and must survive.

        ``allow_none`` short-circuits before the field deserializes, so the
        membership check must not see it.  Plan step R2e-1 made a present
        ``None`` mean CLEAR THE RECURRENCE, so refusing it here would break the
        only way to end a cadence.
        """
        with app.app_context():
            loaded = schema_cls().load(
                {"recurrence_pattern": None}, partial=True,
            )

            assert loaded["recurrence_pattern"] is None, label

    def test_a_non_integer_is_still_refused_by_row_id(self, app):
        """The inherited ``RowId`` rules are intact.

        The membership check is layered ON `RowId`, not instead of it: '007'
        and ' 2 ' name no row and must still fail before the enum is consulted.
        """
        with app.app_context():
            for spelling in ("daily", "007", " 2 ", "+3", "0", "-1"):
                errors = TemplateCreateSchema().validate(
                    {"recurrence_pattern": spelling}, partial=True,
                )

                assert "recurrence_pattern" in errors, spelling
