"""The credit card's terms form (plan step credit_card:CC-2).

ONE schema for ONE door: the "Card terms" form on the cash detail page
renders every control every time, so a submit IS the whole row, and the door
creates the row when the card has none and rewrites it when it has one
(developer ruling **R-CC24**, 2026-09-18: one door, one schema).  There is no
create / update pair here on purpose -- the loan has two because its CREATE
also seeds a rate row and posts a genesis ledger, and the interest door's
every-field-optional shape needs a "required when configuring for the first
time" branch that restates the NOT NULL rule the database already holds.  The
five NOT NULL columns are REQUIRED, ``cashback_rate`` among them: the form
renders it pre-filled with ``0.00`` for a card with no terms, so a browser
always posts a value, and a submit that carries none is a crafted or truncated
POST rather than a choice -- refused, never silently written as zero over a
rate the owner stated.  The two nullable columns read an empty control as
NULL, the "not set" state the model documents.

E-28 / HIGH-06 / PA-02: both rates are validated as decimal fractions pinned
to ``[0, 1]``, matching the model's CHECKs, and the ``@pre_load`` converts the
form percent (``"2.5"``) to its fraction (``"0.025"``) so the schema's
``Range`` and the DB CHECK accept exactly the same set of values.
"""

from decimal import Decimal

from marshmallow import fields, pre_load, validate

from app.schemas.validation._helpers import (
    _DAY_OF_MONTH_RANGE,
    _NON_NEGATIVE_MONETARY,
    _RATE_FRACTION_RANGE,
    BaseSchema,
    _normalize_empty_inputs,
    _normalize_percent_fields,
)

#: A stored dollar figure that must be a real amount: above zero, under the
#: ceiling ``_NON_NEGATIVE_MONETARY`` states (read from it, so the two cannot
#: part).  The two nullable terms use it -- NULL is their "not set" state, so a
#: stored value is never zero (the model's ``IS NULL OR > 0`` CHECKs).
_POSITIVE_MONETARY = validate.Range(
    min=Decimal("0"), min_inclusive=False, max=_NON_NEGATIVE_MONETARY.max,
)


class CreditCardTermsSchema(BaseSchema):
    """Validates the card terms form: the whole ``budget.credit_card_params`` row.

    Every field maps to a column of
    :class:`~app.models.credit_card_params.CreditCardParams` by name, so the
    door can hand the loaded payload to the model as keyword arguments (create)
    or set each key on the existing row (rewrite).  ``places`` mirrors each
    column's scale (``Numeric(5, 4)`` for the rates, ``Numeric(12, 2)`` for the
    dollar figures) so a stored value round-trips to the form without loss,
    the same contract the APY and escrow-inflation fields state.
    """

    _PERCENT_FIELDS = ("min_payment_percent", "cashback_rate")

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions.

        An empty ``auto_redeem_threshold`` or ``credit_limit`` becomes ``None``
        (their "not set" state); an empty required control -- the five NOT
        NULL columns -- is dropped so the field's own "Missing data for
        required field." names it.
        """
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    statement_close_day = fields.Integer(
        required=True, validate=_DAY_OF_MONTH_RANGE,
    )
    payment_due_day = fields.Integer(
        required=True, validate=_DAY_OF_MONTH_RANGE,
    )
    min_payment_percent = fields.Decimal(
        required=True, places=4, as_string=True, validate=_RATE_FRACTION_RANGE,
    )
    min_payment_floor = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # E-12: zero is a value, not missing -- so it is TYPED, never assumed.
    # The column's own default (``0`` on both tiers) serves the ORM
    # constructor and a raw INSERT; the door serves a form that always
    # carries the control, and a submit without it is refused above.
    cashback_rate = fields.Decimal(
        required=True, places=4, as_string=True, validate=_RATE_FRACTION_RANGE,
    )
    auto_redeem_threshold = fields.Decimal(
        allow_none=True, load_default=None, places=2, as_string=True,
        validate=_POSITIVE_MONETARY,
    )
    credit_limit = fields.Decimal(
        allow_none=True, load_default=None, places=2, as_string=True,
        validate=_POSITIVE_MONETARY,
    )
