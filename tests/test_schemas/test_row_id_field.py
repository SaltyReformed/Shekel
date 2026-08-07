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

import pytest
from marshmallow import ValidationError, fields

from app.schemas.validation import _helpers
from app.schemas.validation._helpers import RowId
from app.schemas.validation.transactions import TransactionCreateSchema


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

#: Every field-class spelling in the validation package that is STRICT about
#: what names a row -- ``RowId`` and anything derived from it.
#:
#: The scan reads an AST, so it sees a TOKEN, not a class: a field declared as
#: a ``RowId`` SUBCLASS is exactly as strict as ``RowId`` and was invisible to
#: a gate that matched the one name.  ``RecurrencePatternField`` (plan step
#: R2e-2) is the first such subclass -- it layers "and the id must name a
#: cadence this application MODELS" on top of ``RowId``'s parsing rules.
#:
#: This set is an allowlist, so ``TestNoIdFieldWasMissed
#: ::test_every_strict_spelling_really_derives_from_row_id`` resolves each name
#: and asserts it IS a ``RowId`` subclass -- otherwise widening this set would
#: be a way to smuggle a lax field past the gate below.
_STRICT_ROW_ID_SPELLINGS = frozenset({"RowId", "RecurrencePatternField"})

#: Every ``fields.Integer`` in the validation package that is NOT a row id,
#: as an explicit allowlist.  **The gate below is an allowlist rather than a
#: name pattern, and an adversarial review is why.**  Its first version asked
#: whether the attribute name ended in ``_id``, which cannot see a row id
#: named anything else -- and ``recurrence_pattern`` (the primary key of a
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
    "day_of_month",
    "deductions_per_year",
    "due_day_of_month",
    "effective_month",
    "effective_year",
    "grid_default_periods",
    "inflation_effective_month",
    "interval_n",
    "keep_through_index",
    "large_transaction_threshold",
    "low_balance_threshold",
    "max_term_months",
    "merit_raise_horizon_years",
    "month_of_year",
    "months",
    "new_term_months",
    "num_periods",
    "offset_periods",
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
        import ast  # pylint: disable=import-outside-toplevel
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
                func = call.func
                name = (
                    func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", None)
                )
                found.append(
                    (path.name, node.lineno, target.id, name),
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

    def test_every_strict_spelling_really_derives_from_row_id(self):
        """The strict allowlist cannot be padded with a lax field class.

        :data:`_STRICT_ROW_ID_SPELLINGS` is what the scan accepts INSTEAD of
        ``RowId``, so without this arm the cheapest way past the gate below
        would be to add a name to that set -- a hole in exactly the shape the
        gate exists to close.  Each name is resolved against the schema
        helpers module and must be a real ``RowId`` subclass, so a strict
        spelling is strict by inheritance rather than by assertion.
        """
        for spelling in _STRICT_ROW_ID_SPELLINGS:
            field_cls = getattr(_helpers, spelling, None)
            assert field_cls is not None, (
                f"{spelling} is allowlisted as a strict row-id field class but "
                "does not exist in app.schemas.validation._helpers"
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
        integers = [f for f in found if f[3] in _LAX_INTEGER_SPELLINGS]
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
            if cls in _LAX_INTEGER_SPELLINGS
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
            if cls in _LAX_INTEGER_SPELLINGS
        }
        stale = sorted(_NON_ROW_ID_INTEGERS - declared)
        assert stale == [], (
            "these names are allowlisted as non-id integers but no longer "
            f"name a plain Integer field; remove them: {stale}"
        )
