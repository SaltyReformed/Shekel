"""Ad-hoc transaction and mark-done validation schemas."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    _reject_envelope_on_income,
)


class TransactionUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transaction.

    ``version_id`` is the optimistic-locking counter from the row at
    the moment the cell or popover was rendered.  The route handler
    compares the submitted value against ``Transaction.version_id``
    and short-circuits with 409 Conflict if they differ -- a stale-
    form check that catches the Tab-1/Tab-2 race even when the two
    requests are sequential rather than truly concurrent.  Optional
    so callers without a way to plumb the version through still
    pass validation; in that case only the SQLAlchemy
    ``version_id_col`` race detection applies, which catches the
    truly-concurrent case at flush time.  See commit C-18 of the
    2026-04-15 security remediation plan.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True, validate=validate.Range(min=0))
    status_id = fields.Integer()
    pay_period_id = fields.Integer()
    category_id = fields.Integer()
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)
    # Ad-hoc tracking / visibility flags.  Deliberately NO load_default:
    # this schema is shared across the quick-edit, full-edit, and inline
    # PATCH forms, and only the full-edit popover renders these controls
    # (for ad-hoc rows).  Without a default, a PATCH that omits them
    # leaves the columns untouched, so a quick-edit cannot silently
    # clear an ad-hoc row's flags.  The popover uses a checkbox + hidden
    # "false" field so an explicit true/false is always submitted when
    # the controls are present.
    is_envelope = fields.Boolean()
    companion_visible = fields.Boolean()
    paid_at = fields.DateTime(allow_none=True, dump_only=True)
    version_id = fields.Integer(validate=validate.Range(min=1))


class TransactionCreateSchema(BaseSchema):
    """Validates POST data for creating an ad-hoc transaction."""

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True, validate=validate.Range(min=0))
    account_id = fields.Integer(required=True)
    pay_period_id = fields.Integer(required=True)
    scenario_id = fields.Integer(required=True)
    category_id = fields.Integer(required=True)
    transaction_type_id = fields.Integer(required=True)
    status_id = fields.Integer()
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)
    # Ad-hoc tracking / visibility flags.  load_default=False so a create
    # that omits them (e.g. the Add Transaction modal) defaults to off,
    # which is the correct baseline for a brand-new transaction.
    is_envelope = fields.Boolean(load_default=False)
    companion_visible = fields.Boolean(load_default=False)

    @validates_schema
    def validate_envelope_only_on_expense(self, data, **kwargs):
        """Reject ``is_envelope=True`` on an ad-hoc income transaction."""
        _reject_envelope_on_income(
            data, "Purchase tracking is only available for expenses."
        )


class InlineTransactionCreateSchema(BaseSchema):
    """Validates POST data for inline transaction creation from the grid.

    Unlike TransactionCreateSchema, the name field is auto-derived from
    the category so it is not required from the user.
    """

    estimated_amount = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True, validate=validate.Range(min=0))
    account_id = fields.Integer(required=True)
    category_id = fields.Integer(required=True)
    pay_period_id = fields.Integer(required=True)
    transaction_type_id = fields.Integer(required=True)
    scenario_id = fields.Integer(required=True)
    status_id = fields.Integer()
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    # Ad-hoc tracking / visibility flags.  load_default=False so the
    # quick-create form (which omits these controls) defaults to off.
    is_envelope = fields.Boolean(load_default=False)
    companion_visible = fields.Boolean(load_default=False)

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    @validates_schema
    def validate_envelope_only_on_expense(self, data, **kwargs):
        """Reject ``is_envelope=True`` on an ad-hoc income transaction."""
        _reject_envelope_on_income(
            data, "Purchase tracking is only available for expenses."
        )


class MarkDoneSchema(BaseSchema):
    """Validates POST data for the mark-done status route.

    Used by ``transactions.mark_done`` (both transfer-shadow and
    regular branches) to replace the raw
    ``Decimal(request.form.get("actual_amount"))`` parse the route
    previously used.  Marshmallow's Decimal field rejects malformed
    numeric input with a clean field-level 400 instead of the route's
    catch-and-translate 400, and the ``Range(min=0)`` validator is
    the schema-tier counterpart to the DB CHECK
    ``actual_amount IS NULL OR actual_amount >= 0`` on
    ``budget.transactions.actual_amount``.

    ``allow_none=True`` matches the column's nullability so a JSON
    caller can clear the actual amount explicitly (the form path is
    already handled by ``BaseSchema``'s EXCLUDE policy plus the
    routes' "if value present" check on the loaded result).  The
    routes treat a missing ``actual_amount`` key as "leave the column
    untouched" rather than "clear it" -- mark-done with no body must
    not nullify a previously recorded actual amount.  Audit
    references: F-042 / F-162 / commit C-27 of the 2026-04-15
    security remediation plan.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields stay missing.

        HTML forms always submit every <input> element, including
        empty ones, as empty strings.  Without this hook, an
        unfilled ``actual_amount`` field would arrive as ``""`` and
        fail Decimal coercion -- defeating the point of replacing
        the inline try/except.
        """
        return {k: v for k, v in data.items() if v != ""}

    actual_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=Decimal("0")),
    )
