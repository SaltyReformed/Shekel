"""The rule CONTROL: where this account's merchants go, as the screen asks it.

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

Nothing changed on the way across except what R-GJ added: a row now says
whether a SOURCE files this merchant as a payment to a credit card, because
that is why the owner is being asked (:class:`~._bars.CreationBars`).

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
        line_count: How many of THIS pass's unexplained outflows it names.
            Zero for a merchant whose lines are all explained today and whose
            rule the owner may still want to see or RESTATE -- there is no
            withdrawal as of ruling **R-GS**.
        unofferable: What this merchant's stored answer NAMES that the
            option list cannot show (:class:`Unofferable`).  Both arms are
            rendered as an option of their own, because a select with no
            selected option submits its FIRST and that submission is one the
            owner never made.
        total: What those lines come to, signed.  **The section is where a
            decision is made about several lines at once, so it has to say how
            much money it is a decision about**: on the developer's own
            statement one row of it covers `-$7,412.94`.
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
    line_count: int
    total: Decimal
    unofferable: Unofferable = Unofferable()
    pays_an_account: bool = False


@dataclass(frozen=True)
class MerchantSection:
    """The rule control: where this account's merchants go.

    Plan step ``bank_import:X-f6a-3d``.  **It is the screen's shape matching
    the model's.**  The leftover list asks 91 questions on the developer's own
    statement and the real question is asked 21 times, so a screen with 91
    selects and no per-merchant control is rendering a decision the owner does
    not actually make one line at a time.

    **Stating a rule here MOVES NO MONEY**, which is why it is a separate
    control posting to a separate door: the placements it produces are
    suggestions, and the destination select on each line still opens on *leave
    this line alone* (ruling **R-FZ**).

    **Since ruling R-GJ it is also the only place a bar is LIFTED.**  A
    merchant a source files as a payment to a credit card has no create arm
    until the owner answers for it (:class:`~._bars.CreationBar`), and this
    control is where the answer is given -- so a row must exist here for every
    merchant whose lines are parked, or the app would refuse an act and hide
    the only door that permits it.  That is why :func:`merchant_section` counts
    the PARKED outflows beside the creatable ones rather than the creatable
    ones alone.

    Attributes:
        merchants: One row per merchant this pass has an unexplained outflow
            for, PLUS every merchant the owner has already answered for --
            ascending BY NAME.  The second half is what makes a rule visible and
            restatable once its lines are all explained; without it an answer
            could only be changed while there was work outstanding.  It is
            NARROWER than what a statement may legitimately name
            (:func:`~._rules.account_merchants`, every merchant the account
            has ever recorded), and deliberately: a merchant with neither
            pending work nor an answer is not a question anyone is asking
            today.
        templates: The recurring definitions a rule on this account may name
            (:func:`~._rules.offerable_templates`), as ``(id, name)``
            ascending by name.  The option list, and the same set
            :func:`~._stating.state_rules` checks a submission against.
    """

    merchants: "tuple[MerchantSummary, ...]"
    templates: "tuple[tuple[int, str], ...]"

    @property
    def answered_count(self) -> int:
        """Return how many of these merchants the owner has answered for."""
        return sum(1 for row in self.merchants if row.rule is not None)


@dataclass(frozen=True)
class _Waiting:
    """How much of THIS pass one merchant's unexplained outflows come to.

    Two facts about one merchant's share of one pass, and they are only ever
    read together -- the row prints "N line(s)" over a figure.  Carried as one
    value so a caller cannot pair a count with another merchant's total.

    Attributes:
        count: How many of this pass's unexplained outflows the merchant
            names.  Zero for a merchant whose lines are all explained today and
            whose answer the owner may still want to see or change.
        total: What those lines come to, signed.
    """

    count: int = 0
    total: Decimal = Decimal("0.00")


def _merchant_summary(
    merchant_id: int,
    merchant: str,
    view: RuleView,
    bars: CreationBars,
    waiting: _Waiting,
) -> MerchantSummary:
    """Return one row of the rule control.

    Args:
        merchant_id: The merchant row this asks about.
        merchant: What it is called.
        view: What the owner has said and what it can resolve against.
        bars: Which merchants may not become purchases, and why
            (:class:`~._bars.CreationBars`).
        waiting: This merchant's share of the pass (:class:`_Waiting`).

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
        line_count=waiting.count,
        total=waiting.total,
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
    """Return the rule control's rows and its option list.

    Args:
        outflows: This pass's offerable unexplained outflows -- the ones with a
            create arm AND the ones ruling **R-GJ** parks -- which is what each
            row counts and totals.  **The parked half is load-bearing**: a
            merchant is parked precisely because it has no answer, and dropping
            its lines from the count would remove the row where the answer is
            given.
        view: What the owner has said and what it can resolve against.
        bars: Which merchants may not become purchases, and why.

    Returns:
        The :class:`MerchantSection`.  Every merchant with pending work, plus
        every merchant already answered for, ascending -- so an answer stays
        visible and RESTATABLE after the lines that prompted it are gone.
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
    # **The NAMES come from BOTH halves**, because the two sets are not the
    # same: a merchant with pending work is named by its own lines, and one
    # whose lines are all explained is named by the answer stored for it.
    # Neither half alone covers the union this section renders.
    names.update(
        {rule.merchant_id: rule.merchant
         for rule in view.rules.values()},
    )
    return MerchantSection(
        merchants=tuple(
            _merchant_summary(
                merchant_id, names[merchant_id], view, bars,
                waiting.get(merchant_id, _Waiting()),
            )
            # **Ordered by NAME and not by key** (plan step
            # ``bank_import:X-gd-1``).  The merchant was the key and sorting it
            # WAS alphabetical order; a surrogate id sorts by when the bank
            # first showed each merchant, which is not an order anyone reading
            # a list of merchants is looking for.
            for merchant_id in sorted(
                set(waiting) | set(view.rules),
                key=lambda row_id: names[row_id],
            )
        ),
        templates=tuple(
            sorted(view.template_names.items(), key=lambda pair: pair[1]),
        ),
    )
