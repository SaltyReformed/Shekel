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
from datetime import date

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

#: A first occurrence to state beside a cadence (plan step R7c-b).
#:
#: A LITERAL rather than a clock read, and this file is the one place that is
#: right: every check here is a property of the SCHEMA -- no pay-period
#: schedule, no ``app_context`` database -- so the date is never compared
#: against the app's own today.  It only has to fall inside
#: ``app.utils.dates.CALENDAR_DATE_MIN``..``_MAX``, which the field range-checks.
_A_FIRST_OCCURRENCE = date(2026, 3, 1)

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

        **``starts_on`` rides with it since plan step R7c-b**, and stating it
        is what keeps each case testing what it names: a chosen cadence with no
        first occurrence is refused by
        ``RecurrenceFieldsMixin.validate_recurrence_states_a_start`` on the two
        CREATE schemas, so the refusal cases below would otherwise raise
        whether or not ``validate_authorable_cadence`` still worked, and the
        acceptance cases could not pass at all.

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
            "starts_on": _A_FIRST_OCCURRENCE.isoformat(),
        }
        if interval_n is not None:
            payload["interval_n"] = str(interval_n)
        return payload

    @pytest.mark.parametrize("label,schema_cls", _SCHEMAS)
    @pytest.mark.parametrize(
        "unit,placement",
        [
            # A year-scale cadence deferred onto a month's FIRST paycheck has
            # no cycle month left to name; plan step R8 owns it.
            (
                RecurrenceUnitEnum.YEAR,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
            # The WEEK unit anchors on a date this vocabulary does not collect.
            (RecurrenceUnitEnum.WEEK, PeriodPlacementEnum.CONTAINING_DATE),
            (
                RecurrenceUnitEnum.WEEK,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
        ],
    )
    def test_a_pair_the_resolver_cannot_anchor_is_refused(
        self, app, label, schema_cls, unit, placement,
    ):
        """A cadence with no first occurrence is refused on every schema.

        **The refused SET moved at plan step R7c-c.**  It was every month or
        year INTERVAL the closed pattern set could not name -- ``(2, MONTH)``,
        ``(4, MONTH)``, ``(2, YEAR)`` -- because storage was the binding
        constraint.  Every interval is authorable now, so what is left to
        refuse is a ``(unit, placement)`` pair ``anchor_family`` has no
        derivation for.  The refusal is attached to ``recurrence_unit``
        because that is the control the user changes to get out of the state.
        """
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                schema_cls().load(
                    self._payload(unit, placement, 1),
                    partial=True,
                )

            assert "recurrence_unit" in exc.value.messages, label

    @pytest.mark.parametrize("interval_n", [1, 2, 3, 6, 12])
    def test_the_first_paycheck_placement_passes_at_every_month_interval(
        self, app, interval_n,
    ):
        """Plan ledger row **D32**: the PAIR dependency is gone.

        ``(1, MONTH, first paycheck)`` was the only month cadence that could
        carry that placement, because the closed set had no quarterly or
        semi-annual twin -- so raising a Monthly First rule's interval
        silently rewrote its funding choice.  Every month interval admits it
        now, and this sweep is what says so: a validator that kept the old
        pair rule would refuse four of these five.
        """
        with app.app_context():
            loaded = TemplateCreateSchema().load(
                self._payload(
                    RecurrenceUnitEnum.MONTH,
                    PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                    interval_n,
                ),
                partial=True,
            )

            assert loaded["recurrence_unit"] is RecurrenceUnitEnum.MONTH
            assert loaded["recurrence_placement"] is (
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER
            )

    def test_the_same_placement_at_one_month_passes(self, app):
        """The neighbouring case, and the one a too-broad rule would break.

        Without this the sweeps above would pass against a validator that
        simply refused the first-paycheck placement outright -- which would
        delete a cadence live rules use.
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

    @pytest.mark.parametrize(
        ("label", "schema_cls"),
        [
            ("create", TemplateCreateSchema),
            ("update", TemplateUpdateSchema),
        ],
    )
    def test_a_unit_with_no_interval_is_refused(self, app, label, schema_cls):
        """**A named unit with no interval is bad input, not a partial update.**

        It was read as 1 until plan step R7c-c, on both schemas and in both
        write doors, and the default MOVED MONEY.  An HTML number input
        submits ``""`` when cleared, ``_normalize_empty_inputs`` DROPS the key
        (the field is not ``allow_none``), and the save then stored ``every 1``
        -- so clearing the box on a quarterly bill re-cadenced it to monthly
        and generated 12 occurrences a year where 4 were owed, across the whole
        projection, with nothing on screen saying so.

        R7c-c is what widened it past the PERIOD unit: the months ``<select>``
        it replaced could not post an empty value, so three of the four units
        were covered by the control's shape rather than by any rule.

        **Refused rather than read as "leave the stored one alone"**, which is
        what ``starts_on`` and the closing bound do with the same absence, and
        the difference is which states the control can produce.  Those two are
        DISABLED on a form whose value the app derives (a loan payment's), so
        absence is a real request there.  The interval box is disabled only
        while the definition does not repeat, so beside a named unit its
        absence is a cleared box or a crafted POST and nothing else -- two
        states, not three, which is why this is not one of plan ledger row
        **D36**'s fields and why the refusal belongs at the submission rather
        than as a fourth presence read in a route.

        On BOTH schemas, because the harm is on both: an update re-cadences a
        bill that exists, a create authors the wrong one.
        """
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                schema_cls().load(
                    self._payload(
                        RecurrenceUnitEnum.MONTH,
                        PeriodPlacementEnum.CONTAINING_DATE,
                    ),
                    partial=True,
                )

            assert "interval_n" in exc.value.messages, label

    def test_an_interval_past_the_column_domain_is_refused(self, app):
        """An interval above ``integer``'s ceiling is a 400, not a 500.

        ``budget.recurrence_rules.interval_n`` is a Postgres ``integer``, so
        ``2147483648`` reaches the flush as an unhandled
        ``psycopg2.errors.NumericValueOutOfRange``.  ``ck_recurrence_rules_
        positive_interval`` guards only the bottom of the domain; the type is
        the top, and nothing stated it -- ``is_authorable`` asks only that the
        interval be POSITIVE.

        Latent for three of the four units until plan step R7c-c: while the
        closed pattern set was the storage, ``is_authorable`` refused any MONTH
        or YEAR interval above 6, so only the paycheck unit's free box could
        reach the flush with an unstorable count.  Freeing the interval made
        every unit's box free and the accident stopped covering them.
        """
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                TemplateCreateSchema().load(
                    self._payload(
                        RecurrenceUnitEnum.PERIOD,
                        PeriodPlacementEnum.CONTAINING_DATE,
                        2147483648,
                    ),
                    partial=True,
                )

            assert "interval_n" in exc.value.messages

    def test_the_largest_storable_interval_still_passes(self, app):
        """The neighbouring value, so the bound is AT the column's domain.

        Without this the case above would pass against a bound set anywhere
        below the ceiling -- including one that refused ordinary intervals.
        ``2147483647`` is what a Postgres ``integer`` holds, and a rule that
        far out simply fires once (``_months.walk_months`` stops at the last
        month this application's calendar reaches).
        """
        with app.app_context():
            loaded = TemplateCreateSchema().load(
                self._payload(
                    RecurrenceUnitEnum.PERIOD,
                    PeriodPlacementEnum.CONTAINING_DATE,
                    2147483647,
                ),
                partial=True,
            )

            assert loaded["interval_n"] == 2147483647

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


class TestTheNominalDayMustFitTheFirstOccurrence:
    """The two-field half of ``ck_recurrence_rules_nominal_day`` (R7c-b).

    The field's own ``Range(29, 31)`` mirrors ONE of the CHECK's three
    conjuncts.  The other two are a rule over ``(starts_on, nominal_day)``
    together -- the day must EXCEED the date's own, and the date's day must be
    exactly what clamping the day into that month produces -- so no single
    field can hold them, and a schema that mirrored the domain alone let the
    pair reach :class:`~app.services.recurrence.RecurrenceSpec`, which refuses
    it as a BROKEN INVARIANT.  That is a 500 on a crafted or JavaScript-off
    POST rather than a field error naming the control.

    :func:`~app.services.recurrence.is_offerable_nominal_day` had existed for
    exactly this since plan step R7c-a and had no production caller; these
    cases are what makes it one.
    """

    #: ``(label, starts_on, nominal_day)`` the pair rule must REFUSE.
    _CONTRADICTORY = (
        # April HAS a 30th, so the 15th was never clamped by anything: a
        # nominal day here names a day the rule does not fire on.
        ("a date its month did not clamp", date(2026, 4, 15), 30),
        # April's last day IS the 30th, so 30 restates what the date carries.
        ("a day the date already states", date(2026, 4, 30), 30),
        # January holds all 31, so nothing was lost.
        ("a 31-day month, which clamps nothing", date(2026, 1, 31), 31),
    )

    @pytest.mark.parametrize("label,starts_on,nominal_day", _CONTRADICTORY)
    @pytest.mark.parametrize("schema_label,schema_cls", _SCHEMAS)
    def test_a_contradictory_pair_is_a_field_error(
        self, app, label, starts_on, nominal_day, schema_label, schema_cls,
    ):
        """Every schema that accepts the pair refuses the contradiction.

        Swept over all four because the rule is inherited: an update schema
        that lost it would let the same POST through one door and not the
        other.
        """
        with app.app_context():
            with pytest.raises(ValidationError) as exc:
                schema_cls().load(
                    {
                        "recurrence_unit": str(
                            ref_cache.recurrence_unit_id(
                                RecurrenceUnitEnum.MONTH,
                            ),
                        ),
                        "recurrence_placement": str(
                            ref_cache.period_placement_id(
                                PeriodPlacementEnum.CONTAINING_DATE,
                            ),
                        ),
                        # Stated so the ONLY thing wrong with this submission
                        # is the pair under test.  A chosen cadence with no
                        # interval is refused in its own right from plan step
                        # R7c-c, and a payload carrying two faults cannot say
                        # which one the assertion caught.
                        "interval_n": "1",
                        "starts_on": starts_on.isoformat(),
                        "nominal_day": str(nominal_day),
                    },
                    partial=True,
                )

            assert exc.value.messages.keys() == {"nominal_day"}, (
                f"{schema_label}: {label}"
            )

    def test_the_pair_the_date_DOES_leave_open_is_admitted(self, app):
        """The control case, and it is what stops the rule refusing everything.

        2026-04-30 is April's last day in a 30-day month, so "the last day of
        the month" (31) is a real second reading of it -- the whole reason the
        control exists.  Without this arm a rule that refused every nominal day
        would pass the cases above.
        """
        with app.app_context():
            loaded = TemplateCreateSchema().load(
                {
                    "recurrence_unit": str(
                        ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
                    ),
                    "recurrence_placement": str(
                        ref_cache.period_placement_id(
                            PeriodPlacementEnum.CONTAINING_DATE,
                        ),
                    ),
                    "interval_n": "1",
                    "starts_on": "2026-04-30",
                    "nominal_day": "31",
                },
                partial=True,
            )

            assert loaded["nominal_day"] == 31

    def test_a_MONTHLY_FIRST_cadence_may_carry_one_too(self, app):
        """The cadence the two day questions disagree about.

        ``Monthly First`` anchors on a PAYCHECK, so ``fires_on_day_of_month``
        answers ``False`` for it -- but its occurrences are still days of the
        month and the walk reads ``day_of_month`` for it, so the nominal day is
        meaningful.  Keying this rule on the anchor question instead would
        refuse a pair the write door accepts, which is the same disagreement
        that erased a month-end rent's ``nominal_day`` on the form.
        """
        with app.app_context():
            loaded = TemplateCreateSchema().load(
                {
                    "recurrence_unit": str(
                        ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
                    ),
                    "recurrence_placement": str(
                        ref_cache.period_placement_id(
                            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                        ),
                    ),
                    "interval_n": "1",
                    "starts_on": "2026-04-30",
                    "nominal_day": "31",
                },
                partial=True,
            )

            assert loaded["nominal_day"] == 31

    def test_an_absent_start_skips_the_rule(self, app):
        """A locked "Starts on" control posts nothing, and must stay savable.

        The update door reads ``nominal_day`` off the SAME presence key as
        ``starts_on`` -- they are one statement of when the rule fires -- so a
        submission carrying no date states no day either, and grading the pair
        against a date the payload does not have would refuse a loan payment's
        every ordinary edit.
        """
        with app.app_context():
            loaded = TemplateUpdateSchema().load(
                {
                    "recurrence_unit": str(
                        ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
                    ),
                    "recurrence_placement": str(
                        ref_cache.period_placement_id(
                            PeriodPlacementEnum.CONTAINING_DATE,
                        ),
                    ),
                    "interval_n": "1",
                    "nominal_day": "31",
                },
                partial=True,
            )

            assert loaded["nominal_day"] == 31
            assert "starts_on" not in loaded
