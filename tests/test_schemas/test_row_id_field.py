"""Every submitted ``*_id`` field reads one spelling of one id.

Plan step X-ae / finding N-141.  ``marshmallow.fields.Integer`` is crash-safe
-- it catches the ``ValueError`` -- but it is as lax as ``int()`` about what it
will read, so before :class:`~app.schemas.validation._helpers.RowId` the row-id
declarations in ``app/schemas/validation`` accepted seven spellings of "the row
I mean", two of which name no row at all.  **74 declarations now use ``RowId``**
(75 until plan step X-f1c3c deleted ``AnchorUpdateSchema.version_id`` with the
optimistic lock that read it -- ruling R-EN)
-- 73 named ``*_id`` plus the two ``recurrence_pattern`` fields, which are
``ref.recurrence_patterns`` primary keys under a name the first version of the
gate below could not see.

The completeness arm at the bottom is the one that matters most: it is what
stops the 76th declaration from being written with the lax field.
"""

import ast

import pytest
from marshmallow import ValidationError, fields

from app.schemas.validation import _helpers, _recurrence
from app.schemas.validation._helpers import RowId
from app.schemas.validation.transactions import TransactionCreateSchema


#: Field types that CONTAIN another field rather than declaring one
#: themselves.  ``fields.List(RowId())`` is a row-id declaration and
#: ``fields.List(fields.Integer())`` is a lax one, and both scanners below read
#: the OUTER callee -- so without unwrapping, a container is a hole exactly the
#: size of this gate.  Added at plan step ``bank_import:X-f6a-2``, which
#: declared the package's first ``fields.List`` of ids and was caught by the
#: completeness arm rather than by the sweep the arm exists to protect.
_CONTAINER_FIELD_SPELLINGS = frozenset({"List", "Tuple"})


def _field_callee(call):
    """Return the field type a declaration's right-hand side really builds.

    Reads the callee token, then UNWRAPS a container: a ``fields.List`` is not
    a field type, it is a wrapper around one, and grading the wrapper would
    grade nothing.  Nested containers unwrap all the way down, because
    ``List(List(Integer()))`` is as lax as ``Integer()``.

    A container built with no inner field -- ``fields.List()`` is a
    ``TypeError`` at import, so this cannot arise from working code -- reports
    the container itself, which fails the completeness arm rather than passing
    silently.

    Args:
        call: The :class:`ast.Call` on the right of the assignment.

    Returns:
        The callee token as a string, or ``None`` for a callee shape neither
        scanner recognises (which the completeness arm then reports).
    """
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in _CONTAINER_FIELD_SPELLINGS and call.args:
        inner = call.args[0]
        if isinstance(inner, ast.Call):
            return _field_callee(inner)
    return name


class TestTheFieldRefusesWhatIntegerAccepted:
    """The measured gap between ``fields.Integer`` and a row id."""

    #: Each spelling ``fields.Integer`` reads as an id, with what it reads it
    #: as.  Kept as data so the "Integer really did accept this" premise and
    #: the "RowId refuses it" assertion cannot drift apart.
    LAX_SPELLINGS = (
        ("١٢", 12),      # Arabic-Indic: a whole second alphabet of ids
        ("１２", 12),     # Fullwidth
        (" 12 ", 12),    # int() strips surrounding whitespace
        ("+12", 12),     # int() accepts an explicit sign
        ("1_0", 10),     # PEP 515 underscore separators
        ("007", 7),      # unboundedly many spellings per row
        ("-5", -5),      # names no row
        ("0", 0),        # names no row
    )

    @pytest.mark.parametrize("spelling,integer_reads_it_as", LAX_SPELLINGS)
    def test_integer_accepted_it_and_row_id_does_not(
        self, spelling, integer_reads_it_as,
    ):
        """Both halves asserted together: the defect, then its closure.

        Asserting only the refusal would leave the test passing against a
        world where ``fields.Integer`` had never been lax -- and the whole
        justification for 73 declaration changes is that it was.
        """
        assert fields.Integer().deserialize(spelling) == integer_reads_it_as

        with pytest.raises(ValidationError):
            RowId().deserialize(spelling)

    def test_a_digit_run_past_the_conversion_limit_is_a_field_error(self):
        """Long ASCII digits are a validation error, not a 500.

        ``fields.Integer`` already caught this one, so the field is not
        closing a crash here -- it is giving the same answer through the
        same path as every other malformed id.
        """
        import sys  # pylint: disable=import-outside-toplevel

        oversized = "1" * (sys.get_int_max_str_digits() + 1)
        with pytest.raises(ValidationError):
            RowId().deserialize(oversized)

    def test_the_canonical_spelling_still_loads(self):
        """The id every template emits is unaffected."""
        assert RowId().deserialize("12") == 12
        assert RowId().deserialize("1") == 1

    def test_an_already_typed_integer_is_not_a_spelling(self):
        """A programmatic payload carries a number, not a rendering of one.

        Nothing to normalise, so it goes through ``Integer`` -- but the
        row-id FLOOR still applies, because a value below it names no row
        however it arrived.
        """
        assert RowId().deserialize(12) == 12
        for below_floor in (0, -5):
            with pytest.raises(ValidationError):
                RowId().deserialize(below_floor)

    def test_a_non_integral_number_is_refused_rather_than_truncated(self):
        """``1.9`` does not name row 1.

        The second adversarial review's finding: ``Integer`` TRUNCATES, so a
        JSON body of ``{"account_id": 1.9}`` named row 1 -- the same defect
        as ``"007"`` naming row 7, on the non-string path, in the field
        whose deliverable is one spelling per id.  ``1.0`` is accepted
        because it names row 1 exactly.
        """
        from decimal import Decimal  # pylint: disable=import-outside-toplevel

        for truncating in (1.9, 0.5, Decimal("3.7")):
            with pytest.raises(ValidationError):
                RowId().deserialize(truncating)
        assert RowId().deserialize(1.0) == 1

    def test_dumping_never_raises_a_raw_value_error(self):
        """The strictness is on LOAD only, and this is why it must be.

        marshmallow calls ``_format_num`` from ``_serialize`` OUTSIDE the
        ``_validated`` try/except, so a rule expressed there escapes as a
        raw ``ValueError`` when a schema dumps -- not a ``ValidationError``
        any caller is prepared for.  A first build of this field put the
        rule in ``_format_num`` and an adversarial review demonstrated it on
        ``dump(0)``.  Dumping renders the application's OWN rows; there is
        no submitted spelling to police.
        """
        from marshmallow import Schema  # pylint: disable=import-outside-toplevel

        class _Dumper(Schema):
            """A schema carrying one row-id field, for the dump path."""

            account_id = RowId()

        for value in (12, 0, -5, "007", None):
            # Must not raise; the assertion is the absence of an exception.
            _Dumper().dump({"account_id": value})

    def test_a_boolean_is_refused(self):
        """``True`` is an ``int`` subclass and would otherwise read as row 1.

        Marshmallow's own ``_validated`` short-circuits booleans ahead of
        the field, which is why this holds; asserted so a future override of
        that method cannot quietly reopen it.
        """
        for value in (True, False):
            with pytest.raises(ValidationError):
                RowId().deserialize(value)


class TestTheFieldIsWiredIntoRealSchemas:
    """The field only matters where it is actually declared."""

    def test_a_real_schema_refuses_a_respelled_account_id(self):
        """End to end through a schema a route really loads.

        The unit assertions above grade the field in isolation; this grades
        the wiring, which is the part a 73-declaration sweep can get wrong.
        """
        errors = TransactionCreateSchema().validate({
            "account_id": "١",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "name": "Test",
            "estimated_amount": "10.00",
        })
        assert "account_id" in errors

    def test_the_same_payload_loads_with_the_canonical_spelling(self):
        """The control for the test above: only the spelling differs.

        Without it, that test would pass against a schema that rejected the
        payload for some unrelated reason.
        """
        errors = TransactionCreateSchema().validate({
            "account_id": "1",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "name": "Test",
            "estimated_amount": "10.00",
        })
        assert errors == {}


#: Every source spelling of marshmallow's lax integer field.  ``fields.Int`` IS
#: ``fields.Integer`` (asserted below), but this gate reads SOURCE via the AST,
#: so it sees the token that was written -- and an adversarial review found the
#: alias invisible to a matcher that only knew one of the two names.
_LAX_INTEGER_SPELLINGS = frozenset({"Integer", "Int"})

#: Every FACTORY in the validation package that returns a lax integer field.
#:
#: **This set exists because plan step X-ad-a nearly blinded the gate.**  That
#: step gave ``pay_periods.py`` two shared field builders --
#: ``cadence_days_field`` and ``num_periods_field`` -- so the cadence and batch
#: bounds are stated once instead of six times, and registration became a fifth
#: door without adding a seventh copy.  But this gate reads SOURCE: a field
#: written ``cadence_days = cadence_days_field(...)`` carries the token
#: ``cadence_days_field``, not ``Integer``, so the scan stopped seeing three
#: real declarations.  Nothing failed loudly -- the sweep simply had three
#: fewer subjects, which is the quiet failure a completeness gate exists to
#: prevent in the code it grades and must therefore not have itself.
#:
#: A factory that returns a lax ``Integer`` is exactly as dangerous as an
#: inline one, so the scan grades the two identically and
#: ``test_every_lax_factory_really_returns_a_lax_field`` resolves each name and
#: proves what it builds.  Add a name here when you add a builder; the
#: stale-entry arm below then holds it to being real.
_LAX_INTEGER_FACTORIES = frozenset({"cadence_days_field", "num_periods_field"})

#: What the AST scan treats as "declared lax": the two marshmallow spellings
#: plus every registered factory.
_LAX_DECLARATIONS = _LAX_INTEGER_SPELLINGS | _LAX_INTEGER_FACTORIES

#: Field builders in the package that return something that is NOT an integer
#: field, so the row-id question does not arise for them.  Registered anyway,
#: because ``test_the_factories_are_the_only_unscanned_call_form`` treats an
#: unclassified builder as a hole -- and "it happens to return a String" is a
#: fact about the helper that should be asserted rather than assumed.
_NON_INTEGER_FIELD_FACTORIES = frozenset({"_auth_email_field"})

#: Every marshmallow field spelling the package declares that is not an
#: integer.
#:
#: **Exactly what the package declares, and nothing pre-authorised.**  A first
#: cut listed sixteen marshmallow types "so the set is future-proof", twelve of
#: which named nothing here -- and an adversarial review showed that generosity
#: was a hole, not foresight: ``account_id = fields.Raw()`` would have passed
#: the completeness arm below (``Raw`` is "known") AND the lax-declaration arm
#: (``Raw`` is not an Integer spelling), while ``fields.Raw`` accepts ``"007"``,
#: ``-5`` and ``0`` verbatim -- strictly worse than the ``fields.Integer`` this
#: whole gate exists to remove.  Pre-authorising a type nobody has declared is
#: the same defect as pre-authorising a NAME, which is what
#: ``test_the_allowlist_has_no_stale_entries`` refuses on
#: :data:`_NON_ROW_ID_INTEGERS`.  So this set is held to being real by
#: ``test_the_non_integer_spellings_are_all_declared``: adding a genuinely new
#: field type means declaring it and listing it in the same commit.
_NON_INTEGER_FIELD_SPELLINGS = frozenset({
    "Boolean", "Date", "Decimal", "String",
})

#: Every field-class spelling in the validation package that is STRICT about
#: what names a row -- ``RowId`` and anything derived from it.
#:
#: The scan reads an AST, so it sees a TOKEN, not a class: a field declared as
#: a ``RowId`` SUBCLASS is exactly as strict as ``RowId`` and was invisible to
#: a gate that matched the one name.  ``RecurrencePatternField`` (plan step
#: R2e-2) was the first such subclass; plan step R7b-2 replaced it with the TWO
#: below, because the form stopped posting a closed-set pattern id and started
#: authoring the two axes that pattern encoded.  Each layers "and the id must
#: name a value this application MODELS" on ``RowId``'s parsing rules, through
#: the shared ``_RefEnumField`` base.
#:
#: **The base itself is deliberately absent.**  No schema declares a field as
#: ``_RefEnumField`` -- it has no ``_member_for`` -- so listing it would
#: pre-authorise a name nothing writes, which is the same defect
#: :data:`_NON_INTEGER_FIELD_SPELLINGS` refuses one comment up.
#:
#: This set is an allowlist, so ``TestNoIdFieldWasMissed
#: ::test_every_strict_spelling_really_derives_from_row_id`` resolves each name
#: and asserts it IS a ``RowId`` subclass -- otherwise widening this set would
#: be a way to smuggle a lax field past the gate below.
_STRICT_ROW_ID_SPELLINGS = frozenset({
    "RowId", "RecurrenceUnitField", "PeriodPlacementField",
})

#: Every ``fields.Integer`` in the validation package that is NOT a row id,
#: as an explicit allowlist.  **The gate below is an allowlist rather than a
#: name pattern, and an adversarial review is why.**  Its first version asked
#: whether the attribute name ended in ``_id``, which cannot see a row id
#: named anything else -- and ``recurrence_pattern`` (then the primary key of a
#: ``ref.recurrence_patterns`` row, in two schemas) was exactly that, lax and
#: invisible to the gate written to catch it.  Inverting the question makes
#: the failure mode safe: a new NON-id integer must be named here
#: deliberately, and anything else declared as a plain ``Integer`` fails.
#:
#: Every entry is a count, a year, a month, a day-of-month, an interval, an
#: index, a threshold or an ordering -- none names a row, and several
#: legitimately accept zero, which is why they must NOT become ``RowId``.
_NON_ROW_ID_INTEGERS = frozenset({
    "anchor_staleness_days",
    "arm_adjustment_interval_months",
    "arm_first_adjustment_months",
    "cadence_days",
    "consecutive_high_years",
    "contribution_limit_year",
    # ``day_of_month`` and ``month_of_year`` LEFT this set at plan step R7c-b
    # with the schema fields themselves: a rule's first occurrence is AUTHORED
    # (ruling R-R16), so its date carries the cycle's day and its residue
    # class, and the form collects ``starts_on`` instead of restating either.
    # ``nominal_day`` below is what survives of the pair -- the 0-or-1 day a
    # short month clamped.
    "deductions_per_year",
    "due_day_of_month",
    "effective_month",
    "effective_year",
    "grid_default_periods",
    "inflation_effective_month",
    "interval_n",
    # ``keep_through_index`` LEFT this set at plan step C3-a rather than being
    # relaxed out of it: the truncate form now posts ``keep_through_period_id``,
    # a ``RowId``, because the value selects which pay periods a CASCADE
    # destroys and identity is ``id`` (finding P13).  It was the one entry here
    # that named a row while being spelled as a position.
    "large_transaction_threshold",
    "low_balance_threshold",
    # ``max_occurrences`` is a COUNT of occurrences, not a row: plan step
    # R7b-3's "Ends" control posts it beside a mode naming which shape of
    # closing bound the user chose.  ``>= 1`` here, refused again by
    # ``EndsAfterOccurrences.__post_init__``, and again by
    # ``ck_recurrence_rules_positive_max_occurrences``.
    "max_occurrences",
    "max_term_months",
    "merit_raise_horizon_years",
    "months",
    "new_term_months",
    # ``nominal_day`` is the DAY a rule means when its first occurrence's own
    # month was too short to hold it (ruling R-R3), not a row: plan step
    # R7c-b's form posts it beside ``starts_on`` only where the chosen date
    # leaves the question open.  29-31 here, refused again by
    # ``RecurrenceSpec.__post_init__`` and again by
    # ``ck_recurrence_rules_nominal_day``.
    "nominal_day",
    "num_periods",
    # ``offset_periods`` LEFT this set at plan step R7b-2 with the schema field
    # itself (defect D8): no form ever rendered an input for it, so every
    # submission carried the schema default and the update path wrote it over
    # the rule's real phase.  A stale entry here would pre-authorise a future
    # field of the same name to be lax, which is what this set's own
    # stale-entry arm refuses.
    "other_dependents",
    "pay_periods_per_year",
    "payment_day",
    "qualifying_children",
    "rolling_target_periods",
    "sort_order",
    "tax_year",
    "term_months",
})


class TestNoIdFieldWasMissed:
    """The completeness gate -- what stops the 74th lax declaration.

    A sweep is only as good as the thing that notices the next one, and this
    arc's own history is four ``isdigit()`` sites accumulating with nothing
    watching.  This reads the schema package's source and fails on any
    ``fields.Integer`` that is not in :data:`_NON_ROW_ID_INTEGERS`.
    """

    @staticmethod
    def _id_fields_by_class():
        """Return ``{declaration line: field class name}`` for every ``*_id``.

        Parsed from the AST rather than by ``grep`` so a declaration split
        across lines, or one carrying a comment, is read the same as a
        one-liner.

        Returns:
            list of ``(file, lineno, attribute, field class)`` tuples.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        package = Path(__file__).resolve().parents[2] / "app" / "schemas"
        found = []
        for path in sorted(package.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # BOTH assignment forms.  ``ast.Assign`` is what the package
                # uses today; ``ast.AnnAssign`` (``account_id: int = ...``) is
                # a legal spelling a gate written for tomorrow must not be
                # blind to -- an adversarial review's finding.
                if isinstance(node, ast.Assign):
                    target = node.targets[0]
                elif isinstance(node, ast.AnnAssign):
                    target = node.target
                else:
                    continue
                if not isinstance(target, ast.Name):
                    continue
                call = node.value
                if not isinstance(call, ast.Call):
                    continue
                found.append(
                    (path.name, node.lineno, target.id, _field_callee(call)),
                )
        return found

    def test_the_alias_really_is_the_same_lax_field(self):
        """The premise for grading ``Int`` as well as ``Integer``.

        ``fields.Int`` is not a narrower field that happens to be lax -- it
        IS ``fields.Integer``, so a declaration written with the alias has
        exactly the defect this sweep removed while reading differently in
        source.  A gate over an AST sees the token, not the class.
        """
        assert fields.Int is fields.Integer
        assert {"Integer", "Int"} == _LAX_INTEGER_SPELLINGS

    def test_every_lax_factory_really_returns_a_lax_field(self):
        """The premise for grading a shared builder as a lax declaration.

        :data:`_LAX_INTEGER_FACTORIES` widens what the scan calls "declared
        lax", and a widened allowlist is the cheapest way past a gate -- the
        same hole ``test_every_strict_spelling_really_derives_from_row_id``
        closes on the strict side.  So each registered name is resolved and
        the field it builds is asserted to be the lax ``Integer`` and NOT a
        ``RowId``: a factory that started returning ``RowId`` would belong on
        the strict side, and one that returned something else entirely would
        mean the scan is grading a token that names nothing.
        """
        from app.schemas.validation import (  # pylint: disable=import-outside-toplevel
            pay_periods,
        )

        for factory_name in _LAX_INTEGER_FACTORIES:
            factory = getattr(pay_periods, factory_name, None)
            assert factory is not None, (
                f"{factory_name} is registered as a lax field factory but does "
                "not exist in app.schemas.validation.pay_periods"
            )
            built = factory(required=True)
            assert isinstance(built, fields.Integer), (
                f"{factory_name} is graded as a lax Integer declaration but "
                f"builds a {type(built).__name__}"
            )
            assert not isinstance(built, RowId), (
                f"{factory_name} builds a RowId, so it is STRICT and belongs "
                "in _STRICT_ROW_ID_SPELLINGS rather than here"
            )

    @staticmethod
    def _class_body_field_calls():
        """Return every ``attr = <call>(...)`` written in a CLASS body.

        A narrower reader than :meth:`_id_fields_by_class`, which walks whole
        modules and so also collects local variables inside functions
        (``row_id = parse_row_id(value)``).  Those are harmless to the arms
        that filter by callee name and fatal to an arm that asks "is every
        callee here one I recognise", so the completeness question gets a
        scan scoped to what a schema field actually IS: an attribute of a
        class.

        Returns:
            list of ``(file, attribute, callee token)`` tuples.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        package = Path(__file__).resolve().parents[2] / "app" / "schemas"
        found = []
        for path in sorted(package.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign):
                        target = stmt.targets[0]
                    elif isinstance(stmt, ast.AnnAssign):
                        target = stmt.target
                    else:
                        continue
                    if not isinstance(target, ast.Name):
                        continue
                    if not isinstance(stmt.value, ast.Call):
                        continue
                    found.append((
                        path.name, target.id, _field_callee(stmt.value),
                    ))
        return found

    def test_the_class_body_scan_is_not_vacuous(self):
        """Premise: the narrower reader below is finding real declarations."""
        assert len(self._class_body_field_calls()) > 200

    def test_no_schema_field_is_built_by_an_unclassified_call(self):
        """Every field a schema declares is built by a callee this gate knows.

        The scan reads the callee TOKEN, so a field whose right-hand side is a
        call to something that is neither a marshmallow field class nor a
        registered builder is a field this gate cannot classify -- and an
        unclassifiable field is one the row-id sweep silently stops covering.
        That is not hypothetical: plan step X-ad-a added two shared builders
        to ``pay_periods.py`` and three real declarations left the sweep
        without anything failing.  Only the stale-allowlist arm noticed, and
        only because those three names happened to be allowlisted already --
        a new field would have vanished in silence.

        Failing here forces the next helper to declare which side it is on.
        """
        known = (
            _LAX_DECLARATIONS
            | _STRICT_ROW_ID_SPELLINGS
            | _NON_INTEGER_FIELD_SPELLINGS
            | _NON_INTEGER_FIELD_FACTORIES
        )
        unknown = sorted({
            (file, attr, cls)
            for file, attr, cls in self._class_body_field_calls()
            if cls not in known
        })
        assert unknown == [], (
            "these schema attributes are built by a call this gate cannot "
            "classify, so they are outside the row-id sweep.  Register the "
            "callee: _LAX_INTEGER_FACTORIES if it builds a lax Integer, "
            "_STRICT_ROW_ID_SPELLINGS if it builds a RowId, "
            f"_NON_INTEGER_FIELD_FACTORIES if it builds neither: {unknown}"
        )

    def test_the_non_integer_spellings_are_all_declared(self):
        """No field type is waved through that the package does not declare.

        The stale-entry arm :data:`_NON_ROW_ID_INTEGERS` gets, applied to the
        other registry.  A listed-but-unused type is a standing permission for
        a FUTURE field to be declared with it, unreviewed -- and at least one
        such type (``fields.Raw``) is laxer than the ``fields.Integer`` this
        gate exists to remove, so the permission is not harmless.
        """
        declared = {cls for _, _, cls in self._class_body_field_calls()}
        stale = sorted(_NON_INTEGER_FIELD_SPELLINGS - declared)
        assert stale == [], (
            "these field types are listed as declared non-integer spellings "
            "but nothing in the package declares one, so each is a standing "
            "permission for an unreviewed future field (fields.Raw, for one, "
            f"reads '007', -5 and 0 as ids).  Remove them: {stale}"
        )

    def test_a_lax_non_integer_field_type_would_be_caught(self):
        """The negative control for the arm above, on the type that motivated it.

        ``fields.Raw`` is the concrete escape the review demonstrated: laxer
        than ``Integer``, and invisible to a sweep that only looks for integer
        spellings.  Asserting it is NOT pre-authorised is what makes
        ``account_id = fields.Raw()`` fail the completeness arm rather than
        sail through it.
        """
        assert "Raw" not in _NON_INTEGER_FIELD_SPELLINGS
        assert "Raw" not in _LAX_DECLARATIONS
        assert "Raw" not in _STRICT_ROW_ID_SPELLINGS
        # And it really is lax -- the premise, not an assumption about it.
        assert fields.Raw().deserialize("007") == "007"

    def test_every_non_integer_factory_really_builds_a_non_integer(self):
        """The premise for waving a builder through as "not an integer".

        :data:`_NON_INTEGER_FIELD_FACTORIES` is the escape hatch of the arm
        above, so it needs the same treatment the other two registries get:
        each name is resolved and the field it builds is asserted NOT to be an
        integer.  Otherwise the cheapest way past the sweep would be to
        register a lax integer builder as "not an integer".
        """
        from app.schemas.validation import auth  # pylint: disable=import-outside-toplevel

        modules = {"_auth_email_field": auth}
        for factory_name in _NON_INTEGER_FIELD_FACTORIES:
            module = modules.get(factory_name)
            assert module is not None, (
                f"{factory_name} is registered as a non-integer field factory "
                "but this test does not know which module to resolve it from"
            )
            factory = getattr(module, factory_name, None)
            assert factory is not None, (
                f"{factory_name} is registered as a non-integer field factory "
                f"but does not exist in {module.__name__}"
            )
            built = factory()
            assert not isinstance(built, fields.Integer), (
                f"{factory_name} builds a {type(built).__name__}, which IS an "
                "integer field -- it belongs in _LAX_INTEGER_FACTORIES or "
                "_STRICT_ROW_ID_SPELLINGS, not here"
            )

    def test_every_strict_spelling_really_derives_from_row_id(self):
        """The strict allowlist cannot be padded with a lax field class.

        :data:`_STRICT_ROW_ID_SPELLINGS` is what the scan accepts INSTEAD of
        ``RowId``, so without this arm the cheapest way past the gate below
        would be to add a name to that set -- a hole in exactly the shape the
        gate exists to close.  Each name is resolved against the schema
        helper modules and must be a real ``RowId`` subclass, so a strict
        spelling is strict by inheritance rather than by assertion.

        **Resolved against BOTH helper modules since plan step R7c-b**, which
        moved the recurrence form's two axis fields to ``_recurrence`` when
        ``_helpers`` met the 1,000-line cap.  Searching one module made the
        gate report a MISSING class for a field that had merely moved -- which
        is the right failure (a strict spelling that resolves to nothing is
        exactly what this arm refuses), and the fix is to name where the
        spellings actually live rather than to shorten the list.
        """
        helper_modules = (_helpers, _recurrence)
        for spelling in _STRICT_ROW_ID_SPELLINGS:
            field_cls = next(
                (
                    found for found in (
                        getattr(module, spelling, None)
                        for module in helper_modules
                    ) if found is not None
                ),
                None,
            )
            assert field_cls is not None, (
                f"{spelling} is allowlisted as a strict row-id field class but "
                "exists in none of "
                + ", ".join(module.__name__ for module in helper_modules)
            )
            assert issubclass(field_cls, RowId), (
                f"{spelling} is allowlisted as strict but does not derive from "
                "RowId, so it does not inherit RowId's parsing rules"
            )

    def test_the_scan_finds_the_declarations_it_grades(self):
        """Premise: the AST walk is reading real declarations.

        Without this the assertions below pass against a parser that
        silently found nothing -- the vacuity mode this project's other
        completeness gates each close explicitly.  Both populations are
        floored, because the gate needs BOTH arms to be non-empty: one that
        it finds ``RowId`` fields at all, and one that it finds plain
        ``Integer`` fields it is choosing to allow.
        """
        found = self._id_fields_by_class()
        row_ids = [f for f in found if f[3] in _STRICT_ROW_ID_SPELLINGS]
        integers = [f for f in found if f[3] in _LAX_DECLARATIONS]
        # 74 since plan step R7b-3, down from 77 and for a reason the floor
        # has to record rather than absorb: the three recurrence declarations
        # both template schemas carried VERBATIM -- ``recurrence_unit``,
        # ``recurrence_placement``, ``start_period_id`` -- moved onto
        # ``RecurrenceFormFieldsMixin`` and are declared once.  Three fewer
        # DECLARATIONS, the same fields, and none relaxed: the mixin's are
        # still the strict spellings, which is what the arms below check.
        assert len(row_ids) >= 74, (
            f"the scan found only {len(row_ids)} strict row-id declarations; "
            "it is not reading the schema package"
        )
        assert len(integers) > 40, (
            f"the scan found only {len(integers)} plain Integer declarations; "
            "the allowlist arm would be vacuous"
        )

    def test_every_row_id_field_uses_the_row_id_type(self):
        """No row id is still declared as a plain ``fields.Integer``.

        Asked as an ALLOWLIST rather than a name pattern: anything declared
        as a plain ``Integer`` must be named in
        :data:`_NON_ROW_ID_INTEGERS` as a deliberate non-id, so a row id
        called something other than ``*_id`` cannot slip past -- which is
        exactly how ``recurrence_pattern`` did.
        """
        lax = [
            (file, lineno, attr)
            for file, lineno, attr, cls in self._id_fields_by_class()
            if cls in _LAX_DECLARATIONS
            and attr not in _NON_ROW_ID_INTEGERS
        ]
        assert lax == [], (
            "these fields are declared as the lax fields.Integer, which reads "
            "'١٢', ' 12 ', '+12', '1_0', '007', '-5' and '0' as ids. If the "
            "field names a ROW, declare it as RowId; if it is genuinely a "
            f"count/year/index, add it to _NON_ROW_ID_INTEGERS: {lax}"
        )

    def test_the_allowlist_has_no_stale_entries(self):
        """Every allowlisted name is still a declared plain ``Integer``.

        Without this the allowlist only ever grows: a name left behind after
        its field was deleted or converted would silently pre-authorise a
        FUTURE field of the same name to be lax.
        """
        declared = {
            attr for _, _, attr, cls in self._id_fields_by_class()
            if cls in _LAX_DECLARATIONS
        }
        stale = sorted(_NON_ROW_ID_INTEGERS - declared)
        assert stale == [], (
            "these names are allowlisted as non-id integers but no longer "
            f"name a plain Integer field; remove them: {stale}"
        )
