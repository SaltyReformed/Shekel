"""The door that turns a bank line of money COMING IN into the row it is.

Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``.  **It MOVES MONEY**: a line
recorded here becomes a cash movement the app did not have, dated the day the
bank posted it.

**It exists because the two refusals that bound the create arm point at each
other, and an inflow sits between them.**  :func:`~._accept._reject_empty_side`
refuses a match with no app row, saying *"a bank line with no app row is what
X-f6a-3b turns into a purchase"*; :func:`~._create._load_line` refuses an
inflow, saying -- until this step rewrote it to name this door too --
*"Match it to the row it belongs to instead"*.  Each is right on its own.
Together they leave a whole DIRECTION of movement with no act at all, and the
review screen counts every one of those lines as work forever.

**Measured on the developer's own dev database 2026-08-27**, over the 27 lines
``awaiting_review_count`` reports: EIGHT are inflows smaller than `$39.54`, the
smallest positive row the hand-build form offers -- five dividends of `$0.12`
to `$0.22` and three card refunds of `$11.73` to `$28.29`, **`$58.87`**
together.  No single row and no SUM of positive rows can equal one of them, so
every match that would dispose of one asserts a falsehood and books a residual
nobody owes.  This door is what makes them disposable TRUTHFULLY, by recording
the money instead of the opinion.

**Why an INCOME row and never a purchase.**  A purchase is the app's record of
one payment against a container that reserves for it, and a deposit reserves
nothing -- ``ck_transaction_entries_positive_amount`` says so in the schema.
So this door asks the owner for no destination at all: there is nothing to pick
between, which is why its control is a tick rather than a select.

**A standing rule DOES reach it now** (ruling **R-HT(a)**, plan step
``bank_import:X-gj-2a``), and this paragraph said the opposite until then: *no
merchant rule reaches it -- a rule says where SPENDING goes*.  Ruling **R-GI**
made that true and **R-HT(a)** amended R-GI, giving the answer set a fifth
member that says what a DEPOSIT from a signature IS.  What the rule supplies is
a CLASSIFICATION and not a destination -- the row is still filed against
nothing -- so the tick-not-a-select argument above survives the amendment
whole.

**The ROW is the one :func:`~._variance.mint` already writes**, through the
shared :func:`~._uncategorized.mint_uncategorized`.  **Uncategorized unless a
standing rule says otherwise** (**R-HT(a)**, plan step
``bank_import:X-gj-2a``): with no rule the ledger books it to the per-owner
Uncategorized fallback and the owner categorises it whenever they know what it
was, because the app does not know what a `$0.15` dividend belongs under and
inventing an answer is the misfiling ruling **R-FN** refused one door over.
Where the owner HAS said, that is not an invention -- it is their answer, and
writing the row uncategorized would discard it.

**What it deliberately does NOT do.**  It does not touch the PARKED lines
(ruling **R-GJ**): those are outflows whose money the budget already holds in
another shape, so recording them is the `$7,412.94` double count that ruling
closed.  Their disposition is a group match, and where no group exists the
badge stays non-zero because the books really are missing that money -- which
is the card arc's to dissolve (**N-337**, ``credit_card:CC3b``).

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.enums import SettledDayBasisEnum
from app.exceptions import ValidationError
from app.models.statement_import import BankStatementLine
from app.services.settle_day import SettleDay
from app.services.status_seam import reject_future_settle_day
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_INCOME_RECORDED,
    log_event,
)

from ._accept import MatchContent, record_match
from ._candidates import MatchedSubjects, matched_subjects
from ._creations import CreatedSubject, IncomeCreation, RecordedIncome
from ._offers import merchant_label
from ._placement import inflow_placement_for
from ._rules import RuleView
from ._resolve import load_lines
from ._scope import ReviewScope
from ._uncategorized import MovementToRecord, mint_uncategorized

_logger = logging.getLogger(__name__)


def _load_line(
    creation: IncomeCreation,
    matched: MatchedSubjects,
    scope: ReviewScope,
) -> BankStatementLine:
    """Return the submitted line, refusing one this door may not record.

    **The account scope and the already-matched refusal are
    :func:`~._resolve.load_lines`'**, not restated here, for the reason
    :func:`~._create._load_line` gives: *is this line on this account, and has
    something already claimed it* has to be ONE answer, or it is two places for
    a refusal to stop firing.

    The second refusal IS this door's own, and it is the exact mirror of the
    one :mod:`._create` applies: the line must be money ARRIVING.  A line of
    money leaving is a purchase, and recording it as income would invert the
    sign of a real movement.  **The two DOORS are total over the lines the
    schema allows** -- hand either one any line and exactly one accepts it --
    which is a narrower claim than the one about the two LISTS the screen
    builds from, where :func:`~._leftovers._creatable_lines` drops a class
    (finding **N-325**) that reaches neither.

    **The comparison is ``<= 0`` and the zero is UNREPRESENTABLE, not merely
    unreached.**  ``ck_bank_statement_lines_amount_real_nonzero`` declares
    ``amount <> 0``, so no line the database can hold takes that arm -- it is
    written this way because its sibling is (``>= 0``), because *this door
    takes money arriving* is one comparison rather than two, and because a
    schema is the right place for that guarantee.  There is deliberately no
    BRANCH for it and no case asserting it: a guard no mutation can reach with
    a test that grades nothing is exactly what
    :meth:`~._bars.CreationBars.bar_for` records having had to delete.
    ``TestTheSchemaIsWhatMakesTheTwoDoorsTotal`` grades the constraint itself,
    which is where the fact actually lives.

    Args:
        creation: What the owner submitted.
        matched: What this account's matches have already claimed, as of this
            act.
        scope: The pass, which is the ONE statement of which account's lines
            may be reached.

    Returns:
        The line.

    Raises:
        ValidationError: On any of the three.
    """
    line = load_lines(
        scope.account_id, frozenset({creation.line_id}), matched,
    )[0]
    if line.amount <= 0:
        raise ValidationError(
            "Only money ARRIVING in the account can be recorded this way, and "
            "that line is money going out.  Record it as a purchase, or match "
            "it to the row it belongs to.  Nothing was changed."
        )
    return line


def _observed(line: BankStatementLine) -> SettleDay:
    """Return *line*'s posting day as a day a bank statement SHOWED.

    One construction for the two things that need it -- the refusal that runs
    before the write, and the day the row settles on -- because a refusal
    tested against one value and a write performed with another is two answers
    to one question.  :mod:`._create` states the same helper for the same
    reason.

    Args:
        line: The bank line being recorded.

    Returns:
        A :class:`~app.services.settle_day.SettleDay` over ``posted_on`` on the
        ``observed`` basis (plan step ``balance:X-az``): the bank line IS why
        this row exists, so its posting day is a day a statement showed rather
        than a bound or a day the owner typed.
    """
    return SettleDay(
        day=line.posted_on, basis=SettledDayBasisEnum.OBSERVED,
    )


def record_income_from_line(
    creation: IncomeCreation,
    scope: ReviewScope,
    view: RuleView,
    *,
    applied_by_rule: bool = False,
) -> RecordedIncome:
    """Record one bank line as an income row, and match the line to it.

    The whole act, in the order its refusals have to happen: the line is
    checked, the paycheck that holds it is resolved -- which can refuse -- and
    only then is anything written.  The match goes through
    :func:`~._accept.record_match`, so the correspondence is written by the
    same function that writes every other one, with ruling **R-FT**'s table,
    ruling **R-FV**'s identity-only rule and every guard those carry.

    **The row is placed by the POSTING day**, and that is the residual's rule
    rather than the purchase's.  A purchase has a budget clock of its own -- the
    day it was made -- and this row has none: it IS the movement, so the day the
    bank credited the account is the only day it has.  ``transaction_on`` is
    read for nothing here, which is why it is not read.

    **It records the row it just built rather than asking a door to find it**,
    which is plan step ``bank_import:X-f6a-3c-2``'s rule: a row created inside
    a pass cannot be in an offer set derived before that pass, so proving it
    offerable would refuse every line this door records.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        creation: What the owner submitted, which is one line id and nothing
            else.  **There is no destination and no figure**: an income row has
            no container to choose between, and the amount and the day are read
            from the recorded LINE inside this transaction, so a stale page
            cannot commit a number the bank did not state.
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).
            **Required rather than defaulted**, for the reason
            :func:`~._accept.accept_match`'s is.
        view: What the owner has SAID (:class:`~._rules.RuleView`), from which
            this door derives what the deposit is filed under.

            **The DOOR derives it, and that is the whole of ruling R-HT(a)'s
            consent story** (plan step ``bank_import:X-gj-2a``, corrected by
            adversarial code review 2026-08-31).  A first version carried the
            category on the :class:`~._creations.IncomeCreation` instead, set
            only by :meth:`~._placement.InflowPlacement.creation_for` -- which
            is reachable from the import-time rule pass and from nowhere else.
            So the Reconcile card said *Add as Interest income*, the owner
            pressed OK, and the row was written UNCATEGORIZED: the automatic
            door and the press were two answers to *what is this deposit*, on
            the door that moves money.  Deriving here makes them one answer
            because it is one derivation, which is what that field's own
            docstring had claimed and nothing performed.

            **Read once per BATCH and threaded**, exactly as
            :class:`~._bars.CreationBars` is: a per-act read would ask
            ``merchant_rules`` once per deposit.
        applied_by_rule: Whether a STANDING RULE performed this rather than a
            person ticking it (**R-GT**, **R-HT(a)**).  Keyword-only and
            defaulted FALSE, which is the shape :func:`~._accept.record_match`
            already has and for its reason: the two values are *the owner
            agreed to this* and *the app did it on their behalf*, and the
            default has to be the one that claims less.  It was a hardcoded
            literal ``False`` here until plan step ``bank_import:X-gj-2a`` --
            correct while ruling **bank_import:R-GW** said a rule could never
            answer a deposit, and R-HT(a) is what moved that.

    Returns:
        The :class:`~._creations.RecordedIncome`.

    Raises:
        ValidationError: On any of this door's refusals, the settle day's or
            the period resolution's -- all of which fire BEFORE anything is
            written.  A 400: every one is reachable by an ordinary owner
            working from a stale page.
    """
    # ONE read of what this account's matches have claimed, for this act: the
    # line refusal and the double-count guard inside ``record_match`` both
    # narrow with it, so they cannot disagree.
    matched = matched_subjects(scope.account_id)
    line = _load_line(creation, matched, scope)
    amount = Decimal(str(line.amount))
    # **EVERY refusal this act owes fires BEFORE anything is written**, and
    # the second one is here because an adversarial review 2026-08-27 measured
    # it firing AFTER: ``mint_uncategorized`` writes and settles, and the
    # settle verb is what refuses a day that has not happened yet, so a
    # future-dated line left a settled row behind for the batch's SAVEPOINT to
    # take back -- a dependency :mod:`._accept` explicitly declines, and one
    # this door's only other caller (a direct ``__init__`` import) does not
    # have at all.
    #
    # It is the status seam's OWN refusal, called early rather than restated:
    # :func:`~app.services.status_seam.reject_future_settle_day` is the rule,
    # and :func:`~app.services.status_seam.day_is_in_the_future` is the same
    # predicate the screen asks so it renders no tick for such a line.
    reject_future_settle_day(_observed(line))
    # **The other end of the same clock** (plan step **balance:X-f3c-2b-2b**,
    # finding **N-383**): the refusal above bounds the line at TODAY, and this
    # one bounds it at the day the account's books open.  ``mint_uncategorized``
    # writes and settles, and the settle verb refuses the day only after the
    # row exists -- which is the ordering this comment's neighbour above
    # exists because of, one bound over.
    scope.reject_line_before_books_open(line.posted_on, "this deposit")
    # **The REFUSALS stay above the READ, and that ordering is the merge's own
    # decision** (``balance:X-f3c-2b-2b`` into ``bank_import:X-gj-2a``,
    # 2026-08-31).  Resolving a refusal BELOW the derivation it guards still
    # refuses, so every case asserting a ``ValidationError`` stays green -- and
    # what leaks is the work done in between.  Nothing here writes, so this
    # particular order is not load-bearing TODAY; it is written this way
    # because the paragraph above states the rule as *every refusal this act
    # owes fires before anything is written*, and a derivation that later grew
    # a write would inherit the wrong side of it.
    placement = inflow_placement_for(line.merchant_id, view)
    # **ONE reading of what the owner said, used by the WRITE and by the LOG.**
    # Two expressions of the same rule are two things that can come to
    # disagree, and here they would disagree about what a money row records.
    filed_under = (
        placement.category_id
        if placement is not None and placement.records else None
    )
    # A line posted past the last SAVED pay period is not split off by the
    # review screen's own bounds, so this refusal is live rather than
    # theoretical.
    pay_period_id = scope.period_holding(line.posted_on, "this deposit")
    candidate = mint_uncategorized(
        MovementToRecord(
            # What the BANK NAMES the merchant, not the whole line, for the
            # reason a recorded purchase takes the same label: the app's own
            # rows are called "Dividend Earned", and a row named ``POINT OF
            # SALE CREDIT L340 DATE 04-15 AMAZON MKTPLA...`` would be the only
            # one on the grid nobody can read.  The bank's full wording is not
            # lost -- it stays on the statement line, which the match this door
            # records ties to this row.  ``merchant_label`` is TOTAL: it falls
            # back to the description for a source that names no merchant.
            name=merchant_label(line.merchant_name, line.description),
            signed_amount=amount,
            pay_period_id=pay_period_id,
            posts_on=line.posted_on,
            # **What the owner said this money IS, or nothing** (**R-HT(a)**).
            # It is not read from the wire: the Reconcile card renders no
            # category picker, so this arrives on the creation from the stored
            # rule the caller resolved -- one derivation for the automatic door
            # and the owner's own OK, which is what stops the two answering
            # *what is this deposit* differently.
            # **What the owner SAID this money is, or nothing** (**R-HT(a)**).
            # Resolved from the stored rule HERE, so the automatic door and
            # the owner's own OK reach one answer rather than two.  A
            # placement that does not RECORD -- no rule, *ask me every time*,
            # a spending answer whose refund arm is
            # ``bank_import:X-gj-2b``'s, or an ARCHIVED category -- names no
            # category, and the row is the uncategorized one this door has
            # always written.
            category_id=filed_under,
        ),
        scope,
    )
    accepted = record_match(
        scope,
        MatchContent(
            lines=[line], rows=[candidate],
            # **Created AND named**, which is the purchase's asymmetry without
            # its exception: this row is what the bank line IS, and this act is
            # the only reason it exists, so an undo takes it back whole.  There
            # is no container beside it to leave out.
            created=(CreatedSubject.of(candidate),),
        ),
        matched,
        # **A rule CAN reach this door since plan step
        # ``bank_import:X-gj-2a``** (ruling **R-HT(a)**), where it was a
        # literal ``False`` on ruling **bank_import:R-GW**'s ground that a
        # merchant answer only ever said where SPENDING goes.  R-HT(a) gave the
        # answer set a fifth member that says what a DEPOSIT is, so the flag is
        # now the caller's to state and this door records what it is told
        # rather than asserting what it assumed.
        applied_by_rule=applied_by_rule,
    )
    recorded = RecordedIncome(
        transaction_id=candidate.row_id,
        match_id=accepted.match_id,
        label=candidate.label,
        # **From the CANDIDATE, not from a second copy of the arithmetic.**
        # The writer stored the figure and the candidate carries what the row
        # is worth, so reading it back is the one place it is stated -- a
        # receipt that recomputed it could report a figure the database does
        # not hold.
        amount=candidate.cash_amount,
        posts_on=line.posted_on,
        pay_period_id=pay_period_id,
    )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_INCOME_RECORDED, BUSINESS,
        "A bank statement line was recorded as an income row.",
        user_id=scope.owner_id,
        account_id=scope.account_id,
        line_id=line.id,
        transaction_id=recorded.transaction_id,
        match_id=recorded.match_id,
        amount=str(recorded.amount),
        # **WHICH category, or none**, so the log tells the two cases apart --
        # a rule-filed deposit and a hand-ticked one write different rows and
        # an event that named neither could not say which had happened.
        category_id=filed_under,
        posts_on=recorded.posts_on.isoformat(),
        pay_period_id=pay_period_id,
    )
    return recorded
