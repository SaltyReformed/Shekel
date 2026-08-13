"""
Shekel Budget App -- the two authored recurrence axes at the schema door (R7b-2)

``RecurrenceUnitField`` and ``PeriodPlacementField`` replaced
``RecurrencePatternField`` when plan step R7b-2 stopped the form posting a
closed-set pattern id: a submission now states ``(interval_n, unit, placement)``
and the write door encodes it.  The refusal moved with the vocabulary but its
SHAPE did not -- a submitted ``ref`` id must name a value the application
MODELS, which is narrower than "names a row", and the difference between the
two is a 500.

Three layers, and this file pins all three because each catches something the
others cannot:

1. :class:`~app.schemas.validation._helpers.RowId` -- does this digit string
   name a row AT ALL (``"007"``, ``" 2 "``, ``"0"`` do not);
2. the two ``_RefEnumField`` subclasses -- does that row name a value this
   application models, per axis, with a message naming the CONTROL the user
   touched;
3. :func:`~app.schemas.validation._helpers.validate_authorable_cadence` -- can
   the whole TRIPLE be stored, which is a property of no single field: until
   plan step R7c the cadence is stored as a closed-set pattern, and that set
   covers every N pay periods but only 1, 3 or 6 months, and pairs the
   first-paycheck placement with a ONE-month interval only.

Layer 3 is what a hand-assembled POST meets.  Nothing a user can assemble by
clicking reaches it -- the picker's options are derived from the same table
(``tests/test_routes/test_recurrence_picker.py``) -- and that is exactly why it
is tested here rather than trusted: it is the reason the write door's
``RecurrenceResolutionError`` stays a broken-invariant 500 rather than becoming
a user-facing path.
"""
import pytest
from marshmallow import ValidationError

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.ref import PeriodPlacement, RecurrenceUnit
from app.schemas.validation import TemplateCreateSchema, TemplateUpdateSchema
from app.schemas.validation.transfers import (
    TransferTemplateCreateSchema,
    TransferTemplateUpdateSchema,
)

#: Verbatim copy each field refuses with; pinned so a message cannot drift into
#: naming no control the user can see.
_UNIT_REFUSAL = "Invalid repeat unit."
_PLACEMENT_REFUSAL = "Invalid funding choice."

#: The four schemas that accept a cadence.  Swept rather than sampled: the
#: fields are inherited by the update schemas, and an override that dropped one
#: on a single schema would otherwise pass unnoticed.
_SCHEMAS = (
    ("TemplateCreateSchema", TemplateCreateSchema),
    ("TemplateUpdateSchema", TemplateUpdateSchema),
    ("TransferTemplateCreateSchema", TransferTemplateCreateSchema),
    ("TransferTemplateUpdateSchema", TransferTemplateUpdateSchema),
)

#: The two axes, each with the field name it posts under, the enum whose members
#: it models, the ``ref_cache`` accessor that resolves one, and its refusal.
_AXES = (
    (
        "recurrence_unit", RecurrenceUnitEnum,
        ref_cache.recurrence_unit_id, _UNIT_REFUSAL,
    ),
    (
        "recurrence_placement", PeriodPlacementEnum,
        ref_cache.period_placement_id, _PLACEMENT_REFUSAL,
    ),
)


class TestAnUnmodelledIdIsRefused:
    """An id naming no member of the axis's enum never reaches a route."""

    @pytest.mark.parametrize("label,schema_cls", _SCHEMAS)
    @pytest.mark.parametrize("field,_enum,_accessor,refusal", _AXES)
    def test_every_schema_refuses_it_on_every_axis(
        self, app, label, schema_cls, field, _enum, _accessor, refusal,
    ):
        """All four schemas attach the refusal to the field that carried it."""
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                schema_cls().load({field: "999999"}, partial=True)

            assert field in exc.value.messages, label
            assert refusal in exc.value.messages[field], label

    @pytest.mark.parametrize("field,_enum,_accessor,refusal", _AXES)
    def test_validate_reports_it_without_raising(
        self, app, field, _enum, _accessor, refusal,
    ):
        """``schema.validate`` surfaces it, which is what the routes call.

        The routes call ``validate`` first and flash from the returned dict; a
        rule enforced only on ``load`` would 500 instead of flashing.
        """
        with app.app_context():
            errors = TemplateCreateSchema().validate(
                {field: "999999"}, partial=True,
            )

            assert errors[field] == [refusal]

    @pytest.mark.parametrize(
        ("field", "model", "row_name", "refusal"),
        [
            ("recurrence_unit", RecurrenceUnit, "Fortnight", _UNIT_REFUSAL),
            (
                "recurrence_placement", PeriodPlacement,
                "Two Ahead", _PLACEMENT_REFUSAL,
            ),
        ],
    )
    def test_a_row_that_really_exists_is_still_refused(
        self, app, field, model, row_name, refusal,
    ):
        """**Membership, not existence** -- the whole reason these fields exist.

        Restored after an adversarial review of plan step R7b-2 found it
        dropped: every other refusal here submits ``"999999"``, which names no
        row at all, so all of them pass against a field that merely does
        ``db.session.get(...) is not None``.  That existence probe is precisely
        what ``_RefEnumField`` replaced, because a ``ref`` row the enum does not
        name passes it and then raises inside the write door.

        The state is manufacturable for the same reason it is for patterns:
        ``ref_cache.init`` requires every ENUM member to have a row and says
        nothing about the reverse.  Plan step R8 will add real members for
        exactly these two shapes -- a WEEK-scale unit and a fund-in-advance
        placement (plan ledger row **D20**) -- which is what makes a surplus row
        a state to refuse rather than a hypothetical.
        """
        with app.app_context():
            assert row_name not in {
                member.value for member in (
                    RecurrenceUnitEnum if model is RecurrenceUnit
                    else PeriodPlacementEnum
                )
            }, "the surplus row must not name a member the app models"
            row = model(name=row_name)
            db.session.add(row)
            db.session.flush()

            # The row EXISTS: an existence probe would say yes here.
            assert db.session.get(model, row.id) is not None

            with pytest.raises(ValidationError) as exc:
                TemplateCreateSchema().load({field: str(row.id)}, partial=True)

            assert refusal in exc.value.messages[field]

    def test_each_axis_names_its_own_control(self, app):
        """The two refusals are DIFFERENT sentences, and that is deliberate.

        "Invalid recurrence value" would name nothing the user touched.  A
        shared message would also let a placement error render under the unit
        control, which on a form whose three controls are LINKED reads as the
        wrong one being at fault.

        Asserted against what the SCHEMAS actually produce, not against the two
        constants at the top of this file -- comparing those to each other only
        proves that two literals sixty lines up differ.
        """
        with app.app_context():
            unit_errors = TemplateCreateSchema().validate(
                {"recurrence_unit": "999999"}, partial=True,
            )
            placement_errors = TemplateCreateSchema().validate(
                {"recurrence_placement": "999999"}, partial=True,
            )

            assert unit_errors["recurrence_unit"] != (
                placement_errors["recurrence_placement"]
            )


class TestTheModelledCasesStillPass:
    """Negative controls -- a validator that refused everything would fail here."""

    @pytest.mark.parametrize("label,schema_cls", _SCHEMAS)
    @pytest.mark.parametrize("field,enum_cls,accessor,_refusal", _AXES)
    def test_every_modelled_member_deserializes_to_its_member(
        self, app, label, schema_cls, field, enum_cls, accessor, _refusal,
    ):
        """Each id deserializes to the ENUM MEMBER, not back to the integer.

        The wire format is an id and the logic value is the member, so the
        schema -- the boundary between them -- is where the conversion belongs.
        A field that handed back the id would make every consumer downstream
        repeat a scan that can only ever give one answer.

        **Asked of the FIELD each schema declares rather than through
        ``load()``**, which still sweeps all four schemas and proves each
        declares the field, while keeping this class's subject to the field.
        Going through ``load()`` would entangle it with the cross-field rule
        two classes down: a lone unit is refused there now, and a ``WEEK`` unit
        is refused whatever it is paired with, because no closed-set pattern
        stores it until plan step R8.
        """
        with app.app_context():
            declared = schema_cls().fields.get(field)

            assert declared is not None, f"{label} does not declare {field}"
            for member in enum_cls:
                assert declared.deserialize(str(accessor(member))) is member, (
                    f"{label} / {member.name}"
                )

    @pytest.mark.parametrize("label,schema_cls", _SCHEMAS)
    @pytest.mark.parametrize("field,_enum,_accessor,_refusal", _AXES)
    def test_an_explicit_none_still_passes(
        self, app, label, schema_cls, field, _enum, _accessor, _refusal,
    ):
        """``None`` is the "does not repeat" choice and must survive.

        ``allow_none`` short-circuits before the field deserializes, so the
        membership check must not see it.  Plan step R2e-1 made a present
        ``None`` mean CLEAR THE RECURRENCE, so refusing it here would break the
        only way to end a cadence.
        """
        with app.app_context():
            loaded = schema_cls().load({field: None}, partial=True)

            assert loaded[field] is None, label

    @pytest.mark.parametrize("field,_enum,_accessor,_refusal", _AXES)
    def test_a_non_canonical_spelling_is_still_refused_by_row_id(
        self, app, field, _enum, _accessor, _refusal,
    ):
        """The inherited ``RowId`` rules are intact.

        The membership check is layered ON ``RowId``, not instead of it:
        ``"007"`` and ``" 2 "`` name no row and must fail before the enum is
        consulted.
        """
        with app.app_context():
            for spelling in ("daily", "007", " 2 ", "+3", "0", "-1"):
                errors = TemplateCreateSchema().validate(
                    {field: spelling}, partial=True,
                )

                assert field in errors, spelling


class TestTheTripleMustBeStorable:
    """``validate_authorable_cadence``: the rule no single field can state."""

    @staticmethod
    def _payload(unit, placement, interval_n=None):
        """Return a partial payload naming one cadence.

        Args:
            unit: A ``RecurrenceUnitEnum`` member.
            placement: A ``PeriodPlacementEnum`` member.
            interval_n: The interval, or ``None`` to omit the key entirely --
                which is what an HTML form's empty number input becomes.

        Returns:
            dict: The submission.
        """
        payload = {
            "recurrence_unit": str(ref_cache.recurrence_unit_id(unit)),
            "recurrence_placement": str(
                ref_cache.period_placement_id(placement),
            ),
        }
        if interval_n is not None:
            payload["interval_n"] = str(interval_n)
        return payload

    @pytest.mark.parametrize("label,schema_cls", _SCHEMAS)
    @pytest.mark.parametrize(
        "unit,interval_n",
        [
            (RecurrenceUnitEnum.MONTH, 2),   # every other month: no pattern
            (RecurrenceUnitEnum.MONTH, 4),
            (RecurrenceUnitEnum.MONTH, 12),  # a YEAR spelled in months
            (RecurrenceUnitEnum.YEAR, 2),    # every other year
        ],
    )
    def test_a_month_or_year_interval_with_no_pattern_is_refused(
        self, app, label, schema_cls, unit, interval_n,
    ):
        """A cadence the closed set cannot NAME is refused on every schema.

        Each of these is well defined and the resolver walks it correctly; the
        closed pattern set simply has no name for it until plan step R7c gives
        the table an authored unit and interval.  The refusal is attached to
        ``recurrence_unit`` because that is the control the user changes to
        get out of the state.
        """
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                schema_cls().load(
                    self._payload(
                        unit, PeriodPlacementEnum.CONTAINING_DATE, interval_n,
                    ),
                    partial=True,
                )

            assert "recurrence_unit" in exc.value.messages, label

    @pytest.mark.parametrize("interval_n", [3, 6])
    def test_the_first_paycheck_placement_is_refused_above_one_month(
        self, app, interval_n,
    ):
        """The PAIR dependency: a placement belongs to ``(unit, interval)``.

        ``MONTHLY_FIRST`` is ``(1, MONTH, PERIOD_STARTING_ON_OR_AFTER)`` and
        the closed set has no quarterly or semi-annual twin.  A validator
        keyed on the unit alone would accept both of these -- and so would a
        picker that offered placements per unit, which is why the offer set
        carries whole triples.
        """
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                TemplateCreateSchema().load(
                    self._payload(
                        RecurrenceUnitEnum.MONTH,
                        PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                        interval_n,
                    ),
                    partial=True,
                )

            assert "recurrence_unit" in exc.value.messages

    def test_the_same_placement_at_one_month_passes(self, app):
        """The neighbouring case, and the one a too-broad rule would break.

        Without this the test above would pass against a validator that simply
        refused the first-paycheck placement outright -- which would delete a
        cadence 5 of the 46 live rules use.
        """
        with app.app_context():
            loaded = TemplateCreateSchema().load(
                self._payload(
                    RecurrenceUnitEnum.MONTH,
                    PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                    1,
                ),
                partial=True,
            )

            assert loaded["recurrence_unit"] is RecurrenceUnitEnum.MONTH

    @pytest.mark.parametrize("interval_n", [1, 2, 3, 7, 26])
    def test_any_positive_paycheck_interval_passes(self, app, interval_n):
        """``Every N Periods`` takes its interval from a column, so N is free.

        The one pattern that names no interval, which is what makes the
        paycheck unit's form control a free number box rather than a select.
        """
        with app.app_context():
            loaded = TemplateCreateSchema().load(
                self._payload(
                    RecurrenceUnitEnum.PERIOD,
                    PeriodPlacementEnum.CONTAINING_DATE,
                    interval_n,
                ),
                partial=True,
            )

            assert loaded["interval_n"] == interval_n

    def test_an_omitted_interval_defaults_to_one(self, app):
        """An absent ``interval_n`` is read as 1, matching the write door.

        An untouched HTML number input submits ``""``, which
        ``_normalize_empty_inputs`` DROPS (the field is not ``allow_none``), so
        the cross-field check sees no key at all.  Reading that as 1 is what
        ``build_recurrence_rule_from_form`` does with the same absence; reading
        it as 0 would refuse every such submission.
        """
        with app.app_context():
            loaded = TemplateCreateSchema().load(
                self._payload(
                    RecurrenceUnitEnum.MONTH,
                    PeriodPlacementEnum.CONTAINING_DATE,
                ),
                partial=True,
            )

            assert "interval_n" not in loaded

    @pytest.mark.parametrize(
        ("label", "placement_value"),
        [("absent", None), ("empty", "")],
    )
    def test_a_unit_with_no_placement_is_refused(
        self, app, label, placement_value,
    ):
        """**A named unit with no placement is bad input, not a partial update.**

        Found by two independent adversarial reviews of plan step R7b-2, which
        both reached the same 500.  The guard read "skip when EITHER axis is
        absent", which conflates two different submissions: "neither axis" is
        an amount-only edit and must be skipped, while "a unit and no
        placement" is half a cadence -- and both write doors pop the placement
        with no default (``build_recurrence_rule_from_form``,
        ``update_recurrence_rule_from_form``), so it reached the flush as an
        unhandled ``KeyError``.

        The EMPTY spelling is the worse of the two and needed its own case: the
        field is ``allow_none``, so ``_normalize_empty_inputs`` keeps the key
        with a present ``None``, marshmallow never calls ``_deserialize``, and
        the route builds a spec with ``placement=None``.  Under the PERIOD unit
        the resolver does not read the placement at all, so it resolved fine
        and died in ``encode_cadence`` -- exactly the refusal this layer exists
        to turn into a field error.

        It is defect **D13**'s shape one field over: the transfer route's own
        docstring records the same 500 on ``recurrence_pattern``, whose
        ``allow_none`` was read as "required".
        """
        with app.app_context():
            payload = {
                "recurrence_unit": str(
                    ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD),
                ),
            }
            if placement_value is not None:
                payload["recurrence_placement"] = placement_value

            with pytest.raises(ValidationError) as exc:
                TemplateCreateSchema().load(payload, partial=True)

            assert "recurrence_placement" in exc.value.messages, label

    def test_a_partial_update_naming_neither_axis_is_not_refused(self, app):
        """An amount-only PATCH carries no cadence and must not be graded.

        The check is skipped when either axis is absent, because "does not
        repeat" is the ABSENCE of a cadence and a partial update that omits
        the recurrence keys leaves the stored one alone.  Refusing here would
        make every amount edit unsavable.
        """
        with app.app_context():
            loaded = TemplateUpdateSchema().load(
                {"default_amount": "10.00"}, partial=True,
            )

            assert "recurrence_unit" not in loaded
