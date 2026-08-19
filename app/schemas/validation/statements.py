"""
Shekel Budget App -- Statement Import Validation Schema

The upload form's one schema.  ``docs/coding-standards.md``: *"Marshmallow
schema for every state-changing route... No manual ``request.form.get()`` with
inline try/except."*  A file upload is no exemption -- the source field is a
closed enumeration, and validating it here rather than in the route is what
keeps the route's job to ownership, the unit of work and the flash.

**The offer set is the ADAPTER REGISTRY, resolved at validation time.**  It is
not a literal list here and not a second copy of the enum: a source whose
parser has not been written must be unofferable AND unsubmittable from the same
fact, or the two drift and a tampered form reaches a parser that does not exist.
"""

from marshmallow import (
    ValidationError,
    fields,
    pre_load,
    validate,
    validates_schema,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
)
from app.services.statement_import import supported_sources


def _source_values() -> list[str]:
    """Return the source values a file may actually be imported as.

    Read through the registry rather than the enum so a member without a
    parser is refused here exactly as it is hidden on the form.

    Returns:
        The submittable ``StatementSourceEnum`` values.
    """
    return [member.value for member in supported_sources()]


class StatementUploadSchema(BaseSchema):
    """Validate the statement upload form's non-file field.

    The FILE itself is not a Marshmallow field: ``request.files`` is a
    different mapping from ``request.form``, and Marshmallow validates data
    rather than streams.  The route checks the file's presence and hands the
    bytes to the adapter, which is the only thing that can say whether they are
    a statement.

    Inherits :class:`~app.schemas.validation._helpers.BaseSchema`, whose
    ``unknown = EXCLUDE`` is what drops the form's ``csrf_token`` -- the same
    policy every other schema here takes, rather than a second spelling of it.
    """

    source = fields.String(
        required=True,
        validate=validate.OneOf(_source_values()),
        error_messages={
            "required": "Choose which kind of export this file is.",
        },
    )


#: The most ids one match may name on a side.  A bound rather than a limit
#: anyone will meet: ruling **R-FS**'s largest measured shape is a payroll
#: deposit against three rows, and the hand-build form posts a checkbox per
#: row it renders -- so without a ceiling a crafted submission could ask the
#: accept door to re-derive and settle an account's whole history in one
#: request.  Generous enough that a real statement's biggest group is nowhere
#: near it.
_MAX_MATCH_MEMBERS: int = 100


class StatementMatchSchema(BaseSchema):
    """Validate one accepted match's three id lists (plan step X-f6a-2).

    **Ids only, and that is the design rather than a minimal form.**  The
    accept door re-derives every figure and every day from the rows the ids
    name, inside the same transaction, so a stale page cannot commit a number
    the database no longer holds.  A schema that accepted an amount would be
    the channel for exactly that.

    Every list is ``required=False`` with an empty default: which of the three
    is populated depends on R-FS's shape, and the door's own
    ``_reject_empty_side`` is what refuses a submission naming nothing.  A
    schema arm refusing it too would be a second statement of one rule -- and
    the wrong one, because "at least one line AND at least one row" is a
    relation between two fields rather than a fact about either.

    **The members are :class:`~app.schemas.validation._helpers.RowId`, not
    ``fields.Integer``** (plan step X-ae, finding **N-141**).  Every one of
    these names a ROW, and ``Integer`` reads ``'١٢'``, ``' 12 '``, ``'+12'``,
    ``'1_0'``, ``'007'``, ``'-5'`` and ``'0'`` as ids -- two of which name no
    row at all.  The completeness gate in ``tests/test_schemas`` is what caught
    the first draft of this schema declaring them the lax way.
    """

    line_ids = fields.List(
        RowId(), required=False, load_default=list,
        validate=validate.Length(max=_MAX_MATCH_MEMBERS),
    )
    transaction_ids = fields.List(
        RowId(), required=False, load_default=list,
        validate=validate.Length(max=_MAX_MATCH_MEMBERS),
    )
    entry_ids = fields.List(
        RowId(), required=False, load_default=list,
        validate=validate.Length(max=_MAX_MATCH_MEMBERS),
    )


class StatementMatchReleaseSchema(BaseSchema):
    """Validate the id of the match being released.

    Separate from :class:`StatementMatchSchema` because it is a different act
    on a different object: one names a correspondence to create, the other
    names an act to delete, and a single schema carrying both would have every
    field optional and refuse nothing.
    """

    match_id = RowId(
        required=True,
        error_messages={"required": "Which match do you want to undo?"},
    )


class StatementPurchaseSchema(BaseSchema):
    """Validate one bank line becoming a purchase (plan step X-f6a-3b).

    **One line and one destination, and no figure at all.**  The amount and
    both days come from the recorded LINE inside the same transaction
    (:mod:`app.services.statement_match._create`), so a stale page cannot
    commit a number the bank did not state -- the same reason
    :class:`StatementMatchSchema` beside it carries ids only.

    **The destination arms are OPTIONAL here and exclusive at the door.**
    Which of "an envelope I already have" and "a new envelope" was chosen is a
    fact about the ACT, so the service refuses both-or-neither and this schema
    does not restate it (see :class:`StatementMatchSchema`'s own note on why a
    relation between fields is not a fact about either).  What IS a fact about
    this form is that a new envelope needs both of its own fields, which is the
    cross-field rule below.

    The ``@pre_load`` is not decoration: the destination ``<select>`` submits
    ``""`` when the owner picks "a new envelope", and ``RowId`` reads that as a
    validation error rather than as "absent".
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    line_id = RowId(
        required=True,
        error_messages={"required": "Which statement line are you recording?"},
    )
    transaction_id = RowId(required=False, load_default=None)
    #: Defaulted from what the BANK called the merchant and editable, because
    #: the bank's own words are the only description of this spending that
    #: exists.  The 200 matches ``transactions.name``.
    envelope_name = fields.String(
        required=False, load_default=None,
        validate=validate.Length(min=1, max=200),
    )
    category_id = RowId(required=False, load_default=None)

    @validates_schema
    def validate_new_envelope_is_whole(self, data, **kwargs):
        """Refuse a NEW envelope stated by halves.

        A budget line needs a name AND a category: ``transactions.category_id``
        is what every spending report groups by, and a row created without one
        would be invisible to the very analysis the purchase exists to feed.

        **It applies only when the destination select says "a new envelope"**,
        which is what an absent ``transaction_id`` means.  Asking it
        unconditionally read the always-rendered, always-prefilled name box as
        a destination -- so an owner who picked an envelope they already had
        was told their new envelope was incomplete, about a new envelope they
        had not asked for.  The form submits every control it renders; only the
        select says which arm was chosen.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's context, unused.

        Raises:
            ValidationError: When no envelope is named and the new one is
                missing its name or its category.
        """
        if data.get("transaction_id") is not None:
            return
        if data.get("envelope_name") is None or data.get("category_id") is None:
            raise ValidationError(
                "A new envelope needs both a name and a category.",
                field_name="envelope_name",
            )
