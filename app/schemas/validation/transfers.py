"""Transfer-template and ad-hoc transfer validation schemas."""


from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    _normalize_empty_inputs,
)


def _reject_same_account_transfer(data):
    """Reject a transfer whose source and destination are the same account.

    Shared cross-field rule for the transfer-template and ad-hoc transfer
    create schemas (DRY -- one implementation of the check).  A
    self-transfer moves no money and would produce two shadow legs that
    net to zero; the route surfaces the message to the user.

    Runs only when both ``from_account_id`` and ``to_account_id`` are
    present in the deserialized payload.

    Raises:
        ValidationError: If ``from_account_id`` equals ``to_account_id``.
    """
    if data.get("from_account_id") and data.get("to_account_id"):
        if data["from_account_id"] == data["to_account_id"]:
            raise ValidationError("From and To accounts must be different.")


class TransferTemplateCreateSchema(BaseSchema):
    """Validates POST data for creating a transfer template."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    from_account_id = fields.Integer(required=True)
    to_account_id = fields.Integer(required=True)
    category_id = fields.Integer(required=True)

    # Recurrence rule fields (optional -- omit for one-time / manual).
    # The value is the integer primary key of a ref.recurrence_patterns row,
    # submitted as a string via HTML form data.  Route-level code validates
    # existence via db.session.get().
    recurrence_pattern = fields.Integer(validate=validate.Range(min=1))
    interval_n = fields.Integer(validate=validate.Range(min=1))
    offset_periods = fields.Integer(validate=validate.Range(min=0))
    day_of_month = fields.Integer(validate=validate.Range(min=1, max=31))
    month_of_year = fields.Integer(validate=validate.Range(min=1, max=12))
    start_period_id = fields.Integer()
    end_date = fields.Date(allow_none=True)

    @validates_schema
    def validate_different_accounts(self, data, **kwargs):
        """Reject a transfer whose source and destination are the same account."""
        _reject_same_account_transfer(data)


class TransferTemplateUpdateSchema(TransferTemplateCreateSchema):
    """Validates PUT data for updating a transfer template.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    # Override -- all fields optional for update.
    name = fields.String(validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    from_account_id = fields.Integer()
    to_account_id = fields.Integer()
    category_id = fields.Integer(allow_none=True)

    # Date from which regeneration takes effect.
    effective_from = fields.Date()

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))


class TransferCreateSchema(BaseSchema):
    """Validates POST data for creating an ad-hoc transfer."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    from_account_id = fields.Integer(required=True)
    to_account_id = fields.Integer(required=True)
    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    pay_period_id = fields.Integer(required=True)
    scenario_id = fields.Integer(required=True)
    name = fields.String(validate=validate.Length(max=200))
    category_id = fields.Integer(required=True)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)

    @validates_schema
    def validate_different_accounts(self, data, **kwargs):
        """Reject a transfer whose source and destination are the same account."""
        _reject_same_account_transfer(data)


class TransferUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transfer (inline edit).

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    amount = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    status_id = fields.Integer()
    pay_period_id = fields.Integer()
    name = fields.String(validate=validate.Length(max=200))
    category_id = fields.Integer(allow_none=True)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))
