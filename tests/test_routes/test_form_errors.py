"""
Shekel Budget App -- which Marshmallow error a form actually says out loud

``app.routes._form_errors.ACTIONABLE_FLASH_FIELDS`` is an allowlist of error
KEYS whose messages are flashed verbatim; everything else collapses to
:data:`~app.routes._form_errors.GENERIC_VALIDATION_FLASH`.  An allowlist keyed
on field NAMES has one failure mode, and plan step R7b-2 shipped it: the step
renamed ``recurrence_pattern`` into ``recurrence_unit`` plus
``recurrence_placement``, authored three new refusal messages, and left the
allowlist naming the old key.  Nothing broke loudly.  Every one of those
messages became dead copy, and a user whose cadence was refused got "Please
correct the highlighted errors and try again." after a redirect that highlights
nothing -- the exact asymmetry ``_form_errors``' own module docstring says it
exists to remove.

Two arms, because either alone would have missed it:

* a STALE entry -- a key no schema declares, which can never match;
* a MISSING one -- a refusal the schemas can raise that no user ever reads.

Both are asked of the schema package itself rather than of a hand-written list,
so they stay true as fields move.
"""
import ast
from pathlib import Path

import pytest

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.routes._form_errors import (
    ACTIONABLE_FLASH_FIELDS,
    GENERIC_VALIDATION_FLASH,
    flash_message_for_errors,
)
from app.schemas.validation import TemplateCreateSchema
from app.schemas.validation.transfers import TransferTemplateCreateSchema

#: The refusals that MUST reach the user, and the submission that raises each.
#:
#: Every message a recurrence field or the cross-field cadence rule can produce.
#: Written as (message, payload) pairs rather than asserted from the constants
#: so a message edited in one place and not the other fails here.
_MUST_BE_HEARD = (
    (
        "Invalid repeat unit.",
        {"recurrence_unit": "999999"},
    ),
    (
        "Invalid funding choice.",
        {"recurrence_placement": "999999"},
    ),
)


def _declared_schema_fields():
    """Return every field name declared in the validation package.

    Read from the AST rather than by instantiating each schema: the question is
    "does this name exist as a field anywhere", and a class-body scan answers it
    without needing to know which schemas exist or which need an app context.

    Returns:
        set[str]: Every ``name = <call>(...)`` attribute in a class body under
        ``app/schemas/``.
    """
    package = Path(__file__).resolve().parents[2] / "app" / "schemas"
    names = set()
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.Assign):
                    continue
                if not isinstance(statement.value, ast.Call):
                    continue
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


class TestTheAllowlistNamesRealFields:
    """Arm one: no entry can be a key nothing raises."""

    def test_the_scan_reads_real_declarations(self):
        """Premise: the AST walk is finding fields at all.

        Without this the assertion below passes against a parser that silently
        found nothing -- the vacuity mode this project's other completeness
        gates each close explicitly.
        """
        declared = _declared_schema_fields()

        assert len(declared) > 100, (
            f"the scan found only {len(declared)} declared fields; it is not "
            "reading the schema package"
        )
        assert "recurrence_unit" in declared
        assert "is_envelope" in declared

    def test_every_allowlisted_key_is_a_declared_field(self):
        """A key no schema declares can never match, so its slot is dead.

        This is the arm that fails on the R7b-2 defect: ``recurrence_pattern``
        survived the rename here and matched nothing thereafter.
        """
        declared = _declared_schema_fields()

        stale = sorted(set(ACTIONABLE_FLASH_FIELDS) - declared)

        assert stale == [], (
            f"these keys are flashed verbatim but no schema declares them, so "
            f"the message can never appear: {stale}"
        )


class TestEveryRecurrenceRefusalIsHeard:
    """Arm two: a refusal the schemas raise must reach the user verbatim."""

    @pytest.mark.parametrize("expected,payload", _MUST_BE_HEARD)
    def test_a_field_refusal_is_flashed_verbatim(self, app, expected, payload):
        """Each axis field's own message survives the allowlist."""
        with app.app_context():
            errors = TemplateCreateSchema().validate(payload, partial=True)

            assert errors, f"{payload} was not refused at all"
            assert flash_message_for_errors(errors) == expected

    def test_an_unstorable_triple_is_flashed_verbatim(self, app):
        """The cross-field rule's message, on the door a crafted POST meets.

        ``(2, MONTH, covering paycheck)`` is well defined, walks correctly, and
        has no closed-set pattern to be stored as until plan step R7c.  The
        picker cannot offer it, so a user only reaches this by hand -- and the
        message must still say which control to change rather than "correct the
        highlighted errors".
        """
        with app.app_context():
            errors = TemplateCreateSchema().validate({
                "recurrence_unit": str(
                    ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
                ),
                "recurrence_placement": str(
                    ref_cache.period_placement_id(
                        PeriodPlacementEnum.CONTAINING_DATE,
                    ),
                ),
                "interval_n": "2",
            }, partial=True)

            assert errors, "an unstorable cadence was accepted"
            assert flash_message_for_errors(errors) == (
                "That repeat schedule cannot be saved yet. Pick a different "
                "interval or a different funding choice."
            )

    def test_a_half_stated_cadence_is_flashed_verbatim(self, app):
        """A unit with no placement names the control the user must fill.

        The refusal both adversarial reviews of plan step R7b-2 found missing
        entirely; without its key on the allowlist the user would be told to
        correct highlighted errors on a form that highlights none.
        """
        with app.app_context():
            errors = TemplateCreateSchema().validate({
                "recurrence_unit": str(
                    ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD),
                ),
            }, partial=True)

            assert flash_message_for_errors(errors) == (
                "Choose which paycheck funds each occurrence."
            )

    def test_the_transfer_schema_refuses_and_is_heard_identically(self, app):
        """Both template kinds, because the asymmetry is what this module fixed.

        ``_form_errors`` exists because the same schema refusal used to explain
        itself on the transaction form and not on the transfer one.
        """
        with app.app_context():
            errors = TransferTemplateCreateSchema().validate({
                "recurrence_unit": str(
                    ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD),
                ),
            }, partial=True)

            assert flash_message_for_errors(errors) == (
                "Choose which paycheck funds each occurrence."
            )


class TestTheGenericFallbackSurvives:
    """The negative control: not every error is worth repeating."""

    def test_an_unlisted_field_error_falls_back(self, app):
        """A stock validator message stays behind the generic prompt.

        Without this, an allowlist widened to "flash the first error" would
        pass every test above -- and the module docstring's whole argument is
        that "Not a valid integer." beside a visible widget adds noise.
        """
        with app.app_context():
            errors = TemplateCreateSchema().validate(
                {"day_of_month": "99"}, partial=True,
            )

            assert "day_of_month" in errors
            assert flash_message_for_errors(errors) == GENERIC_VALIDATION_FLASH
