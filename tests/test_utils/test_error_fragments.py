"""Unit tests for the designed error-fragment helpers.

The marker-header convention (closeout plan session 4): routes that
render a deliberate error state stamp ``Shekel-Designed-Fragment: 1`` so
the one global ``htmx:beforeSwap`` listener in ``app.js`` swaps the body
despite the 4xx/5xx status.  These tests pin the helper's response shape
and the field-error flattening used by the single-message surfaces.
"""

from app.utils.error_fragments import (
    DESIGNED_FRAGMENT_HEADER,
    designed_error,
    flatten_schema_errors,
)


class TestDesignedError:
    """Response-tuple shape of the marker wrapper."""

    def test_wraps_body_status_and_marker(self):
        """The tuple carries the body, status, and the marker header."""
        body, status, headers = designed_error("<div>oops</div>", 422)
        assert body == "<div>oops</div>"
        assert status == 422
        assert headers == {DESIGNED_FRAGMENT_HEADER: "1"}

    def test_header_name_matches_js_listener(self):
        """The header name is the one app.js reads -- a rename must touch both.

        ``app.js`` calls ``getResponseHeader("Shekel-Designed-Fragment")``;
        this assertion is the Python-side half of that contract.
        """
        assert DESIGNED_FRAGMENT_HEADER == "Shekel-Designed-Fragment"


class TestFlattenSchemaErrors:
    """Marshmallow error-dict flattening for one-line banner surfaces."""

    def test_named_fields_are_prefixed(self):
        """Each field contributes 'field: message'; fields join with ';'."""
        flat = flatten_schema_errors({
            "amount": ["Not a valid number."],
            "description": ["Missing data for required field."],
        })
        assert flat == (
            "amount: Not a valid number.; "
            "description: Missing data for required field."
        )

    def test_schema_level_errors_are_bare(self):
        """The _schema pseudo-field has no name worth showing."""
        flat = flatten_schema_errors({"_schema": ["Amounts must balance."]})
        assert flat == "Amounts must balance."

    def test_multiple_messages_per_field_join_with_spaces(self):
        """A field with two messages keeps both, space-separated."""
        flat = flatten_schema_errors({
            "amount": ["Not a valid number.", "Must be positive."],
        })
        assert flat == "amount: Not a valid number. Must be positive."
