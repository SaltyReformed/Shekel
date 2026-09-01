"""Transaction-entry create / update validation schemas."""


from decimal import Decimal

from marshmallow import (
    ValidationError,
    fields,
    pre_load,
    validate,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
)


def _reject_zero(value: Decimal) -> None:
    """Refuse a purchase amount of exactly zero on the EDIT form.

    **The ROW's invariant, restated on the form that can give it a status
    code.**  ``ck_transaction_entries_positive_amount`` is ``amount <> 0``
    (ruling **bank_import:R-II**) and
    ``entry_service._refusals._reject_zero_amount`` is the actual gate -- it
    covers the bank-import door, which reaches the service without passing
    through any schema.  This exists because the two tiers answer with
    different HTTP: a schema failure is a 422 and a service ``ValidationError``
    is a 400, and dropping the field rule silently moved an ordinary bad input
    from one to the other.

    **It is the same courtesy/gate split the purchase DATE already uses** --
    ``purchased_on``'s ``max`` attribute on the picker beside
    ``_reject_future_purchase_date`` in the service -- rather than a second
    rule that could drift: both say ``<> 0`` and neither says anything about
    the SIGN, which is what :class:`EntryCreateSchema` alone bounds.

    Args:
        value: The submitted amount.

    Raises:
        ValidationError: When *value* is exactly zero.
    """
    if value == 0:
        raise ValidationError(
            "A purchase amount cannot be zero. Enter what the purchase cost, "
            "or what a refund returned as a negative amount."
        )


class EntryCreateSchema(BaseSchema):
    """Validates POST data for creating a transaction entry.

    Requires amount (>= 0.01), description (1--200 chars), and
    ``purchased_on``.  is_credit defaults to False.

    **The ``>= 0.01`` bound is THIS FORM's rule and not the table's** (ruling
    **bank_import:R-II**).  ``transaction_entries`` requires only ``amount
    <> 0``, because a merchant credit files as a NEGATIVE purchase; what makes
    a negative wrong HERE is that someone typed it, which is a typo rather than
    a refund.  The bank-import door does not pass through this schema and is
    the caller that legitimately writes one
    (``statement_match._create._born_purchase``), and
    :class:`EntryUpdateSchema` deliberately carries no bound at all.

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

    # **No lower bound, and its absence is the ruling** (developer
    # 2026-08-31, ruling **bank_import:R-II**).  ``EntryCreateSchema`` keeps
    # ``Range(min=0.01)`` because a negative TYPED into the add-purchase form
    # is a typo; this door edits a purchase that already exists, whose sign may
    # be one the BANK stated -- a merchant credit files as a negative purchase
    # since plan step ``bank_import:X-gj-2b``.
    #
    # Bounding it here refused an edit the owner could not avoid making: the
    # inline form renders ``value="{{ entry.amount }}"`` and submits every
    # control it holds, so a refund rendered into an input with ``min="0.01"``
    # could not be re-described or re-dated without also changing a figure the
    # owner was not trying to touch.
    #
    # **The row invariant is NOT dropped with the bound.**  ``_reject_zero``
    # below keeps zero out at 422, and
    # ``entry_service._refusals._reject_zero_amount`` is the gate under it for
    # the callers that reach the service without a schema.  What went is the
    # SIGN bound and only the sign bound.
    amount = fields.Decimal(places=2, as_string=True, validate=_reject_zero)
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
