"""Why a bank line may NOT become a purchase, and the lines that may not.

Ruling **R-GJ**, plan step ``bank_import:X-ga``.  :mod:`._create` is the door
that turns a bank line into a purchase; this is the one statement of which
lines it may not be opened for at all.

**A warning paragraph is not a door, and the difference is measured.**  The
review screen already told the owner that a card payment "would be counted
twice", and beside that sentence it rendered a working destination select.  On
the developer's own dev database, nine Capital One ACH payments became purchases
in EIGHT `$0.00`-budget envelopes holding **`$7,412.94`** -- eight and not nine
because two of the nine fell in one pay period -- while the app was already
holding 22 ``CC Payback`` rows RECORDING **`$6,286.46`** of that same card's
spending -- one YTD pass, past the paragraph.  (`$6,343.58` is what those rows
BUDGETED; the settled figures are what they say the money was, which is the
column the double count is against.)  A tenth line (`-$466.47`, 2026-06-17) was
correctly group-matched to four of those payback rows instead, which is the arm
this module leaves open.  R-GJ's answer is structural: for a barred line the
create arm does not exist -- not on the screen, not in the sweep, and not at
the door for a crafted request.

**Two bars, and the second is the developer's ruling of 2026-08-24 about who
may decide.**

* :attr:`CreationBar.NEVER_A_PURCHASE` -- the owner has answered *never a
  purchase* for this merchant (:class:`~._rules.RuleAnswer`'s third
  answer).  Until this step that answer was a CAPTION: it withheld a sweep
  value, and the line's own select went on offering every envelope in the
  period while :func:`~._create.create_purchase_from_line` read no rule at
  all.  A stated decision the money door ignores is the defect, and this is
  the whole of its repair.
* :attr:`CreationBar.PAYS_AN_ACCOUNT_YOU_HOLD` -- the SOURCE files this
  merchant's lines as a payment to an account the owner holds.  **No answer
  lifts it**, because there is no answer that would make such a line spending.

**What the source's opinion is read FOR, and the correction that got it
right.**  ``source_category`` is the bank's own words, and
:class:`~app.models.statement_import.BankStatementLine` keeps it as provenance.
Measured on the developer's own 378 recorded lines, SECU files 22 of them under
``Financial Services/Credit Card Payment`` -- the 15 Capital One payments, and
**7 "Bank Of America Online Pmt" lines at `$531.94` which are the Van Loan car
payment**, four already matched to ``Transfer to Van Loan`` shadow rows.

A first version read that as *the label is WRONG about 7 of its own 22* and
built a permissive arm around it: the label would only REQUIRE an answer, and
any answer would lift the bar.  **That inference was false and the arm it
justified is what left the measured hole open.**  The label is imprecise about
WHICH account -- a card, a car loan -- and the app does not act on which: it
acts on *is this spending*, and the answer is NO for all 22.  Only the wording
was ever wrong, and the wording is fixed here rather than the refusal being
weakened.  What the permissive arm bought was worth zero lines: of the 7 Van
Loan lines, 4 are matched and 3 fall before the pay calendar opens, so not one
has ever been offered a create arm.

**The merchant is the grain, because the merchant is the rule's grain.**  A
bar per LINE would offer a create arm on some of one merchant's lines and
withhold it on others, which is incoherent on a screen whose answers are stated
once per merchant.  It costs nothing measurable: all 15 Capital One lines and
all 7 Van Loan lines carry the category, and of the 62 merchants across the
developer's 378 recorded lines exactly ONE carries two different categories at
all -- ``Member Deposit``, which SECU spells ``Income/Other Income`` on some
lines and ``Uncategorized Income`` on others, neither an account payment.

**The known cost, stated rather than discovered.**  If a source ever files
genuine SPENDING under one of these categories, that merchant has no create arm
and no answer opens one -- a dead end until the vocabulary is corrected.  It is
0 of 378 lines today (all 22 are a card payment or a car payment), and it is
the deliberate trade: a loud dead end on an unmeasured case against a silent
one-click double count on a measured one, on a screen whose whole subject is
money.

**Where the vocabulary lives, and where it belongs.**  Which of a source's own
category strings name a payment to an account the owner holds is the SOURCE ADAPTER's
knowledge (ruling **R-FP**), and it would sit beside
:mod:`app.services.statement_import` if it could: that package imports this one
(``_reads`` takes ``removals_by_match``), so the edge back would be a cycle.
It is keyed by :class:`~app.enums.StatementSourceEnum` here so that the second
adapter costs one entry rather than a rewrite, which is the whole of what
``statement_import._adapters`` buys with its single row.

**THE SECOND BAR IS INTERIM AND THE SEAM IS CLEAN.**  Paying a credit card is a
transfer between two accounts the owner holds, and from scratch there is no
card-payment code in this layer at all: the card account exists, the payment is
a transfer, and the checking line matches the transfer's checking-side shadow
1:1 on the ordinary exact tier like any other transfer.  That is the card arc's
ruled model -- finding **N-337**, owner ``credit_card:CC3b`` -- and when it
ships such a line is MATCHED before it ever reaches the creatable list, at
which point :attr:`CreationBar.PAYS_AN_ACCOUNT_YOU_HOLD`,
:data:`~._vocabulary.ACCOUNT_PAYMENT_CATEGORIES` and
:func:`~._vocabulary.account_payment_merchants` delete whole.  **R-GJ closes
nothing of N-337**; it removes the wrong answer until the right one ships, and
it writes no data and teaches the owner no decision that has to be un-taught on
the way out.

:attr:`CreationBar.NEVER_A_PURCHASE` is NOT interim and does not go with it: a
stated decision the money door honours is correct whatever the card arc does.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.  It READS and never
writes; :func:`reject_barred_line` raises, which is the only thing here with a
consequence, and it lives beside the bar it enforces so a refusal and the
sentence the screen prints for the same line cannot drift apart.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.exceptions import ValidationError
from app.models.statement_import import BankStatementLine

from ._offers import BankLine
from ._rules import StandingRule, rules_for
from ._vocabulary import account_payment_merchants

class CreationBar(enum.Enum):
    """Why one merchant's lines may not become purchases.

    Two, and they are not the same kind of fact: the first is a DECISION the
    owner made, and the second is an OBSERVATION about what the money did.  A
    screen that collapsed them would tell someone who answered *never* that
    their bank had decided for them, and someone whose bank filed a transfer
    that they had once said something they never said.

    Neither is a question.  A first version made the second one an
    *unanswered* state that any answer lifted, and an adversarial review
    2026-08-24 measured what that left open: the answer that lifts it is
    ``a new envelope``, which is the answer the developer had actually saved
    and which booked `$7,412.94`.  Paying an account you hold is not spending
    whoever is asked, so nothing here asks.
    """

    NEVER_A_PURCHASE = "never_a_purchase"
    PAYS_AN_ACCOUNT_YOU_HOLD = "pays_an_account_you_hold"


def _core_sentence(
    barred_by: CreationBar, merchant: str, also_pays_an_account: bool,
) -> str:
    """Return the sentence both the screen and the door say about a bar.

    **One wording, two registers.**  The screen adds what to do instead and the
    door adds *nothing was changed*, but what the two claim about the line is
    written once -- a control and the refusal behind it stating different
    reasons is how an owner learns to distrust both.

    **BOTH bars can hold at once, and until plan step ``bank_import:X-gf-3a``
    only the first was ever said.**  :meth:`CreationBars.bar_for` asks the
    owner's own answer first, which is the right order and is not what was
    wrong: what was wrong is that *you have said this is never a purchase*
    implies that unsaying it would open the door, and for a merchant a source
    also files as an account payment it would not -- no answer lifts the second
    bar.  Measured on the developer's own dev data 2026-08-27: merchant
    ``Capital One Credit Card`` carries BOTH, and it is **9 of his 9 parked
    lines**, so the incomplete sentence was the only one any of them ever got.
    The second clause is added rather than the first replaced, because which
    bar the owner DECIDED is still the thing to say first.

    Args:
        barred_by: Which bar applies -- the first that does, the owner's own
            answer before the bank's filing.
        merchant: The bank's own merchant string, which is the rule key.
        also_pays_an_account: Whether a source ALSO files this merchant as a
            payment to an account the owner holds.  Read only where
            *barred_by* is the owner's own answer: where it is the filing
            itself, the sentence already says so and a second clause would
            repeat it.

    Returns:
        The shared sentence.
    """
    if barred_by is CreationBar.NEVER_A_PURCHASE:
        stated = (
            f"You have said {merchant} is never a purchase, so nothing can "
            f"record it as one."
        )
        if not also_pays_an_account:
            return stated
        return (
            f"{stated}  Your bank also files {merchant} as a payment to an "
            f"account you hold, which no answer lifts, so saying something "
            f"else about it would not open this either."
        )
    return (
        f"Your bank files {merchant} as a payment to an account you hold "
        f"rather than as spending.  Money that moves between your own accounts "
        f"was spent somewhere else, and your budget already holds it there, so "
        f"recording it here would count it twice."
    )


def _refusal_for(
    barred_by: CreationBar, merchant: str, also_pays_an_account: bool,
) -> str:
    """Return what the DOOR says when it refuses a line for *merchant*.

    Private, and beside :func:`reject_barred_line` which is its only caller:
    the wording of a refusal and the refusal itself are one fact, and the last
    time this package let a sentence about a line live apart from the control
    that acted on it, the sentence said *nothing here records it* over a select
    that did.

    Args:
        barred_by: The bar the caller established.
        merchant: The bank's own merchant string, which is the rule key.
        also_pays_an_account: Whether a source ALSO files this merchant as a
            payment to an account the owner holds.  Threaded through for the
            reason this function exists at all: the screen states it, and a
            door that did not would be the two-registers drift this module's
            one wording exists to prevent.

    Returns:
        The refusal sentence, ending in the phrase every designed refusal in
        this package ends in, because the door leaves nothing behind.
    """
    return (
        f"{_core_sentence(barred_by, merchant, also_pays_an_account)}  Match "
        f"it to rows you already hold instead, or leave it where it is.  "
        f"Nothing was changed."
    )


@dataclass(frozen=True)
class CreationBars:
    """Which of one account's merchants may not become purchases, and why.

    **ONE derivation at one instant**, which is the argument
    :class:`~._rules.RuleView` makes beside it: what the owner has answered
    and what the bank has filed are read from the same moment, so the screen
    and the door cannot disagree about whether a merchant has been answered
    for.

    **It is NOT carried on the** :class:`~._scope.ReviewScope`, for the reason
    :func:`~._leftovers._leftovers` states about the rules themselves: the
    rule-stating route derives its scope ONCE, BEFORE its write, and a reader
    taking these off the scope would show the answers that pass had just
    replaced.  The batch door builds one per REQUEST instead, exactly as it
    builds one :class:`~._create.MintedEnvelopes`.

    Attributes:
        never: The merchant ROW IDS answered *never a purchase*.
        account_payments: The merchant row ids whose lines this account's
            sources file as a payment to an account the owner holds.

    **Both are sets of ids and not of names** (plan step
    ``bank_import:X-gd-1``).  A merchant is a row, so a bar is about that row;
    two sets of strings compared against a line's own string was the same
    equality join in three places, each free to fold case differently from the
    others.

    **There is no set of merchants ANSWERED-FOR here, and a first version had
    one.**  It served the arm that let any answer lift the second bar, which is
    the hole an adversarial review measured; with that arm gone the field had
    no reader, and a stored fact nothing reads is the denormalization these
    registries exist to remove.
    """

    never: "frozenset[int]"
    account_payments: "frozenset[int]"

    @classmethod
    def build(
        cls,
        owner_id: int,
        account_id: int,
        rules: dict[int, StandingRule] | None = None,
    ) -> "CreationBars":
        """Derive the bars for one pass over one account.

        Args:
            owner_id: The user the caller proved owns the account.
            account_id: The account being reviewed.
            rules: What the owner has answered, where the caller has already
                read them (:attr:`~._rules.RuleView.rules` has), else
                ``None`` to read them here.  The same shape -- and the same
                reason -- as :func:`~._rules.rules_for`'s callers have:
                one request that has the answers must not ask for them twice.

        Returns:
            The :class:`CreationBars`.  Two indexed reads, or one when the
            answers arrive with the call.
        """
        if rules is None:
            rules = rules_for(owner_id, account_id)
        return cls(
            never=frozenset(
                merchant_id for merchant_id, rule in rules.items()
                if rule.is_never
            ),
            account_payments=account_payment_merchants(account_id),
        )

    def bar_for(self, merchant_id: "int | None") -> "CreationBar | None":
        """Return why *merchant_id* may not become a purchase, or ``None``.

        **Total over ``None`` without a branch for it**, and an adversarial
        review 2026-08-24 is why there is no branch: a first version opened
        with ``if merchant is None: return None``, which no mutation could ever
        reach.  Neither of the two sets can hold ``NULL`` --
        :func:`~._vocabulary.account_payment_merchants` filters
        ``merchant_id.isnot(None)``, and the other is keyed on
        ``merchant_rules.merchant_id``, which is ``NOT NULL`` -- so
        ``None`` is absent from both and falls through
        to the same answer the branch gave.  Two guards for one fact meant
        neither could fail while the other stood, and the case written to grade
        it asserted ``None is None``.  The surviving guard is the QUERY's,
        which is also the one that has to hold: a set claiming ``NULL`` is a
        merchant would be false about the data as well as dangerous here.

        Args:
            merchant_id: The line's merchant row, or ``None`` where the source
                names none -- which keys no rule and is filed under no category
                this account can be asked about, so it is never barred.  The
                same total :func:`~._placement.placements_for` gives it.

        Returns:
            The :class:`CreationBar`, or ``None`` when this merchant's lines
            may be recorded as purchases.  **The owner's own answer is asked
            FIRST**, so a merchant they have answered *never* is told they
            decided rather than told what their bank filed -- and that ordering
            is the whole of the difference between the two, because both
            refuse.

            **No answer lifts the second bar**, and a first version's
            ``if merchant in self.answered: return None`` between the two arms
            is what an adversarial review 2026-08-24 measured as the step's own
            hole: the answer that lifted it was ``a new envelope``, which is
            the answer the developer had saved for Capital One and the one that
            booked `$7,412.94` through the sweep.  :func:`~._stating.
            state_rules` refuses EVERY answer but *never a purchase* for such
            a merchant now (plan step ``bank_import:X-gd-2``), so a stored one
            cannot sit inert and a stated one cannot be traded away for *ask
            me every time* either.
        """
        if merchant_id in self.never:
            return CreationBar.NEVER_A_PURCHASE
        if merchant_id in self.account_payments:
            return CreationBar.PAYS_AN_ACCOUNT_YOU_HOLD
        return None

    def pays_an_account(self, merchant_id: "int | None") -> bool:
        """Return whether a source files this merchant as paying an account.

        **The RULE door's question, and the control's** -- not the line's,
        which asks :meth:`bar_for` instead.  The door refuses an answer that
        would file such a merchant's money as spending
        (:func:`~._stating.state_rules`), and the control says why before the
        answer is attempted, so the refusal is not the first the owner hears of
        it.

        Args:
            merchant_id: The line's merchant row, or ``None``, which is never
                one -- for the reason :meth:`bar_for` states, and through the
                same single guard rather than a second one here.

        Returns:
            Whether it is one.
        """
        return merchant_id in self.account_payments


@dataclass(frozen=True)
class ParkedLine:
    """One unexplained OUTFLOW that may not become a purchase.

    Ruling **R-GJ**'s other arm: a line the create door is closed for is not
    hidden, it is PARKED -- listed with the reason, and still tickable in the
    hand-build form below, which is where a card payment meets the payback rows
    it repays.  Measured: the one Capital One line handled that way
    (`-$466.47`, 2026-06-17) is grouped with four ``CC Payback`` rows whose
    RECORDED figures sum to exactly `$466.47`, so that one needed no difference
    at all.  Naming one where a group does leave it is ruling **R-FN**'s
    machinery, already built at ``X-f6d-4``.

    **It is NOT a** :class:`~._leftovers.CreatableLine` **with the controls turned
    off.**  That value carries the destinations a line may be recorded into and
    the placement suggesting one of them, and a parked line has neither -- a
    record that carried empty versions of both would be a control one Jinja
    condition away from rendering again.

    Attributes:
        line: The bank's own record of the movement.
        barred_by: Why the create door is closed for it -- the FIRST bar that
            holds, the owner's own answer before the bank's filing.
        also_pays_an_account: Whether a source ALSO files this merchant as a
            payment to an account the owner holds.  **A line can carry both
            bars and 9 of the developer's 9 parked lines do** (measured
            2026-08-27, plan step ``bank_import:X-gf-3a``), which is what
            decides whether this line has a door at all: an answer the owner
            gave can be given again, and the second bar is lifted by nothing,
            so a merchant carrying both has no answer worth sending them to
            change.  Carried rather than re-asked, because the pass has already
            derived it (:meth:`CreationBars.pays_an_account`) and a value
            re-deriving its own reason is the redundant producer call this
            package refuses.
    """

    line: BankLine
    barred_by: CreationBar
    #: **Required, and no default**, because the wrong default is the one that
    #: reads as safe: ``False`` would render :attr:`answer_door` as a link on a
    #: line whose answer no door will accept, which is 9 of 9 on the
    #: developer's own data.  A value whose absence produces a working-looking
    #: control is the shape ruling **R-GJ** cost `$7,412.94` to learn.
    also_pays_an_account: bool

    @property
    def reason(self) -> str:
        """Return the sentence the screen prints beside this line.

        Server-derived rather than a Jinja branch on :attr:`barred_by`, for the
        reason :attr:`~._placement.Placement.sweep_class` is: a template
        restating a partition is a second place for it to be wrong, and here
        the two arms say opposite things about whether the owner has already
        decided.
        """
        return (
            f"{_core_sentence(self.barred_by, self.line.merchant, self.also_pays_an_account)}"
            f"  If some of your own rows are what this paid, tick them "
            f"together below and match them."
        )

    @property
    def answer_door(self) -> "str | None":
        """Return where the answer that parks this line is changed, or ``None``.

        Plan step ``bank_import:X-gf-3a``.  **A parked line's only door is one
        it does not name**, which is what this closes: since ruling
        **bank_import:R-GX** an ANSWERED merchant leaves the review screen's
        own control and appears on the register, so a line parked by an answer
        the owner now disagrees with had nowhere on this page to say so.

        **It is ``None`` where changing the answer would change nothing**, and
        that is the whole reason it is derived here rather than being a link
        the template always renders.  A merchant a source files as a payment to
        an account the owner holds is barred by that filing, which no answer
        lifts (:meth:`CreationBars.bar_for`), and
        :func:`~._stating.state_rules` refuses every answer but *never a
        purchase* for one.  Sending the owner to restate it would be the
        *chooser whose submission can never succeed* shape this package has now
        closed five times -- and it is not the rare case: on the developer's
        own data 2026-08-27 it is 9 of 9 parked lines, so a link rendered
        unconditionally would have been wrong on every line it ever appeared
        beside.

        Returns:
            The sentence naming the act, which the template renders as a link
            to the register -- the URL being the one fact a service may not
            build -- or ``None`` where no answer would open this line.
        """
        if self.barred_by is not CreationBar.NEVER_A_PURCHASE:
            return None
        if self.also_pays_an_account:
            return None
        return f"Change what you have said about {self.line.merchant}"


def reject_barred_line(
    line: BankStatementLine, bars: CreationBars,
) -> None:
    """Refuse a line ruling **R-GJ** says may never become a purchase.

    **The door's half of a structural refusal**, plan step
    ``bank_import:X-ga``.  The screen does not render a create control for such
    a line at all (:attr:`~._reads.ReviewSet.parked`), so this fires only on a
    stale page or a crafted body -- and it has to exist for exactly that
    reason: the last version of this rule was a paragraph on the screen with a
    working select underneath it, and one YTD pass booked
    **`$7,412.94`** of Capital One payments the app already held as ``CC
    Payback`` rows straight past it.  A refusal a browser can walk around is
    not a refusal.

    **It reads the bars the PASS derived rather than deriving its own**, which
    is the rule :class:`~._create.MintedEnvelopes` states beside it: a door that read
    ``merchant_rules`` per act would ask it 90 times for the
    developer's own statement, and a redundant producer call inside one request
    is this project's DRY violation rather than a cost.

    It fires AFTER :func:`_load_line`, so a line this account does not hold, or
    one another match already claims, is answered by the sentence about THAT
    rather than by one about a merchant the caller may not even be entitled to
    hear named.

    Args:
        line: The recorded line, already proved reachable by this pass.
        bars: Which of this account's merchants may not become purchases, and
            why.

    Raises:
        ValidationError: When a bar applies.  A 400: an owner working from a
            page rendered before they answered for the merchant reaches it.
    """
    barred_by = bars.bar_for(line.merchant_id)
    if barred_by is not None:
        raise ValidationError(_refusal_for(
            barred_by,
            line.merchant_name,
            bars.pays_an_account(line.merchant_id),
        ))
