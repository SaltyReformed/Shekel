"""
Shekel Budget App -- C-27 Input Validation Schema Tests

Direct schema tests for the two schemas added in commit C-27 of the
2026-04-15 security remediation plan:

  - :class:`app.schemas.validation.MarkDoneSchema` -- replaces the
    raw ``Decimal(request.form.get("settled_amount"))`` parse in
    ``transactions.mark_done`` (both branches) and (transitively)
    any future caller that sets a transaction's actual amount via
    the mark-done endpoint.

  - :class:`app.schemas.validation.DebtStrategyCalculateSchema` --
    replaces the hand-parsed ``extra_monthly``, ``strategy``, and
    ``custom_order`` checks in ``debt_strategy.calculate`` so the
    field-level Range, OneOf, and Length validators run before any
    domain logic.

Route-level tests live in ``tests/test_routes/test_debt_strategy.py``
(strategy endpoint) and the various route tests for transactions and
the dashboard; this module exercises the schemas directly so a
regression in the validation rules is caught even if the route
wiring hides the symptom.
"""

from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.schemas.validation import (
    DebtStrategyCalculateSchema,
    MarkDoneSchema,
)


# ── MarkDoneSchema ───────────────────────────────────────────────────


class TestMarkDoneSchema:
    """Direct tests for :class:`MarkDoneSchema`."""

    def test_empty_payload_yields_empty_dict(self):
        """An empty form payload deserialises to an empty dict.

        The schema treats a missing ``actual_amount`` as "leave the
        column untouched" -- the route reads the loaded dict via
        ``data.get("settled_amount")`` and only writes the column
        when the key is present.  Verifying the empty-payload UX is
        the contract that lets the routes call ``schema.load``
        unconditionally on every mark-done request.
        """
        result = MarkDoneSchema().load({})
        assert result == {}

    def test_empty_string_maps_to_explicit_none(self):
        """An empty ``actual_amount`` string loads as an explicit None.

        HTML forms submit unfilled inputs as empty strings.  Without
        the ``strip_empty_strings`` pre-load hook, those empties
        would fail Decimal coercion.  Because the field is
        ``allow_none``, the empty input maps to the same explicit
        ``None`` a JSON caller posts as ``null`` (the nullable-field
        clear rule in ``_normalize_empty_inputs``); the routes treat
        ``None`` as "leave untouched" (see
        ``test_explicit_none_passes``), so the form UX is unchanged
        while the loaded shape is now honest about the user's input.
        """
        result = MarkDoneSchema().load({"settled_amount": ""})
        assert result == {"settled_amount": None}

    def test_valid_decimal_round_trips(self):
        """Numeric ``actual_amount`` strings deserialise to Decimal."""
        result = MarkDoneSchema().load({"settled_amount": "42.50"})
        assert result["settled_amount"] == Decimal("42.50")

    def test_explicit_none_passes(self):
        """``allow_none=True`` -- explicit None is accepted (JSON path).

        The HTML form path strips empty strings before they reach
        Marshmallow, but a future JSON caller might post
        ``{"settled_amount": null}`` to clear the column.  The schema
        accepts that shape; the routes still treat ``None`` as
        "leave untouched" (pre-C-27 behaviour) so the JSON path
        does not regress the column.
        """
        result = MarkDoneSchema().load({"settled_amount": None})
        assert result == {"settled_amount": None}

    def test_negative_actual_amount_rejected(self):
        """Negative ``actual_amount`` is rejected by ``Range(min=0)``.

        Backstops the DB CHECK ``actual_amount IS NULL OR
        actual_amount >= 0`` on ``budget.transactions.settled_amount``;
        without the schema-tier check, a negative value would surface
        as a 500 IntegrityError on commit instead of a clean 400.
        """
        with pytest.raises(ValidationError) as exc:
            MarkDoneSchema().load({"settled_amount": "-10.00"})
        assert "settled_amount" in exc.value.messages

    def test_zero_actual_amount_accepted(self):
        """Zero ``actual_amount`` is accepted (DB CHECK is ``>= 0``).

        A zero actual amount is legitimate -- a $0 entry on a
        cancelled bill, an income source that produced no payout
        this period, etc.  The schema's ``min=Decimal("0")`` is
        inclusive (matches the DB CHECK semantics).
        """
        result = MarkDoneSchema().load({"settled_amount": "0.00"})
        assert result["settled_amount"] == Decimal("0.00")

    def test_an_actual_amount_the_column_cannot_hold_is_rejected(self):
        """A figure at or above ``10 ** 10`` is refused at the SCHEMA tier.

        ``budget.transactions.settled_amount`` is ``numeric(12, 2)``, so
        anything from ``10_000_000_000.00`` up raises
        ``psycopg2.errors.NumericValueOutOfRange`` at flush.  Nothing
        catches that, so before plan step X-f2-c3 it was a 500 on both
        doors this schema serves -- and on the reconcile panel, which
        commits a whole statement walk at once, it discarded every
        other tick submitted with it.

        The bound is ``_NON_NEGATIVE_MONETARY``'s ``$10,000,000``,
        well below the column's ceiling: this is a personal budget,
        so the tighter bound also catches the extra-digit typo that
        motivated the shared constant in the first place.
        """
        with pytest.raises(ValidationError) as exc:
            MarkDoneSchema().load({"settled_amount": "10000000000.00"})
        assert "settled_amount" in exc.value.messages

    def test_the_largest_accepted_actual_amount_still_loads(self):
        """The control for the bound above: its own maximum is ACCEPTED.

        Without this, tightening the range to something absurd (or to
        zero) would leave the refusal test green while refusing every
        real correction.
        """
        result = MarkDoneSchema().load({"settled_amount": "10000000.00"})
        assert result["settled_amount"] == Decimal("10000000.00")

    def test_non_numeric_actual_amount_rejected(self):
        """A non-numeric ``actual_amount`` produces Marshmallow's coercion error.

        Pre-C-27 the route caught ``InvalidOperation`` and returned
        ``"Invalid actual amount"``; post-C-27 Marshmallow's Decimal
        field rejects the value with ``"Not a valid number."`` and
        the route returns ``jsonify(errors=...)`` so HTMX form
        callers can render the per-field message.
        """
        with pytest.raises(ValidationError) as exc:
            MarkDoneSchema().load({"settled_amount": "abc"})
        assert "settled_amount" in exc.value.messages

    def test_unknown_fields_silently_ignored(self):
        """``BaseSchema`` EXCLUDE -- stray form fields do not surface as errors.

        The mark-done routes accept other form fields incidentally
        (CSRF token, HTMX target hints), and ``MarkDoneSchema``
        inherits ``BaseSchema``'s ``Meta.unknown = EXCLUDE`` so
        those incidental fields cannot break validation.  Asserts
        the schema returns just the expected key.
        """
        result = MarkDoneSchema().load({
            "settled_amount": "12.34",
            "csrf_token": "abc123",
            "hx-target": "foo",
        })
        assert result == {"settled_amount": Decimal("12.34")}


# ── DebtStrategyCalculateSchema ──────────────────────────────────────


class TestDebtStrategyCalculateSchema:
    """Direct tests for :class:`DebtStrategyCalculateSchema`."""

    def test_defaults_when_payload_empty(self):
        """An empty payload defaults to the avalanche strategy with no extra.

        Mirrors the pre-C-27 route's ``request.form.get(..., default)``
        fallbacks.  A defensive choice for a future caller that
        legitimately omits the field; the HTML form always sends
        both because the radio set has an explicit default.
        """
        result = DebtStrategyCalculateSchema().load({})
        assert result["extra_monthly"] == Decimal("0")
        assert result["strategy"] == "avalanche"
        assert result["custom_order"] is None

    def test_valid_avalanche_payload(self):
        """A valid avalanche payload deserialises to typed values."""
        result = DebtStrategyCalculateSchema().load({
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert result["extra_monthly"] == Decimal("200")
        assert result["strategy"] == "avalanche"

    def test_valid_custom_payload(self):
        """A valid custom payload preserves ``custom_order`` for the route to parse."""
        result = DebtStrategyCalculateSchema().load({
            "extra_monthly": "50",
            "strategy": "custom",
            "custom_order": "1,2,3",
        })
        assert result["strategy"] == "custom"
        assert result["custom_order"] == "1,2,3"

    def test_negative_extra_monthly_rejected(self):
        """Negative ``extra_monthly`` is rejected by the field-level Range."""
        with pytest.raises(ValidationError) as exc:
            DebtStrategyCalculateSchema().load({
                "extra_monthly": "-100",
                "strategy": "avalanche",
            })
        assert "extra_monthly" in exc.value.messages

    def test_extra_above_cap_rejected(self):
        """``extra_monthly`` above the schema cap is rejected.

        The cap is ``Decimal('1000000')`` -- well above any
        realistic debt-payoff budget.  Above the bound the value
        is rejected so an order-of-magnitude typo does not
        amplify through the payoff simulation.
        """
        with pytest.raises(ValidationError) as exc:
            DebtStrategyCalculateSchema().load({
                "extra_monthly": "9999999999",
                "strategy": "avalanche",
            })
        assert "extra_monthly" in exc.value.messages

    def test_unknown_strategy_rejected(self):
        """An unknown strategy is rejected by the field-level OneOf."""
        with pytest.raises(ValidationError) as exc:
            DebtStrategyCalculateSchema().load({
                "extra_monthly": "200",
                "strategy": "highest_balance",
            })
        assert "strategy" in exc.value.messages

    def test_custom_without_order_rejected(self):
        """Strategy=custom without custom_order is rejected at the schema tier.

        Cross-field rule
        :meth:`DebtStrategyCalculateSchema.validate_custom_requires_order`.
        Asserts the rule fires on the ``custom_order`` field so the
        UX message reads as a per-field error, matching the
        legacy "Custom strategy requires a priority order." surface.
        """
        with pytest.raises(ValidationError) as exc:
            DebtStrategyCalculateSchema().load({
                "extra_monthly": "200",
                "strategy": "custom",
            })
        assert "custom_order" in exc.value.messages
        flat_messages = exc.value.messages["custom_order"]
        assert any("priority order" in m.lower() for m in flat_messages)

    def test_custom_with_empty_order_rejected(self):
        """Empty ``custom_order`` for custom strategy is rejected.

        ``strip_empty_strings`` pre-load drops the empty string,
        leaving ``custom_order`` absent from the dict; the cross-
        field rule then reports the missing field.  Equivalent to
        :meth:`test_custom_without_order_rejected` but covers the
        empty-input UX explicitly.
        """
        with pytest.raises(ValidationError) as exc:
            DebtStrategyCalculateSchema().load({
                "extra_monthly": "200",
                "strategy": "custom",
                "custom_order": "",
            })
        assert "custom_order" in exc.value.messages

    def test_custom_order_too_long_rejected(self):
        """``custom_order`` exceeding the 500-char cap is rejected.

        ``validate.Length(min=1, max=500)`` keeps the route's
        ``int(x.strip())`` loop bounded.  Pathological payloads
        cannot reach the route's ``ValueError``-catching loop.
        """
        with pytest.raises(ValidationError) as exc:
            DebtStrategyCalculateSchema().load({
                "extra_monthly": "200",
                "strategy": "custom",
                "custom_order": ",".join(["1"] * 300),
            })
        assert "custom_order" in exc.value.messages

    def test_unknown_fields_silently_ignored(self):
        """Stray form fields do not surface as errors (BaseSchema EXCLUDE)."""
        result = DebtStrategyCalculateSchema().load({
            "extra_monthly": "100",
            "strategy": "avalanche",
            "csrf_token": "abc",
            "stray_field": "ignored",
        })
        assert result["strategy"] == "avalanche"
        assert "csrf_token" not in result
        assert "stray_field" not in result
