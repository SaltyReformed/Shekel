"""What a merchant RULE is, and what this account has one for.

Plan step ``bank_import:X-f6a-3d``, decomposed at ``bank_import:X-gd-2``.  The
review screen's leftover list is 91 lines on the developer's own statement and
**21 merchants**, so the work it asks for is 91 decisions where the real
question was asked 21 times.  This module holds the ANSWER to those 21 and what
it can still resolve against; :mod:`._stating` is the door that records one and
:mod:`._placement` is what an answer comes to for a single line.

**The split is by CONSEQUENCE, not by size** (the module passed 1,000 lines and
that is what forced the question, not what answered it).  Everything here
READS: a value type, four indexed queries and one derivation over them, none of
which can mutate a row.  Everything in :mod:`._stating` writes.  A reader that
needed only to know what the owner said used to import a module that also held
the write door, its refusals and its receipt vocabulary.

**Nothing here writes money, and nothing here CAN.**  A rule is read to
SUGGEST; the only thing that records a purchase is an explicit destination
submitted for one specific line (:mod:`._create`).  That separation is the
developer's ruling of 2026-08-19 and it is what keeps ruling **R-FZ**'s *the
destination select IS the tick* whole: the select still opens on *leave this
line alone*, the rule is rendered beside it, and one sweep control -- the
same shape the per-class proposal sweep already has -- is what turns
suggestions into ticks.  A default that arrives pointing at money is exactly
what R-FZ removed, and a remembered default would be one.

**A rule resolves against the pass's own offer set, so it can never widen
it.**  :func:`~._placement.placements_for` picks from
:func:`~._candidates.destinations_for` -- narrowed by what this pass has
already matched -- rather than querying for a row of its own, which is the
property :func:`~._resolve.resolve_rows` rests on: a destination the screen may
not offer is one a rule cannot reach either.  Every way a rule can fail to
resolve is REPORTED rather than substituted for, because substituting is how a
suggestion becomes a guess.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.category import Category
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.transaction_template import TransactionTemplate




class RuleAnswer(enum.Enum):
    """Which of the five answers a stated rule gives (**R-GI**, **R-HT(a)**).

    **A closed set of five, and the model's CHECK is what closes it**
    (``ck_merchant_rules_one_answer``).  It is derived from which columns a row
    carries plus one boolean rather than stored beside them, because a stored
    discriminator is a second statement of what the columns already say --
    which is :meth:`of`'s own argument, and ruling **R-GS**'s.

    The absence of a rule is NOT a member: it is ``None`` where a
    :class:`StandingRule` would be, and the screen says *you have not said*
    rather than *you said nothing*.  Collapsing it into :attr:`ALWAYS_ASK` is
    the tempting move and it loses the one fact that separates them -- *I have
    not decided* against *I have decided to have no standing answer* -- which
    is exactly what an import must not confuse, because one of them is a
    question still owed to the owner and the other is a question already
    answered.

    **Three of the five SUGGEST and two do not, and the split is not
    cosmetic.**  :attr:`TEMPLATE` and :attr:`NEW_ENVELOPE` name a container for
    money LEAVING, so :func:`~._placement.placements_for` resolves them against
    one outflow line.  :attr:`INCOME_CATEGORY` names what money ARRIVING is, so
    :func:`~._placement.inflow_placement_for` resolves it against one inflow
    line.  :attr:`NEVER` names none and BARS -- it is a
    :class:`~._bars.CreationBar`, resolved before a destination is looked for
    (ruling **R-GJ**) -- and :attr:`ALWAYS_ASK` names none and bars nothing,
    which makes it the only answer whose effect on money is exactly the effect
    of having said nothing.

    **The three that suggest are told apart by DIRECTION as well as by
    container, and no answer resolves in both directions.**  An outflow under
    :attr:`INCOME_CATEGORY` names no container, and an inflow under
    :attr:`TEMPLATE` or :attr:`NEW_ENVELOPE` is a merchant CREDIT -- a refund,
    which ruling **R-HT(a)** files as a NEGATIVE purchase back into that same
    container.  **That second arm is `bank_import:X-gj-2b-2`'s and is not built
    here.**  The row itself is no longer what blocks it: plan step
    ``bank_import:X-gj-2b-1`` made ``ck_transaction_entries_positive_amount``
    ``amount <> 0``, so a negative purchase is writable.  What is missing is the
    resolution that names WHICH container, so until it ships such a line is
    reported as unresolved rather than filed somewhere the owner did not
    name.
    """

    TEMPLATE = "template"
    NEW_ENVELOPE = "new_envelope"
    INCOME_CATEGORY = "income_category"
    NEVER = "never"
    ALWAYS_ASK = "always_ask"

    @classmethod
    def of(cls, row: MerchantRule) -> "RuleAnswer":
        """Return which answer *row* holds, read off its own columns.

        **The inverse of :func:`_columns_of`, and the pair is graded as a round
        trip** over every member rather than by two independent cases: a fifth
        answer added to one side and not the other is the failure this shape
        invites, and the round trip is what catches it.

        Args:
            row: The stored rule.

        Returns:
            Its :class:`RuleAnswer`.  TOTAL with no fall-through arm, because
            ``ck_merchant_rules_one_answer`` has already made the five
            combinations exclusive -- and, in particular, pins
            ``never_a_purchase`` false on all three answers that name
            something, so the order these are asked in cannot change the
            answer.  **That is what the migration adding the fifth had to widen
            every PRE-EXISTING arm for** (plan step ``bank_import:X-gj-2a``): a
            CHECK naming only the new column's own arm would still admit a row
            carrying a template AND an income category, and this reading takes
            the container first, so such a row would file SPENDING under an
            answer stated about deposits.
        """
        if row.template_id is not None:
            return cls.TEMPLATE
        if row.envelope_name is not None:
            return cls.NEW_ENVELOPE
        if row.income_category_id is not None:
            return cls.INCOME_CATEGORY
        return cls.NEVER if row.never_a_purchase else cls.ALWAYS_ASK


@dataclass(frozen=True)
class StandingRule:
    """One stated answer to "where does this merchant's spending go?".

    Attributes:
        merchant_id: The :class:`~app.models.merchant.Merchant` this answers
            for, which is the key (plan step ``bank_import:X-gd-1``).  It was
            the bank's own string, matched to a line's own copy of it by
            equality; the merchant is a row now, so the key is its id.
        merchant: What that merchant is CALLED, carried beside its id because
            every sentence this module writes names it and the query that read
            the answer read the name with it.  The same read-model join
            :attr:`~._offers.BankLine.merchant` is.
        answer: Which of the four it is (:meth:`RuleAnswer.of`).
        template_id: The recurring definition to file into, for
            :attr:`RuleAnswer.TEMPLATE`; ``None`` otherwise.
        envelope_name: What to call the envelope to create, for
            :attr:`RuleAnswer.NEW_ENVELOPE`; ``None`` otherwise.
        category_id: The category to create it under, likewise.
        income_category_id: What a DEPOSIT from this merchant is, for
            :attr:`RuleAnswer.INCOME_CATEGORY`; ``None`` otherwise.  **A
            separate field from** :attr:`category_id` **for the reason the
            column is separate from its own**: the two answer different
            questions -- where this merchant's spending goes, and what its
            deposits are -- and one field holding either would need every
            reader to know which answer it was looking at before it could read
            it.
    """

    merchant_id: int
    merchant: str
    answer: RuleAnswer
    template_id: "int | None" = None
    envelope_name: "str | None" = None
    category_id: "int | None" = None
    income_category_id: "int | None" = None

    @property
    def is_new_envelope(self) -> bool:
        """Return whether this answer creates an envelope.

        **The screen's own question, answered here rather than as a truth test
        on :attr:`envelope_name` in a Jinja condition** -- which would be the
        arm INFERRED from a column rather than read from the one field that
        states it, the shape that made the existing-envelope destination
        unreachable from a browser at plan step X-f6a-3b.
        """
        return self.answer is RuleAnswer.NEW_ENVELOPE

    @property
    def is_income_category(self) -> bool:
        """Return whether this answer says what a DEPOSIT from here is.

        Asked here for the reason :attr:`is_new_envelope` is: the screen's own
        question, answered on the value that states it rather than inferred in
        a Jinja condition from whether an id happens to be set.
        """
        return self.answer is RuleAnswer.INCOME_CATEGORY

    @property
    def is_never(self) -> bool:
        """Return whether this answer is *never a purchase*.

        Asked here for the reason :attr:`is_new_envelope` is, and for one more:
        a template comparing ``answer.value == 'never'`` would be a string
        comparison standing in for an identity, which is the substitution this
        project's reference rule exists to refuse.
        """
        return self.answer is RuleAnswer.NEVER

    @property
    def is_always_ask(self) -> bool:
        """Return whether this answer is *ask me every time* (ruling **R-GS**).

        Asked here for the reason the two above are.  **It is the answer whose
        rendering is easiest to get wrong**, because it looks like the absence
        of one: the screen must show it SELECTED, on a control whose unselected
        state submits *I have not said* -- the same shape that once let a stale
        template silently withdraw a rule
        (:attr:`RuleView.stale_templates`).
        """
        return self.answer is RuleAnswer.ALWAYS_ASK

    @classmethod
    def of(cls, row: MerchantRule, merchant: str) -> "StandingRule":
        """Return *row* as the value every reader here shares.

        Args:
            row: The stored rule.
            merchant: What the merchant it answers for is called, read in the
                same statement as the row itself
                (:func:`rules_for`) -- so no reader here has to go back for
                a name to put in a sentence.

        Returns:
            Its :class:`StandingRule`, carrying the answer
            :meth:`RuleAnswer.of` reads off the columns the model's CHECK has
            already made exclusive.
        """
        return cls(
            merchant_id=row.merchant_id,
            merchant=merchant,
            answer=RuleAnswer.of(row),
            template_id=row.template_id,
            envelope_name=row.envelope_name,
            category_id=row.category_id,
            income_category_id=row.income_category_id,
        )


def _named_templates(
    template_ids: "set[int]", account_id: int,
) -> "dict[int, str]":
    """Return ``{id: name}`` for *template_ids* on *account_id*, in one query.

    **The account clause closes finding N-353** (`X-gd-1`'s adversarial
    security review, 2026-08-25).  This was the one query on user data in this
    package with no scope clause: it selected by id alone, and the NAME it
    returns is RENDERED, on the control that says where a merchant's spending
    goes.  Nothing reachable leaked -- the ids arrive from
    ``merchant_rules.template_id`` on rows already scoped to this owner and
    account, and ``fk_merchant_rules_template_account`` holds each to its
    account -- but that is safety by DERIVATION over an open set of future
    callers, and this project has measured its own only-way arguments wrong
    often enough to stop accepting them.  The caller already holds the account,
    so the safety is local now and costs nothing.

    Args:
        template_ids: The ids wanted.  Empty issues no query -- ``IN ()`` is a
            statement with no rows to find.
        account_id: The account whose templates may be named.

    Returns:
        The names by id.  An id belonging to another account is ABSENT rather
        than refused, which is what :meth:`RuleView.label_for` is total over:
        a name it cannot find is a phrase, not a raise on a read path.
    """
    if not template_ids:
        return {}
    rows = (
        db.session.query(TransactionTemplate.id, TransactionTemplate.name)
        .filter(
            TransactionTemplate.id.in_(template_ids),
            TransactionTemplate.account_id == account_id,
        )
        .all()
    )
    return dict(rows)


def _named_categories(
    category_ids: "set[int]", owner_id: int,
) -> "dict[int, str]":
    """Return ``{id: display name}`` for *category_ids* of *owner_id*.

    :func:`_named_templates`' twin, for the other arm a stored answer can name
    (plan step ``bank_import:X-gd-2``).  It exists for the same reason and is
    scoped for the same reason: the NAME it returns is rendered, so the safety
    is local rather than inherited from where the ids came from.

    Args:
        category_ids: The ids wanted.  Empty issues no query.
        owner_id: The user whose categories may be named.

    Returns:
        The display names by id.  Built with
        :attr:`~app.models.category.Category.display_name` rather than a
        column, because that is what every other category control on this
        screen shows and a bare ``item_name`` would read as a different
        category.

    """
    if not category_ids:
        return {}
    rows = (
        db.session.query(Category)
        .filter(Category.id.in_(category_ids), Category.user_id == owner_id)
        .all()
    )
    return {row.id: row.display_name for row in rows}


@dataclass(frozen=True)
class RuleView:
    """What the owner has SAID, and what it can still resolve against.

    **One derivation at one instant**, which is the same argument
    :class:`~._scope.ReviewScope` makes one tier down and is not a parameter
    list dressed up: a rule names a template and a category, and whether
    either is still reachable is read from the same moment the rule is.
    Read separately, a category archived between two of those reads would be
    resolvable by one caller and refused by the next inside one render.

    Attributes:
        rules: What the owner has answered, by merchant id
            (:func:`rules_for`).
        template_names: What to call each recurring definition a rule on this
            account may name (:func:`offerable_templates`) -- the option list,
            and the sentence an unresolvable placement explains itself with.
        active_categories: The categories a new envelope may still be created
            under, and the ones an income answer may still file a deposit into
            (:func:`active_category_names`).  **One mapping for both**,
            because it is one question -- has the owner retired this category
            -- and an answer naming a retired one is reported rather than acted
            on, in either direction.  It carries the NAMES too, so the card
            that states an income answer prints the label from the same read
            that resolved the answer.
        stale_templates: What to call a template a stored rule NAMES that is
            no longer offerable, by id.  **A rendered control must be able to
            show the answer it holds, even a stale one**: without this the
            select had no option carrying the stored value, so it displayed --
            and submitted -- its first, which is *I have not said*, and the
            next Save silently WITHDREW a rule the owner never touched.  A
            template is deactivated or un-enveloped through
            ``routes/templates/crud.py``, so this is live user state rather
            than a hypothetical.  Found by adversarial financial review
            2026-08-19.  Usually empty, and then it costs no query at all.
        stale_categories: The same, for a CATEGORY a stored answer names that
            is no longer active, by id (plan step ``bank_import:X-gd-2``).
            **BOTH answer columns feed it since plan step
            ``bank_import:X-gj-2a``** -- the category a *new envelope* answer
            creates under, and the income category a deposit answer files under
            -- because what this dict answers is *what is this archived
            category called*, which is one question and has no direction.  One
            id can arrive from both answers on two different merchants, and a
            set is what makes that cost one lookup.  **The template arm had this and the
            category arm did not**, and the category arm's failure was worse:
            with no option carrying the stored value the select submitted
            ``""``, which reaches the door as a NEW ENVELOPE answer missing its
            category and is REFUSED -- so pressing Save to answer about one
            merchant printed "a new envelope needs both a name and a category"
            for another the owner never touched, on every pass, naming the
            wrong half.  Found by two adversarial reviews 2026-08-26, which
            also measured what made it reachable: teaching
            ``archive_helpers.category_has_usage`` about this table turned a
            permanent delete (which cascaded the rule away, leaving nothing to
            mis-render) into an ARCHIVE, which is exactly this state.
    """

    rules: "dict[int, StandingRule]"
    template_names: "dict[int, str]"
    active_categories: "dict[int, str]"
    stale_templates: "dict[int, str]"
    stale_categories: "dict[int, str]"

    @classmethod
    def build(cls, owner_id: int, account_id: int) -> "RuleView":
        """Derive the view for one pass over one account.

        Args:
            owner_id: The user the caller proved owns the account.
            account_id: The account being reviewed.

        Returns:
            The :class:`RuleView`.  Three small indexed reads, plus one each
            for a stored answer whose template has stopped being offerable or
            whose category has been archived; the review screen's cost is its
            3.6 s candidate derivation, not this.
        """
        rules = rules_for(owner_id, account_id)
        offerable = offerable_templates(account_id)
        active = active_category_names(owner_id)
        return cls(
            rules=rules,
            template_names=offerable,
            active_categories=active,
            stale_templates=_named_templates(
                {
                    rule.template_id for rule in rules.values()
                    if rule.template_id is not None
                    and rule.template_id not in offerable
                },
                account_id,
            ),
            stale_categories=_named_categories(
                {
                    named for rule in rules.values()
                    for named in (rule.category_id, rule.income_category_id)
                    if named is not None and named not in active
                },
                owner_id,
            ),
        )

    def label_for(self, template_id: int) -> str:
        """Return what to call *template_id*, offerable or not.

        Args:
            template_id: The recurring definition a rule names.

        Returns:
            Its name.  TOTAL, because a caller asking is holding an id a rule
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

    def category_label_for(self, category_id: int) -> str:
        """Return what to call *category_id* when it is no longer active.

        :meth:`label_for`'s twin, and TOTAL for the same reason: a caller
        asking is holding an id a stored rule carries, and
        ``fk_merchant_rules_category_owner`` holds that id to this owner -- so
        the only way it is unknown here is a row hard-deleted between this
        view's two reads, where the honest answer is a phrase rather than a
        raise on a read path.

        Args:
            category_id: The category a stored *new envelope* answer names.

        Returns:
            Its display name, or a phrase.  **Active first**, since plan step
            ``bank_import:X-gj-2a``: this answered only for an ARCHIVED
            category, which was every category a caller had reason to name
            while the only readers were the two unresolved-placement sentences.
            Ruling **R-HT(a)**'s card names a LIVE one, and a reader that fell
            through to *an archived category* for it would print that phrase on
            the sentence describing a working rule.
        """
        return (
            self.active_categories.get(category_id)
            or self.stale_categories.get(category_id)
            or "an archived category"
        )


def rules_for(
    owner_id: int, account_id: int,
) -> "dict[int, StandingRule]":
    """Return every rule this owner has stated for this account, by merchant.

    **The merchant's NAME is read in the same statement as its answer** (plan
    step ``bank_import:X-gd-1``).  Every sentence this module writes names the
    merchant and the screen prints it beside the control, so a second read for
    it would be a redundant producer call inside one request -- which this
    project treats as a DRY violation rather than a cost.

    Args:
        owner_id: The user the caller proved owns the account.
        account_id: The account being reviewed.

    Returns:
        ``{merchant_id: StandingRule}``.  One answer per merchant is
        structural (``uq_merchant_rules_account_merchant``), so the
        mapping cannot lose a row to a collision.
    """
    rows = (
        db.session.query(MerchantRule, Merchant.name)
        .join(
            Merchant,
            db.and_(
                Merchant.id == MerchantRule.merchant_id,
                Merchant.account_id == MerchantRule.account_id,
            ),
        )
        .filter(
            MerchantRule.user_id == owner_id,
            MerchantRule.account_id == account_id,
        )
        .all()
    )
    return {
        row.merchant_id: StandingRule.of(row, name) for row, name in rows
    }


def account_merchants(account_id: int) -> "dict[int, str]":
    """Return every merchant this account's statements have ever named.

    **It was a UNION of two derivations and it is now a table** (plan step
    ``bank_import:X-gd-1``).  ``statable_merchants`` computed *every merchant
    this account's recorded lines name* and unioned it with *every merchant
    already answered for*; the second half existed because deleting an import
    took a merchant's lines with it and would otherwise have made a stated
    answer unwithdrawable -- the section renders an answered merchant whichever
    half it came from, and a check reading only the first half would have
    refused that submission whole.  An ANSWERED
    :class:`~app.models.merchant.Merchant` row OUTLIVES its lines, so the union
    IS this table and the second half has nothing left to add.  The set is
    exactly that union rather than a superset of it: deleting an import sweeps
    the merchants no line names and no answer is about
    (``statement_import._undo._forget_orphan_merchants``), which is the half
    that preserved nothing.

    **What it is still FOR is the sentence, not the scope.**  A rule names a
    ``merchant_id`` held to this account by
    ``fk_merchant_rules_merchant_account``, so a merchant this account
    has never seen is unwritable rather than refused; this read is what lets
    :func:`_refuse_unknown_merchants` answer a stale page with a sentence
    instead of an ``IntegrityError``, and what gives every refusal here a name
    to print.

    Args:
        account_id: The account being reviewed.  ``merchants`` carries no
            ``user_id`` of its own -- it is account-scoped, exactly as
            ``bank_statement_lines`` is -- so the account IS the ownership
            statement here.

    Returns:
        ``{merchant_id: name}``.  **In no stated order**, because both readers
        index it and neither iterates: the order a screen shows merchants in is
        :func:`~._section.merchant_section`'s, which sorts by name over a
        NARROWER set and is graded there.  A promise of order here would be one
        no caller keeps and no case could catch breaking.
    """
    rows = (
        db.session.query(Merchant.id, Merchant.name)
        .filter(Merchant.account_id == account_id)
        .all()
    )
    return dict(rows)


def offerable_templates(account_id: int) -> "dict[int, str]":
    """Return the recurring definitions a rule on this account may name.

    **Not every template, and the filter is the create door's own.**
    :func:`~._create.create_purchase_from_line` files a purchase through
    ``entry_service.create_entry``, which refuses a parent that does not track
    purchases -- so a rule naming a template that generates a plain budget
    line would be an answer whose every placement is refused.  The other two
    clauses mirror :func:`~._candidates.destinations_for`: a transfer's legs
    are the transfer service's, and money coming IN is not a purchase.

    Args:
        account_id: The account being reviewed.

    Returns:
        ``{template_id: name}``, which is what the rule control renders and
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


def active_category_names(owner_id: int) -> "dict[int, str]":
    """Return the categories a new envelope may still be created under.

    **The create door's own clause, asked where the SUGGESTION is made.**
    ``_create._owned_category`` refuses an archived category, because
    ``category_service.list_active_categories`` is what the picker renders --
    so a placement naming one would be a control whose submission can never
    succeed, which is the shape finding **N-325** was just closed for one
    field over.  A HARD-deleted category takes its rule with it
    (``fk_merchant_rules_category_owner`` cascades); archiving is a soft
    state that leaves the row pointing at something the door will refuse, so
    this is the live half.

    **It returns the NAMES as well as the ids since plan step
    ``bank_import:X-gj-2a``**, and the reason is that a second reader appeared
    rather than that names are nice to have: ruling **R-HT(a)**'s income answer
    is stated as a category, so the Reconcile card's sentence has to print what
    that category is CALLED -- and reading it back in a second query would be
    the redundant producer call this project treats as a DRY violation, with
    the sharper risk that the label a card prints could then come from a
    different instant than the answer it describes.  Membership is unchanged:
    every existing reader asks ``id in view.active_categories``, which a
    mapping answers exactly as a set did.

    Args:
        owner_id: The user whose categories may be reached.

    Returns:
        ``{category_id: display name}``, built with
        :attr:`~app.models.category.Category.display_name` for the reason
        :func:`_named_categories` builds its own that way: a bare ``item_name``
        reads as a different category from the one every other control shows.
    """
    rows = (
        db.session.query(Category)
        .filter(Category.user_id == owner_id, Category.is_active.is_(True))
        .all()
    )
    return {row.id: row.display_name for row in rows}
