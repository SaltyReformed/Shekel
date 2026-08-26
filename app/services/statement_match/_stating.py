"""The door that records where the owner says a merchant's spending goes.

Plan step ``bank_import:X-f6a-3d``, split out of :mod:`._rules` at
``bank_import:X-gd-2``.  :mod:`._rules` holds what a rule IS and reads the ones
this account has; everything here WRITES one, and the split is by that
consequence rather than by size.

**It MOVES NO MONEY and can move none**, which is what makes it its own door
rather than an item kind inside :func:`~._batch.apply_reviewed`: a rule is read
to suggest, and the only thing that records a purchase is an explicit
destination on one specific line (:mod:`._create`).  Keeping the two apart is
what lets the money door's ceiling, its receipt and its refusal vocabulary stay
about money.

**A rule is RESTATED and never UN-stated** (ruling **R-GS**).  There is no
delete arm here and no submission that means *forget what I said*: the owner
who wants no standing answer for a merchant states :attr:`~._rules.RuleAnswer.
ALWAYS_ASK`, which is an answer.  The screen's *I have not said* never reaches
this module -- the route drops it -- which is why
:class:`~._rules.RuleSubmission` can require its answer at all.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.  :func:`state_rules`
MUTATES and does NOT commit -- the route owns the unit of work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.merchant_rule import MerchantRule
from app.utils.log_events import (
    BUSINESS,
    EVT_MERCHANT_RULE_STATED,
    log_event,
)

from ._rules import RuleAnswer, account_merchants, offerable_templates
from ._vocabulary import account_payment_merchants


_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuleSubmission:
    """What the owner submitted about ONE merchant.

    Ids and names only, and no line and no figure: stating a rule is not an
    act on money, and the door re-derives everything it checks from the ids.

    **It names no OWNER and no ACCOUNT**, for the reason
    :class:`~._creations.PurchaseCreation` states: whose account this is, is the
    route's one proved statement, and a submission carrying its own pair would
    be a second one that could disagree with it.

    Attributes:
        merchant_id: Which merchant this answers for.  **The id and not the
            name** (plan step ``bank_import:X-gd-1``): the form used to post
            the bank's own string back, so the door's whole defence against a
            crafted merchant was a scope comparison in Python; it posts the
            merchant ROW now, which ``fk_merchant_rules_merchant_account``
            holds to this account.
        answer: The answer.  **Not optional, and ruling R-GS is why**: there
            is no withdrawal, so there is no submission that means *forget what
            I said*.  The screen's do-nothing option means *I am not stating
            anything about this merchant*, which the route drops before this
            door sees it -- an act on no row does not travel as a value.
        template_id: For :attr:`RuleAnswer.TEMPLATE`.
        envelope_name: For :attr:`RuleAnswer.NEW_ENVELOPE`.
        category_id: Likewise.

    **The three fields below are read only for the answer that uses them**
    (:func:`_columns_of`), so a submission pairing :attr:`RuleAnswer.NEVER`
    with a ``template_id`` writes a *never a purchase* row rather than a
    template one.  It used to copy all three onto the row unconditionally,
    which made this a public door whose stored row could contradict its own
    stated answer if a caller paired them wrong -- reachable only by a caller
    inside this app, and this is the shape the project fixes structurally
    rather than by asking callers to be careful.
    """

    merchant_id: int
    answer: RuleAnswer
    template_id: "int | None" = None
    envelope_name: "str | None" = None
    category_id: "int | None" = None


@dataclass(frozen=True)
class StatedRules:
    """What one pass over the rule section did.

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


def _trimmed(statement: RuleSubmission) -> RuleSubmission:
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


def _columns_of(statement: RuleSubmission) -> "dict[str, object]":
    """Return the four columns *statement*'s answer comes to.

    **The ONE statement of what each answer stores**, and the inverse of
    :meth:`RuleAnswer.of`.  Both the comparison (:func:`_same_answer`) and the
    write (:func:`_apply_one`) go through it, so "has this changed" and "what
    do I write" cannot disagree about a column -- which they could while each
    listed the columns itself, and which the fourth answer would have made a
    live hazard rather than a tidiness one: a flag compared but not written is
    a *never a purchase* that reads back as *ask me every time*.

    **It reads each field only for the answer that uses it.**  A submission
    pairing :attr:`RuleAnswer.NEVER` with a ``template_id`` yields the *never*
    columns and drops the id, so a row that contradicts its own answer is
    unconstructible here rather than refused by the CHECK after the fact.

    Args:
        statement: What the owner submitted, already
            :func:`trimmed <_trimmed>`.

    Returns:
        ``{column: value}`` for all four answer columns, every one of them
        stated -- there is no "leave this as it was", because restating a rule
        replaces the whole answer.
    """
    answer = statement.answer
    return {
        "template_id": (
            statement.template_id if answer is RuleAnswer.TEMPLATE else None
        ),
        "envelope_name": (
            statement.envelope_name
            if answer is RuleAnswer.NEW_ENVELOPE else None
        ),
        "category_id": (
            statement.category_id
            if answer is RuleAnswer.NEW_ENVELOPE else None
        ),
        "never_a_purchase": answer is RuleAnswer.NEVER,
    }


def _same_answer(row: MerchantRule, statement: RuleSubmission) -> bool:
    """Return whether *row* already says what *statement* says.

    Args:
        row: The stored rule.
        statement: What was submitted for the same merchant, already
            :func:`trimmed <_trimmed>`.

    Returns:
        Whether the two agree on every answer column.  Compared column by
        column rather than by rebuilding a :class:`StandingRule`, because what
        matters is whether a WRITE would change the row -- and read from
        :func:`_columns_of` so the set compared is the set written.
    """
    return all(
        getattr(row, column) == value
        for column, value in _columns_of(statement).items()
    )


def _checked_template(
    statement: RuleSubmission, merchant: str, templates: "dict[int, str]",
) -> str:
    """Return the template's name, refusing one this account may not name.

    Args:
        statement: What the owner submitted.
        merchant: What the merchant it answers for is called, for the refusal.
        templates: The account's offerable templates
            (:func:`offerable_templates`).

    Returns:
        The template's name, for the receipt.

    Raises:
        ValidationError: When the id names no template this account may file a
            purchase into.  ``fk_merchant_rules_template_account``
            refuses a foreign one anyway, but it arrives as an
            ``IntegrityError`` -- "Something went wrong" and a logged
            traceback for what is ordinarily a stale page.
    """
    named = templates.get(statement.template_id)
    if named is None:
        raise ValidationError(
            f"There is no recurring envelope on this account for "
            f"{merchant} to go into -- it may have been deleted or "
            f"turned off.  Reload the page and pick another.  Nothing was "
            f"changed."
        )
    return named


def _reject_incomplete_new_envelope(
    statement: RuleSubmission, merchant: str,
) -> None:
    """Refuse a NEW ENVELOPE answer stated by halves.

    **``_create._reject_incomplete_new_envelope``'s twin, and it was missing.**
    The name box carries no ``required`` and no ``minlength``, so clearing it is
    an ordinary browser action; ``envelope_name`` is ``load_default=None``,
    which makes marshmallow's field ``allow_none``, so ``""`` is normalized to
    ``None`` BEFORE ``validate.Length(min=1)`` runs -- and ``"   "`` passes
    Length outright.  Either row is unwritable
    (``ck_merchant_rules_one_answer`` for the first,
    ``ck_merchant_rules_envelope_name_not_blank`` for the second), so
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
        merchant: What the merchant it answers for is called, for the refusal.

    Raises:
        ValidationError: When the new-envelope answer is named without both of
            its own fields.
    """
    if statement.answer is not RuleAnswer.NEW_ENVELOPE:
        return
    named = (statement.envelope_name or "").strip()
    if not named or statement.category_id is None:
        raise ValidationError(
            f"A new envelope for {merchant} needs both a name and a "
            f"category.  Nothing was changed for it."
        )


def _reject_spending_answer(
    statement: RuleSubmission, merchant: str,
    account_payments: "frozenset[int]",
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

    **EVERY other answer is refused, and *ask me every time* stopped being an
    exception when the withdrawal did** (ruling **R-GS**, plan step
    ``bank_import:X-gd-2``).  The sentence here used to read "*never a
    purchase* and the withdrawal are both still legal, because both are TRUE
    of such a merchant" -- and the withdrawal no longer exists, so the only
    reason ever given for the second exemption named a deleted door.  Its
    replacement is not the same fact: a withdrawal left the merchant
    UNANSWERED, while *ask me every time* stores a promise this app cannot
    keep.  Such a line is rendered with no create control at all
    (:class:`~._bars.CreationBar`), so nothing will ever ask -- which is the
    chooser-whose-submission-can-never-succeed shape this package has closed
    four times.

    **And it is the standing bar that would be traded away.**  Restating
    *never a purchase* as *ask me every time* takes the merchant out of
    :attr:`~._bars.CreationBars.never` and leaves only
    :attr:`~._bars.CreationBar.PAYS_AN_ACCOUNT_YOU_HOLD`, which
    :mod:`._bars` records as INTERIM and deletes whole when ``credit_card:CC3b``
    ships.  Measured on the developer's own data: that is `Capital One Credit
    Card`, 9 unexplained outflows and `-$7,412.94`.  One select, no warning,
    under a caption reading *an answer can always be changed*.  Found by
    adversarial review 2026-08-26.

    So the refusal is stated the way ruling **R-GJ** states it -- *no answer
    lifts it*, and *never a purchase* is the answer that fits -- rather than as
    a list of the answers that happen to be wrong.  An owner who has answered
    for such a merchant may restate that answer; they may not exchange it for
    one that is false of the merchant.

    Args:
        statement: What the owner submitted for one merchant.
        merchant: What that merchant is called, for the refusal.
        account_payments: The merchant ids a source files as paying an account
            (:attr:`~._bars.CreationBars.account_payments`).

    Raises:
        ValidationError: When a spending answer is stated for one of them.
            Refuses THIS statement only -- the pass's other answers land, which
            is what the per-item savepoint is for.
    """
    if statement.merchant_id not in account_payments:
        return
    if statement.answer is RuleAnswer.NEVER:
        return
    raise ValidationError(
        f"Your bank files {merchant} as a payment to an account you "
        f"hold rather than as spending, so it cannot be filed in a budget "
        f"line -- the money it moved was spent somewhere else and your budget "
        f"already holds it there.  \"Never a purchase\" is the answer that "
        f"fits.  Nothing was changed for it."
    )


def _checked_category(
    statement: RuleSubmission, merchant: str, owner_id: int,
) -> Category:
    """Return the category the new-envelope answer names, refusing another's.

    The IDOR probe every create door in this project performs before a write.
    ``fk_merchant_rules_category_owner`` makes a foreign one unwritable,
    and this is what turns that into a sentence rather than a 500.

    Args:
        statement: What the owner submitted.
        merchant: What the merchant it answers for is called, for the refusal.
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
            f"That category is not one of yours, so {merchant} "
            f"cannot go under it.  Reload the page and pick another.  Nothing "
            f"was changed."
        )
    return category


@dataclass(frozen=True)
class _Answering:
    """What ONE pass over the merchant section reads, at one instant.

    **The package's own idiom, applied to the write door** (plan step
    ``bank_import:X-gd-1``): :class:`RuleView` makes this argument for the
    read side and :class:`~._scope.ReviewScope` makes it one tier down.  Every
    field here is read by :func:`state_rules` before its first write and
    used by every item of the pass, so threading them as six parameters was
    six chances for one item to be answered against a fact another item was
    answered against differently.

    Attributes:
        owner_id: The user the route proved owns the account.
        account_id: The account being answered for.
        stored: The answers already held, by merchant id.  **It is HELD IN
            STEP as rows are added**, which is why it is a
            mapping this pass mutates rather than a snapshot: a submission
            naming one merchant twice must restate the row it just wrote
            rather than insert a second and raise ``IntegrityError`` past that
            item's savepoint.
        names: This account's merchants by id (:func:`account_merchants`),
            which is both the scope a submitted id is checked against and
            where every sentence gets the name it prints.
        templates: The recurring definitions an answer on this account may
            name (:func:`offerable_templates`).
        account_payments: The merchants a source files as paying an account
            the owner holds (ruling **R-GJ**).
    """

    owner_id: int
    account_id: int
    stored: "dict[int, MerchantRule]"
    names: "dict[int, str]"
    templates: "dict[int, str]"
    account_payments: "frozenset[int]"


def _apply_one(
    statement: RuleSubmission, answering: _Answering,
) -> "str | None":
    """Record one statement, and return the sentence for it or ``None``.

    Args:
        statement: What the owner submitted for one merchant.
        answering: What this pass read and what it has written so far
            (:class:`_Answering`).

    Returns:
        One sentence naming what changed, or ``None`` when nothing did.

    Raises:
        ValidationError: On a template or category this owner may not name.
    """
    merchant = answering.names[statement.merchant_id]
    stored = answering.stored
    templates = answering.templates
    statement = _trimmed(statement)
    row = stored.get(statement.merchant_id)
    # **COMPLETENESS is checked BEFORE the unchanged short-circuit, and
    # OFFERABILITY after it.**  The two are different questions and only the
    # second may be deferred.  An incomplete NEW ENVELOPE answer -- a name with
    # no category, or a category with no name -- comes to the SAME four columns
    # as *ask me every time* through :func:`_columns_of`, which reads a field
    # only for the answer that uses it.  So against a stored *ask me every
    # time* row the short-circuit found "nothing changed" and returned before
    # the refusal ran: the owner cleared the name box, pressed Save, and the
    # receipt counted it as already answered -- while the identical click on an
    # UNANSWERED merchant said "a new envelope needs both a name and a
    # category".  Found by adversarial review 2026-08-26.
    #
    # It cannot fire on a faithful round trip, which is what the ordering below
    # protects: a stored NEW ENVELOPE row always has both fields and the
    # section renders both, so re-submitting the page unchanged never reaches
    # this refusal.
    _reject_incomplete_new_envelope(statement, merchant)
    # **UNCHANGED is decided before anything is VALIDATED, and the order is the
    # fix rather than a saving.**  A stored answer can stop being offerable
    # under the owner's feet -- a template deactivated through the templates
    # screen -- and the section renders it back as the answer it is.  Validating
    # first would refuse a submission that changes nothing, so pressing Save to
    # answer for one merchant would report a refusal for another the owner
    # never touched.  Restating what is already stored needs no check, because
    # the row it would write already exists.
    if row is not None and _same_answer(row, statement):
        return None
    if statement.answer is RuleAnswer.TEMPLATE:
        named = _checked_template(statement, merchant, templates)
        said = f"{merchant} goes in {named}."
    elif statement.answer is RuleAnswer.NEW_ENVELOPE:
        category = _checked_category(statement, merchant, answering.owner_id)
        said = (
            f"{merchant} gets a new envelope called "
            f"{statement.envelope_name}, under {category.display_name}."
        )
    elif statement.answer is RuleAnswer.NEVER:
        said = f"{merchant} is never a purchase."
    else:
        # **The fourth answer, and it is a DECISION rather than the absence of
        # one** (ruling **R-GS**).  It replaced the withdrawal, which deleted
        # the row: an owner who wants no standing answer for a merchant has
        # said something, and the screen that asks them to state a rule needs
        # to know they already answered it.
        said = f"{merchant}: ask me every time."
    if row is None:
        row = MerchantRule(
            user_id=answering.owner_id, account_id=answering.account_id,
            merchant_id=statement.merchant_id, **_columns_of(statement),
        )
        db.session.add(row)
        # **Held in step as rows are added**, so a submission naming one
        # merchant twice restates the row it just wrote rather than inserting a
        # second and raising ``IntegrityError`` past this item's savepoint.
        # Unreachable from the rendered form, which emits one row per merchant;
        # reachable by a crafted body, where the blast radius is the whole
        # pass.  Found by adversarial financial review 2026-08-19.
        stored[statement.merchant_id] = row
        return said
    for column, value in _columns_of(statement).items():
        setattr(row, column, value)
    return said


def state_rules(
    statements: "tuple[RuleSubmission, ...]",
    owner_id: int,
    account_id: int,
) -> StatedRules:
    """Record where this owner says each merchant goes.

    **It MOVES NO MONEY, and that is the whole reason it is its own door**
    rather than an item kind inside :func:`~._batch.apply_reviewed`.  A rule
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
    :class:`RuleView`, so one request that writes and then re-renders reads
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
        The :class:`StatedRules`.
    """
    stored = {
        row.merchant_id: row
        for row in db.session.query(MerchantRule).filter(
            MerchantRule.user_id == owner_id,
            MerchantRule.account_id == account_id,
        ).all()
    }
    # **ONE read of this account's merchants, and it does two jobs.**  It is
    # the set a submitted id is checked against, and it is where every sentence
    # below gets the name it prints -- so the door never asks twice for a fact
    # it already holds, which is the DRY rule this project applies to producer
    # calls inside one request.  It replaced a THIRD job at plan step
    # ``bank_import:X-gd-1``: it used to be what made a stored answer correct,
    # and ``fk_merchant_rules_merchant_account`` is that now.
    names = account_merchants(account_id)
    _refuse_unknown_merchants(statements, names)
    # **Read HERE rather than taken from a caller**, for the reason the
    # templates beside it are: this door reaches nothing a read pass holds, and
    # a fact threaded in across a write boundary would rest on an enumeration
    # of what cannot have changed.  One indexed statement over the account's
    # recorded lines (ruling **R-GJ**, plan step ``bank_import:X-ga``).
    answering = _Answering(
        owner_id=owner_id, account_id=account_id, stored=stored, names=names,
        templates=offerable_templates(account_id),
        account_payments=account_payment_merchants(account_id),
    )
    said, refused, unchanged = [], [], 0
    for statement in statements:
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
            _reject_spending_answer(
                statement, answering.names[statement.merchant_id],
                answering.account_payments,
            )
            sentence = _apply_one(statement, answering)
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
            _logger, logging.INFO, EVT_MERCHANT_RULE_STATED, BUSINESS,
            "An owner stated where a merchant's spending goes.",
            user_id=owner_id, account_id=account_id,
            merchant_id=statement.merchant_id,
            answer=statement.answer.value,
            # **The columns WRITTEN, not the ids submitted** (adversarial
            # review 2026-08-26).  ``_columns_of`` reads a field only for the
            # answer that uses it, so a body pairing *never a purchase* with a
            # ``template_id`` stores neither -- and logging the raw submission
            # would make the audit record assert a template the row does not
            # carry, contradicting the row it exists to explain.
            **{
                column: value
                for column, value in _columns_of(statement).items()
                if column in ("template_id", "category_id")
            },
        )
    return StatedRules(
        stated=tuple(said), refused=tuple(refused),
        unchanged_count=unchanged,
    )


def _refuse_unknown_merchants(
    statements: "tuple[RuleSubmission, ...]",
    names: "dict[int, str]",
) -> None:
    """Refuse a statement about a merchant this account has never seen.

    **It is a SENTENCE now, not the guard** (plan step ``bank_import:X-gd-1``).
    A stated answer names a ``merchant_id``, and
    ``fk_merchant_rules_merchant_account`` holds that row to this
    account -- so a foreign or invented merchant is unwritable rather than
    refused here.  What this buys is the same thing :func:`_checked_template`
    buys one refusal over: the constraint arrives as an ``IntegrityError``,
    which reaches the owner as "Something went wrong" with a logged traceback
    for what is ordinarily a stale page.

    Until then this WAS the guard.  It compared the submitted string against a
    DISTINCT over every recorded line, unioned with the answers already stored,
    and without it the table would have held an answer keyed on any string a
    caller liked -- inert, but unbounded write amplification against a table
    with no other ceiling.

    **It is the whole submission's rather than one item's**, and that has not
    changed: the section renders a SUBSET of this account's merchants, so a
    statement naming one outside it is a crafted request rather than a stale
    page, and there is no pass to salvage.  It is the asymmetry
    ``StatementPurchaseSchema`` already states for a malformed payload.

    Args:
        statements: What the owner submitted.
        names: This account's merchants by id (:func:`account_merchants`),
            which :func:`state_rules` has already read.

    Raises:
        ValidationError: When any statement names a merchant outside them.
    """
    unknown = {
        statement.merchant_id for statement in statements
        if statement.merchant_id not in names
    }
    if unknown:
        # **The ids are NOT named back.**  The refusal this replaced quoted the
        # merchant strings, which were the owner's own words and read as an
        # explanation; a row id is the app's own surrogate and means nothing to
        # anybody reading it, so quoting it would be noise that also says how
        # this account's keys are numbered.
        raise ValidationError(
            f"{len(unknown)} of the merchants you answered for are not ones "
            f"your bank has shown on this account, so there is nothing to say "
            f"about them.  Reload the page.  Nothing was changed."
        )
