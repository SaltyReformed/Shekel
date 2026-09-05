"""Every merchant this account has seen, and what the owner said about each.

Plan step ``bank_import:X-gk``, ruling **bank_import:R-IC**.  The audit's own
fix column named this surface -- *a Rules settings list, one row per rule, edit
on click* (``docs/design/bank_import_audit.md``, row 11) -- and only the second
half of that sentence was ever built.  This is the first half: the durable home
for a merchant's standing answer.

**What was wrong is that the question had three PARTIAL homes and no whole
one.**  Measured 2026-08-31 on a migrated clone of the developer's own
database, account 1: **62 merchants, 30 answered and 32 not**.
:class:`~._section.MerchantSection` asks about a merchant only while this
pass has an unexplained outflow for it AND nobody has answered -- **0 rows**
that day.  :class:`~._section.MerchantRegister` shows the 30 answered.
:func:`~._offered_rules.rules_worth_offering` offers a rule only for a merchant
the pass just filed spending for.  So **32 of 62 -- every unanswered one -- were
on no surface at all**, and there was no way to say where ``Duke Energy`` (8
lines, `$2,232.34`), ``T-Mobile`` (8 lines, `$540.00`) or ``Audible`` (7 lines,
`$111.72`) goes without first finding an unexplained line from it.

**One merchant is edited at a time, and that is structural rather than
cosmetic** (developer, 2026-08-31).  The register submits every merchant it
renders in one form, and this arc has paid three times for the blast radius
that gives one press: a deactivated template made a select fall onto *I have
not said* and the next Save silently WITHDREW a rule; an archived category made
another fall onto the empty option, so a Save aimed at one merchant printed a
refusal for a second the owner never touched; and the incomplete-new-envelope
short-circuit read "nothing changed" for a third.  A form carrying ONE merchant
puts that whole class OUT OF REACH of the rendered page -- which is the honest
claim, and not the "unconstructible" this said until an adversarial review
built a two-merchant body and watched one land while the other was refused.
The DOOR is shared with three surfaces that legitimately submit many merchants
at once, so it reads every merchant a body names; what this page changes is
what a browser can send from it.

**It is also what keeps the render bounded.**  Measured the same day: the
register's control renders **30 rows in 129,413 bytes**, a mean of **4,313
bytes a row**, because every row carries a ``<select>`` over all 17 offerable
templates and another over all 31 active categories.  At 62 merchants that
shape is ~267 KB.  A row that STATES its answer and opens the control only when
asked renders the two big selects once, for the row being edited -- which is
what finding **N-326** asks for, and this page does not rebuild it wider.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.  It READS and never
writes -- the door is
:func:`~app.routes.accounts._statement_rules.record_submitted_rules`, which the
review queue, the register and the Reconcile receipt already post to, so four
surfaces keep ONE grader and ONE writer.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date

from app.extensions import db
from app.models.statement_import import BankStatementLine

from ._bars import MerchantAnswers
from ._rules import RuleAnswer, RuleView, account_merchants
from ._section import MerchantSummary, merchant_summary, offered_answers


#: How many rows one render of the directory may draw.
#:
#: **A different number from :data:`~._accepted_view.REGISTER_LIMIT` for a
#: different reason**, which is the distinction
#: :data:`~app.schemas.validation.merchant_rules._MAX_RULE_ITEMS` draws against
#: the money batch's ceiling.  That one bounds VALUED acts -- each re-prices its
#: member rows through :func:`~._release.planned_removals` -- so 50 is a cost
#: bound.  A row here states a name, a phrase and two figures and costs no
#: derivation at all, so this bounds the PAGE: 200 rows is past any account
#: today (62 on the developer's own) and still refuses to draw the 20,000
#: ``_secu_csv.MAX_LINES`` admits in one import.
#:
#: **The ceiling is on the RENDER and not on the derivation**, and saying so is
#: the honest half: reading the answers and the activity is one indexed query
#: each over the account, whatever this number is.  What it bounds is the
#: thing finding **N-326** measures, which is bytes on a page.
#:
#: **It is a DEFAULT and not a refusal**, which this note said the opposite of
#: until an adversarial review measured it (2026-08-31).  It read "still
#: refuses to draw the 20,000 ``_secu_csv.MAX_LINES`` admits in one import" --
#: and it refuses nothing: the footer renders a link that lifts it, and
#: ``?all=1`` drew all 260 rows of a staged account.  What the ceiling gives is
#: a bounded page BY DEFAULT and an owner who has to ask for the whole one.
DIRECTORY_LIMIT: int = 200

#: What a row says where the owner has answered nothing.  **It is a phrase and
#: not an empty cell**, for the reason :class:`~._rules.RuleAnswer` has no
#: member for it: *I have not decided* is a question still owed, and a blank
#: reads as a screen that failed to render rather than as a state.
NOT_SAID: str = "You have not said"


class MerchantWanted(enum.Enum):
    """Which merchants a render of the directory is about.

    **Three filters over ONE list rather than three lists**, because the
    membership question is the only thing that differs: the row, the phrase and
    the door are identical whichever is chosen.  The counts beside them are of
    the WHOLE account (:class:`FilterCount`), so switching filter never changes
    what any of the three says.
    """

    ALL = "all"
    UNANSWERED = "unsaid"
    ANSWERED = "answered"

    @property
    def label(self) -> str:
        """Return what the filter is called on the bar.

        Returns:
            Its label.  **The service's and not the template's**, for the
            reason :attr:`~._reconcile.Tab.label` is: a template restating a
            partition is a second place for it to be wrong.
        """
        return _WANTED_LABELS[self]

    def holds(self, entry: "MerchantEntry") -> bool:
        """Return whether *entry* belongs under this filter.

        Args:
            entry: One merchant of the directory.

        Returns:
            Whether it is wanted.  Asked through
            :attr:`MerchantEntry.is_answered`, which reads the presence of a
            stored answer -- never a string comparison against an answer's
            name, which is the substitution this project's reference rule
            refuses.
        """
        if self is MerchantWanted.ALL:
            return True
        if self is MerchantWanted.ANSWERED:
            return entry.is_answered
        return not entry.is_answered


#: What each filter is called.  A mapping rather than a method body so
#: :meth:`MerchantWanted.label` is total by construction: a member added
#: without a label raises here rather than rendering an empty tab.
_WANTED_LABELS: "dict[MerchantWanted, str]" = {
    MerchantWanted.ALL: "All",
    MerchantWanted.UNANSWERED: "You have not said",
    MerchantWanted.ANSWERED: "Answered",
}


@dataclass(frozen=True)
class MerchantActivity:
    """How much of this account's statement record one merchant names.

    **What makes a bank abbreviation answerable** (developer, 2026-08-31).  The
    developer's own unanswered set holds ``Dbcode. Io``, ``Emperors Choice C``
    and ``Fid Bkg Svc Llc Moneyline``; a count and a day are what turn one of
    those into a merchant somebody recognises, and they rank which unanswered
    rows are worth answering for.

    **It counts every line this account holds from the merchant, explained or
    not, and it is not a pass.**  The register carries no count deliberately --
    counting WAITING lines is the review pass's work and a surface with no pass
    would be stating a figure it cannot know
    (:class:`~._section.MerchantRegister`).  This is the other question: how
    much of the bank's record names this merchant at all, which is one grouped
    read over ``budget.bank_statement_lines`` with no calendar, no matcher and
    no candidate derivation behind it.

    Attributes:
        line_count: How many recorded lines name the merchant.  ``0`` where an
            ANSWERED merchant has outlived the lines that prompted it, which is
            a real state: a merchant row survives its lines
            (:func:`~._rules.account_merchants`).
        last_seen: The latest day the bank posted one of them, or ``None``
            where there are none left.
    """

    line_count: int
    last_seen: date | None


@dataclass(frozen=True)
class MerchantEntry:
    """One merchant of the directory: the row, what it says, what it did.

    **It COMPOSES a** :class:`~._section.MerchantSummary` **rather than adding
    fields to it**, which is the shape :class:`~._section.WaitingMerchant`
    already takes and for the same reason: the summary is *which merchant, and
    what did they say*, and it is shared by three surfaces that do not share
    what surrounds it.

    Attributes:
        summary: The merchant and its stored answer, built by the one producer
            all four surfaces share (:func:`~._section.merchant_summary`).
        says: What the stored answer says, in words -- or :data:`NOT_SAID`.
            **Derived here and never in Jinja**: the four answers are told
            apart by :class:`~._rules.RuleAnswer` identity, and a template
            branching on ``answer.value == 'never'`` would be the string
            comparison standing in for an identity that this project's
            reference rule exists to refuse.
        activity: How much of the bank's record names this merchant
            (:class:`MerchantActivity`).
        is_open: Whether THIS row is the one whose control is showing.  **A
            screen fact carried on the model**, exactly as
            :class:`~._reconcile.Tab` is: which row is open decides which
            markup the page draws, and a page deciding it for itself would be
            a second statement of what the request already said.
    """

    summary: MerchantSummary
    says: str
    activity: MerchantActivity
    is_open: bool = False

    @property
    def is_answered(self) -> bool:
        """Return whether the owner has stated an answer for this merchant.

        Returns:
            Whether a rule is stored.  **The presence of the row and not a
            fifth answer**: :class:`~._rules.RuleAnswer` has no member for *I
            have not said*, because not having said something is the absence of
            a row, and collapsing it into *ask me every time* would lose the
            one fact that separates a question still owed from a question
            already answered.
        """
        return self.summary.rule is not None


@dataclass(frozen=True)
class FilterCount:
    """One filter of the bar, and how many merchants it holds.

    **Counted over the WHOLE account and never over what is rendered**, which
    is the property that lets the bar be read while a search is narrowing the
    list: *All 62 / You have not said 32 / Answered 30* answers "what do I
    still owe" whatever is on screen, where a count of the shown rows would
    answer "what did I just type".

    Attributes:
        wanted: The filter (:class:`MerchantWanted`).
        count: How many of this account's merchants it holds.
    """

    wanted: MerchantWanted
    count: int


@dataclass(frozen=True)
class MerchantDirectory:
    """Every merchant this account has seen, as one render shows them.

    Attributes:
        entries: The rows this render DRAWS, ascending by merchant name --
            already narrowed by the filter and the search, and already cut to
            :data:`DIRECTORY_LIMIT`.
        counts: One :class:`FilterCount` per filter, in bar order, over the
            whole account.
        matched_count: How many merchants the filter and the search matched
            before the ceiling.  **The denominator the footer needs**: a list
            that is truncated and does not say so is a page claiming to be the
            whole record, which is the disclosure ruling **bank_import:R-GX**
            already requires of the accepted register.
        templates: The recurring definitions a rule on this account may name,
            ``(id, name)`` ascending by name -- the option list the open row
            renders, and the same set :func:`~._stating.state_rules` checks a
            submission against, so the control cannot offer what the door
            refuses.
    """

    entries: "tuple[MerchantEntry, ...]"
    counts: "tuple[FilterCount, ...]"
    matched_count: int
    templates: "tuple[tuple[int, str], ...]"

    @property
    def total(self) -> int:
        """Return how many merchants this account has seen at all.

        **Read off the ALL filter's own count rather than stored beside it**,
        so the headline number and the bar can never disagree -- and named
        here rather than indexed out of :attr:`counts` by position, which
        would make a reordering of :class:`MerchantWanted` silently change
        what a page states.

        Returns:
            The whole account's merchant count.  It raises rather than
            defaulting if no ALL count is present, because :attr:`counts` is
            built over every member of the enum: an absence would be a
            producer defect, and a ``0`` would report it as an empty account.
        """
        return next(
            count.count for count in self.counts
            if count.wanted is MerchantWanted.ALL
        )

    @property
    def withheld_count(self) -> int:
        """Return how many matched merchants this render did not draw.

        Returns:
            The remainder the ceiling cut.  Derived rather than stored, so it
            cannot disagree with the list beside it.
        """
        return self.matched_count - len(self.entries)

    @property
    def opened(self) -> "MerchantEntry | None":
        """Return the row whose control is showing, or ``None``.

        **What a route 404s on.**  A request naming a merchant this account has
        never seen opens no row, and the honest answer is the security response
        rule's -- 404 for both *not found* and *not yours* -- rather than a page
        that silently ignores half the URL.

        Returns:
            The open entry, or ``None`` when no row is open.
        """
        for entry in self.entries:
            if entry.is_open:
                return entry
        return None


def merchant_activity(account_id: int) -> "dict[int, MerchantActivity]":
    """Return what this account's recorded lines say about each merchant.

    ONE grouped read over ``budget.bank_statement_lines``, served by
    ``idx_bank_statement_lines_account_merchant``.

    Args:
        account_id: The account whose lines to measure.  ``bank_statement_lines``
            carries no ``user_id`` of its own -- it is account-scoped, exactly
            as ``merchants`` is -- so the account IS the ownership statement
            here, and the caller has already proved it.

    Returns:
        ``{merchant_id: MerchantActivity}``, holding only the merchants some
        line names.  A merchant with no line left is ABSENT rather than carried
        as a zero, because the caller has the whole merchant list and pairs
        them (:func:`merchant_directory`) -- so the zero is stated in exactly
        one place.
    """
    rows = (
        db.session.query(
            BankStatementLine.merchant_id,
            db.func.count(BankStatementLine.id),
            db.func.max(BankStatementLine.posted_on),
        )
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.merchant_id.isnot(None),
        )
        .group_by(BankStatementLine.merchant_id)
        .all()
    )
    return {
        merchant_id: MerchantActivity(line_count=count, last_seen=last)
        for merchant_id, count, last in rows
    }


def _template_phrase(summary: MerchantSummary, view: RuleView) -> str:
    """Return what a TEMPLATE answer says, in words.

    Args:
        summary: The merchant row, whose answer names a template.
        view: What the owner has said and what it can resolve against.

    Returns:
        The recurring definition's name, marked where the picker can no longer
        show it.  **The mark is derived from
        :attr:`~._section.Unofferable.template`, which the shared producer has
        already decided**, rather than from a second membership test here: two
        readings of *is this still offerable* is exactly how the option list
        and the sentence beside it come to disagree.
    """
    named = view.label_for(summary.rule.template_id)
    if summary.unofferable.template is None:
        return named
    return f"{named} -- no longer offered"


def _new_envelope_phrase(
    summary: MerchantSummary, categories: "dict[int, str]",
) -> str:
    """Return what a NEW ENVELOPE answer says, in words.

    **It names the CATEGORY as well as the envelope**, because the category is
    what every spending report groups by -- which is the same reason the
    control's own category select carries no default (``_merchant_rule_macros
    .html``).  A phrase naming only the envelope would let an owner check half
    of what they answered.

    Args:
        summary: The merchant row, whose answer creates an envelope.
        categories: What each ACTIVE category of this owner is called, from the
            one read the caller already performs for the picker -- so the
            phrase and the options the owner would choose between come from one
            list rather than two.

    Returns:
        The phrase.  TOTAL over every stored answer: an ARCHIVED category is
        named from :attr:`~._section.Unofferable.category`, an active one from
        *categories*, and the one remaining case -- a category hard-deleted
        between two reads of one render -- gets a phrase rather than a raise on
        a read path, which is the answer
        :meth:`~._rules.RuleView.category_label_for` already gives for its own.
    """
    rule = summary.rule
    named = f'a new envelope called "{rule.envelope_name}"'
    if summary.unofferable.category is not None:
        return f"{named}, under {summary.unofferable.category} -- archived"
    under = categories.get(rule.category_id)
    return f"{named}, under {under}" if under else named


def says_of(
    summary: MerchantSummary, view: RuleView, categories: "dict[int, str]",
) -> str:
    """Return what this merchant's stored answer says, in words.

    **TOTAL over the five answers and their absence**, and told apart by
    :class:`~._rules.RuleAnswer` identity rather than by a truth test on a
    column: inferring the arm from ``envelope_name`` being set is the shape
    that made the existing-envelope destination unreachable from a browser at
    plan step ``X-f6a-3b``.

    Args:
        summary: The merchant row (:func:`~._section.merchant_summary`).
        view: What the owner has said and what it can resolve against.
        categories: What each of this owner's active categories is called.

    Returns:
        The phrase the row prints, or :data:`NOT_SAID`.

    Raises:
        ValueError: When the stored answer is a member this function has no
            phrase for.  **A programming error rather than a designed
            refusal**: every value here is read off a row by
            :meth:`~._rules.RuleAnswer.of`, so it fires only if a member is
            added to the enum and not to this chain -- which is exactly what
            ruling **R-HT(a)**'s member did before this arm existed.
    """
    rule = summary.rule
    if rule is None:
        return NOT_SAID
    if rule.answer is RuleAnswer.TEMPLATE:
        return _template_phrase(summary, view)
    if rule.answer is RuleAnswer.NEW_ENVELOPE:
        return _new_envelope_phrase(summary, categories)
    if rule.answer is RuleAnswer.INCOME_CATEGORY:
        return _income_phrase(rule, view)
    if rule.answer is RuleAnswer.NEVER:
        return "Never a purchase"
    if rule.answer is RuleAnswer.ALWAYS_ASK:
        return "Ask me every time"
    # **NAMED rather than reached by falling through.**  This chain ended on a
    # bare ``return "Ask me every time"``, so ruling **R-HT(a)**'s income
    # answer -- stored correctly, filing money correctly -- would have been
    # DESCRIBED on the merchants page as the answer the owner did not give.
    # Caught by ``TestWhatARowSays``, which walks every enum member, and it is
    # the third chain of this shape plan step ``bank_import:X-gj-2a`` had to
    # close (the route's submission dispatch and the receipt's sentence were
    # the others).
    raise ValueError(
        f"{rule.answer} is a rule answer the merchants directory cannot "
        f"describe; give it a phrase rather than letting it print another "
        f"answer's.",
    )


def _income_phrase(rule, view: RuleView) -> str:
    """Return what an INCOME-CATEGORY answer says, in words.

    Ruling **R-HT(a)**, plan step ``bank_import:X-gj-2a``.  The row states what
    a DEPOSIT from this merchant is, which is a different sentence from the
    three about spending -- *deposits are* rather than *goes in*.

    **The ARCHIVED case is marked, exactly as the new-envelope phrase marks
    its own** (:func:`_new_envelope_phrase`): a stored answer naming a category
    the owner has since retired still has to render, or the row cannot show the
    answer it holds -- and it is the state
    :func:`~._placement._income_placement` reports rather than acts on, so the
    directory must not present it as working.

    Args:
        rule: The stored answer, whose ``answer`` is
            :attr:`~._rules.RuleAnswer.INCOME_CATEGORY`.
        view: What the owner has said and what it can resolve against, which
            names an active category and a stale one alike
            (:meth:`~._rules.RuleView.category_label_for`).

    Returns:
        The phrase the row prints.
    """
    named = view.category_label_for(rule.income_category_id)
    if rule.income_category_id in view.active_categories:
        return f"Deposits are income under {named}"
    return f"Deposits are income under {named} -- archived"


@dataclass(frozen=True)
class DirectoryAsk:
    """What one render of the directory was asked for.

    **A parameter object rather than four more arguments**, because these four
    ARE one cohesive entity -- *which merchants, in what state, and how many of
    them* -- which is the remedy this project takes for a PUBLIC function over
    the argument ceiling.  It is also the one value a ROUTE builds: every field
    here comes off the request, and nothing below the route may read one.

    Attributes:
        wanted: Which merchants this render is about
            (:class:`MerchantWanted`).
        text: What the owner typed in the search box.  Matched
            case-insensitively against the merchant's NAME, which is the only
            thing on a row a person knows to type.
        opened: The merchant whose control is showing, or ``None``.  **It
            overrides the filter and the search for its own row**, so an answer
            that moves a merchant out of the current filter cannot delete the
            control being used to give it.
        limit: How many rows to draw, or ``None`` for all of them.  See
            :data:`DIRECTORY_LIMIT`.
    """

    wanted: MerchantWanted = MerchantWanted.ALL
    text: str = ""
    opened: int | None = None
    limit: int | None = DIRECTORY_LIMIT

    @property
    def needle(self) -> str:
        """Return the search text as it is compared.

        Returns:
            It, stripped and folded to lower case.  **Folded once here rather
            than per row**, so 62 comparisons cannot each fold it differently
            and the value the box redisplays stays exactly what was typed.
        """
        return self.text.strip().lower()

    def shows(self, entry: "MerchantEntry") -> bool:
        """Return whether *entry* survives the filter and the search.

        Args:
            entry: One merchant of the account.

        Returns:
            Whether it is shown.  **An OPEN row is shown whatever the filter
            and the search say**, because a row the owner is editing must not
            vanish under them when the answer they are about to give would move
            it out of the filter.
        """
        if entry.is_open:
            return True
        if not self.wanted.holds(entry):
            return False
        return not self.needle or self.needle in entry.summary.merchant.lower()


def _drawn(
    matched: "tuple[MerchantEntry, ...]", limit: "int | None",
) -> "tuple[MerchantEntry, ...]":
    """Return the rows to draw: the ceiling cuts, but never the OPEN row.

    **The ceiling used to cut it, and the route turned that into a 404 on a
    merchant the account HOLDS** (found on re-read and by three independent
    adversarial reviews, 2026-08-31).  :meth:`DirectoryAsk.shows` exempts the
    open row from the filter and the search; nothing exempted it from the
    ceiling, and :attr:`MerchantDirectory.opened` scans the DRAWN rows -- so a
    merchant sorting past :data:`DIRECTORY_LIMIT` was cut, ``opened`` answered
    ``None``, and the route refused with the sentence it reserves for *this
    account has never seen it*.  Two docstrings disagreeing about what one 404
    means is what gave it away.

    **Appending keeps the list sorted**, so this needs no re-sort and the fix
    is a property of the ordering rather than a repair on top of it: *matched*
    ascends by name and a cut row is at an index past *limit*, so it sorts
    after every row that survived the cut.

    Args:
        matched: Every row the filter and the search kept, ascending by name.
        limit: How many to draw, or ``None`` for all of them.

    Returns:
        The first *limit* rows, plus the open row when the cut took it -- so
        the page draws at most one more than the ceiling, and only for the row
        the owner is actually editing.
    """
    if limit is None:
        return matched
    drawn = matched[:limit]
    if any(entry.is_open for entry in drawn):
        return drawn
    return drawn + tuple(entry for entry in matched[limit:] if entry.is_open)


def merchant_directory(
    owner_id: int,
    account_id: int,
    categories: "dict[int, str]",
    asked: DirectoryAsk = DirectoryAsk(),
) -> MerchantDirectory:
    """Return every merchant this account has seen, as one render shows them.

    **One derivation at one instant**, which is the argument
    :class:`~._rules.RuleView` makes one tier down: the answers, what they can
    resolve against, and the bars that refuse two of them are read from the
    same moment, so the control cannot offer an option the door would refuse.

    **It costs no** :class:`~._scope.ReviewScope`, which is the substantive
    claim and is measured: this function imports nothing from :mod:`._scope`,
    :mod:`._candidates`, :mod:`._offers` or the matcher, and a statement census
    of one render found no pay-calendar read belonging to it.

    **SIX reads, not three**, and this said three until an adversarial review
    counted them (2026-08-31): ``rules_for``, ``offerable_templates`` and
    ``active_category_names`` inside :meth:`~._rules.RuleView.build` (eight when
    a stored answer names something no longer offerable),
    ``account_payment_merchants`` inside
    :meth:`~._bars.CreationBars.build`, then :func:`merchant_activity` and
    :func:`~._rules.account_merchants`.  The old sentence enumerated the three
    it found interesting and read as a total.

    **One of the six is dead work on a closed render and is kept
    deliberately.**  ``CreationBars`` is read only by
    :attr:`~._section.MerchantSummary.pays_an_account`, which the page renders
    only inside the OPEN row -- so on a plain list nothing displays it.
    Building it only when a row is open would put ``False`` on every other row,
    which is a figure that is FALSE rather than absent and is the shape
    :class:`~._section.WaitingMerchant` exists to refuse.  Priced on the
    developer's own account (378 lines, 62 merchants): **0.092 ms**, nine
    shared-buffer hits.  The trade is not worth a false field.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account whose merchants to list.
        categories: What each of this owner's ACTIVE categories is called,
            from the one read the route already performs for the open row's
            picker.  **Passed in rather than read here** so the phrase a closed
            row prints and the options its open row offers come from one list:
            a service reading its own would make two statements of *which
            categories are active* inside one render.
        asked: What this render was asked for -- the filter, the search, the
            open row and the ceiling (:class:`DirectoryAsk`).  Defaulted to the
            whole account so a caller wanting every merchant states nothing.

    Returns:
        The :class:`MerchantDirectory`.
    """
    # BOTH off ONE read (:class:`~._bars.MerchantAnswers`).  This site read
    # ``merchant_rules`` TWICE, at two instants, between plan step
    # ``bank_import:X-gj-2b-2`` and its review: the conversion replaced the
    # ``CreationBars.build`` line and left a ``RuleView.build`` above it, while
    # ``MerchantAnswers.build`` calls ``RuleView.build`` itself -- so the value
    # introduced to stop a consumer holding the two from different instants
    # re-created that hazard at one of the four sites it was meant to fix.
    answers = MerchantAnswers.build(owner_id, account_id)
    view, bars = answers.view, answers.bars
    activity = merchant_activity(account_id)
    entries = tuple(
        MerchantEntry(
            summary=summary,
            says=says_of(summary, view, categories),
            activity=activity.get(
                summary.merchant_id, MerchantActivity(0, None),
            ),
            is_open=summary.merchant_id == asked.opened,
        )
        # **Ascending by NAME and not by key** (plan step
        # ``bank_import:X-gd-1``): a surrogate id sorts by when the bank first
        # showed each merchant, which is not an order anyone reading a
        # directory is looking for.
        for summary in sorted(
            (
                merchant_summary(merchant_id, name, view, bars)
                for merchant_id, name in account_merchants(account_id).items()
            ),
            key=lambda row: row.merchant,
        )
    )
    matched = tuple(entry for entry in entries if asked.shows(entry))
    return MerchantDirectory(
        entries=_drawn(matched, asked.limit),
        counts=tuple(
            FilterCount(
                wanted=member,
                count=sum(1 for entry in entries if member.holds(entry)),
            )
            for member in MerchantWanted
        ),
        matched_count=len(matched),
        templates=offered_answers(view),
    )
