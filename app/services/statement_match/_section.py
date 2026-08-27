"""The rule CONTROLS: where this account's merchants go, as the screens ask it.

Plan step ``bank_import:X-f6a-3d`` built this; plan step ``bank_import:X-ga``
moved it out of :mod:`._reads`.  **The split is a line cap made useful rather
than worked around**, which is the argument :mod:`._creations` already makes
one tier down: adding ruling **R-GJ**'s bars took that module past this
project's 1,000-line bound, and the two honest answers are to cut prose or to
cut the module.  The seam is the SUBJECT: :mod:`._reads` answers *what does the
review screen show about this pass's LINES*, and this answers *what does it ask
about this account's MERCHANTS*, which is a question with its own grain -- one
row per merchant, not one per line -- and its own door
(:func:`~._stating.state_rules`, which moves no money).

**There are TWO controls since plan step ``bank_import:X-gf-2``, and the
difference between them is a difference in KIND** (ruling
**bank_import:R-GX**).  :class:`MerchantSection` is the QUEUE's: the merchants
this pass has an unexplained outflow for and the owner has never answered
about, which is a decision they OWE.  :class:`MerchantRegister` is the
REGISTER's: every answer they have already GIVEN, shown so it can be restated.
They share a row (:class:`MerchantSummary`) and an option list
(:func:`_offered`), because *which merchant, and what did they say* is one
question wherever it is asked; what they do not share is this pass's waiting
lines, which only the queue measures and only the queue states
(:class:`WaitingMerchant`).

Nothing changed on the way across from :mod:`._reads` except what R-GJ added: a
row says whether a SOURCE files this merchant as a payment to a credit card,
because that is why the owner is being asked (:class:`~._bars.CreationBars`).

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no query -- every fact it
needs arrives on a :class:`~._rules.RuleView` or a
:class:`~._bars.CreationBars`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ._bars import CreationBars
from ._offers import BankLine
from ._rules import StandingRule, RuleView


@dataclass(frozen=True)
class Unofferable:
    """What a stored answer NAMES that the picker cannot show, by arm.

    **One value because it is one fact**: a rendered select submits the option
    carrying ``selected``, and its FIRST when none does -- so an answer whose
    subject is missing from the option list is submitted as something the owner
    never chose.  Each arm gets an option of its own carrying the stored value,
    and the two arms fail differently only in what the wrong submission then
    means.

    Both were found the same way and a year apart in nothing but this arc's own
    time: the template arm by adversarial financial review 2026-08-19, when a
    deactivated template made the select fall onto *I have not said* and the
    next Save silently withdrew a rule; the category arm by two adversarial
    reviews 2026-08-26, when an archived category made it fall onto the empty
    option and the door refused a merchant the owner never touched.  They are
    grouped rather than carried as two more fields because the second is what
    took :class:`MerchantSummary` past its attribute ceiling, and a ceiling is
    a question about whether the fields are one thing.

    Attributes:
        template: What to call the recurring definition a stored answer names
            when it is no longer offerable, else ``None``.  Deactivated or
            un-enveloped through ``routes/templates/crud.py``.
        category: What to call the category a stored *new envelope* answer
            files under when it is no longer active, else ``None``.
            Archiving is what ``archive_helpers.category_has_usage`` now
            produces where a permanent delete used to cascade the rule away,
            so this is live state rather than a hypothetical.
    """

    template: "str | None" = None
    category: "str | None" = None


@dataclass(frozen=True)
class MerchantSummary:
    """One merchant the rule section asks the owner about.

    Attributes:
        merchant_id: The :class:`~app.models.merchant.Merchant` this row asks
            about, which is the rule's key and what the form posts back.
        merchant: What that merchant is called, which is what the row prints.
        rule: What the owner has said, or ``None`` for *not said yet*.
        unofferable: What this merchant's stored answer NAMES that the
            option list cannot show (:class:`Unofferable`).  Both arms are
            rendered as an option of their own, because a select with no
            selected option submits its FIRST and that submission is one the
            owner never made.
        pays_an_account: Whether a source files this merchant's lines as a
            payment to an account the owner holds rather than as spending
            (ruling **R-GJ**, plan step ``bank_import:X-ga``).  **It is why
            two of this row's options are refused**: such a merchant's money
            was spent somewhere else, so it cannot be filed in a budget line,
            and :func:`~._stating.state_rules` refuses a template or a
            new-envelope answer for it outright.  Saying so on the row is what
            keeps that refusal from being the first the owner hears of it --
            the *chooser whose submission can never succeed* shape this package
            has now closed four times.
    """

    merchant_id: int
    merchant: str
    rule: StandingRule | None
    unofferable: Unofferable = Unofferable()
    pays_an_account: bool = False


@dataclass(frozen=True)
class WaitingMerchant:
    """One merchant on the QUEUE, with its share of the pass beside it.

    **The pass's share is a fact about the PASS and not about the merchant**,
    which is why it composes a :class:`MerchantSummary` rather than adding two
    fields to it (plan step ``bank_import:X-gf-2``).  Those two fields used to
    live on the summary, and the register that now renders answered merchants
    has no pass to measure them against: it would have carried ``0`` for
    ``Capital One`` while nine of its lines worth `-$7,412.94` were waiting --
    a figure that is false rather than absent.  Composed, the surface with no
    pass cannot state one.

    Attributes:
        summary: The merchant and what the owner has said about it.
        line_count: How many of THIS pass's unexplained outflows it names --
            always at least one, because a merchant with no waiting line and
            no answer is not a question anybody is asking.
        total: What those lines come to, signed.  **The control decides about
            several lines at once, so it says how much money it is a decision
            about.**
    """

    summary: MerchantSummary
    line_count: int
    total: Decimal


@dataclass(frozen=True)
class MerchantSection:
    """The QUEUE's rule control: the merchants with no answer yet.

    Plan step ``bank_import:X-f6a-3d``; narrowed to the unanswered at
    ``bank_import:X-gf-2`` (ruling **bank_import:R-GX**).  **It is the
    screen's shape matching the model's.**  The leftover list asks 91
    questions on the developer's own statement and the real question is asked
    21 times, so a screen with 91 selects and no per-merchant control is
    rendering a decision the owner does not actually make one line at a time.

    **An ANSWERED merchant is not here, and that is the ruling rather than a
    filter.**  The review screen shows what is still being decided; a merchant
    the owner has answered for is a decision already made, and it is rendered
    -- and RESTATED -- on the register (:class:`MerchantRegister`).  Measured
    on the developer's own data 2026-08-27: this control was 30 rows and
    225,472 bytes, of which 29 rows were answers he had already given, on a
    review body of 578,523 bytes.

    **Stating a rule here MOVES NO MONEY**, which is why it is a separate
    control posting to a separate door: the placements it produces are
    suggestions, and the destination select on each line still opens on *leave
    this line alone* (ruling **R-FZ**).  A default that arrives pointing at
    money is exactly what R-FZ removed.

    **What ruling R-GJ still requires, and why the split cannot hide a door.**
    A parked line's only way out is an ANSWER, so a merchant whose lines are
    parked must have a row somewhere -- otherwise the app refuses an act and
    hides the only door that permits it.  Take the two bars in turn.
    :attr:`~._bars.CreationBar.NEVER_A_PURCHASE` IS an answer, so a merchant
    carrying it has by construction been answered for and its row is on the
    register.  :attr:`~._bars.CreationBar.PAYS_AN_ACCOUNT_YOU_HOLD` is lifted
    by NOTHING (:mod:`._bars`) -- there is no answer that would make such a
    line spending -- so no door exists for this control to hide, and its
    merchant appears here only while it is also unanswered.  What the parked
    outflows are counted for is narrower than either: a merchant with no answer
    at all, whose lines are parked by the second bar, still needs the row where
    an answer is GIVEN.

    **A first draft of this paragraph said the second bar is lifted by an
    answer, and that inference is the one this arc paid `$7,412.94` to learn
    was false** (:mod:`._bars`, adversarial review 2026-08-24: the answer that
    lifted it was *a new envelope*, and it booked exactly that).  It was
    restated here as a justification for a filter, one reader away from
    rebuilding the permissive arm; measured false again by adversarial review
    2026-08-27.

    **The parked LINE does not name the register**: its sentence is
    :attr:`~._bars.ParkedLine.reason`, which says what the owner decided and
    what to do instead, and putting the register beside the per-line reason is
    `X-gf-3`'s -- the step that renders those reasons.  The register is reached
    from an unconditional card at the foot of the review screen meanwhile.

    Attributes:
        merchants: One :class:`WaitingMerchant` per merchant this pass has an
            unexplained outflow for AND the owner has not answered for,
            ascending BY NAME.
        templates: The recurring definitions a rule on this account may name
            (:func:`~._rules.offerable_templates`), as ``(id, name)``
            ascending by name.  The option list, and the same set
            :func:`~._stating.state_rules` checks a submission against.
    """

    merchants: "tuple[WaitingMerchant, ...]"
    templates: "tuple[tuple[int, str], ...]"


@dataclass(frozen=True)
class MerchantRegister:
    """The REGISTER's rule control: every answer the owner has given.

    Plan step ``bank_import:X-gf-2``, ruling **bank_import:R-GX**.  The other
    half of :class:`MerchantSection`, and the difference between them is a
    difference in KIND: that one asks a question, and this one shows an
    answer and lets it be changed.

    **Its membership is ONE TABLE READ and not a union** (plan step
    ``bank_import:X-gd-1`` is what made that true).  An answered
    :class:`~app.models.merchant.Merchant` row OUTLIVES its lines, so *every
    merchant the owner has answered for* is exactly ``merchant_rules`` joined
    to ``merchants`` (:func:`~._rules.rules_for`) -- there is no second half to
    add, and this surface needs no pass, no candidate derivation and no
    calendar.

    **It carries no count of waiting lines**, and the absence is deliberate:
    counting them is the PASS's work, this surface does not run one, and a
    column reading *none right now* over nine parked lines would be false.
    The queue counts lines; the register holds answers.

    **A rule is RESTATED and never UN-STATED** (ruling **R-GS**), so nothing
    here offers *I have not said*: it is the opening state of a control that
    has never been answered, and every row here has been.

    Attributes:
        merchants: One :class:`MerchantSummary` per stated answer, ascending
            BY NAME -- the order a list of merchants is read in, where the
            surrogate id would sort by when the bank first showed each one
            (plan step ``bank_import:X-gd-1``).
        templates: The option list, exactly as :class:`MerchantSection` holds
            it: the same set the door checks a submission against, so the
            control cannot offer what the door refuses.
    """

    merchants: "tuple[MerchantSummary, ...]"
    templates: "tuple[tuple[int, str], ...]"


@dataclass(frozen=True)
class _Waiting:
    """How much of THIS pass one merchant's unexplained outflows come to.

    Two facts about one merchant's share of one pass, and they are only ever
    read together -- the row prints "N line(s)" over a figure.  Carried as one
    value so a caller cannot pair a count with another merchant's total.

    Attributes:
        count: How many of this pass's unexplained outflows the merchant
            names.  **The zero is the ACCUMULATOR's seed and is never
            emitted**: since plan step ``bank_import:X-gf-2`` this map is keyed
            only by merchants a line of this pass named, so every row built
            from it counts at least one.  It described a row the old
            ``waiting.get(merchant_id, _Waiting())`` fallback produced, for a
            merchant answered for and no longer waiting -- which is the
            register's row now, and carries no count at all.
        total: What those lines come to, signed.
    """

    count: int = 0
    total: Decimal = Decimal("0.00")


def _merchant_summary(
    merchant_id: int,
    merchant: str,
    view: RuleView,
    bars: CreationBars,
) -> MerchantSummary:
    """Return one merchant and what the owner has said about it.

    **Shared by both controls** (plan step ``bank_import:X-gf-2``): the queue
    asks about a merchant with no answer and the register shows one with an
    answer, and *which merchant, and what did they say* is the same question on
    both.  What differs is only what surrounds it -- this pass's waiting lines
    on one side (:class:`WaitingMerchant`), nothing on the other.

    Args:
        merchant_id: The merchant row this asks about.
        merchant: What it is called.
        view: What the owner has said and what it can resolve against.
        bars: Which merchants may not become purchases, and why
            (:class:`~._bars.CreationBars`).

    Returns:
        Its :class:`MerchantSummary`, carrying a label for a stored template
        that has stopped being offerable so the control can show the answer it
        holds.

    **The label is derived from "is it offerable", not from "is it in
    :attr:`~._rules.RuleView.stale_templates`", and the difference is what
    makes the option TOTAL** (plan step ``bank_import:X-gd-2``).  The two sets
    agree on every row the database can hold -- a rule's template is on this
    account by ``fk_merchant_rules_template_account``, and the stale read is
    over exactly the non-offerable ids -- but they are two reads, so a template
    hard-deleted between them is offerable in neither and named by neither.
    A select with no option carrying its stored value displays and submits its
    FIRST, and this step changed what that first option is: it used to be
    *I have not said*, which withdrew the rule, and *I have not said* is no
    longer rendered for an answered merchant, so the first option is now a real
    envelope and the silent outcome would be a rule RE-AIMED at one the owner
    never picked.  :meth:`~._rules.RuleView.label_for` is total, so asking it
    whenever the answer is not offerable leaves no case with no option.
    """
    rule = view.rules.get(merchant_id)
    names_a_stale_template = (
        rule is not None
        and rule.template_id is not None
        and rule.template_id not in view.template_names
    )
    return MerchantSummary(
        merchant_id=merchant_id,
        merchant=merchant,
        rule=rule,
        unofferable=Unofferable(
            template=(
                view.label_for(rule.template_id) if names_a_stale_template
                else None
            ),
            category=(
                view.category_label_for(rule.category_id)
                if rule is not None
                and rule.category_id is not None
                and rule.category_id not in view.active_categories
                else None
            ),
        ),
        pays_an_account=bars.pays_an_account(merchant_id),
    )


def merchant_section(
    outflows: "list[BankLine]", view: RuleView, bars: CreationBars,
) -> MerchantSection:
    """Return the QUEUE's rule control: the merchants with no answer yet.

    Args:
        outflows: This pass's offerable unexplained outflows -- the ones with a
            create arm AND the ones ruling **R-GJ** parks -- which is what each
            row counts and totals.  **The parked half is load-bearing**: a
            merchant a source files as an account payment is parked precisely
            because it has no answer, and dropping its lines would remove the
            row where the answer is given.
        view: What the owner has said and what it can resolve against.
        bars: Which merchants may not become purchases, and why.

    Returns:
        The :class:`MerchantSection` -- every merchant this pass has an
        unexplained outflow for and the owner has NOT answered for, ascending
        by name.  An answered merchant is on the register instead (ruling
        **bank_import:R-GX**), where its answer is shown and restated whether
        or not any of its lines are still waiting.
    """
    waiting: "dict[int, _Waiting]" = {}
    names: "dict[int, str]" = {}
    for line in outflows:
        merchant_id = line.merchant_id
        if merchant_id is None:
            # A source naming no merchant keys no rule, so there is nothing
            # to ask about it -- the same NULL that makes a placement
            # impossible makes a row here meaningless.
            continue
        so_far = waiting.get(merchant_id, _Waiting())
        waiting[merchant_id] = _Waiting(
            count=so_far.count + 1, total=so_far.total + line.amount,
        )
        names[merchant_id] = line.merchant
    # **The names come off the LINES alone now**, and that is the whole of what
    # narrowing to the unanswered changed here: the second source was the
    # stored answers, which named the merchants whose lines are all explained
    # -- and every one of those rows has moved to the register.  A merchant in
    # this dict was named by a line of this pass, so there is no key without a
    # name.
    return MerchantSection(
        merchants=tuple(
            WaitingMerchant(
                summary=_merchant_summary(
                    merchant_id, names[merchant_id], view, bars,
                ),
                line_count=share.count,
                total=share.total,
            )
            # **Ordered by NAME and not by key** (plan step
            # ``bank_import:X-gd-1``).  The merchant was the key and sorting it
            # WAS alphabetical order; a surrogate id sorts by when the bank
            # first showed each merchant, which is not an order anyone reading
            # a list of merchants is looking for.
            for merchant_id, share in sorted(
                (
                    (row_id, share) for row_id, share in waiting.items()
                    if row_id not in view.rules
                ),
                key=lambda pair: names[pair[0]],
            )
        ),
        templates=_offered(view),
    )


def answered_merchants(
    view: RuleView, bars: CreationBars,
) -> MerchantRegister:
    """Return the REGISTER's rule control: every answer the owner has given.

    Plan step ``bank_import:X-gf-2``, ruling **bank_import:R-GX**.  **One
    table read** -- the answers themselves, which already carry the merchant's
    name (:func:`~._rules.rules_for`) -- so this surface costs no pass, no
    candidate derivation and no calendar, and the page that renders it is not
    the 3.6-second one.

    Args:
        view: What the owner has said and what it can resolve against.
        bars: Which merchants may not become purchases, and why.  Read even
            here, because the register is where an answer is CHANGED: a
            merchant a source files as a payment to an account the owner holds
            has two of its four options refused by the door, and a control that
            did not say so would be the *chooser whose submission can never
            succeed* this package has closed four times.

    Returns:
        The :class:`MerchantRegister`, ascending by merchant name.
    """
    return MerchantRegister(
        merchants=tuple(
            _merchant_summary(rule.merchant_id, rule.merchant, view, bars)
            for rule in sorted(
                view.rules.values(), key=lambda rule: rule.merchant,
            )
        ),
        templates=_offered(view),
    )


def _offered(view: RuleView) -> "tuple[tuple[int, str], ...]":
    """Return the option list both controls render, ascending by name.

    One spelling, because the two controls offer the same answers: what
    differs between them is which merchants are asked about, never what may be
    said.

    Args:
        view: What the owner has said and what it can resolve against.

    Returns:
        ``(template_id, name)`` pairs.
    """
    return tuple(
        sorted(view.template_names.items(), key=lambda pair: pair[1]),
    )
