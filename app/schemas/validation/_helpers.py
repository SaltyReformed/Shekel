"""Shared validation primitives.

The base schema (CSRF-stripping ``unknown = EXCLUDE`` policy), the
shared range validators, the percent-to-fraction ``@pre_load`` helper
(E-28 / HIGH-06), and the cross-schema envelope-on-income rule.  Every
domain module in this package imports its base and helpers from here so
the percent-conversion and monetary-range rules have a single home."""


from decimal import Decimal, InvalidOperation

from marshmallow import (
    Schema,
    validate,
    ValidationError,
    EXCLUDE,
)

from app import ref_cache


# ── Shared range validators (commit C-24) ─────────────────────────
#
# These constants centralise the percent-format and monetary range
# rules used across more than one schema below.  Validator instances
# are immutable for the parameter set they were constructed with, so
# a single shared instance per pattern is safe; if two fields need
# different bounds (e.g. raise percentage vs FICA rate), declare a
# second constant rather than mutating an existing one.

# Percent input that maps to a decimal fraction in storage: 0..100
# percent inclusive (e.g. user-entered "6.2" for a 6.2% rate, route
# divides by 100 before persistence).  Used by FICA, state flat-rate,
# loan interest, escrow inflation, default inflation, etc.
_PERCENT_INPUT_RANGE = validate.Range(
    min=Decimal("0"), max=Decimal("100"),
)

# Monetary range for W-4 / tax credit fields where the DB CHECK is
# ``>= 0``.  10,000,000 is generous: it accommodates very large W-4
# adjustments while still rejecting an obvious typo (extra digit) on
# a routine entry.  Columns are ``Numeric(12, 2)`` so the database
# can hold up to ~10B; this validator caps the schema layer well
# below that.
_NON_NEGATIVE_MONETARY = validate.Range(
    min=Decimal("0"), max=Decimal("10000000"),
)


# E-28 / HIGH-06 (Commit 24): the percent-to-fraction divisor used by
# schemas whose form input is a percent (e.g. "4.5" for 4.5%) but whose
# storage column is the equivalent decimal fraction (Decimal("0.045")).
# Defined once here so a future tweak to the convention (no realistic
# scenario) lands in one place.
_PERCENT_DIVISOR = Decimal("100")


def _normalize_percent_fields(data, field_names):
    """Divide each named percent field in ``data`` by 100 in place.

    Used inside a schema's ``@pre_load`` to bridge the form input
    (user-facing percent like ``"4.5"``) and the storage representation
    (decimal fraction like ``"0.045"``) so the schema's ``Range``
    validator operates in the same domain as the DB ``CHECK``
    constraint (E-28).

    Args:
        data: the incoming ``@pre_load`` payload (a mapping).  Empty
            strings should already have been stripped by the caller;
            this helper assumes any present key has a non-empty value.
        field_names: tuple of percent-field names declared by the
            schema's ``_PERCENT_FIELDS`` attribute.

    Returns:
        ``data`` with each named field replaced by its decimal-fraction
        string equivalent.  Fields whose value cannot be parsed as a
        ``Decimal`` are left untouched so the field-level validator
        can surface the "Not a valid number." error rather than this
        helper masking it with a ``decimal.InvalidOperation``.  Fields
        not present in ``data`` are skipped.

    Side effects:
        mutates ``data`` in place; the returned reference is the
        same object passed in for caller convenience.
    """
    for name in field_names:
        if name not in data:
            continue
        raw = data[name]
        if raw is None:
            continue
        try:
            data[name] = str(Decimal(str(raw)) / _PERCENT_DIVISOR)
        except InvalidOperation:
            # Leave the raw value in place so the field validator
            # rejects it with its native "Not a valid number."
            # message.  Mirrors :func:`app.routes.investment._convert_percentage_inputs`
            # for narrow-catch parity.
            pass
    return data


class BaseSchema(Schema):
    """Base schema that strips CSRF tokens from form submissions."""

    class Meta:
        """Marshmallow options: silently drop unknown fields (e.g. the CSRF token)."""

        unknown = EXCLUDE


def _reject_envelope_on_income(data, message):
    """Raise ValidationError when ``is_envelope`` is set on an income payload.

    Shared cross-field rule for the template and transaction create
    schemas (DRY -- one implementation of the check).  Envelope /
    purchase-tracking semantics only apply to expenses: an income flow
    has no per-period budget to track individual purchases against, and
    the carry-forward ``settle-and-roll`` branch that envelope tracking
    feeds is expense-only.

    Runs only when both ``is_envelope`` and ``transaction_type_id`` are
    present in the deserialized payload.  Partial updates that omit the
    type skip the schema check and rely on a route-layer fallback
    against the stored type.

    Args:
        data: The deserialized schema payload.
        message: The error message to raise.  Passed in so each caller
            can phrase it for its own entity (template vs ad-hoc
            transaction) without forking the check logic.

    Raises:
        ValidationError: If ``is_envelope`` is True and
            ``transaction_type_id`` resolves to the Income type.  The
            error is attached to the ``is_envelope`` field for
            consistency with the other cross-field validators here.
    """

    if not data.get("is_envelope"):
        return
    txn_type_id = data.get("transaction_type_id")
    if txn_type_id is None:
        return
    if ref_cache.transaction_type_is_income(txn_type_id):
        raise ValidationError(message, field_name="is_envelope")
