"""Transaction-entry create / update validation schemas."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
)

from app.schemas.validation._helpers import BaseSchema


class EntryCreateSchema(BaseSchema):
    """Validates POST data for creating a transaction entry.

    Requires amount (>= 0.01), description (1--200 chars), and
    entry_date.  is_credit defaults to False.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    description = fields.String(
        required=True, validate=validate.Length(min=1, max=200),
    )
    entry_date = fields.Date(required=True)
    is_credit = fields.Boolean(load_default=False)


class EntryUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transaction entry.

    All fields optional for partial updates.  When present, the same
    validation rules as EntryCreateSchema apply.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    description = fields.String(validate=validate.Length(min=1, max=200))
    entry_date = fields.Date()
    is_credit = fields.Boolean()

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))
