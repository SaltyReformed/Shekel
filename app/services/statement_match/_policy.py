"""Where the owner has SAID a merchant goes, and what that means for one line.

Plan step ``bank_import:X-f6a-3d``.  The review screen's leftover list is 91
lines on the developer's own statement and **21 merchants**, so the work it
asks for is 91 decisions where the real question was asked 21 times.  This
module holds the answer to those 21 and resolves it, per line, into something
the screen can show.

**Nothing here writes money, and nothing here CAN.**  A policy is read to
SUGGEST; the only thing that records a purchase is an explicit destination
submitted for one specific line (:mod:`._create`).  That separation is the
developer's ruling of 2026-08-19 and it is what keeps ruling **R-FZ**'s *the
destination select IS the tick* whole: the select still opens on *leave this
line alone*, the policy is rendered beside it, and one sweep control -- the
same shape the per-class proposal sweep already has -- is what turns
suggestions into ticks.  A default that arrives pointing at money is exactly
what R-FZ removed, and a remembered default would be one.

**A policy resolves against the pass's own offer set, so it can never widen
it.**  :func:`placements_for` picks from :func:`~._candidates.destinations_for`
-- narrowed by what this pass has already matched -- rather than querying for a
row of its own, which is the property :func:`~._resolve.resolve_rows` rests on:
a destination the screen may not offer is one a policy cannot reach either.
Every way a policy can fail to resolve is REPORTED rather than substituted for,
because substituting is how a suggestion becomes a guess.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.  :func:`state_policies`
MUTATES and does NOT commit -- the route owns the unit of work.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, replace

from app import ref_cache
from app.enums import TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.merchant_destination import MerchantDestination
from app.models.statement_import import BankStatementLine
from app.models.transaction_template import TransactionTemplate
from app.utils.log_events import (
    BUSINESS,
    EVT_MERCHANT_DESTINATION_STATED,
    log_event,
)

from ._vocabulary import account_payment_merchants


_logger = logging.getLogger(__name__)


class PolicyAnswer(enum.Enum):
    """Which of the three answers a stated policy gives.

    **A closed set of three, and the model's CHECK is what closes it**
    (``ck_merchant_destinations_one_answer``).  It is derived from which
    columns a row carries rather than stored beside them, because a stored
    discriminator is a second statement of what the columns already say.

    The absence of a policy is NOT a member: it is ``None`` where a
    :class:`MerchantPolicy` would be, and the screen says *you have not said*
    rather than *you said nothing*.  Collapsing the two is what would make
    "never a purchase" unsayable, and that answer is the one worth the most on
    the developer's own data -- 9 lines and `-$7,412.94`.
    """

    TEMPLATE = "template"
    NEW_ENVELOPE = "new_envelope"
    NEVER = "never"


@dataclass(frozen=True)
class MerchantPolicy:
    """One stated answer to "where does this merchant's spending go?".

    Attributes:
        merchant: The bank's own merchant string, which is the key.
        answer: Which of the three it is.
        template_id: The recurring definition to file into, for
            :attr:`PolicyAnswer.TEMPLATE`; ``None`` otherwise.
        envelope_name: What to call the envelope to create, for
            :attr:`PolicyAnswer.NEW_ENVELOPE`; ``None`` otherwise.
        category_id: The category to create it under, likewise.
    """

    merchant: str
    answer: PolicyAnswer
    template_id: "int | None" = None
    envelope_name: "str | None" = None
    category_id: "int | None" = None

    @property
    def is_new_envelope(self) -> bool:
        """Return whether this answer creates an envelope.

        **The screen's own question, answered here rather than as a truth test
        on :attr:`envelope_name` in a Jinja condition** -- which would be the
        arm INFERRED from a column rather than read from the one field that
        states it, the shape that made the existing-envelope destination
        unreachable from a browser at plan step X-f6a-3b.
        """
        return self.answer is PolicyAnswer.NEW_ENVELOPE

    @property
    def is_never(self) -> bool:
        """Return whether this answer is *never a purchase*.

        Asked here for the reason :attr:`is_new_envelope` is, and for one more:
        a template comparing ``answer.value == 'never'`` would be a string
        comparison standing in for an identity, which is the substitution this
        project's reference rule exists to refuse.
        """
        return self.answer is PolicyAnswer.NEVER

    @classmethod
    def of(cls, row: MerchantDestination) -> "MerchantPolicy":
        """Return *row* as the value every reader here shares.

        Args:
            row: The stored policy.

        Returns:
            Its :class:`MerchantPolicy`.  The answer is read off the columns
            the model's CHECK already made exclusive, so there is no fourth
            branch to fall through to.
        """
        if row.template_id is not None:
            answer = PolicyAnswer.TEMPLATE
        elif row.envelope_name is not None:
            answer = PolicyAnswer.NEW_ENVELOPE
        else:
            answer = PolicyAnswer.NEVER
        return cls(
            merchant=row.merchant,
            answer=answer,
            template_id=row.template_id,
            envelope_name=row.envelope_name,
            category_id=row.category_id,
        )


def _named_templates(template_ids: "set[int]") -> "dict[int, str]":
    """Return ``{id: name}`` for *template_ids*, in one statement or none.

    Args:
        template_ids: The ids wanted.  Empty issues no query -- ``IN ()`` is a
            statement with no rows to find.

    Returns:
        The names by id.
    """
    if not template_ids:
        return {}
    rows = (
        db.session.query(TransactionTemplate.id, TransactionTemplate.name)
        .filter(TransactionTemplate.id.in_(template_ids))
        .all()
    )
    return dict(rows)


@dataclass(frozen=True)
class PolicyView:
    """What the owner has SAID, and what it can still resolve against.

    **One derivation at one instant**, which is the same argument
    :class:`~._scope.ReviewScope` makes one tier down and is not a parameter
    list dressed up: a policy names a template and a category, and whether
    either is still reachable is read from the same moment the policy is.
    Read separately, a category archived between two of those reads would be
    resolvable by one caller and refused by the next inside one render.

    Attributes:
        policies: What the owner has answered, by merchant
            (:func:`policies_for`).
        template_names: What to call each recurring definition a policy on this
            account may name (:func:`offerable_templates`) -- the option list,
            and the sentence an unresolvable placement explains itself with.
        active_categories: The categories a new envelope may still be created
            under (:func:`active_category_ids`).
        stale_templates: What to call a template a stored policy NAMES that is
            no longer offerable, by id.  **A rendered control must be able to
            show the answer it holds, even a stale one**: without this the
            select had no option carrying the stored value, so it displayed --
            and submitted -- its first, which is *I have not said*, and the
            next Save silently WITHDREW a policy the owner never touched.  A
            template is deactivated or un-enveloped through
            ``routes/templates/crud.py``, so this is live user state rather
            than a hypothetical.  Found by adversarial financial review
            2026-08-19.  Usually empty, and then it costs no query at all.
    """

    policies: "dict[str, MerchantPolicy]"
    template_names: "dict[int, str]"
    active_categories: "frozenset[int]"
    stale_templates: "dict[int, str]"

    @classmethod
    def build(cls, owner_id: int, account_id: int) -> "PolicyView":
        """Derive the view for one pass over one account.

        Args:
            owner_id: The user the caller proved owns the account.
            account_id: The account being reviewed.

        Returns:
            The :class:`PolicyView`.  Three small indexed reads, and a fourth
            only when a stored policy names a template that has stopped being
            offerable; the review screen's cost is its 3.6 s candidate
            derivation, not this.
        """
        policies = policies_for(owner_id, account_id)
        offerable = offerable_templates(account_id)
        return cls(
            policies=policies,
            template_names=offerable,
            active_categories=active_category_ids(owner_id),
            stale_templates=_named_templates(
                {
                    policy.template_id for policy in policies.values()
                    if policy.template_id is not None
                    and policy.template_id not in offerable
                },
            ),
        )

    def label_for(self, template_id: int) -> str:
        """Return what to call *template_id*, offerable or not.

        Args:
            template_id: The recurring definition a policy names.

        Returns:
            Its name.  TOTAL, because a caller asking is holding an id a policy
            already carries -- and a composite foreign key holds that id to
            this account -- so the only way it is unknown here is a row deleted
            between this view's two reads, where the honest answer is a phrase
            rather than a raise on a read path.
        """
        return (
            self.template_names.get(template_id)
            or self.stale_templates.get(template_id)
            or "a recurring envelope"
        )


def policies_for(
    owner_id: int, account_id: int,
) -> "dict[str, MerchantPolicy]":
    """Return every policy this owner has stated for this account, by merchant.

    Args:
        owner_id: The user the caller proved owns the account.
        account_id: The account being reviewed.

    Returns:
        ``{merchant: MerchantPolicy}``.  One statement per merchant is
        structural (``uq_merchant_destinations_owner_account_merchant``), so
        the mapping cannot lose a row to a collision.
    """
    rows = (
        db.session.query(MerchantDestination)
        .filter(
            MerchantDestination.user_id == owner_id,
            MerchantDestination.account_id == account_id,
        )
        .all()
    )
    return {row.merchant: MerchantPolicy.of(row) for row in rows}


def statable_merchants(
    account_id: int, stored: "dict[str, MerchantDestination] | None" = None,
) -> "frozenset[str]":
    """Return every merchant a policy on this account may be stated ABOUT.

    **ONE scope, shared by the section that offers a row and the door that
    writes one** -- the property :func:`~._candidates.destinations_for` gives a
    destination, and the reason it is one function rather than two sets is that
    two drifted the first time they were written.

    Two halves, and the second is not decoration:

    * every merchant this account's RECORDED lines name -- recorded rather than
      merely unexplained, because a merchant whose every line is already
      matched is still one the owner may want to answer for, and the next
      statement will bring more of it;
    * every merchant this owner has ALREADY answered for here.  Without it a
      policy would become unwithdrawable the moment its lines went: the section
      renders an answered merchant whichever half it came from, and a check
      reading only the first half would refuse that submission whole.  No door
      in ``app/`` deletes an import today -- that is finding **N-302**, owned by
      ``X-f6a-4``, the NEXT step in this arc -- so this is a defect one step
      from live rather than a hypothetical.

    Args:
        account_id: The account being reviewed.  ``bank_statement_lines``
            carries no ``user_id`` of its own -- it is account-scoped, held to
            its import's account by a composite key -- so the account IS the
            ownership statement here, exactly as it is for
            :func:`~._reads._unmatched_lines`.
        stored: The owner's policies on this account, where the caller has
            already read them (:func:`state_policies` has), else ``None`` to
            read them here.

    Returns:
        The merchants, as a set.  A line naming none contributes nothing.
    """
    recorded = (
        db.session.query(BankStatementLine.merchant)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.merchant.isnot(None),
        )
        .distinct()
        .all()
    )
    if stored is None:
        stored = {
            row.merchant: row
            for row in db.session.query(MerchantDestination).filter(
                MerchantDestination.account_id == account_id,
            ).all()
        }
    return frozenset(row[0] for row in recorded) | frozenset(stored)


def offerable_templates(account_id: int) -> "dict[int, str]":
    """Return the recurring definitions a policy on this account may name.

    **Not every template, and the filter is the create door's own.**
    :func:`~._create.create_purchase_from_line` files a purchase through
    ``entry_service.create_entry``, which refuses a parent that does not track
    purchases -- so a policy naming a template that generates a plain budget
    line would be an answer whose every placement is refused.  The other two
    clauses mirror :func:`~._candidates.destinations_for`: a transfer's legs
    are the transfer service's, and money coming IN is not a purchase.

    Args:
        account_id: The account being reviewed.

    Returns:
        ``{template_id: name}``, which is what the policy control renders and
        what :func:`_template_placement` names in its refusals.
    """
    rows = (
        db.session.query(TransactionTemplate)
        .filter(
            TransactionTemplate.account_id == account_id,
            TransactionTemplate.is_envelope.is_(True),
            TransactionTemplate.is_active.is_(True),
            # The reference rule: IDs for logic, strings for display.
            TransactionTemplate.transaction_type_id != ref_cache.txn_type_id(
                TxnTypeEnum.INCOME,
            ),
        )
        .order_by(TransactionTemplate.name)
        .all()
    )
    return {row.id: row.name for row in rows}


def active_category_ids(owner_id: int) -> "frozenset[int]":
    """Return the categories a new envelope may still be created under.

    **The create door's own clause, asked where the SUGGESTION is made.**
    ``_create._owned_category`` refuses an archived category, because
    ``category_service.list_active_categories`` is what the picker renders --
    so a placement naming one would be a control whose submission can never
    succeed, which is the shape finding **N-325** was just closed for one
    field over.  A HARD-deleted category takes its policy with it
    (``fk_merchant_destinations_category_owner`` cascades); archiving is a soft
    state that leaves the row pointing at something the door will refuse, so
    this is the live half.

    Args:
        owner_id: The user whose categories may be reached.

    Returns:
        Their active category ids.
    """
    rows = (
        db.session.query(Category.id)
        .filter(Category.user_id == owner_id, Category.is_active.is_(True))
        .all()
    )
    return frozenset(row[0] for row in rows)


@dataclass(frozen=True)
class PolicyStatement:
    """What the owner submitted about ONE merchant.

    Ids and names only, and no line and no figure: stating a policy is not an
    act on money, and the door re-derives everything it checks from the ids.

    **It names no OWNER and no ACCOUNT**, for the reason
    :class:`~._creations.PurchaseCreation` states: whose account this is, is the
    route's one proved statement, and a submission carrying its own pair would
    be a second one that could disagree with it.

    Attributes:
        merchant: Which merchant this answers for.
        answer: The answer, or ``None`` to WITHDRAW the policy -- which is a
            real submission rather than an absence, because the control's
            do-nothing option has to be able to mean *forget what I said*.
        template_id: For :attr:`PolicyAnswer.TEMPLATE`.
        envelope_name: For :attr:`PolicyAnswer.NEW_ENVELOPE`.
        category_id: Likewise.
    """

    merchant: str
    answer: "PolicyAnswer | None"
    template_id: "int | None" = None
    envelope_name: "str | None" = None
    category_id: "int | None" = None


@dataclass(frozen=True)
class StatedPolicies:
    """What one pass over the policy section did.

    Attributes:
        stated: One sentence per merchant whose answer CHANGED, in submitted
            order.
        refused: One sentence per statement that could not be recorded.
        unchanged_count: How many statements repeated what was already stored.
            **Reported rather than dropped silently**: the form submits every
            merchant it renders, so most of a pass is ordinarily no-ops, and a
            receipt that said "3 recorded" with no denominator would read as
            though the other 18 had failed.
    """

    stated: "tuple[str, ...]"
    refused: "tuple[str, ...]"
    unchanged_count: int


def _trimmed(statement: PolicyStatement) -> PolicyStatement:
    """Return *statement* with its envelope name stripped of surrounding space.

    The database CHECK compares ``btrim(envelope_name)``, so a name differing
    from the stored one only by whitespace would read back as a different
    answer -- and be written every time the section is saved.

    Args:
        statement: What the owner submitted.

    Returns:
        It, or a copy with the name trimmed.
    """
    if statement.envelope_name is None:
        return statement
    return replace(statement, envelope_name=statement.envelope_name.strip())


def _same_answer(row: MerchantDestination, statement: PolicyStatement) -> bool:
    """Return whether *row* already says what *statement* says.

    Args:
        row: The stored policy.
        statement: What was submitted for the same merchant.

    Returns:
        Whether the two agree on all three columns.  Compared column by column
        rather than by rebuilding a :class:`MerchantPolicy`, because what
        matters is whether a WRITE would change the row.
    """
    return (
        row.template_id == statement.template_id
        and row.envelope_name == statement.envelope_name
        and row.category_id == statement.category_id
    )


def _checked_template(
    statement: PolicyStatement, templates: "dict[int, str]",
) -> str:
    """Return the template's name, refusing one this account may not name.

    Args:
        statement: What the owner submitted.
        templates: The account's offerable templates
            (:func:`offerable_templates`).

    Returns:
        The template's name, for the receipt.

    Raises:
        ValidationError: When the id names no template this account may file a
            purchase into.  ``fk_merchant_destinations_template_account``
            refuses a foreign one anyway, but it arrives as an
            ``IntegrityError`` -- "Something went wrong" and a logged
            traceback for what is ordinarily a stale page.
    """
    named = templates.get(statement.template_id)
    if named is None:
        raise ValidationError(
            f"There is no recurring envelope on this account for "
            f"{statement.merchant} to go into -- it may have been deleted or "
            f"turned off.  Reload the page and pick another.  Nothing was "
            f"changed."
        )
    return named


def _reject_incomplete_new_envelope(statement: PolicyStatement) -> None:
    """Refuse a NEW ENVELOPE answer stated by halves.

    **``_create._reject_incomplete_new_envelope``'s twin, and it was missing.**
    The name box carries no ``required`` and no ``minlength``, so clearing it is
    an ordinary browser action; ``envelope_name`` is ``load_default=None``,
    which makes marshmallow's field ``allow_none``, so ``""`` is normalized to
    ``None`` BEFORE ``validate.Length(min=1)`` runs -- and ``"   "`` passes
    Length outright.  Either row is unwritable
    (``ck_merchant_destinations_one_answer`` for the first,
    ``ck_merchant_destinations_envelope_name_not_blank`` for the second), so
    the flush raised ``IntegrityError``, which is not a
    :class:`~app.exceptions.ValidationError` and so escaped this item's
    savepoint into the route's database arm -- rolling back every answer that
    had already landed and reaching the owner as "Something went wrong" with a
    logged traceback.  That is the exact hazard :func:`_checked_template` cites
    as its own reason for existing.  Found by adversarial financial review
    2026-08-19.

    It fires BEFORE :func:`_checked_category`, which would otherwise answer a
    missing name with a true sentence about the wrong problem -- the ordering
    ``_create`` states for the same pair.

    Args:
        statement: What the owner submitted.

    Raises:
        ValidationError: When the new-envelope answer is named without both of
            its own fields.
    """
    if statement.answer is not PolicyAnswer.NEW_ENVELOPE:
        return
    named = (statement.envelope_name or "").strip()
    if not named or statement.category_id is None:
        raise ValidationError(
            f"A new envelope for {statement.merchant} needs both a name and a "
            f"category.  Nothing was changed for it."
        )


def _reject_spending_answer(
    statement: PolicyStatement, account_payments: "frozenset[str]",
) -> None:
    """Refuse filing a merchant's money as spending when it pays an ACCOUNT.

    Ruling **R-GJ**, plan step ``bank_import:X-ga``.  A merchant whose lines a
    SOURCE files as a payment to an account the owner holds has no
    create-a-purchase arm and no answer opens one
    (:meth:`~._bars.CreationBars.bar_for`) -- so a stored *template* or *new
    envelope* answer for one would be an answer nothing could ever apply.

    **Refused rather than left inert**, and an adversarial review 2026-08-24 is
    why: while any answer LIFTED the bar, ``a new envelope`` was the answer the
    developer had actually saved for ``Capital One Credit Card`` and the one
    that booked `$7,412.94` through the sweep.  Silently keeping such a row
    would leave the same words on the screen meaning something different, which
    is worse than the refusal -- the owner would read *Capital One goes in a
    new envelope* and be right about nothing.

    *Never a purchase* and the withdrawal are both still legal, because both
    are TRUE of such a merchant.

    Args:
        statement: What the owner submitted for one merchant.
        account_payments: The merchants a source files as paying an account
            (:attr:`~._bars.CreationBars.account_payments`).

    Raises:
        ValidationError: When a spending answer is stated for one of them.
            Refuses THIS statement only -- the pass's other answers land, which
            is what the per-item savepoint is for.
    """
    if statement.merchant not in account_payments:
        return
    if statement.answer not in (
        PolicyAnswer.TEMPLATE, PolicyAnswer.NEW_ENVELOPE,
    ):
        return
    raise ValidationError(
        f"Your bank files {statement.merchant} as a payment to an account you "
        f"hold rather than as spending, so it cannot be filed in a budget "
        f"line -- the money it moved was spent somewhere else and your budget "
        f"already holds it there.  \"Never a purchase\" is the answer that "
        f"fits.  Nothing was changed for it."
    )


def _checked_category(statement: PolicyStatement, owner_id: int) -> Category:
    """Return the category the new-envelope answer names, refusing another's.

    The IDOR probe every create door in this project performs before a write.
    ``fk_merchant_destinations_category_owner`` makes a foreign one unwritable,
    and this is what turns that into a sentence rather than a 500.

    Args:
        statement: What the owner submitted.
        owner_id: Whose categories may be reached.

    Returns:
        The category.

    Raises:
        ValidationError: When the id names no ACTIVE category of this owner's.
            Archived is refused for the reason
            :func:`~._create._owned_category` gives: the picker renders
            ``category_service.list_active_categories``, so accepting one here
            would be the offer-versus-accept drift this arc keeps closing.
    """
    category = (
        db.session.query(Category)
        .filter(
            Category.id == statement.category_id,
            Category.user_id == owner_id,
            Category.is_active.is_(True),
        )
        .one_or_none()
    )
    if category is None:
        raise ValidationError(
            f"That category is not one of yours, so {statement.merchant} "
            f"cannot go under it.  Reload the page and pick another.  Nothing "
            f"was changed."
        )
    return category


def _apply_one(
    statement: PolicyStatement,
    owner_id: int,
    account_id: int,
    stored: "dict[str, MerchantDestination]",
    templates: "dict[int, str]",
) -> "str | None":
    """Record one statement, and return the sentence for it or ``None``.

    Args:
        statement: What the owner submitted for one merchant.
        owner_id: The user the route proved owns the account.
        account_id: The account.
        stored: The policies already held, by merchant.
        templates: The account's offerable templates.

    Returns:
        One sentence naming what changed, or ``None`` when nothing did.

    Raises:
        ValidationError: On a template or category this owner may not name.
    """
    row = stored.get(statement.merchant)
    # **UNCHANGED is decided before anything is validated, and the order is the
    # fix rather than a saving.**  A stored answer can stop being offerable
    # under the owner's feet -- a template deactivated through the templates
    # screen -- and the section renders it back as the answer it is.  Validating
    # first would refuse a submission that changes nothing, so pressing Save to
    # answer for one merchant would report a refusal for another the owner
    # never touched.  Restating what is already stored needs no check, because
    # the row it would write already exists.
    if row is not None and statement.answer is not None and _same_answer(
        row, _trimmed(statement),
    ):
        return None
    if statement.answer is None:
        if row is None:
            return None
        db.session.delete(row)
        del stored[statement.merchant]
        return f"{statement.merchant}: you have not said where these go."
    if statement.answer is PolicyAnswer.TEMPLATE:
        named = _checked_template(statement, templates)
        said = f"{statement.merchant} goes in {named}."
    elif statement.answer is PolicyAnswer.NEW_ENVELOPE:
        _reject_incomplete_new_envelope(statement)
        category = _checked_category(statement, owner_id)
        statement = _trimmed(statement)
        said = (
            f"{statement.merchant} gets a new envelope called "
            f"{statement.envelope_name}, under {category.display_name}."
        )
    else:
        said = f"{statement.merchant} is never a purchase."
    if row is None:
        row = MerchantDestination(
            user_id=owner_id, account_id=account_id,
            merchant=statement.merchant,
        )
        db.session.add(row)
        # **Held in step as rows are added and deleted**, so a submission
        # naming one merchant twice restates the row it just wrote rather than
        # inserting a second and raising ``IntegrityError`` past this item's
        # savepoint.  Unreachable from the rendered form, which emits one row
        # per merchant; reachable by a crafted body, where the blast radius is
        # the whole pass.  Found by adversarial financial review 2026-08-19.
        stored[statement.merchant] = row
    row.template_id = statement.template_id
    row.envelope_name = statement.envelope_name
    row.category_id = statement.category_id
    return said


def state_policies(
    statements: "tuple[PolicyStatement, ...]",
    owner_id: int,
    account_id: int,
) -> StatedPolicies:
    """Record where this owner says each merchant goes.

    **It MOVES NO MONEY, and that is the whole reason it is its own door**
    rather than an item kind inside :func:`~._batch.apply_reviewed`.  A policy
    is read to suggest; a purchase is recorded only by an explicit destination
    on one line.  Keeping the two apart is what lets the money door's ceiling,
    its receipt and its refusal vocabulary stay about money -- and it is the
    same boundary :func:`~._release.release_match` is a separate door for.

    **Each statement is its own SAVEPOINT**, which is the ruled per-item
    isolation applied one door over.  Its value here is different: a refused
    statement is always a stale page or a crafted request, but refusing the
    whole submission would re-render the section from the DATABASE and so
    discard the other twenty answers the owner had just picked.

    **It takes ids rather than a ReviewScope**, unlike every other door in this
    package, and the reason is that it reaches nothing the scope holds: no
    candidate, no price, no calendar.  Requiring one would make a preference
    write pay the pass's 3.6 s derivation.  The ownership statement is still
    the route's one statement, exactly as :func:`~._release.release_match`
    takes it.

    **It reads the offerable templates for ITSELF rather than taking a
    :class:`PolicyView`, so one request that writes and then re-renders reads
    them twice** -- and that is deliberate rather than an oversight.  Sharing
    one read across a write boundary would rest on "nothing in this door can
    change a template", which is an enumeration; the same argument was made
    about a candidate's PRICE at plan step ``bank_import:X-f6a-3c-2`` and
    adversarial review measured it false, with a sibling writer nobody had
    enumerated.  The read is one indexed statement over 39 rows on the
    developer's own account, so re-asking costs nothing that matters and
    depends on no enumeration.

    Does NOT commit -- the route owns the session boundary.

    Args:
        statements: What the owner submitted, in the order the screen rendered
            it.
        owner_id: The user the route proved owns the account.
        account_id: The account being reviewed.

    Returns:
        The :class:`StatedPolicies`.
    """
    stored = {
        row.merchant: row
        for row in db.session.query(MerchantDestination).filter(
            MerchantDestination.user_id == owner_id,
            MerchantDestination.account_id == account_id,
        ).all()
    }
    # **The scope check reads the policies THIS door already read.**  It was a
    # separate function the route called first, which asked
    # ``merchant_destinations`` a second time inside one request -- a redundant
    # producer call, which this project treats as a DRY violation rather than a
    # cost.  Found by adversarial design review 2026-08-19.
    _refuse_unknown_merchants(statements, account_id, stored)
    templates = offerable_templates(account_id)
    # **Read HERE rather than taken from a caller**, for the reason the
    # templates beside it are: this door reaches nothing a read pass holds, and
    # a fact threaded in across a write boundary would rest on an enumeration
    # of what cannot have changed.  One indexed statement over the account's
    # recorded lines (ruling **R-GJ**, plan step ``bank_import:X-ga``).
    account_payments = account_payment_merchants(account_id)
    said, refused, unchanged = [], [], 0
    for statement in statements:
        if statement.answer is None and statement.merchant not in stored:
            # Not an answer and nothing to withdraw.  The section submits every
            # merchant it renders, so this is most of an ordinary pass -- and
            # counting it as UNCHANGED would make the receipt's own sentence
            # false about it ("already answered for" is exactly what it is
            # not).
            continue
        savepoint = db.session.begin_nested()
        try:
            # **BEFORE ``_apply_one``, so it fires ahead of that function's
            # unchanged short-circuit** (ruling **R-GJ**).  An answer this
            # refuses may ALREADY be stored -- the developer's own
            # ``Capital One Credit Card -> a new envelope`` was -- and a bank
            # may start filing a merchant as an account payment after an answer
            # was given, so restating one has to be refused rather than
            # reported as no change.  It costs a standing refusal on that one
            # merchant until it is corrected, which is the loud end of a choice
            # whose quiet end is an illegal answer stored forever.
            _reject_spending_answer(statement, account_payments)
            sentence = _apply_one(
                statement, owner_id, account_id, stored, templates,
            )
            # Inside this statement's savepoint, for the reason
            # ``_batch._run``'s flush is inside its own: autoflush would
            # otherwise emit this row's INSERT while the NEXT statement's first
            # query runs, so refusing that one would roll back this one's work.
            db.session.flush()
        except ValidationError as exc:
            savepoint.rollback()
            refused.append(str(exc))
            continue
        savepoint.commit()
        if sentence is None:
            unchanged += 1
            continue
        said.append(sentence)
        log_event(
            _logger, logging.INFO, EVT_MERCHANT_DESTINATION_STATED, BUSINESS,
            "An owner stated where a merchant's spending goes.",
            user_id=owner_id, account_id=account_id,
            merchant=statement.merchant,
            answer=statement.answer.value if statement.answer else "withdrawn",
            template_id=statement.template_id,
            category_id=statement.category_id,
        )
    return StatedPolicies(
        stated=tuple(said), refused=tuple(refused),
        unchanged_count=unchanged,
    )


def _refuse_unknown_merchants(
    statements: "tuple[PolicyStatement, ...]",
    account_id: int,
    stored: "dict[str, MerchantDestination]",
) -> None:
    """Refuse a statement about a merchant this account may not be asked about.

    **The scope check, and it is the whole submission's rather than one
    item's.**  A merchant outside :func:`statable_merchants` cannot have come
    from this screen at all -- the section renders a SUBSET of that set -- so a
    statement naming one is a crafted request rather than a stale page, and
    there is no pass to salvage.  It is the asymmetry
    ``StatementPurchaseSchema`` already states for a malformed payload.

    Without it the table would happily hold a policy for any string a caller
    liked: a row keyed on a merchant nothing joins is inert, but it is
    unbounded write amplification against a table with no other ceiling.

    Args:
        statements: What the owner submitted.
        account_id: The account being reviewed.
        stored: The policies already held, which :func:`state_policies` has
            already read -- the ANSWERED half of the scope, threaded rather
            than re-queried.

    Raises:
        ValidationError: When any statement names a merchant outside the scope.
    """
    known = statable_merchants(account_id, stored)
    unknown = sorted(
        {
            statement.merchant for statement in statements
            if statement.merchant not in known
        }
    )
    if unknown:
        raise ValidationError(
            f"Your bank has never shown "
            f"{', '.join(repr(name) for name in unknown)} on this account, so "
            f"there is nothing to say about it.  Reload the page.  Nothing was "
            f"changed."
        )
