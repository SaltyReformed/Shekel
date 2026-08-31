"""What the RECONCILE screen shows: one card per bank line, on four verbs.

Plan step ``bank_import:X-gj-1a``; rulings **bank_import:R-HP**, **R-HQ**,
**R-HR**, **R-HS**, **R-HW** and **R-HX**.  The direction this builds to was
locked at Loop A round 4 on 2026-08-29 and is
``docs/design/bank_import_audit.md``.

**One page replaces three.**  The review queue, the register and the
hand-build workbench become five TABS over one list -- To explain, Explained,
Filed by rules, Transfers, Skipped -- with a hero that answers *am I done* and
holding chips for the lines that are not work.  This module is the whole of
what that page displays; the template displays it and computes nothing.

**Every tab is its own request, and that is a measurement rather than a
style.**  Ruling **R-GX** split the register off the review screen because
rendering both valued all 221 of the developer's accepted acts to draw a panel
he was not reading -- 442,109 bytes of a 578,523-byte page.  Folding them back
into one render would rebuild exactly that, so :func:`reconcile_page` builds
the cards of ONE tab and takes the other four's counts from
:func:`~._accepted_view.accepted_counts` and the pass itself.

**What each tab holds, and where its cards come from:**

===================  ==========================================================
tab                  source
===================  ==========================================================
To explain           the pass: :attr:`~._reads.ReviewSet.proposals`,
                     ``creatable`` and ``recordable_inflows``, in three
                     sections by what SUGGESTED the verb (**R-HP**)
Explained            :func:`~._accepted_view.accepted_register`, acts a person
                     ticked
Filed by rules       :func:`~._accepted_view.accepted_register` narrowed to
                     the acts a standing rule performed (**R-GT**)
Transfers            the pass's ``parked`` lines a source files as paying an
                     account the owner holds -- a HOLDING state (**R-HQ**),
                     never inbox work
Skipped              the pass's ``parked`` lines the owner has answered *never
                     a purchase* for, whose standing answer IS the disposition
===================  ==========================================================

**A line with no available act never enters To explain** (**R-HQ**).  That is
what makes the inbox reach zero: measured 2026-08-29 on the developer's own
account, 9 of the 27 unexplained lines are card payments no screen in the app
can resolve, and a queue that cannot empty is not a queue.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.  The two reads it does
perform take the owner and account the route proved, exactly as
:func:`~._accepted_view.accepted_register` does.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from ._accepted_view import REGISTER_LIMIT, accepted_counts, accepted_register
from ._cards import (
    CardSection,
    LineCard,
    act_sections,
    parked_card,
    to_explain_sections,
)
from ._last_import import last_import
from ._reads import review_set

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from datetime import date

    from ._gaps import BooksBound

    from app.services.bank_agreement import BankAgreement

    from ._bars import ParkedLine
    from ._last_import import LastImport
    from ._reads import ReviewSet
    from ._scope import ReviewScope


class Tab(enum.Enum):
    """One of the five lists the Reconcile page holds.

    **The order of the members is the order the tab bar renders them**, which
    is the order the work goes in: what is still to do, then what was done,
    then the two holding states.
    """

    TO_EXPLAIN = "to_explain"
    EXPLAINED = "explained"
    FILED_BY_RULES = "filed_by_rules"
    TRANSFERS = "transfers"
    SKIPPED = "skipped"

    @property
    def label(self) -> str:
        """Return what the tab bar calls this tab.

        Returns:
            The label, server-derived so the tab bar and any sentence naming a
            tab cannot drift apart.
        """
        return _TAB_LABELS[self]

    @property
    def holds_settled_acts(self) -> bool:
        """Return whether this tab's cards are ACTS rather than bank lines.

        Plan step ``bank_import:X-gj-1c``.  **The one fact a template needs to
        pick a partial**, and it is the service's rather than a Jinja
        condition over the tab's name: which KIND a tab's cards are is already
        stated as the tab's own fact (:attr:`~._cards.CardSection.cards`), and
        the two kinds carry disjoint controls -- a bank line takes an OK
        inside the Apply form, an applied act takes an Undo which is a form of
        its own and may not nest inside one.

        Returns:
            ``True`` for :attr:`EXPLAINED` and :attr:`FILED_BY_RULES`.
        """
        return _TAB_HOLDS_ACTS[self]


#: What each tab is called.  A table rather than a property full of branches,
#: for the reason :data:`~._verbs._WORDS` is one.
_TAB_LABELS: "dict[Tab, str]" = {
    Tab.TO_EXPLAIN: "To explain",
    Tab.EXPLAINED: "Explained",
    Tab.FILED_BY_RULES: "Filed by rules",
    Tab.TRANSFERS: "Transfers",
    Tab.SKIPPED: "Skipped",
}

#: Which tabs hold :class:`~._cards.ActCard` and which hold
#: :class:`~._cards.LineCard`.  A TOTAL table beside :data:`_TAB_LABELS`
#: rather than a membership test against two names, so a sixth tab added later
#: fails loudly here -- exactly as :func:`_tab_sections`' dispatch does -- and
#: cannot default into rendering a bank-line card for acts.
_TAB_HOLDS_ACTS: "dict[Tab, bool]" = {
    Tab.TO_EXPLAIN: False,
    Tab.EXPLAINED: True,
    Tab.FILED_BY_RULES: True,
    Tab.TRANSFERS: False,
    Tab.SKIPPED: False,
}


#: The sweep classes in RISK ORDER, each with the phrase that names what one
#: click would do.  Ruling **R-FZ(c)**: *the riskiest class may not ride the
#: same click as the safest*, so there is one control per class and never a
#: "tick all".
#:
#: **It is TWO partitions concatenated, and both are already server-derived**
#: -- :attr:`~._offers.MatchProposal.review_class` over the proposals and
#: :attr:`~._placement.Placement.sweep_class` over the spending a rule places.
#: They are listed here in one sequence because the footer offers one row of
#: controls, and the order is each partition's own: ascending by how much
#: accepting changes, day effects before amount effects, and creating an
#: envelope last because it is the one act an undo cannot fully reverse.
#:
#: **The review screen still counts these in JINJA**
#: (``_statement_review_body.html``'s ``selectattr | length``) and stays live
#: until ``X-gi`` retires it, which is the shape
#: :func:`~._queue._sweeps_for` exists to refuse: a caption may not promise a
#: number a template counted.  This page counts them in the service; the two
#: copies of the LABELS, here and in :data:`~._queue._SWEEP_LABELS`, go with
#: that retirement and had already drifted by a word on arrival.
SWEEP_LABELS: "tuple[tuple[str, str], ...]" = (
    ("confirm", "that only confirm a day you already had"),
    ("correct", "that move a day onto the bank's"),
    ("settle", "that mark a row as having happened"),
    ("reprice", "that change an amount onto the bank's"),
    ("into_open", "into a budget line that is still open"),
    ("into_closed", "into one that has already closed, raising what it recorded"),
    ("creates", "into a NEW envelope this would create"),
)


@dataclass(frozen=True)
class Hero:
    """The four figures that answer *am I done*.

    Ruling: the number is the hero (the design language's first principle).
    Done is ``off_by`` at zero and ``to_explain`` at zero, and the page says
    exactly that under them.

    Attributes:
        day: The day all three figures are about -- the latest COMPARED day
            the bank's own record can price
            (:attr:`~app.services.bank_agreement.BankAgreement.headline`) -- or
            ``None`` when no day is priceable, which is an account with no
            anchored import.
        bank: What the bank's own record says the account held that day, or
            ``None`` with *day*.
        books: What the app's records say it held that day, or ``None`` with
            *day*.
        off_by: The books LESS the bank, or ``None`` with *day*.  **Signed in
            that direction**, which is
            :attr:`~app.services.bank_agreement.AgreementDay.gap`'s own
            convention: positive means the books stand higher.
        to_explain: How many lines the inbox holds.
        unpriced_after: How many COMPARED days fall after :attr:`day` that the
            bank's own record cannot price.  **Zero is what makes the hero
            current**, and it is not implied by the other four: :attr:`day`
            walks BACK to the last day both records speak for, so an account
            whose statements stop three weeks before its records do reports a
            perfectly balanced comparison three weeks old.  The page states it
            (*priced through <day>*) and :attr:`ReconcilePage.is_done` refuses
            to claim done over it.  Named by adversarial review 2026-08-29.

    **The three money figures are all-or-nothing**, and that is the point of
    carrying the day beside them: a hero that printed two of three would be
    inviting a subtraction the app could not perform.
    """

    day: "date | None"
    bank: "Decimal | None"
    books: "Decimal | None"
    off_by: "Decimal | None"
    to_explain: int
    unpriced_after: int


@dataclass(frozen=True)
class HoldingChip:
    """One count of lines that are NOT work, in the hero's quiet row.

    Ruling **bank_import:R-HQ**.  A card payment with no card account to pair
    with, and a line older than the pay calendar, are states rather than
    tasks; the chip says how many and how much, and the tab that owns them is
    one click away.

    Attributes:
        label: What these lines are, in the reader's words.
        count: How many.
        amount: What they come to as a MAGNITUDE, or ``None`` where a sum
            says nothing.  **Positive, and the label supplies the direction**:
            every parked line is an outflow and
            :attr:`~._offers.BankLine.amount` is signed positive INTO the
            account, so carrying the raw sum would print `-$7,412.94` under
            the words *waiting for the account they paid* --
            :attr:`~app.services.bank_agreement.BankAgreement.bank_ahead` was
            corrected for exactly that, one module over, after two reviews
            found `-$15,028.03` on screen under *ran ahead*.
        day: The day that bounds them, or ``None``.
        tab: The tab that lists them (:class:`Tab`), or ``None`` where none
            does -- which is a chip that states a fact rather than offering a
            way in.

    **A chip renders only when its count is non-zero**, which is why the
    builder omits it rather than emitting a zero: a row of zeroes is the
    *nothing to see here* panel this rebuild removed.
    """

    label: str
    count: int
    amount: "Decimal | None"
    day: "date | None"
    tab: "Tab | None"


@dataclass(frozen=True)
class Sweep:
    """One risk class of cards, and the one click that OKs them.

    Ruling **R-FZ(c)**, and the same value :class:`~._queue.QueueSweep` is,
    over the new screen's two partitions.

    Attributes:
        css_class: The class key, which is the value a card carries in
            :attr:`LineCard.sweep_class`.
        count: How many cards on THIS tab the click would set.
        label: The phrase naming what it does to them.
    """

    css_class: str
    count: int
    label: str


@dataclass(frozen=True)
class TabCount:
    """One tab's name and how many cards it holds.

    Attributes:
        tab: Which tab (:class:`Tab`).
        count: Its size.
    """

    tab: Tab
    count: int


@dataclass(frozen=True)
class ReconcilePage:  # pylint: disable=too-many-instance-attributes
    """Everything the Reconcile page renders, for ONE of its five tabs.

    Pylint: ``too-many-instance-attributes`` (9/7) -- **nine because the page
    renders nine distinct things**: which tab is open, the hero, what the last
    import did, the holding chips, the tab bar, the cards, the sweeps, the
    footer's disclosure and what the account's opening already accounts for.
    Every one of them is read by ``_statement_reconcile_body.html``, so the
    count is re-derivable rather than asserted; folding any pair would be the
    speculative nesting ``CLAUDE.md`` rule 13 forbids, and
    :class:`~._reads.ReviewSet` carries the same disable for the same reason.
    *It read (8/7) until plan step balance:X-f3c-2b-2b added the ninth* --
    a count in a rationale is a measurement, and this one went stale in the
    same commit that made it stale.

    **It carried an ``account_id`` until plan step ``bank_import:X-gj-1b``,
    and NOTHING read it** -- not a template, not the route, not a test.  Every
    URL on the page is built from the ``account`` object the route passes
    beside this one, and the account this page is ABOUT is already
    :attr:`~._scope.ReviewScope.account_id`, which
    :func:`reconcile_page` checks the agreement against.  A field written on
    every render and read on none is the shape this package keeps deleting;
    it went rather than being re-documented.  Found by adversarial design
    review 2026-08-30.

    Attributes:
        tab: Which tab's cards :attr:`sections` holds (:class:`Tab`).
        hero: The four figures that answer *am I done* (:class:`Hero`).
        last_import: What the newest import on this account did
            (:class:`~._last_import.LastImport`), or ``None`` for an account
            nobody has imported into.  **Provenance rather than a hero
            figure**: the locked direction prints it right of the four
            numbers, and it answers *is what I am looking at current* rather
            than *am I done*.
        chips: The holding counts (:class:`HoldingChip`), non-zero only.
        counts: Every tab and its size (:class:`TabCount`), in tab order, so
            the bar is drawn whichever tab is open.
        sections: The open tab's cards (:class:`CardSection`), non-empty only.
        sweeps: The one-click controls this tab offers (:class:`Sweep`), which
            is empty for every tab but To explain.
        unexamined: What this pass did NOT look at, one sentence each, for the
            footer's disclosure.  Empty when it examined everything.
        books_bound: The lines this account's opening equity already
            accounts for
            (:class:`~._gaps.BooksBound`), or ``None`` where it accounts
            for none.  **Named ``books_bound`` and not ``books``**, because
            :attr:`Hero.books` on the same page is the app's own BALANCE and
            one page carrying two unrelated ``books`` is a misreading waiting
            to happen.  **Carried whole rather than flattened into**
            :attr:`unexamined`, because unlike every sentence in that tuple
            this one ends in an ACT, and the template has to render that act
            as a link -- which is the one fact a service may not build
            (:attr:`~._bars.ParkedLine.answer_door`).
    """

    tab: Tab
    hero: Hero
    last_import: "LastImport | None"
    chips: "tuple[HoldingChip, ...]"
    counts: "tuple[TabCount, ...]"
    sections: "tuple[CardSection, ...]"
    sweeps: "tuple[Sweep, ...]"
    unexamined: "tuple[str, ...]"
    books_bound: "BooksBound | None"

    @property
    def is_done(self) -> bool:
        """Return whether there is nothing left to do on this account.

        Returns:
            ``True`` when the books and the bank agree on the hero's day, that
            day is the last one both records reach, and no line is waiting to
            be explained.  **All three, and a page with no priceable day is
            never done**: an account whose bank balance nothing places has not
            been shown to agree, and one whose comparison stops weeks short
            has been shown to agree about a week that is not this one.
            Reporting either unknown as agreement is the failure this whole
            arc exists to stop.
        """
        return (
            self.hero.off_by == 0
            and self.hero.unpriced_after == 0
            and self.hero.to_explain == 0
        )


def _parked_tab(parked: "ParkedLine") -> Tab:
    """Return which holding tab one parked line belongs on.

    **The bank's OBSERVATION decides, not the owner's answer**, and the two
    bars are different kinds of fact (:class:`~._bars.CreationBar`): a source
    filing this merchant as paying an account the owner holds is an
    observation about where the money went, and *never a purchase* is a
    decision they made.  A line carrying BOTH is a TRANSFER, because the
    money did move between two accounts whatever the owner also said about it
    -- and filing it under Skipped would tell them they had disposed of
    money the app is still waiting to pair.

    **A line carrying only the owner's answer is already SKIPPED**, by that
    standing answer rather than by a stored disposition, which is why the
    Skipped tab needs none of ``X-gj-4``'s store to have members.

    Measured 2026-08-29 on the developer's own account: 9 of 9 parked lines
    carry both bars, so every one is a transfer and the Skipped arm has never
    rendered on his data.

    Args:
        parked: The :class:`~._bars.ParkedLine`.

    Returns:
        :attr:`Tab.TRANSFERS` or :attr:`Tab.SKIPPED`.
    """
    return Tab.TRANSFERS if parked.also_pays_an_account else Tab.SKIPPED


def _sweeps(sections: "tuple[CardSection, ...]") -> "tuple[Sweep, ...]":
    """Return the one-click controls this tab's cards support.

    **Counted over the cards that will actually be rendered**, so a caption
    cannot promise a number the control does not deliver -- which is the rule
    :func:`~._queue._sweeps_for` states and the review screen broke by
    counting in Jinja.

    Args:
        sections: The tab's sections.

    Returns:
        One :class:`Sweep` per class with at least one card, in
        :data:`SWEEP_LABELS`' risk order.
    """
    counts: "dict[str, int]" = {}
    for section in sections:
        for card in section.cards:
            css_class = card.sweep_class
            if css_class is not None:
                counts[css_class] = counts.get(css_class, 0) + 1
    return tuple(
        Sweep(css_class=key, count=counts[key], label=label)
        for key, label in SWEEP_LABELS if key in counts
    )


def _hero(agreement: "BankAgreement | None", to_explain: int) -> Hero:
    """Return the four figures that answer *am I done*.

    Args:
        agreement: The account's :class:`~app.services.bank_agreement
            .BankAgreement`, or ``None`` for an account holding no recorded
            line.
        to_explain: How many lines the inbox holds.

    Returns:
        The :class:`Hero`.  Its three money figures are all present or all
        absent, because they are one comparison on one day: an account with no
        anchored import has a bank side nothing places, and printing two of
        three would invite a subtraction the app cannot perform.
    """
    day = None if agreement is None else agreement.headline
    if day is None:
        return Hero(
            day=None, bank=None, books=None, off_by=None,
            to_explain=to_explain, unpriced_after=0,
        )
    return Hero(
        day=day.day,
        bank=day.bank_balance,
        books=day.app_balance,
        off_by=day.gap,
        to_explain=to_explain,
        unpriced_after=sum(
            1 for other in agreement.compared if other.day > day.day
        ),
    )


def _chips(
    review: "ReviewSet",
    transfers: "tuple[LineCard, ...]",
) -> "tuple[HoldingChip, ...]":
    """Return the quiet counts under the hero.

    Ruling **bank_import:R-HQ**: what is not work is a count, never a queue
    row.  **A chip whose count is zero is omitted**, so the row says something
    or is not there.

    **There was a third chip, *already explained*, and plan step
    ``bank_import:X-gj-1c`` DELETED it.**  It carried
    :attr:`~._accepted_view.AcceptedCounts.total` and led to the register,
    which renders every accepted act; once the two settled TABS exist that
    total is the union of two tabs, so the chip would have promised a number
    neither of the tabs it could link to delivers -- the caption-over-a-count
    defect :func:`~._queue._sweeps_for` exists to refuse.  The tab bar states
    both halves with their own counts, which is the same fact said once per
    place it is true rather than twice.

    **The books bound is NOT a chip either**, and it was one until an
    adversarial design review measured what that cost (2026-08-31): the chip
    carried the count, the day and a link, and the sentence under it repeated
    all three three lines later.  That is the clutter :func:`_unexamined` one
    function down says this rebuild removed.  It renders as
    :attr:`ReconcilePage.books_bound` instead -- once, with the act as a link,
    the way the review body and the workbench already render the same value.

    **The two paragraphs above removed DIFFERENT chips and both removals
    stand** (merge of ``balance:X-f3c-2b-2b`` into ``bank_import:X-gj-1c``,
    2026-08-31).  The merge is worth recording because the textual resolution
    that compiles is the WRONG one: this function's parameter list is the
    bank_import side's two, and the balance side calls it with a third,
    ``explained``, feeding the very chip R-HU deleted.  Taking that call site
    whole would have restored a chip pointing at a retired page, and taking the
    signature whole would have been a ``TypeError`` -- one of those fails
    loudly and the other does not.

    Args:
        review: The pass, which owns the pay-calendar and books bounds.
        transfers: The cards on the Transfers tab.

    Returns:
        The chips, in reading order.  **Every one of them names a tab this
        page serves or names no tab at all**, which is what lets the route
        turn :attr:`HoldingChip.tab` into a URL without an arm for a tab it
        cannot render.
    """
    chips = []
    if transfers:
        chips.append(HoldingChip(
            label="waiting for the account they paid",
            count=len(transfers),
            amount=sum(
                (abs(card.line.amount) for card in transfers),
                Decimal("0.00"),
            ),
            day=None,
            tab=Tab.TRANSFERS,
        ))
    bounds = review.bounds
    if bounds.before_calendar_count:
        chips.append(HoldingChip(
            label="before your pay calendar opens",
            count=bounds.before_calendar_count,
            amount=None,
            day=bounds.before_calendar_last_day,
            tab=None,
        ))
    return tuple(chips)


def _unexamined(review: "ReviewSet") -> "tuple[str, ...]":
    """Return what this pass did NOT look at, one sentence each.

    **Three of the five bounds, and the partition is decided HERE.**  The
    other two are stated elsewhere on the page and stating either twice is
    the clutter this rebuild removed:
    :attr:`~._gaps.ReviewBounds.before_calendar_count` is a
    :class:`HoldingChip` above the fold, and
    :attr:`~._gaps.ReviewBounds.books` is
    :attr:`ReconcilePage.books_bound`, which carries an ACT and so cannot
    be one of these plain sentences.  A template picking three of five
    would be the *second place for a partition to be wrong* this package
    refuses everywhere else.

    Args:
        review: The pass.

    Returns:
        The sentences, or empty when this pass examined everything -- which is
        the state that needs no disclosure at all.
    """
    bounds = review.bounds
    said = []
    if bounds.crowded_days:
        said.append(
            f"The app did not search {len(bounds.crowded_days)} day(s) for "
            f"groups, because too many of your rows fall on them: "
            f"{', '.join(str(day) for day in bounds.crowded_days)}."
        )
    if bounds.unpriceable_count:
        said.append(
            f"{bounds.unpriceable_count} of your rows could not be priced, "
            f"so nothing here was offered against them."
        )
    if bounds.impossible_day_count:
        said.append(
            f"{bounds.impossible_day_count} bank line(s) are dated as made "
            f"AFTER they posted, so no day exists a purchase could be made "
            f"on and nothing here can record them."
        )
    return tuple(said)


def reconcile_page(
    scope: "ReviewScope",
    agreement: "BankAgreement | None",
    tab: Tab,
    limit: "int | None" = REGISTER_LIMIT,
) -> ReconcilePage:
    """Return everything the Reconcile page renders, for ONE of its tabs.

    Plan step ``bank_import:X-gj-1a``.  The whole of what the screen displays,
    so the template displays it and computes nothing -- which is the hard
    constraint the design language states and the review screen broke by
    counting its own sweeps in Jinja.

    **The pass is derived whichever tab is open**, and that is not waste: the
    hero's *to explain*, all three holding chips and three of the five tab
    counts are facts about it.  Measured on the developer's own account
    2026-08-29: ``review_set`` 0.136 s, ``accepted_counts`` one aggregate
    query, ``bank_agreement`` 0.108 s.

    **The two settled tabs' CARDS are built only when one is open.**  Their
    counts come from :func:`~._accepted_view.accepted_counts`, which is three
    aggregates over one indexed read; building them would value EVERY act on
    the account -- :data:`~._accepted_view.REGISTER_LIMIT` bounds what is
    rendered and not what is priced -- re-deriving each one's removals and
    re-pricing its member rows, which is the cost ruling **R-GX** split the
    register off the review screen to stop paying.

    Args:
        scope: The pass to build from
            (:class:`~._scope.ReviewScope`).  **The route builds it**, which
            is the rule every read pass in this project is held to; a producer
            below the route takes one and never derives its own.
        agreement: The account's :class:`~app.services.bank_agreement
            .BankAgreement`, or ``None`` when it holds no recorded line.
            Passed in for the same reason: it needs a
            :class:`~app.services.balance_at.BalanceContext`, and only a route
            builds one of those either.
        tab: Which tab's cards to build (:class:`Tab`).
        limit: How many SETTLED acts a settled tab may render, or ``None`` for
            the whole record -- which is what the tab's own *show the other N*
            link asks for (plan step ``bank_import:X-gj-1c``).  It defaults to
            :data:`~._accepted_view.REGISTER_LIMIT`, and an act that NO LONGER
            HOLDS is never subject to it whatever this says: which acts the
            bound may reach is
            :func:`~._accepted_view.accepted_register`'s rule and is not
            restated here.  **The link exists because this page retires the
            register** (**R-HU**): the register offered exactly this, and on
            the developer's own account it reaches 171 of his 221 acts, so
            dropping it would put them out of reach rather than merely
            unlisted.

    Returns:
        The :class:`ReconcilePage`.

    Raises:
        ValueError: When *scope* and *agreement* name different accounts.
            **Two arguments a caller pairs by hand are two chances to pair one
            act's account with another's**, which is the rule
            :func:`~._accept._record` already states -- and here the
            consequence is one account's hero over another's lines, with no
            figure on the page wrong enough to look wrong.
    """
    if agreement is not None and agreement.account_id != scope.account_id:
        raise ValueError(
            f"The pass is for account {scope.account_id} and the agreement "
            f"for account {agreement.account_id}; one page cannot show one "
            f"account's lines beside another's balances."
        )
    review = review_set(scope)
    # **THE PAGE DERIVES NO CANDIDATE ROWS**, and it did until plan step
    # ``bank_import:X-gj-1b``.  It built one
    # :class:`~._panel.MatchCandidates` index and handed every card its own
    # line's pay period; the cards then rendered none of it, because the
    # panel's row list is lazy-loaded and the fragment asks for itself.  So
    # the index was derived over 248 cards and 11 periods for a value nobody
    # read.  ``accounts.statement_reconcile_match`` builds it now, once per
    # opened tab, which is where it is looked at.
    parked = tuple(
        (_parked_tab(one), parked_card(review, one))
        for one in review.parked
    )
    # **The two holding tabs, keyed by the tab that owns them.**  A mapping
    # rather than two tuples threaded side by side, so a third holding state
    # -- ``X-gj-4``'s recorded disposition is the one already ruled -- adds a
    # key here and an arm to :func:`_parked_tab`, and no signature grows.
    holding = {
        where: tuple(card for tab_, card in parked if tab_ is where)
        for where in (Tab.TRANSFERS, Tab.SKIPPED)
    }
    transfers = holding[Tab.TRANSFERS]
    counts = accepted_counts(scope.owner_id, scope.account_id)
    # **Distinct LINES, not proposals**, which is the spelling
    # :attr:`~._reads.ReviewSet.explained_by_a_proposal` records the reason
    # for: every tier builds ``lines=(line,)`` today, so the two agree on
    # every input that exists -- and a multi-line tier would make the other
    # one wrong silently.  The inbox counts what a reader can act on, and
    # that is lines.
    to_explain = (
        len({
            line.line_id
            for proposal in review.proposals for line in proposal.lines
        })
        + len(review.creatable)
        + len(review.recordable_inflows)
    )

    sections = _tab_sections(scope, review, tab, holding, limit)
    return ReconcilePage(
        tab=tab,
        hero=_hero(agreement, to_explain),
        # **One row read, and one COUNT where that row exists.**  The
        # provenance line the locked direction prints beside the four figures
        # is a fact about the last import rather than about the comparison, so
        # it is read here and carried whole.  ``EXPLAIN ANALYZE`` on a
        # restored production clone 2026-08-30: 0.034 ms and 0.071 ms of
        # execution, against ``review_set``'s own 0.136 s on the same
        # account -- so the provenance costs about a two-thousandth of what
        # the page already pays to exist.
        last_import=last_import(scope.owner_id, scope.account_id),
        # TWO arguments, not the balance side's three: its ``explained`` feeds
        # the *already explained* chip plan step ``bank_import:X-gj-1c``
        # deleted (**R-HU**), whose count is the union of two tabs.
        chips=_chips(review, transfers),
        books_bound=review.bounds.books,
        counts=(
            TabCount(tab=Tab.TO_EXPLAIN, count=to_explain),
            TabCount(tab=Tab.EXPLAINED, count=counts.by_hand),
            TabCount(tab=Tab.FILED_BY_RULES, count=counts.by_rule),
            TabCount(tab=Tab.TRANSFERS, count=len(transfers)),
            TabCount(tab=Tab.SKIPPED, count=len(holding[Tab.SKIPPED])),
        ),
        sections=sections,
        # **Only the inbox sweeps.**  A settled act is undone one at a time
        # (**R-GY** confirms where it destroys a row), and a holding card has
        # no act to sweep at all.
        sweeps=_sweeps(sections) if tab is Tab.TO_EXPLAIN else (),
        unexamined=_unexamined(review),
    )


def _holding(cards: "tuple[LineCard, ...]") -> "tuple[CardSection, ...]":
    """Return a holding tab's one unnamed section, or nothing at all.

    Args:
        cards: The tab's cards.

    Returns:
        One :class:`~._cards.CardSection` withholding nothing, or ``()`` --
        because an empty section is ABSENT rather than rendered empty.
    """
    if not cards:
        return ()
    return (CardSection(section=None, cards=cards, withheld=0),)


def _tab_sections(
    scope: "ReviewScope",
    review: "ReviewSet",
    tab: Tab,
    holding: "dict[Tab, tuple[LineCard, ...]]",
    limit: "int | None",
) -> "tuple[CardSection, ...]":
    """Return the cards of the open tab, and of no other.

    **A dispatch over the five, exhaustive by construction**: every member of
    :class:`Tab` has an arm, so a sixth tab added later fails loudly here
    rather than rendering an empty page.

    Args:
        scope: The pass's scope, for the two reads the settled tabs need.
        review: The pass.
        tab: Which tab is open.
        holding: The cards of each holding tab, keyed by that tab.
        limit: How many SETTLED acts a settled tab may render, or ``None`` for
            all of them.  It reaches only the two settled arms: the inbox is
            bounded by what the pass found and a holding tab renders every
            line in its state (see :func:`_holding`).

    Returns:
        The sections, empty where the tab has no cards.

    Raises:
        ValueError: When *tab* is not one this function knows, which cannot
            happen for a :class:`Tab` member and is raised rather than
            defaulted so that adding one is a failure and not a blank screen.
    """
    if tab is Tab.TO_EXPLAIN:
        return to_explain_sections(review)
    if tab is Tab.EXPLAINED:
        return act_sections(accepted_register(
            scope.owner_id, scope.account_id, limit, applied_by_rule=False,
        ))
    if tab is Tab.FILED_BY_RULES:
        # **The register's reader and not** :func:`~._filing.rule_filed_acts`,
        # which is the IMPORT page's receipt: it bounds at
        # :data:`~._filing.RECEIPT_LIMIT` and orders by age alone, where a tab
        # beside Explained has to bound the same way and float an act that has
        # stopped holding to the top.  The two surfaces ask different
        # questions of one set, so each keeps its own reader.
        return act_sections(accepted_register(
            scope.owner_id, scope.account_id, limit, applied_by_rule=True,
        ))
    if tab in holding:
        # **A holding tab withholds nothing**: it renders every line in its
        # state, because a count of card payments the owner cannot act on is
        # useless if it is also truncated.
        return _holding(holding[tab])
    raise ValueError(f"No Reconcile tab is built for {tab!r}.")
