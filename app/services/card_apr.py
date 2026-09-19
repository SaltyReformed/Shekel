"""
Shekel Budget App -- The Card's APR

A credit card's annual percentage rate rides ``budget.rate_history``
UNCHANGED (plan step **credit_card:CC-3**, design
``docs/design/credit_card_from_scratch.md`` 3.4): the table is account-scoped
and effective-dated, its ``interest_rate`` is a fraction CHECKed to ``[0, 1]``,
and one row per effective date is the rule its unique key states.  What the
card adds is its OWN reading of those rows -- a dated series of APRs and the
one in effect on a day -- and its own doors (:mod:`app.routes.card.apr`).

**The query has one home and this module does not re-spell it** (rule 14):
:func:`app.services.loan_loaders.load_rate_history` is the account-keyed read
every consumer of the table shares, and this module maps its rows to
:class:`CardApr`.  What it does NOT do is map them to the loan engine's
:class:`~app.services.amortization_engine.RateChangeRecord`: that record
carries a recast P&I (``monthly_pi``) a card never has, and the loan's
consumers reach the table only through a
:class:`~app.models.loan_params.LoanParams` row, which no door creates for a
revolving type (``ck_account_types_revolving_is_plain`` keeps the type
un-amortizing, and ``ensure_type_params`` seeds the row for amortizing types
alone) -- so a card born a card is never in the loan pipeline's account
set, and never sees the loan's rate periods.  The one way a revolving
account can hold a ``LoanParams`` row is a RE-TYPE: an amortizing account
with no postings may change type across the amortizing boundary
(``account_validation._validate_account_type_change`` refuses the crossing
only with posting history) and ``ensure_type_params`` removes nothing on a
kind change, so an empty-ledger Mortgage re-typed to Credit Card keeps its
row and stays in that set.  That is the re-type semantics every ``*Params``
table has today, reported at CC-3 rather than changed by it.

Nothing prices off the card's APR until plan step **CC-3i** gives the finance
charge to `recurrence:R16-d`'s one interest producer (ruling **R-CC19**); until
then :func:`apr_in_effect` serves the cash detail page's "APR in effect
today" (developer ruling **R-CC28**).

**The two writes are the card's own** (developer ruling **R-CC27**,
2026-09-18): :func:`set_apr` sets the row FOR a date -- one
``INSERT ... ON CONFLICT DO UPDATE`` on the table's unique key, so a same-date
submit rewrites the rate and two in-flight submits cannot race to an
``IntegrityError`` (the :func:`app.services.pay_schedule_service.ensure_schedule_row`
idiom, a ``pg_insert`` on a named constraint; that one does DO NOTHING)
-- and :func:`remove_apr` deletes a row the card holds.  The loan's
door appends and translates a collision into a refusal; the card's has no
collision to translate.

Flask-isolated; the writes flush and never commit (the door owns the
transaction).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.extensions import db
from app.models.loan_features import (
    RATE_HISTORY_UNIQUE_CONSTRAINT,
    RateHistory,
)
from app.services.loan_loaders import load_rate_history
from app.utils.effective_dated import in_effect_on


@dataclass(frozen=True)
class CardApr:
    """One APR of a card, effective from a date until the next row's.

    Attributes:
        row_id: The ``budget.rate_history`` row's id, which the remove door
            names (**R-CC27**).
        effective_date: The first day this APR applies.
        apr: The rate as a FRACTION (``Decimal("0.2499")`` for 24.99%), the
            stored domain (E-28).
    """

    row_id: int
    effective_date: date
    apr: Decimal


def load_card_aprs(account_id: int) -> list[CardApr]:
    """Load a card's APR series, newest effective date first.

    The ORDER is the shared query's (``effective_date`` DESC): the surface
    lists the latest change first, and :func:`apr_in_effect` orders for
    itself, so no consumer depends on it beyond display.

    Args:
        account_id: The revolving account whose rows to read.  The kind gate
            is the caller's (:func:`app.routes.card._helpers.load_card_or_404`);
            this loader reads whatever rows the account holds.

    Returns:
        The :class:`CardApr` list, possibly empty.
    """
    return [
        CardApr(
            row_id=row.id,
            effective_date=row.effective_date,
            apr=row.interest_rate,
        )
        for row in load_rate_history(account_id)
    ]


def apr_in_effect(aprs: list[CardApr], day: date) -> Decimal | None:
    """Return the APR in effect on *day*, or ``None`` before the first row.

    The ONE effective-dated walk (:func:`app.utils.effective_dated.in_effect_on`,
    the escrow versions' too; the two walks with fallbacks of their own that
    ledger row **BAL-524** folds onto it are plan step ``balance:X-ct``'s)
    over the card's rows, in any input order.
    Before the earliest row there IS no rate -- the card's series does not
    hold flat backwards the way an amount series does
    (:func:`app.services.template_amount_service.amount_as_of`), because
    nothing the owner stated applies there -- and ``None`` says so rather than
    a zero that would price a finance charge at nothing.

    Args:
        aprs: The card's :class:`CardApr` rows (:func:`load_card_aprs`).
        day: The day to resolve.

    Returns:
        The fraction in effect, or ``None`` when no row is dated on or before
        *day*.
    """
    row = in_effect_on(aprs, day)
    return None if row is None else row.apr


def set_apr(account_id: int, effective_date: date, apr: Decimal) -> None:
    """Set the card's APR effective from *effective_date*: create or rewrite.

    ONE statement against ``uq_rate_history_account_effective_date``: a row
    for the date is inserted, or the existing one's ``interest_rate`` is
    rewritten in place -- its id, ``created_at`` and the columns a card never
    writes (``monthly_pi``, ``notes``) untouched.  Because the choice is the
    database's, two submits in flight for the same date both succeed and the
    later one's rate stands, where a query-then-insert would raise for one of
    them.

    Args:
        account_id: The revolving account (the door has proved it is the
            owner's and a card with terms).
        effective_date: The first day the rate applies.
        apr: The rate as a FRACTION in ``[0, 1]`` (the schema has already
            divided the form percent; ``ck_rate_history_valid_interest_rate``
            refuses anything else).
    """
    db.session.execute(
        pg_insert(RateHistory.__table__)
        .values(
            account_id=account_id, effective_date=effective_date,
            interest_rate=apr,
        )
        .on_conflict_do_update(
            constraint=RATE_HISTORY_UNIQUE_CONSTRAINT,
            set_={"interest_rate": apr},
        ),
    )


def remove_apr(account_id: int, row_id: int) -> bool:
    """Delete the card's APR row *row_id*, reporting whether one was there.

    Scoped by BOTH ids: a row of another account -- the owner's own loan, or
    anyone's -- is not this card's to remove, and the answer is the same
    ``False`` a missing id gets, which the door renders as 404 (the project's
    "not found" and "not yours" rule).

    Args:
        account_id: The revolving account the door has gated.
        row_id: The ``budget.rate_history`` row's id, from the URL.

    Returns:
        ``True`` when the row was this card's and is now deleted; ``False``
        when no such row exists on this card (nothing written).
    """
    deleted = (
        db.session.query(RateHistory)
        .filter_by(id=row_id, account_id=account_id)
        .delete()
    )
    return deleted == 1
