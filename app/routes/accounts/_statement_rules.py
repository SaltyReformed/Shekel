"""One reading of the merchant-rule form, and one door behind both surfaces.

Plan step ``bank_import:X-gf-2``.  Two surfaces render the rule control -- the
review QUEUE, for a merchant with no answer yet, and the REGISTER, where an
answer already given is changed (ruling **bank_import:R-GX**) -- and they
submit the identical form to two doors that differ only in which screen they
answer with.  What the wire means is therefore stated once, here, rather than
in whichever door was written first.

**The DOOR is here too, and pylint's cross-file ``duplicate-code`` is what
said so**: validate, map, record, commit, and turn each of the two failures
into a sentence is one story, and the two routes differ only in which surface
they re-render.  So the act returns a :class:`RuleOutcome` and the caller
renders it -- which keeps the template each surface swaps in a fact about that
surface, and keeps this module free of any opinion about either.

Route-layer module (leading underscore = route-internal), beside
:mod:`._statement_release` and for the same reason: it translates a submitted
FORM, which is an HTTP-shaped concern, and hands the service its own values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from app.exceptions import ValidationError
from app.extensions import db
from app.routes.accounts._statement_doors import refusal_sentence
from app.schemas.validation.merchant_rules import (
    ALWAYS_ASK,
    NEVER,
    NOT_SAID,
    MerchantRuleBatchSchema,
    rule_payload,
)
from app.schemas.validation.statements import NEW_ENVELOPE
from app.services.statement_match import (
    RuleAnswer,
    RuleSubmission,
    StatedRules,
    state_rules,
)

#: One schema instance, constructed at import like every sibling's.
_schema = MerchantRuleBatchSchema()

#: What a database failure tells the owner.  It names no table -- the traceback
#: goes to the log -- and it ends the way every refusal in this package does,
#: which is true because the caller owns the unit of work: a pass that could
#: not commit has written nothing.
_DB_ERROR_MESSAGE = (
    "Something went wrong saving that, and nothing was changed.  Here is "
    "where you were."
)


@dataclass(frozen=True)
class RuleOutcome:
    """What one pass over a merchant-rule form did, before it is rendered.

    **The two fields are exclusive by construction**, which is the shape
    :class:`~app.services.statement_match.PlannedRemovals` was corrected into
    for the same reason: a value carrying both would let a caller render a
    receipt for a pass that was refused, and every reader but the one that
    remembered to branch would read it as a promise.

    Attributes:
        recorded: What the door recorded, or ``None`` when nothing was.
        refusal: The one sentence explaining why nothing was recorded at all,
            or ``None``.  Distinct from a refused ITEM, which travels inside
            *recorded*: this one means the submission never reached the door.
    """

    recorded: StatedRules | None
    refusal: str | None


def record_submitted_rules(
    form, owner_id: int, account_id: int, logger: logging.Logger,
) -> RuleOutcome:
    """Record what the submitted form says about each merchant.

    **It MOVES NO MONEY and can move none** (the developer's ruling of
    2026-08-19).  A rule is read to SUGGEST a destination; the only thing that
    records a purchase is an explicit destination submitted for one specific
    line, which is what keeps ruling **R-FZ**'s *the destination select IS the
    tick* whole.

    It COMMITS, because the request's unit of work is exactly this pass: the
    caller re-renders from the state that survives, and a refusal leaves
    nothing behind.

    Args:
        form: The submitted ``MultiDict``.
        owner_id: The user the route proved owns the account.
        account_id: The account being reviewed.
        logger: The calling module's logger, so a database error is logged
            under the door the owner pressed rather than under this one -- the
            discipline :class:`~._statement_doors.StatementDoorContext` states
            for the same reason.

    Returns:
        The :class:`RuleOutcome`.
    """
    payload = rule_payload(form)
    errors = _schema.validate(payload)
    if errors:
        return RuleOutcome(recorded=None, refusal=refusal_sentence(errors))
    statements = submitted_rules(_schema.load(payload))

    try:
        recorded = state_rules(statements, owner_id, account_id)
        db.session.commit()
    except ValidationError as exc:
        db.session.rollback()
        return RuleOutcome(recorded=None, refusal=str(exc))
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "user_id=%d failed to state merchant rules on account %d",
            owner_id, account_id,
        )
        return RuleOutcome(recorded=None, refusal=_DB_ERROR_MESSAGE)
    return RuleOutcome(recorded=recorded, refusal=None)


def submitted_rules(submitted) -> "tuple[RuleSubmission, ...]":
    """Return the loaded payload as the statements the service records.

    **PUBLIC because two route modules read it** -- the queue's door and the
    register's -- which is this project's rule for a name reached from another
    module rather than borrowed through an underscore (finding **N-33**'s
    shape).

    **The wire's five values become the service's four answers, and the fifth
    becomes NOTHING** (ruling **R-GS**, plan step ``bank_import:X-gd-2``).  The
    mapping happens HERE because it is a fact about the FORM rather than about
    the domain: the service's
    :class:`~app.services.statement_match.RuleAnswer` has no member for *I have
    not said*, since not having said something is the absence of a row.

    **:data:`~app.schemas.validation.statements.NOT_SAID` is DROPPED rather
    than carried as a null answer.**  It used to travel to the door as
    ``answer=None`` and mean *withdraw*, so the door had a delete arm and an
    optional answer; there is no withdrawal now, so a submission that states
    nothing is an item with nothing in it.  Dropping it here is what lets
    :class:`~app.services.statement_match.RuleSubmission` require its answer,
    which is what makes an answer-less rule row unconstructible one tier down.
    It is most of an ordinary pass: the section submits every merchant it
    renders, and a merchant with no rule submits this.

    Args:
        submitted: What :class:`~app.schemas.validation.statements
            .MerchantRuleBatchSchema` loaded.

    Returns:
        One :class:`~app.services.statement_match.RuleSubmission` per merchant
        the section rendered AND ANSWERED FOR, in the order it rendered them.
    """
    statements = []
    for item in submitted["rules"]:
        answer = item["answer"]
        if answer == NOT_SAID:
            continue
        if answer == NEVER:
            statements.append(RuleSubmission(
                merchant_id=item["merchant_id"], answer=RuleAnswer.NEVER,
            ))
        elif answer == ALWAYS_ASK:
            statements.append(RuleSubmission(
                merchant_id=item["merchant_id"], answer=RuleAnswer.ALWAYS_ASK,
            ))
        elif answer == NEW_ENVELOPE:
            statements.append(RuleSubmission(
                merchant_id=item["merchant_id"],
                answer=RuleAnswer.NEW_ENVELOPE,
                envelope_name=item["envelope_name"],
                category_id=item["category_id"],
            ))
        else:
            statements.append(RuleSubmission(
                merchant_id=item["merchant_id"],
                answer=RuleAnswer.TEMPLATE,
                template_id=answer,
            ))
    return tuple(statements)
