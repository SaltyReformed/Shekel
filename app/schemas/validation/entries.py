"""Transaction-entry create / update validation schemas."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
)


class EntryCreateSchema(BaseSchema):
    """Validates POST data for creating a transaction entry.

    Requires amount (>= 0.01), description (1--200 chars), and
    ``purchased_on``.  is_credit defaults to False.

    **``settled_on`` is deliberately absent from the CREATE door.**  It records
    the day the user SAW the purchase on a bank statement, and at the moment a
    purchase is entered there is nothing to have seen.  It is set later, either
    by ticking the purchase at a balance true-up or by editing the entry -- see
    :class:`EntryUpdateSchema`.  Offering it here would invite a value that is
    a forecast rather than an observation.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    description = fields.String(
        required=True, validate=validate.Length(min=1, max=200),
    )
    purchased_on = fields.Date(required=True)
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
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    description = fields.String(validate=validate.Length(min=1, max=200))
    purchased_on = fields.Date()
    # ``allow_none`` is what makes an emptied date input CLEAR the observation
    # rather than being dropped as "not provided" (see
    # ``_normalize_empty_inputs``).  Clearing it is a real user action: "I
    # ticked this as posted and the statement does not actually show it", which
    # must put the purchase back among the outstanding ones.
    settled_on = fields.Date(allow_none=True)
    is_credit = fields.Boolean()

    # Optimistic-locking pin (commit C-18).
    version_id = RowId(validate=validate.Range(min=1))
