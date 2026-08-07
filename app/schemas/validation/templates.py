"""Transaction-template create / update validation schemas."""


from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RecurrencePatternField,
    RowId,
    _normalize_empty_inputs,
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
        """Drop empty inputs; map empties on nullable fields to None.

        HTML forms always submit every <input> element, even hidden ones,
        as empty strings.  Without this hook, those empty strings fail
        OneOf / Integer validation on optional fields.  The nullable
        fields (``due_day_of_month``, ``end_date``) keep the key as an
        explicit ``None`` so clearing them on update actually persists.
        """
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    category_id = RowId(required=True)
    transaction_type_id = RowId(required=True)
    account_id = RowId(required=True)

    # Tracking & visibility flags.
    is_envelope = fields.Boolean(load_default=False)
    companion_visible = fields.Boolean(load_default=False)

    # Recurrence rule fields.
    # The value is the integer primary key of a ref.recurrence_patterns row,
    # submitted as a string via HTML form data.  ``RecurrencePatternField``
    # rather than a bare ``RowId``: it also refuses an id no
    # ``RecurrencePatternEnum`` member names -- what the application MODELS is
    # narrower than what the table HOLDS, and the gap is a 500 (plan step
    # R2e-2).  Carrying the rule in the FIELD is what stops a third schema
    # declaring a pattern without it; ``validate.Range(min=1)`` went with the
    # move because ``RowId`` already floors at ``MIN_ROW_ID``.
    #
    # ``RowId`` underneath rather than ``Integer`` because it IS a row id
    # despite the name (plan step X-ae): an adversarial review found
    # ``Integer`` reading '١', ' 2 ', '+3', '007' and '1_0' as pattern ids,
    # and the completeness gate could not see it while that gate matched on a
    # ``_id`` SUFFIX.
    #
    # ``allow_none`` so the form's "None (one-time / manual)" option survives
    # the pre_load hook as an explicit ``None`` rather than a dropped key
    # (plan step R2e-1).  The two are different requests and the update route
    # acts on them differently -- a present ``None`` CLEARS the recurrence, an
    # absent key leaves it alone -- so collapsing them would make an amount-only
    # PATCH silently delete a template's cadence.  This is the same reason
    # ``due_day_of_month`` and ``end_date`` below are nullable.
    recurrence_pattern = RecurrencePatternField(allow_none=True)
    interval_n = fields.Integer(validate=validate.Range(min=1))
    offset_periods = fields.Integer(validate=validate.Range(min=0))
    day_of_month = fields.Integer(validate=validate.Range(min=1, max=31))
    due_day_of_month = fields.Integer(
        validate=validate.Range(min=1, max=31), allow_none=True,
    )
    month_of_year = fields.Integer(validate=validate.Range(min=1, max=12))
    start_period_id = RowId()
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
    category_id = RowId()
    transaction_type_id = RowId()
    account_id = RowId()

    # Date from which regeneration takes effect.
    effective_from = fields.Date()

    # Optimistic-locking pin (commit C-18).
    version_id = RowId(validate=validate.Range(min=1))
