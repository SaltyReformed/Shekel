"""Ad-hoc transaction and mark-done validation schemas."""


from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _NON_NEGATIVE_MONETARY,
    _normalize_empty_inputs,
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
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    actual_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0),
    )
    status_id = RowId()
    pay_period_id = RowId()
    category_id = RowId()
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)
    # The civil day the money moved (ruling R-ED, plan step X-f1c).  Editable on
    # a FINALISED row, unlike every other money field: the locked set protects
    # BUDGET DECISIONS the user made (amount, period, category, due date) from
    # being rewritten after the fact, and this is not one -- it is an OBSERVED
    # FACT about their bank, which gets corrected when the statement disagrees.
    # The same line ``TransactionEntry`` draws one table over, where
    # ``purchased_on`` is guarded and ``settled_on`` is freely editable.
    #
    # Deliberately NOT ``allow_none``: an empty input loads as ABSENT (see
    # ``_normalize_empty_inputs``), i.e. "leave the day alone", never as a
    # request to clear it.  A settled row always carries the day its money
    # moved -- the way to remove one is to move the row out of the settled
    # band, which the status seam does as part of the same write.
    settled_on = fields.Date()
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
    version_id = RowId(validate=validate.Range(min=1))


class TransactionCreateSchema(BaseSchema):
    """Validates POST data for creating an ad-hoc transaction."""

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    actual_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0),
    )
    account_id = RowId(required=True)
    pay_period_id = RowId(required=True)
    scenario_id = RowId(required=True)
    category_id = RowId(required=True)
    transaction_type_id = RowId(required=True)
    # No ``status_id``: a transaction is born Projected (the route assigns it
    # unconditionally).  The only path to a settled status is the status seam
    # (``status_seam.apply_status_change``), so a submitted status is dropped by
    # ``unknown=EXCLUDE`` rather than minting a born-settled row that
    # would have no settle day, bypass ``verify_transition``, and post
    # nothing to the ledger.  Record an already-paid item by creating it
    # Projected, then marking it done.
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

    Unlike TransactionCreateSchema, the name field is OPTIONAL: the
    quick-create form offers it so an ad-hoc row can be named at the
    Tier-1 entry point (grid audit A5), and the route falls back to the
    category display name when it is omitted or left blank (the
    ``strip_empty_strings`` hook drops an empty submit, so the loaded
    payload simply lacks the key).
    """

    name = fields.String(validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    actual_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0),
    )
    account_id = RowId(required=True)
    category_id = RowId(required=True)
    pay_period_id = RowId(required=True)
    transaction_type_id = RowId(required=True)
    scenario_id = RowId(required=True)
    # No ``status_id``: born Projected (see TransactionCreateSchema).  A
    # submitted status is dropped by ``unknown=EXCLUDE``; the route assigns
    # Projected and the status seam owns every later transition.
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    # Ad-hoc tracking / visibility flags.  load_default=False so the
    # quick-create form (which omits these controls) defaults to off.
    is_envelope = fields.Boolean(load_default=False)
    companion_visible = fields.Boolean(load_default=False)

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    @validates_schema
    def validate_envelope_only_on_expense(self, data, **kwargs):
        """Reject ``is_envelope=True`` on an ad-hoc income transaction."""
        _reject_envelope_on_income(
            data, "Purchase tracking is only available for expenses."
        )


class MarkDoneSchema(BaseSchema):
    """Validates POST data for the mark-done status route.

    Used by ``transactions.mark_done`` (both transfer-shadow and
    regular branches) and, since plan step X-f2-c2, by the reconcile
    panel's amount boxes
    (``routes.accounts.reconcile._submitted_corrections``) -- the same
    question feeding the same parameter of the same verb, so one field
    declaration answers it.  Marshmallow's Decimal field rejects
    malformed numeric input with a clean field-level 400 instead of
    the route's catch-and-translate 400, and
    ``_NON_NEGATIVE_MONETARY`` is the schema-tier counterpart to the
    DB CHECK ``actual_amount IS NULL OR actual_amount >= 0`` on
    ``budget.transactions.actual_amount``.

    **Its UPPER bound is what plan step X-f2-c3 added, and the lower
    half alone was a 500.**  The column is ``numeric(12, 2)``, so a
    figure at or above ``10 ** 10`` cannot be stored: it passed the
    ``>= 0`` validator, reached the settle verb and raised
    ``psycopg2.errors.NumericValueOutOfRange`` at flush -- unhandled,
    so a 500 on a door an ordinary crafted POST reaches.  The reconcile
    panel commits a whole statement walk in ONE transaction, so a
    single unstorable box discarded every other tick submitted beside
    it.  Sharing the app's own monetary range rather than declaring a
    second one is the point: a bound this field states for itself is a
    second answer to "what is a valid money input", on a money path.
    50 of this package's 104 ``fields.Decimal`` declarations still carry
    no upper bound, which is ledger finding **N-256** rather than this
    step's to sweep.

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
        """Drop empty inputs; map empties on nullable fields to None.

        HTML forms always submit every <input> element, including
        empty ones, as empty strings.  Without this hook, an
        unfilled ``actual_amount`` field would arrive as ``""`` and
        fail Decimal coercion -- defeating the point of replacing
        the inline try/except.  Since ``actual_amount`` is
        ``allow_none``, an empty input now loads as an explicit
        ``None``, which the routes treat the same as an absent key:
        leave the column untouched (their reads are
        ``data.get``/``is not None``-guarded, so a ``None`` never
        nullifies a previously recorded actual amount).
        """
        return _normalize_empty_inputs(self, data)

    actual_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
