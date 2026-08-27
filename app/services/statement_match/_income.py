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
between, which is why its control is a tick rather than a select and why no
merchant rule reaches it (a rule says where SPENDING goes; ruling **R-GI**).

**The ROW is the one :func:`~._variance.mint` already writes**, through the
shared :func:`~._uncategorized.mint_uncategorized`: uncategorized, so the
ledger books it to the per-owner Uncategorized fallback and the owner can
categorise it whenever they know what it was.  The app does not know what a
`$0.15` dividend belongs under, and inventing an answer is the misfiling that
ruling **R-FN** already refused one door over.

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
from ._resolve import load_lines
from ._scope import ReviewScope
from ._uncategorized import mint_uncategorized

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
    creation: IncomeCreation, scope: ReviewScope,
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
    # A line posted past the last SAVED pay period is not split off by the
    # review screen's own bounds, so this refusal is live rather than
    # theoretical.
    pay_period_id = scope.period_holding(line.posted_on, "this deposit")
    candidate = mint_uncategorized(
        # What the BANK NAMES the merchant, not the whole line, for the reason
        # a recorded purchase takes the same label: the app's own rows are
        # called "Dividend Earned", and a row named
        # ``POINT OF SALE CREDIT L340 DATE 04-15 AMAZON MKTPLA...`` would be
        # the only one on the grid nobody can read.  The bank's full wording is
        # not lost -- it stays on the statement line, which the match this door
        # records ties to this row.  ``merchant_label`` is TOTAL: it falls back
        # to the description for a source that names no merchant.
        merchant_label(line.merchant_name, line.description),
        amount, pay_period_id, line.posted_on, scope,
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
        # **Never a rule** (ruling **bank_import:R-GW**).  A merchant answer says where
        # SPENDING goes, and a deposit is not spending, so nothing but a person
        # ticking this line can reach this door -- and a literal ``False`` here
        # is that fact rather than a default nobody chose.
        applied_by_rule=False,
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
        "A bank statement line was recorded as an uncategorized income row.",
        user_id=scope.owner_id,
        account_id=scope.account_id,
        line_id=line.id,
        transaction_id=recorded.transaction_id,
        match_id=recorded.match_id,
        amount=str(recorded.amount),
        posts_on=recorded.posts_on.isoformat(),
        pay_period_id=pay_period_id,
    )
    return recorded
