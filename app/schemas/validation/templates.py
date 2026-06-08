"""Transaction-template create / update validation schemas."""


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


class TemplateCreateSchema(BaseSchema):
    """Validates POST data for creating a transaction template.

    Includes a cross-field rule (``validate_envelope_only_on_expense``)
    that rejects ``is_envelope=True`` when ``transaction_type_id``
    refers to an income type.  Envelope rollover semantics (period-
    bounded amounts, leftover folds into the next period via
    ``Carry Fwd``) only apply to expense categories like groceries or
    spending money.  Income flows are settled via the
    ``Projected -> Received -> Settled`` workflow and the discrete
    carry-forward path; they have no rollover.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so Marshmallow treats them as missing.

        HTML forms always submit every <input> element, even hidden ones,
        as empty strings.  Without this hook, those empty strings fail
        OneOf / Integer validation on optional fields.
        """
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    category_id = fields.Integer(required=True)
    transaction_type_id = fields.Integer(required=True)
    account_id = fields.Integer(required=True)

    # Tracking & visibility flags.
    is_envelope = fields.Boolean(load_default=False)
    companion_visible = fields.Boolean(load_default=False)

    # Recurrence rule fields (optional -- omit for one-time / manual).
    # The value is the integer primary key of a ref.recurrence_patterns row,
    # submitted as a string via HTML form data.  Route-level code validates
    # existence via db.session.get().
    recurrence_pattern = fields.Integer(validate=validate.Range(min=1))
    interval_n = fields.Integer(validate=validate.Range(min=1))
    offset_periods = fields.Integer(validate=validate.Range(min=0))
    day_of_month = fields.Integer(validate=validate.Range(min=1, max=31))
    due_day_of_month = fields.Integer(
        validate=validate.Range(min=1, max=31), allow_none=True,
    )
    month_of_year = fields.Integer(validate=validate.Range(min=1, max=12))
    start_period_id = fields.Integer()
    end_date = fields.Date(allow_none=True)

    @validates_schema
    def validate_envelope_only_on_expense(self, data, **kwargs):
        """Reject ``is_envelope=True`` on income transaction templates.

        Envelope semantics (the source of truth for the carry-forward
        ``settle-and-roll`` branch -- see
        ``docs/carry-forward-aftermath-design.md``) only make sense for
        expense categories.  An income flow that arrives late is handled
        by the existing status workflow, not by rolling unspent funds
        into the next period.

        The validator runs only when both ``is_envelope`` and
        ``transaction_type_id`` are present in the deserialized payload.
        ``TemplateUpdateSchema`` partial updates that omit
        ``transaction_type_id`` skip the schema-level check; the route
        layer falls back to the existing template's stored
        ``transaction_type_id`` (see ``_is_tracking_on_non_expense`` in
        ``app/routes/templates.py``) so the rule is enforced end-to-end.

        The error is attached to the ``is_envelope`` field for
        consistency with other cross-field validators in this module
        (e.g. ``validate_goal_mode_fields``); the route layer surfaces
        the message to the user via ``flash``.

        Raises:
            ValidationError: If ``is_envelope`` is True and
                ``transaction_type_id`` resolves to the Income type.
        """
        _reject_envelope_on_income(
            data,
            "Purchase tracking is only available for expense templates.",
        )


class TemplateUpdateSchema(TemplateCreateSchema):
    """Validates PUT data for updating a template.

    All fields optional (partial update), plus an effective date for
    recurrence regeneration.  Inherits the
    ``validate_envelope_only_on_expense`` cross-field rule from
    ``TemplateCreateSchema``; on partial updates that omit one of the
    two relevant fields, the validator returns early and the route
    layer applies the rule against the existing template's stored
    values.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    # Override -- all fields optional for update.
    name = fields.String(validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    category_id = fields.Integer()
    transaction_type_id = fields.Integer()
    account_id = fields.Integer()

    # Date from which regeneration takes effect.
    effective_from = fields.Date()

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))
