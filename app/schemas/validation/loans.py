"""Loan-params, true-up, rate-change, escrow, payoff, refinance schemas."""


from datetime import date
from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    _normalize_percent_fields,
)


class LoanParamsCreateSchema(BaseSchema):
    """Validates POST data for creating loan parameters.

    Universal max of 600 for term_months; type-specific limits are
    enforced by the route using ref.account_types.max_term_months.

    E-28 / HIGH-06 / PA-02: ``interest_rate`` is validated as a
    decimal fraction.  ``loan_params.interest_rate`` carries a DB
    CHECK ``interest_rate >= 0`` (no upper bound on the storage tier),
    but a 100% APR is the practical user-facing ceiling, so the
    schema pins the fraction to ``[0, 1]``.  The ``@pre_load``
    converts the form percent (e.g. ``"4.5"``) to its fraction
    equivalent (``"0.045"``) so the schema validates the same domain
    the database stores; ``loan_resolver`` reads the stored
    fraction directly.
    """

    _PERCENT_FIELDS = ("interest_rate",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip empty strings, then convert percent fields to fractions."""
        data = {k: v for k, v in data.items() if v != ""}
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    # F-107 / C-25: DB CHECK enforces ``original_principal > 0``;
    # schema must reject 0 too so the gap surfaces as a 400 not a 500.
    original_principal = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0"), min_inclusive=False),
    )
    current_principal = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    interest_rate = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    term_months = fields.Integer(required=True, validate=validate.Range(min=1, max=600))
    origination_date = fields.Date(required=True)
    payment_day = fields.Integer(required=True, validate=validate.Range(min=1, max=31))
    is_arm = fields.Boolean(load_default=False)
    arm_first_adjustment_months = fields.Integer(allow_none=True)
    arm_adjustment_interval_months = fields.Integer(allow_none=True)


class LoanParamsUpdateSchema(BaseSchema):
    """Validates POST data for updating loan parameters.

    All fields optional (partial update).  ``original_principal`` and
    ``origination_date`` are omitted -- not updatable after initial
    setup.  ``current_principal`` is omitted (E-18 / Commit 16): the
    column is non-authoritative seed and the displayed current
    balance is the loan resolver's output.  Users edit the balance
    by appending a :class:`LoanAnchorEvent` via the dated true-up
    form (validated by :class:`LoanAnchorTrueupSchema` below), not by
    POSTing this schema.  Stray ``current_principal`` form fields
    submitted by a stale client are silently excluded by
    :class:`BaseSchema`'s ``unknown = EXCLUDE`` policy and ignored.
    """

    _PERCENT_FIELDS = ("interest_rate",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip empty strings, then convert percent fields to fractions.

        See :class:`LoanParamsCreateSchema` for the E-28 fraction-
        domain rationale.
        """
        data = {k: v for k, v in data.items() if v != ""}
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    interest_rate = fields.Decimal(
        places=5, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    term_months = fields.Integer(validate=validate.Range(min=1, max=600))
    payment_day = fields.Integer(validate=validate.Range(min=1, max=31))
    is_arm = fields.Boolean()
    arm_first_adjustment_months = fields.Integer(allow_none=True)
    arm_adjustment_interval_months = fields.Integer(allow_none=True)


class LoanAnchorTrueupSchema(BaseSchema):
    """Validates POST data for the loan dashboard balance true-up form.

    Records a dated loan-balance assertion (E-18 decision D-C / Commit
    16) by appending a ``user_trueup`` :class:`LoanAnchorEvent`.  The
    resolver replays confirmed payments forward from this event to
    derive every loan-touching display, so a trueup is the user's way
    of saying "as of date D, the lender reports my balance is $X."

    Schema-tier validation:

    * ``anchor_date`` is required and must not be in the future --
      a trueup is a historical assertion; "the lender will say X
      next month" is not meaningful.
    * ``anchor_balance`` is required and must be ``>= 0`` -- a
      negative loan balance is not a real-world state.  The
      ``ck_loan_anchor_events_balance_nonneg`` CHECK at the storage
      tier backstops this if a future caller bypasses the schema.

    The pre-origination check (``anchor_date >= params.origination_date``)
    is enforced by the route, not the schema, because the schema does
    not have access to the loan's origination date.  Routing the
    check through the route layer keeps the schema standalone and
    keeps :class:`LoanParams` out of the schemas module's import
    graph.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    anchor_date = fields.Date(required=True)
    anchor_balance = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0")),
    )

    @validates_schema
    def validate_not_future(self, data, **kwargs):
        """Reject ``anchor_date`` strictly after today.

        Imported inline -- the module loads at app construction time
        and ``date.today()`` evaluated at import time would freeze
        the "today" floor at gunicorn boot, defeating the validator
        for any request after the boot day.  Resolving ``today``
        per-request keeps the floor live.
        """
        anchor_date = data.get("anchor_date")
        if anchor_date is not None and anchor_date > date.today():
            raise ValidationError(
                {"anchor_date": [
                    "Anchor date cannot be in the future."
                ]}
            )


class RateChangeSchema(BaseSchema):
    """Validates POST data for recording a variable-rate change.

    E-28 / HIGH-06 / PA-02: ``interest_rate`` is validated as a
    decimal fraction matching the DB ``CHECK(interest_rate >= 0 AND
    interest_rate <= 1)`` on ``rate_history``.
    """

    _PERCENT_FIELDS = ("interest_rate",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip empty strings, then convert percent fields to fractions."""
        data = {k: v for k, v in data.items() if v != ""}
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    effective_date = fields.Date(required=True)
    interest_rate = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    # Optional recorded recast P&I (principal + interest, no escrow)
    # the lender set when this rate took effect.  When provided, the
    # rate-period engine holds it constant for the period this change
    # begins, so a mid-life ARM shows the lender's exact statement
    # payment instead of the from-origination derived approximation.
    # Matches the DB CHECK ``monthly_pi IS NULL OR monthly_pi > 0``.
    monthly_pi = fields.Decimal(
        allow_none=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0"), min_inclusive=False),
    )
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))


class EscrowComponentSchema(BaseSchema):
    """Validates POST data for creating/updating an escrow component.

    E-28 / HIGH-06 / PA-02: ``inflation_rate`` is validated as a
    decimal fraction matching the DB CHECK on
    ``escrow_components.inflation_rate``
    (``IS NULL OR (>= 0 AND <= 1)``).  Nullable -- the user may omit
    the field for an escrow component with no scheduled inflation.
    """

    _PERCENT_FIELDS = ("inflation_rate",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip empty strings, then convert percent fields to fractions."""
        data = {k: v for k, v in data.items() if v != ""}
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    annual_amount = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    inflation_rate = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )



class PayoffCalculatorSchema(BaseSchema):
    """Validates POST data for payoff scenario analysis."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    mode = fields.String(required=True, validate=validate.OneOf(["extra_payment", "target_date"]))
    extra_monthly = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    target_date = fields.Date()


class RefinanceSchema(BaseSchema):
    """Validates POST data for refinance what-if calculator input.

    E-28 / HIGH-06 / PA-02: ``new_rate`` is validated as a decimal
    fraction; the ``@pre_load`` converts the form percent (e.g.
    ``"5.0"``) to its fraction equivalent (``"0.05"``) so the
    schema, the loan resolver, and the engine all agree on the
    rate's domain.  The ``new_principal`` field is optional -- when
    omitted, the route auto-calculates as
    ``current_real_principal + closing_costs``.
    """

    _PERCENT_FIELDS = ("new_rate",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip empty strings, then convert percent fields to fractions."""
        data = {k: v for k, v in data.items() if v != ""}
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    new_rate = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    new_term_months = fields.Integer(
        required=True, validate=validate.Range(min=1, max=600),
    )
    closing_costs = fields.Decimal(
        load_default=Decimal("0.00"), places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    new_principal = fields.Decimal(
        load_default=None, allow_none=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )


class LoanPaymentTransferSchema(BaseSchema):
    """Validates POST data for creating a recurring loan payment transfer.

    The source_account_id is required (the account money comes from).
    The amount is optional -- if omitted, the route uses the computed
    monthly payment (P&I + escrow).  If provided, must be positive.
    """

    source_account_id = fields.Integer(required=True, validate=validate.Range(min=1))
    amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False))
