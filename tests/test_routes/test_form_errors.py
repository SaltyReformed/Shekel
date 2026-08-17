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

Three arms, because no two of them catch the same thing:

* a STALE entry -- a key no schema declares, which can never match.  Asked of
  the schema package's AST.
* a MISSING one -- a field the recurrence schema RAISES a refusal against that
  the allowlist does not carry, so the message is dead copy.  Asked of
  ``_recurrence.py``'s AST.
* the MESSAGES themselves -- each refusal's exact sentence, driven through
  ``load()`` and compared verbatim, so a message edited in one place and not
  the other fails here.

**The middle arm arrived at plan step R7c-c, and its absence had shipped three
silent refusals**: ``interval_n`` (R7c-c's own), and ``starts_on`` and
``nominal_day`` from R7c-b.  Until then this module's docstring claimed both
arms were "asked of the schema package itself rather than of a hand-written
list" -- which was true of the stale arm and FALSE of the other, whose whole
content was the hand-written tuple below.  A list is exactly what an addition
walks past, which is why the same defect landed three more times after the
gate that was supposed to end it: the stale arm catches a RENAME and is
structurally blind to an ADDITION.

The message arm is still hand-written, and that is correct -- an exact sentence
cannot be derived from an AST.  What it no longer has to carry alone is
completeness.
"""
import ast
from pathlib import Path

import pytest
from marshmallow import ValidationError

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
    # The "Ends" bound's three, from plan step R7b-3.  They live in a
    # ``@post_load`` hook, which is why this arm LOADS rather than validating.
    (
        "Choose when this stops repeating.",
        {"recurrence_end_mode": "whenever"},
    ),
    (
        "Choose the date this stops repeating, or set it to never end.",
        {"recurrence_end_mode": "on_date"},
    ),
    (
        "Enter how many times this repeats, or set it to never end.",
        {"recurrence_end_mode": "after_occurrences"},
    ),
)

#: The three refusals that were dead copy until plan step R7c-c, as
#: ``(message, payload_builder)`` -- a callable rather than a literal because
#: each needs a ``ref`` row id, which is an app-context read.
#:
#: Separate from :data:`_MUST_BE_HEARD` for that reason alone; the arm that
#: drives them is the same.
_MUST_BE_HEARD_WITH_A_CADENCE = (
    (
        "Say how often this repeats.  Enter a number beside the unit, like 3 "
        "for every 3 months.",
        lambda: {
            "recurrence_unit": str(
                ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
            ),
            "recurrence_placement": str(
                ref_cache.period_placement_id(
                    PeriodPlacementEnum.CONTAINING_DATE,
                ),
            ),
            "starts_on": "2026-04-15",
        },
    ),
    (
        "Choose the date this first happens.  It is what the recurrence "
        "repeats from, so nothing is generated before it.",
        lambda: {
            "recurrence_unit": str(
                ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
            ),
            "recurrence_placement": str(
                ref_cache.period_placement_id(
                    PeriodPlacementEnum.CONTAINING_DATE,
                ),
            ),
            "interval_n": "1",
        },
    ),
    (
        "Apr 15, 2026 cannot mean day 30. That date already says which day "
        "this repeats on, so there is nothing else for it to mean.",
        lambda: {
            "recurrence_unit": str(
                ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
            ),
            "recurrence_placement": str(
                ref_cache.period_placement_id(
                    PeriodPlacementEnum.CONTAINING_DATE,
                ),
            ),
            "interval_n": "1",
            "starts_on": "2026-04-15",
            "nominal_day": "30",
        },
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


#: The one refusal site in ``_recurrence.py`` whose field is not a literal.
#:
#: ``compose_end_bound`` re-raises an
#: :class:`~app.services.recurrence.EndBoundInputError` with
#: ``field_name=exc.field``, so which control it names is decided by the shape
#: that failed and no AST can read it.  Its three possible values are covered by
#: :data:`_MUST_BE_HEARD`, which drives all three shapes and compares the
#: sentences -- named here so the gap is declared rather than silently missing.
_DYNAMICALLY_NAMED_FIELDS = frozenset(
    {"recurrence_end_mode", "end_date", "max_occurrences"},
)


def _fields_the_recurrence_schema_refuses():
    """Return every field name ``_recurrence.py`` raises a refusal against.

    Two spellings, because the module uses both:

    * ``ValidationError(..., field_name="X")`` -- a literal keyword;
    * ``raise ValidationError(CONSTANT)`` where ``CONSTANT`` is a module-level
      dict of ``{field: [messages]}``, which is how a refusal TWO layers raise
      is stated once (:data:`~app.schemas.validation.RECURRENCE_NEEDS_A_START`).

    Read from the AST rather than by driving every reachable payload: the
    question is "which controls can this module name", and enumerating the
    submissions that reach each raise is the hand-written list this arm exists
    to replace.

    Returns:
        set[str]: The field names, excluding the dynamic site
        (:data:`_DYNAMICALLY_NAMED_FIELDS`).
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "app" / "schemas" / "validation" / "_recurrence.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Module-level ``NAME = {"field": [...]}`` constants, so a raise that hands
    # ValidationError one of them can be resolved to the keys it carries.
    dict_constants = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) and not isinstance(
            node, ast.AnnAssign,
        ):
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            continue
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        keys = {
            key.value for key in value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        for target in targets:
            if isinstance(target, ast.Name):
                dict_constants[target.id] = keys

    fields = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "ValidationError":
            continue
        for keyword in node.keywords:
            if keyword.arg != "field_name":
                continue
            if isinstance(keyword.value, ast.Constant):
                fields.add(keyword.value.value)
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id in dict_constants:
                fields |= dict_constants[arg.id]
    return fields


class TestEveryRefusalTheSchemaRaisesIsHeard:
    """Arm two: no refusal in ``_recurrence.py`` may be dead copy.

    **The arm that fails on the R7c-b and R7c-c defects**, which is three
    refusals the previous two arms passed happily: ``starts_on``,
    ``nominal_day`` and ``interval_n`` each named a control the user could fix
    and each redirected to "correct the highlighted errors" instead, on a page
    that highlights nothing.
    """

    def test_the_scan_finds_the_refusals_it_is_meant_to(self):
        """Premise: the AST walk reads both spellings.

        Without this the assertion below passes against a parser that found
        nothing -- the vacuity mode every completeness gate in this project
        closes explicitly.  Both spellings are named because they are read by
        different branches: a literal ``field_name=`` keyword, and the keys of
        a module-level dict constant handed to ``ValidationError`` positionally.
        """
        refused = _fields_the_recurrence_schema_refuses()

        # The literal-keyword branch.
        assert "recurrence_placement" in refused
        # The dict-constant branch (``RECURRENCE_NEEDS_A_START``).
        assert "starts_on" in refused

    def test_every_refused_field_is_on_the_allowlist(self):
        """A refusal whose key is unlisted can never reach the user."""
        refused = _fields_the_recurrence_schema_refuses()

        unheard = sorted(
            refused - set(ACTIONABLE_FLASH_FIELDS) - _DYNAMICALLY_NAMED_FIELDS
        )

        assert unheard == [], (
            f"app/schemas/validation/_recurrence.py refuses these fields but "
            f"ACTIONABLE_FLASH_FIELDS does not carry them, so each message is "
            f"dead copy and the user gets the generic prompt: {unheard}"
        )


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
        """Each refusal's own message survives the allowlist.

        **It LOADS rather than validating, since plan step R7b-3**, and the
        change is what lets this arm see half the refusals it claims to cover.
        ``Schema.validate`` is ``_do_load(postprocess=False)``: it SKIPS
        ``@post_load``, where the closing bound's three refusals live -- so an
        arm built to catch "a refusal no user ever reads" was structurally
        blind to them, and all three shipped as dead copy until an adversarial
        review measured the generic prompt coming back instead.  It is the same
        asymmetry ``load_form_or_redirect`` exists to remove one layer up.
        """
        with app.app_context():
            with pytest.raises(ValidationError) as exc_info:
                TemplateCreateSchema().load(payload, partial=True)

            errors = exc_info.value.normalized_messages()
            assert errors, f"{payload} was not refused at all"
            assert flash_message_for_errors(errors) == expected

    @pytest.mark.parametrize("expected,build", _MUST_BE_HEARD_WITH_A_CADENCE)
    def test_a_cadence_refusal_is_flashed_verbatim(self, app, expected, build):
        """The three that were dead copy until plan step R7c-c.

        Each needs a WELL-FORMED cadence around the one thing under test, so
        the assertion cannot pass on a neighbouring refusal: a payload missing
        two halves would flash whichever key the allowlist happens to reach
        first, which is precisely the confusion that let these ship silent.
        """
        with app.app_context():
            with pytest.raises(ValidationError) as exc_info:
                TemplateCreateSchema().load(build(), partial=True)

            errors = exc_info.value.normalized_messages()
            assert errors, "the submission was not refused at all"
            assert flash_message_for_errors(errors) == expected

    def test_an_unauthorable_pair_is_flashed_verbatim(self, app):
        """The cross-field rule's message, on the door a crafted POST meets.

        **The unauthorable cadence has MOVED TWICE, and each move closed a
        real gap.**  It was ``(2, MONTH, covering paycheck)`` -- well defined,
        walked correctly, and with no closed-set pattern to be stored as --
        until plan step R7c-c freed the interval.  It was then
        ``(1, YEAR, first paycheck)``, refused because ``anchor_family`` had no
        first-occurrence derivation for it; plan step **R8-a** measured that
        refusal stale -- ruling **R-R16** made the first occurrence AUTHORED
        and deleted the derivation it cited -- and admitted the pair.

        What is unauthorable now is the ``WEEK`` unit, at either placement: a
        weekly occurrence is neither a payday nor a day of the month, so
        ``recurrence_engine.compute_due_date`` has nothing to date its
        generated rows from until plan step **R5**.

        The picker cannot offer it, so a user only reaches this by hand -- and
        the message must still say which control to change rather than "correct
        the highlighted errors".
        """
        with app.app_context():
            errors = TemplateCreateSchema().validate({
                "recurrence_unit": str(
                    ref_cache.recurrence_unit_id(RecurrenceUnitEnum.WEEK),
                ),
                "recurrence_placement": str(
                    ref_cache.period_placement_id(
                        PeriodPlacementEnum.CONTAINING_DATE,
                    ),
                ),
                "interval_n": "1",
            }, partial=True)

            assert errors, "an unauthorable cadence was accepted"
            assert flash_message_for_errors(errors) == (
                "That repeat schedule cannot be saved yet. Pick a different "
                "repeat unit or a different funding choice."
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
            # ``due_day_of_month`` since plan step R7c-c.  The subject has moved
            # twice now and for the same reason both times: this arm needs a
            # field that is DECLARED (an unknown key is dropped by
            # ``unknown = EXCLUDE`` and produces no error at all, which would
            # make the assertion below pass against an EMPTY dict -- the
            # tautology a moved subject leaves behind) and NOT allowlisted.
            # R7c-b deleted ``day_of_month``; R7c-c put ``nominal_day`` on the
            # allowlist, because the pair rule beside it authors a real sentence
            # naming the control.
            #
            # ``due_day_of_month`` is the right subject and not merely the next
            # one available: it is the servicer's date for a bill the cadence
            # schedules elsewhere, no layer authors a refusal against it, and 99
            # is outside its 1-31 domain -- so what comes back is marshmallow's
            # own stock Range sentence, which is the shape under test.
            errors = TemplateCreateSchema().validate(
                {"due_day_of_month": "99"}, partial=True,
            )

            assert "due_day_of_month" in errors
            assert flash_message_for_errors(errors) == GENERIC_VALIDATION_FLASH
