"""Tests for the shared empty-input normalization rule.

``_normalize_empty_inputs`` (``app/schemas/validation/_helpers.py``)
replaced the per-schema "drop every empty string" comprehension: an
empty submit on an ``allow_none`` field now loads as an explicit
``None`` (the form's null state -- a "-- None --" select, a cleared
date or number input) instead of vanishing from the payload.  Dropping
those keys made every nullable field unclearable from the UI, because
the update routes apply only the keys present in the loaded dict (the
deep-hunt pension salary-unlink follow-up).

These tests pin the rule through real schemas rather than calling the
helper directly, so a schema that stops routing its ``@pre_load``
through the helper fails here too.
"""

from marshmallow import fields
from werkzeug.datastructures import MultiDict

from app.schemas.validation._helpers import (
    BaseSchema,
    _normalize_empty_inputs,
)
from app.schemas.validation import (
    AccountTypeUpdateSchema,
    PensionProfileUpdateSchema,
    TransferUpdateSchema,
)


class TestEmptyInputNormalization:
    """The drop-or-None rule, exercised through representative schemas."""

    def test_empty_nullable_field_loads_as_none(self):
        """An empty submit on an allow_none field keeps the key as None.

        The pension form's "-- None --" option posts
        ``salary_profile_id=""``; the loaded payload must carry the
        explicit ``None`` so the update route's setattr loop clears the
        link (the original registered defect).
        """
        data = PensionProfileUpdateSchema().load({"salary_profile_id": ""})
        assert data == {"salary_profile_id": None}

    def test_empty_non_nullable_field_is_dropped(self):
        """An empty submit on a non-nullable field stays absent.

        ``name`` cannot be None; an untouched empty input means "not
        provided", so partial-update semantics (leave the stored value
        alone) still apply.
        """
        data = PensionProfileUpdateSchema().load({"name": ""})
        assert "name" not in data

    def test_non_empty_values_pass_through(self):
        """Non-empty values are untouched by the normalization."""
        data = PensionProfileUpdateSchema().load({
            "salary_profile_id": "7",
            "name": "State Pension",
        })
        assert data["salary_profile_id"] == 7
        assert data["name"] == "State Pension"

    def test_unknown_empty_key_is_dropped(self):
        """An empty value on an undeclared key (e.g. csrf_token) drops.

        Undeclared keys have no field to consult, so they keep the
        plain-drop behavior; non-empty unknowns are excluded later by
        ``BaseSchema``'s ``unknown = EXCLUDE`` policy as before.
        """
        data = PensionProfileUpdateSchema().load({"csrf_token": ""})
        assert data == {}

    def test_dump_only_nullable_field_stays_dropped(self):
        """A dump_only field keeps the drop behavior even when nullable.

        A ``dump_only`` field can never load a value, so mapping its empty
        submit to ``None`` would only hand the loader a key it discards; the
        key is dropped instead.

        **It is asserted against the HELPER, not through ``load()``, and that
        took two attempts** (plan step X-f1).  The case used
        ``TransactionUpdateSchema.paid_at`` -- the only ``dump_only`` field in
        the whole validation package -- and ruling R-EC deleted the field with
        the column, which left the assertion passing on ``unknown=EXCLUDE``
        rather than on the rule.  Re-pointing it at a locally-declared
        ``dump_only`` field did not fix that: with the ``not field.dump_only``
        arm DELETED the loaded payload is byte-identical, because marshmallow
        discards a ``dump_only`` key on load either way.  **The branch is
        invisible downstream of ``load()``**, so a test that goes through
        ``load()`` cannot grade it -- measured, by deleting the arm and watching
        the suite stay green.  Calling the helper directly is what makes the
        rule falsifiable.
        """

        class _DumpOnlyProbe(BaseSchema):
            """A minimal schema carrying the two field shapes under test."""

            settled_on = fields.Date(allow_none=True, dump_only=True)
            name = fields.String(allow_none=True)

        cleaned = _normalize_empty_inputs(
            _DumpOnlyProbe(), {"settled_on": "", "name": ""},
        )
        # The dump_only key is DROPPED; the ordinary nullable one beside it is
        # kept as an explicit None, so this separates the two arms rather than
        # passing on an empty payload.
        assert cleaned == {"name": None}

    def test_transfer_category_clear_loads_as_none(self):
        """The transfer full-edit "-- None --" category posts as None.

        ``transfer_service.update_transfer`` was built to accept
        ``category_id=None`` as "clear the category" (its
        ``_get_owned_category`` docstring says so); this pins the
        schema finally delivering that shape from the form's empty
        select.
        """
        data = TransferUpdateSchema().load({"category_id": ""})
        assert data == {"category_id": None}

    def test_multidict_checkbox_last_value_still_wins(self):
        """AccountTypeUpdateSchema keeps last-wins MultiDict handling.

        The account-type edit form renders hidden ``false`` BEFORE the
        checkbox, so a checked box submits ``["false", "true"]`` and
        the last value must win; the empty nullable
        ``max_term_months`` then goes through the shared rule and
        loads as an explicit ``None`` (the "remove the term limit"
        clear).
        """
        data = AccountTypeUpdateSchema().load(MultiDict([
            ("has_amortization", "false"),
            ("has_amortization", "true"),
            ("max_term_months", ""),
        ]))
        assert data["has_amortization"] is True
        assert data["max_term_months"] is None
