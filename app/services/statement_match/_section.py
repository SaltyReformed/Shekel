"""The policy CONTROL: where this account's merchants go, as the screen asks it.

Plan step ``bank_import:X-f6a-3d`` built this; plan step ``bank_import:X-ga``
moved it out of :mod:`._reads`.  **The split is a line cap made useful rather
than worked around**, which is the argument :mod:`._creations` already makes
one tier down: adding ruling **R-GJ**'s bars took that module past this
project's 1,000-line bound, and the two honest answers are to cut prose or to
cut the module.  The seam is the SUBJECT: :mod:`._reads` answers *what does the
review screen show about this pass's LINES*, and this answers *what does it ask
about this account's MERCHANTS*, which is a question with its own grain -- one
row per merchant, not one per line -- and its own door
(:func:`~._policy.state_policies`, which moves no money).

Nothing changed on the way across except what R-GJ added: a row now says
whether a SOURCE files this merchant as a payment to a credit card, because
that is why the owner is being asked (:class:`~._bars.CreationBars`).

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no query -- every fact it
needs arrives on a :class:`~._policy.PolicyView` or a
:class:`~._bars.CreationBars`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ._bars import CreationBars
from ._offers import BankLine
from ._policy import MerchantPolicy, PolicyView


@dataclass(frozen=True)
class MerchantSummary:
    """One merchant the policy section asks the owner about.

    Attributes:
        merchant_id: The :class:`~app.models.merchant.Merchant` this row asks
            about, which is the rule's key and what the form posts back.
        merchant: What that merchant is called, which is what the row prints.
        policy: What the owner has said, or ``None`` for *not said yet*.
        line_count: How many of THIS pass's unexplained outflows it names.
            Zero for a merchant whose lines are all explained today and whose
            policy the owner may still want to see or withdraw.
        stale_template_label: What to call the recurring definition this
            merchant's stored answer names, when that definition is no longer
            offerable -- else ``None``.  The screen renders it as an option of
            its own, because a select with no selected option submits its
            FIRST, which here means *I have not said*: without it the screen
            reported such a policy as unanswered and the next Save silently
            withdrew it (:attr:`~._policy.PolicyView.stale_templates`).
        total: What those lines come to, signed.  **The section is where a
            decision is made about several lines at once, so it has to say how
            much money it is a decision about**: on the developer's own
            statement one row of it covers `-$7,412.94`.
        pays_an_account: Whether a source files this merchant's lines as a
            payment to an account the owner holds rather than as spending
            (ruling **R-GJ**, plan step ``bank_import:X-ga``).  **It is why
            two of this row's options are refused**: such a merchant's money
            was spent somewhere else, so it cannot be filed in a budget line,
            and :func:`~._policy.state_policies` refuses a template or a
            new-envelope answer for it outright.  Saying so on the row is what
            keeps that refusal from being the first the owner hears of it --
            the *chooser whose submission can never succeed* shape this package
            has now closed four times.
    """

    merchant_id: int
    merchant: str
    policy: MerchantPolicy | None
    line_count: int
    total: Decimal
    stale_template_label: "str | None" = None
    pays_an_account: bool = False


@dataclass(frozen=True)
class MerchantSection:
    """The policy control: where this account's merchants go.

    Plan step ``bank_import:X-f6a-3d``.  **It is the screen's shape matching
    the model's.**  The leftover list asks 91 questions on the developer's own
    statement and the real question is asked 21 times, so a screen with 91
    selects and no per-merchant control is rendering a decision the owner does
    not actually make one line at a time.

    **Stating a policy here MOVES NO MONEY**, which is why it is a separate
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
            ascending BY NAME.  The second half is what makes a policy visible and
            withdrawable once its lines are all explained; without it an answer
            could only be changed while there was work outstanding.  It is
            NARROWER than what a statement may legitimately name
            (:func:`~._policy.account_merchants`, every merchant the account
            has ever recorded), and deliberately: a merchant with neither
            pending work nor an answer is not a question anyone is asking
            today.
        templates: The recurring definitions a policy on this account may name
            (:func:`~._policy.offerable_templates`), as ``(id, name)``
            ascending by name.  The option list, and the same set
            :func:`~._policy.state_policies` checks a submission against.
    """

    merchants: "tuple[MerchantSummary, ...]"
    templates: "tuple[tuple[int, str], ...]"

    @property
    def answered_count(self) -> int:
        """Return how many of these merchants the owner has answered for."""
        return sum(1 for row in self.merchants if row.policy is not None)


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
    view: PolicyView,
    bars: CreationBars,
    waiting: _Waiting,
) -> MerchantSummary:
    """Return one row of the policy control.

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
    """
    policy = view.policies.get(merchant_id)
    stale = (
        policy is not None
        and policy.template_id is not None
        and policy.template_id in view.stale_templates
    )
    return MerchantSummary(
        merchant_id=merchant_id,
        merchant=merchant,
        policy=policy,
        line_count=waiting.count,
        total=waiting.total,
        stale_template_label=(
            view.stale_templates[policy.template_id] if stale else None
        ),
        pays_an_account=bars.pays_an_account(merchant_id),
    )


def merchant_section(
    outflows: "list[BankLine]", view: PolicyView, bars: CreationBars,
) -> MerchantSection:
    """Return the policy control's rows and its option list.

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
        visible and withdrawable after the lines that prompted it are gone.
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
        {policy.merchant_id: policy.merchant
         for policy in view.policies.values()},
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
                set(waiting) | set(view.policies),
                key=lambda row_id: names[row_id],
            )
        ),
        templates=tuple(
            sorted(view.template_names.items(), key=lambda pair: pair[1]),
        ),
    )
