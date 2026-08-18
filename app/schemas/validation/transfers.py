"""Transfer-template and ad-hoc transfer validation schemas."""


from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app.schemas.validation._helpers import (
    EFFECTIVE_DATE_MAX,
    EFFECTIVE_DATE_MIN,
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
)
from app.schemas.validation._recurrence import RecurrenceFormFieldsMixin


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


class TransferTemplateCreateSchema(RecurrenceFormFieldsMixin, BaseSchema):
    """Validates POST data for creating a transfer template."""

    # **The ONE create schema that may not require a first occurrence**, and
    # the exception is the DESTINATION rather than a relaxation (plan step
    # R7c-b, developer ruling 2026-08-15).  This form offers every active
    # account as a destination, so a recurring LOAN PAYMENT can be created
    # here -- and a loan payment's first occurrence is the loan's first
    # contractual installment, which the app derives and the form therefore
    # renders locked.  A locked control posts nothing.
    #
    # The rule is not dropped, it MOVED to where the destination is known:
    # ``_transfer_creation_helpers.settle_first_occurrence`` derives it for a
    # loan and refuses a cadence without one for anything else, with this
    # module's own ``RECURRENCE_NEEDS_A_START``.  A schema never learns which
    # accounts are loans, which is the same reason the update path's one
    # authoring branch is refused in its route.
    #
    # ``TemplateCreateSchema`` keeps the requirement: a transaction template
    # has no destination account and can never be a loan payment.
    recurrence_start_is_required = False

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    from_account_id = RowId(required=True)
    to_account_id = RowId(required=True)
    category_id = RowId(required=True)

    # Every recurrence control this form submits is on
    # :class:`~app.schemas.validation._recurrence.RecurrenceFormFieldsMixin`.
    # ``due_day_of_month`` is the transaction form's alone.

    # The pay period a NON-REPEATING transfer lands in
    # (``_instances._materialize_one_time_transfer``), and the transfer form's
    # alone.  It is NOT a recurrence control and has not been one since plan
    # step R7b-4: it sat on the shared mixin while ONE ``<select>`` meant two
    # things -- "First paycheck" for a repeating definition and "Pay period"
    # for a one-time transfer, relabelled by script on every unit change --
    # and that step gave the first meaning its own DATE control.  What is left
    # is a question only this kind can ask, so it is declared where that kind
    # is.  A transaction template that does not repeat generates nothing and
    # waits for the user; a transfer that does not repeat still moves money
    # exactly once, and this says when.
    start_period_id = RowId()

    @validates_schema
    def validate_different_accounts(self, data, **kwargs):
        """Reject a transfer whose source and destination are the same account."""
        _reject_same_account_transfer(data)


class TransferTemplateUpdateSchema(TransferTemplateCreateSchema):
    """Validates PUT data for updating a transfer template.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    # An UPDATE may omit the first occurrence, and the omission MEANS
    # something: "leave the stored one alone".  See
    # ``RecurrenceFormFieldsMixin.validate_recurrence_states_a_start`` for the
    # ruling and for where the one authoring branch of an update is refused
    # instead.  It is what lets a loan payment -- whose bound the app derives,
    # and whose control therefore renders disabled -- be renamed at all.
    recurrence_start_is_required = False

    # Override -- all fields optional for update.
    name = fields.String(validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    from_account_id = RowId()
    to_account_id = RowId()
    category_id = RowId(allow_none=True)

    # The date this amount takes effect: STORED as a version of the
    # template's amount (plan step X-au-a) and, in the same value, the
    # bound the regeneration sweeps from.
    #
    # Bounded because an HTML date input accepts a four-digit-year typo
    # and the consequence is permanent: an adversarial review submitted
    # ``0202-08-11`` and it became the series' EARLIEST version, which
    # anchors every date before the series and which the withdrawal door
    # refuses to remove.  The window matches the tax-config year bound
    # (``routes/salary/tax_config.py``), and
    # ``ck_template_amount_versions_effective_date_range`` mirrors it at
    # the storage tier for raw-SQL writers.
    effective_from = fields.Date(
        validate=validate.Range(
            min=EFFECTIVE_DATE_MIN, max=EFFECTIVE_DATE_MAX,
        ),
    )

    # Optimistic-locking pin (commit C-18).
    version_id = RowId(validate=validate.Range(min=1))


class TransferCreateSchema(BaseSchema):
    """Validates POST data for creating an ad-hoc transfer."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    from_account_id = RowId(required=True)
    to_account_id = RowId(required=True)
    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    pay_period_id = RowId(required=True)
    scenario_id = RowId(required=True)
    name = fields.String(validate=validate.Length(max=200))
    category_id = RowId(required=True)
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
    status_id = RowId()
    pay_period_id = RowId()
    name = fields.String(validate=validate.Length(max=200))
    category_id = RowId(allow_none=True)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)
    # The civil day the money moved, for BOTH shadows (ruling R-ED, plan step
    # X-f1c).  Editable on a finalised transfer for the reason
    # ``routes/transactions/_helpers._LOCKED_EDIT_FIELDS`` carries: the locked
    # fields are budget decisions, and this is an observed fact about the bank.
    #
    # This door is not a convenience twin of the transaction one -- it is the
    # ONLY door onto the rows finding **N-181** names.  All 8 settled rows whose
    # day the X-f1b backfill had to invent from a pay period's start are
    # transfer SHADOWS (four pairs), and a shadow's full-edit popover is THIS
    # form: ``routes/transactions/forms.get_full_edit`` redirects a shadow here
    # rather than rendering the transaction popover.
    #
    # Deliberately NOT ``allow_none``: an empty input loads as ABSENT ("leave
    # the day alone"), never as a request to clear it.  Clearing it on a settled
    # transfer is refused by ``transfer_service._status.apply_settle_day_correction`` --
    # the balance walk REFUSES a settled row with no day -- and the way to
    # remove one is to revert the transfer to Projected.
    settled_on = fields.Date()

    # Optimistic-locking pin (commit C-18).
    version_id = RowId(validate=validate.Range(min=1))
