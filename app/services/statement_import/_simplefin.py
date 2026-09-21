"""The SimpleFIN feed's READER: one Bridge account answer -> what the feed states.

A SOURCE ADAPTER over the one line shape (ruling **R-FP**), beside the CSV's
and never in the file parsers' table (ruling **R-BI30**, plan step
``bank_import:X-f6b-2``): its input is not a file but one member of Bridge's
``accounts`` answer, the window the sync asked for, and the day it is --
three things no upload form hands a parser.  It is PURE: no database, no
clock, no request.  The sync reads the clock once at its boundary and passes
``today`` in, and records what this returns through
:func:`~._record.record_parsed`.

**What the feed records is what is FINAL** (ruling **R-BI22**, as amended by
**R-BI34** and by **R-BI35**, **R-BI36**, **R-BI37** of 2026-09-21).  Bridge
delivers a line the same day in the bank's raw text
(``APPLE.COM/BILL           CUPERTINO    CA``) and rewrites it when the bank
POSTS it -- MX's naming, ``<merchant> <Category/Sub>`` -- which is also when
its day and amount become certain: measured 2026-09-20 against SECU's own
export beside the developer's feed, a raw line's day moved 09-16 -> 09-18 on
posting while Bridge still showed it raw, and a raw line was still raw four
days on.  A recorded raw line is therefore a wrong merchant for ever and, in
four of five posting outcomes, a refusal the record door raises every night
after (``StatementLineIdMoved``, ``StatementLineConflict``) or a duplicate
line.  So:

* a line is CLEAN when its description splits on ``" <category>/"`` for one
  of the ten top-level MX categories measured (:data:`_MX_CATEGORIES`, the
  EARLIEST such marker in the text, the description read with a leading
  space so a category at its very start is found there); its merchant is the
  prefix before the category -- the CSV's own merchant word, 21 of 21 on the
  measured overlap -- and the category string is the sighting's
  ``source_category``.  An EMPTY prefix (``Shopping/Clothing`` alone, 0 of
  153 measured lines) is the source naming no merchant, which is what a
  ``None`` merchant means on the line shape: the line is categorised, so it
  is final, and it records with no merchant word rather than opening a hole
  no posting would close.  Every other line is RAW, and RAW is two kinds
  (ruling **R-BI37**): the bank's own text, and a line MX HAS categorised
  under a name this module does not know (``Dr Smith Health/Doctor``).  Both
  hold their day; the reading names the second kind's categories so the
  sync's WARNING can say the hole is one line of :data:`_MX_CATEGORIES` from
  closing, where a raw day of bank text closes on its own when the bank
  posts;
* **a raw day is a HOLE in coverage** (ruling **R-BI34**): no raw line is ever
  recorded, and the window is declared as the maximal RUNS of days around
  every raw day, one :class:`~._line.ParsedStatement` per run, each
  declaring exactly the days whose lines are all final.  A raw day's clean
  lines are held with it: a run that vouched for that day would vouch for a
  line that may still move.  The sync re-fetches a hole nightly until it
  closes;
* a ``$0.00`` line is never recorded (``ck_bank_statement_lines_amount_real_nonzero``
  refuses it) and is counted apart, but a raw ``$0.00`` line is raw like any
  other and its day is a hole (ruling **R-BI36**): a hold that later posts
  as an amount under that day would otherwise post into a day already
  declared covered, R-BI34's own rejected silent miss;
* the span is capped at YESTERDAY (``min(window_end - 1, today - 1)``,
  ruling **R-BI22**): today's lines may still arrive;
* HELD is every non-zero line in no run -- raw, clean on a raw day, after the
  cap -- and what the sync reports;
* the CLAIM is stated by the present window's FIRST run (ruling **R-BI35**,
  amending R-BI34's "on the last run"): Bridge's balance minus EVERY line
  posted after that run's end, held or in a later run, as of that end.
  That is the level the recorded lines reach on that day whenever Bridge's
  balance contains every line it lists, whether or not a later line is
  recorded.  The first run's opening is the day before the window when the
  window's first day is final, and the previous nights' coverage prices it,
  so :func:`~._anchor.resolve_anchor` SOLVES the claim against the recorded
  lines or records it UNSOLVED and visible (measured 2026-09-20: a phantom
  line in Bridge's list and not in its balance would have placed a level
  43.40 above the recorded walk had the claim sat on the last run, whose
  opening is a hole).  When the window's FIRST day is raw the first run
  starts after it and its opening is that hole: the claim is then recorded
  unsolved until the hole closes, and every raw line in the window is either
  after the run and subtracted or before it and behind that unsolved
  opening, so no wrong figure is ever solved.  On an account's true FIRST
  import the assumed arm places the claim unchecked, phantom and all -- the
  ruling's stated cost, exposed by the first CSV checkpoint after it.
  Bridge's balance is as of NOW, so only the present window -- the one
  whose end is tomorrow, read off the window itself -- states a claim; a
  back-walk window and a hole re-fetch state none;
* ``transaction_on`` is never written: Bridge's ``transacted_at`` equalled
  ``posted`` on 183 of 183 measured lines, so it states no second day.
  ``running_balance`` is never written: the feed carries none.

Measured on the developer's own feed (two fetches of 2026-09-18, 07-05..08-19
with 91 lines and 08-05..09-19 with 92, the latter re-served identically on
2026-09-20): every ``posted`` is 12:00:00 UTC, so the display-tz civil day
(America/New_York, :func:`~app.utils.dates.to_display_date`) coincided with
the UTC day on every line; ids are ``TRN-`` + a uuid, 40 characters; amounts
are decimal STRINGS; the list is newest-first, and it is ordered here as the
CSV adapter orders its file.  An answer that is not that shape is refused
for THAT account (:class:`~app.exceptions.FeedAnswerUnreadable`, ruling
**R-BI31**: the sync's unit of work is the account), with the field named
for the log and no URL in the sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.exceptions import FeedAnswerUnreadable
from app.utils.dates import to_display_date
from app.utils.money import MoneyTextError, money_from_text

from ._line import ParsedStatement, StatementLine

#: The one currency this app records.  Bridge lists the owner's accounts in
#: whatever each is denominated in; one in anything else is refused rather
#: than recorded as dollars.
_CURRENCY = "USD"

#: MX's ten top-level categories, measured 2026-09-18 on the developer's own
#: feed (finding F7 of plan step ``bank_import:X-f6b-2``): a cleaned
#: description is ``<merchant> <Category/Sub...>`` and ``Home/Mortgage/Rent``
#: has two slashes, so the split is on ``" <category>/"`` and not on the last
#: slash.  A description that splits on none of these is RAW (ruling
#: **R-BI22**); one that MX categorised under an eleventh name is raw of the
#: second kind (ruling **R-BI37**), and adding the name here is what closes
#: its hole.
_MX_CATEGORIES = frozenset({
    "Entertainment",
    "Financial Services",
    "Food & Drink",
    "Home",
    "Income",
    "Miscellaneous",
    "Services",
    "Shopping",
    "Transportation",
    "Utilities",
})

#: The markers a description may split on.  The EARLIEST one in the text
#: wins (:func:`_split_description`), which is what makes ``" Services/"``
#: inside ``" Financial Services/"`` harmless: the longer marker starts ten
#: characters earlier.
_CATEGORY_MARKERS = tuple(f" {name}/" for name in _MX_CATEGORIES)

#: The SHAPE of an MX category: Title-case words, ``&`` allowed, before a
#: slash, after a space -- what tells a categorised line under a name this
#: module does not know (ruling **R-BI37**) from the bank's own text, which
#: is upper-case (``DEPOSIT/BRANCH 000/S*0000N`` matches nothing here).  Used
#: only AFTER the known names failed, and only to NAME the hole: what it
#: captures may carry the merchant's trailing Title-case words too
#: (``Smith Health`` for ``Dr Smith Health/Doctor``), which is enough for a
#: reader to pick the category out of.
_CATEGORY_SHAPE = re.compile(r" ((?:[A-Z][a-z]+|&)(?: (?:[A-Z][a-z]+|&))*)/")

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class FeedReading:
    """What one Bridge account's answer states, as the sync records it.

    Attributes:
        runs: One :class:`~._line.ParsedStatement` per maximal run of final
            days in the window, oldest first, each with the clean lines of
            its days in chronological order.  Empty when no day was final.
        holes: The raw days inside the window's capped span, ascending: the
            days this answer could not declare, for the sync to name and
            re-fetch.
        unknown_categories: ``(day, category)`` for every hole the second
            kind of raw line opens -- a categorised line under a name
            :data:`_MX_CATEGORIES` lacks -- ascending, each once, for the
            sync's WARNING to name (ruling **R-BI37**).
        held_count: Non-zero lines in no run -- raw, clean on a raw day, or
            after the cap.
        zero_skipped: ``$0.00`` lines, which are never recorded.
    """

    runs: tuple[ParsedStatement, ...]
    holes: tuple[date, ...]
    unknown_categories: tuple[tuple[date, str], ...]
    held_count: int
    zero_skipped: int


@dataclass(frozen=True)
class _FeedLine:
    """One transaction as read.

    Attributes:
        line: The line it states.
        raw: Whether it is raw of either kind: not recordable tonight.
        unknown_category: For the second kind of raw line, the category
            shape the text carries under a name this module lacks; ``None``
            for a clean line and for the bank's own text.
    """

    line: StatementLine
    raw: bool
    unknown_category: "str | None"


def _field(item: object, name: str) -> object:
    """Return *item*'s *name*, refusing an answer that has no such field."""
    try:
        return item[name]
    except (KeyError, TypeError):
        raise FeedAnswerUnreadable(
            f"Bridge's answer for this account has no '{name}', which this "
            f"app needs to read it, so nothing was recorded for it.",
            field=name, problem="missing",
        ) from None


def _text(item: object, name: str) -> str:
    """Return *item*'s *name* as a non-empty string, or refuse."""
    value = _field(item, name)
    if not isinstance(value, str) or not value:
        raise FeedAnswerUnreadable(
            f"Bridge's answer for this account states its '{name}' as "
            f"something other than text, so nothing was recorded for it.",
            field=name, problem="shape",
        )
    return value


def _money(item: object, name: str) -> Decimal:
    """Return *item*'s *name* as cents, refusing anything but a finite decimal string.

    Through :func:`~app.utils.money.money_from_text`, the ONE walk from a
    source's text to a recorded figure -- Bridge writes every figure as a
    STRING (``"386.05"``), and the walk refuses anything else unread, a
    quiet ``NaN``, and a figure too large to round.

    Args:
        item: The account or transaction object.
        name: ``balance`` or ``amount``.

    Returns:
        The figure, rounded to cents.

    Raises:
        FeedAnswerUnreadable: When the field is missing or the walk refuses
            it, with the walk's reason in the sentence.
    """
    try:
        return money_from_text(_field(item, name))
    except MoneyTextError as exc:
        raise FeedAnswerUnreadable(
            f"Bridge's answer for this account states a '{name}' that is "
            f"{exc.reason}, so nothing was recorded for it.",
            field=name, problem="shape",
        ) from None


def _civil_day(item: object) -> date:
    """Return the display-tz civil day a transaction's ``posted`` names.

    ``posted`` is a unix epoch (SimpleFIN's shape; ``0`` for a line the bank
    has not posted, which the sync never asks for and this does not read as
    1970).  The instant is converted through
    :func:`~app.utils.dates.to_display_date`, the app's one civil-day rule,
    so a late-evening Eastern posting does not roll onto the next UTC day.
    A float epoch is read as an int one is: an instant loses no money
    precision through a float, where an amount would.

    Args:
        item: The transaction object.

    Returns:
        The civil day.

    Raises:
        FeedAnswerUnreadable: When ``posted`` is missing, not a positive
            number, or not an instant Python can place.
    """
    value = _field(item, "posted")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
    ):
        raise FeedAnswerUnreadable(
            "Bridge's answer for this account states a line's 'posted' as "
            "something other than a timestamp, so nothing was recorded for "
            "it.",
            field="posted", problem="shape",
        )
    try:
        instant = datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise FeedAnswerUnreadable(
            "Bridge's answer for this account states a line's 'posted' as an "
            "instant this app cannot place, so nothing was recorded for it.",
            field="posted", problem="shape",
        ) from None
    return to_display_date(instant)


def _split_description(description: str) -> "tuple[str, str] | None":
    """Return ``(merchant, category)`` for a cleaned description, or ``None``.

    The rule finding F7 measured: the FIRST ``" <known category>/"`` in the
    text (:data:`_CATEGORY_MARKERS`), the text read with a leading space so
    a category at its very start is a marker too and not a merchant named
    after its own first word (``Financial Services/Credit Card Payment``
    would otherwise split at ``" Services/"`` with ``Financial`` the
    merchant; found by adversarial review 2026-09-21).  The merchant is what
    precedes the marker, ``None`` when nothing does; the category is
    everything from the name on, slashes included.

    Args:
        description: What Bridge wrote for the line.

    Returns:
        The pair for a clean line; ``None`` for a raw one of either kind.
    """
    padded = " " + description
    found = [
        index for index in (padded.find(marker) for marker in _CATEGORY_MARKERS)
        if index >= 0
    ]
    if not found:
        return None
    # ``index`` is in the padded text: the marker's space is ``description``'s
    # character before it, so the merchant ends one earlier and the category
    # starts there.
    index = min(found)
    return (description[:index - 1] if index > 0 else None), description[index:]


def _unknown_category(description: str) -> "str | None":
    """Return the category shape a raw description carries, or ``None``.

    Asked only of a description :func:`_split_description` refused: a match
    here is a line MX categorised under a name :data:`_MX_CATEGORIES` lacks
    (ruling **R-BI37**), and what is returned is the text from the earliest
    shape on -- ``Smith Health/Doctor`` -- for the sync's WARNING to name.
    No match is the bank's own text.

    Args:
        description: What Bridge wrote for the line.

    Returns:
        The category shape and what follows it, or ``None``.
    """
    match = _CATEGORY_SHAPE.search(description)
    if match is None:
        return None
    return description[match.start() + 1:]


def _read_line(item: object) -> _FeedLine:
    """Read one transaction object into the line it states.

    Args:
        item: One member of the account's ``transactions`` list.

    Returns:
        The :class:`_FeedLine`.

    Raises:
        FeedAnswerUnreadable: When a field is missing or not its shape.
    """
    external_id = _text(item, "id")
    posted_on = _civil_day(item)
    amount = _money(item, "amount")
    description = _text(item, "description")
    split = _split_description(description)
    merchant, category = split if split is not None else (None, None)
    return _FeedLine(
        line=StatementLine(
            posted_on=posted_on,
            transaction_on=None,
            amount=amount,
            description=description,
            merchant=merchant,
            source_category=category,
            external_id=external_id,
            running_balance=None,
        ),
        raw=split is None,
        unknown_category=(
            _unknown_category(description) if split is None else None
        ),
    )


def _runs(window_start: date, cap: date, raw_days: "set[date]") -> "list[tuple[date, date]]":
    """Return the maximal runs of days in ``[window_start, cap]`` not in *raw_days*.

    Args:
        window_start: The first day of the span.
        cap: The last, inclusive; a cap before the start is an empty span.
        raw_days: The days a raw line is posted on.

    Returns:
        ``(first, last)`` per run, ascending, each inclusive.
    """
    runs: "list[tuple[date, date]]" = []
    start: "date | None" = None
    day = window_start
    while day <= cap:
        if day in raw_days:
            if start is not None:
                runs.append((start, day - _ONE_DAY))
                start = None
        elif start is None:
            start = day
        day += _ONE_DAY
    if start is not None:
        runs.append((start, cap))
    return runs


@dataclass(frozen=True)
class _Answer:
    """One account's answer, read and refused of everything it can be.

    Attributes:
        external_account_id: Bridge's id for the account.
        balance: Bridge's balance, as of the answer.
        lines: Every line, ``$0.00`` ones included, oldest first.
    """

    external_account_id: str
    balance: Decimal
    lines: "list[_FeedLine]"


def _read_answer(item: object, *, window_start: date, window_end: date) -> _Answer:
    """Read one account's answer, refusing what the module docstring names.

    Args:
        item: One member of Bridge's ``accounts`` list.
        window_start: The first day the sync asked for.
        window_end: The day after the last, exclusive.

    Returns:
        The :class:`_Answer`.

    Raises:
        FeedAnswerUnreadable: The account is not in dollars, a field is
            missing or not its shape, or a line is posted outside the window.
    """
    currency = _text(item, "currency")
    if currency != _CURRENCY:
        # Bridge's string, bounded: it reaches a flash, and a code is three
        # letters.
        raise FeedAnswerUnreadable(
            f"Bridge lists this account in {currency[:8]!r}, and this app "
            f"records only {_CURRENCY}, so nothing was recorded for it.",
            field="currency", problem="currency",
        )
    external_account_id = _text(item, "id")
    balance = _money(item, "balance")
    transactions = _field(item, "transactions")
    if not isinstance(transactions, list):
        raise FeedAnswerUnreadable(
            "Bridge's answer for this account carries no transaction list, "
            "so nothing was recorded for it.",
            field="transactions", problem="shape",
        )
    read = [_read_line(transaction) for transaction in transactions]
    outside = sum(
        1 for feed_line in read
        if not window_start <= feed_line.line.posted_on < window_end
    )
    if outside:
        # Refused every night the line stays in the answer: the strict
        # posture, and the log says which account and why.
        raise FeedAnswerUnreadable(
            f"Bridge answered with {outside} line(s) posted outside the days "
            f"it was asked for, so nothing was recorded for this account.",
            field="posted", problem="outside_window",
        )
    # Oldest first, as every adapter's lines are.  Bridge lists newest first;
    # a stable sort over the reversed list keeps Bridge's own order within a
    # day, as the CSV adapter's reversal keeps its file's.
    return _Answer(
        external_account_id=external_account_id,
        balance=balance,
        lines=sorted(reversed(read), key=lambda feed_line: feed_line.line.posted_on),
    )


@dataclass(frozen=True)
class _Partition:
    """The capped span cut into runs around the raw days (ruling **R-BI34**).

    Attributes:
        runs: ``(first, last)`` per run, ascending, inclusive.
        in_run: Per run, its lines -- clean, non-zero, oldest first.
        holes: The raw days inside the span, ascending.
        unknown_categories: The holes the second kind of raw line opens,
            named (ruling **R-BI37**).
        held_count: Non-zero lines in no run.
        zero_skipped: ``$0.00`` lines, in a run's days or not.
    """

    runs: "list[tuple[date, date]]"
    in_run: "list[list[StatementLine]]"
    holes: "tuple[date, ...]"
    unknown_categories: "tuple[tuple[date, str], ...]"
    held_count: int
    zero_skipped: int


def _partition(lines: "list[_FeedLine]", *, window_start: date, cap: date) -> _Partition:
    """Cut ``[window_start, cap]`` into runs around the raw days.

    A raw line of either kind, ``$0.00`` or not, makes its day raw (rulings
    **R-BI34**, **R-BI36**).  Every line of a run is clean and non-zero by
    construction -- a raw day is in no run, a clean line on a raw day is
    held with it, a ``$0.00`` line is never recorded -- so held is the
    non-zero lines left over.

    Args:
        lines: The answer's lines, oldest first.
        window_start: The first day of the span.
        cap: The last day of the span, inclusive.

    Returns:
        The :class:`_Partition`.
    """
    raw_days = {feed_line.line.posted_on for feed_line in lines if feed_line.raw}
    runs = _runs(window_start, cap, raw_days)
    non_zero = [feed_line for feed_line in lines if feed_line.line.amount != 0]
    in_run = [
        [
            feed_line.line for feed_line in non_zero
            if first <= feed_line.line.posted_on <= last
        ]
        for first, last in runs
    ]
    return _Partition(
        runs=runs,
        in_run=in_run,
        holes=tuple(sorted(day for day in raw_days if window_start <= day <= cap)),
        unknown_categories=tuple(sorted({
            (feed_line.line.posted_on, feed_line.unknown_category)
            for feed_line in lines
            if feed_line.unknown_category is not None
            and window_start <= feed_line.line.posted_on <= cap
        })),
        held_count=len(non_zero) - sum(len(run_lines) for run_lines in in_run),
        zero_skipped=len(lines) - len(non_zero),
    )


def read_account(
    item: object,
    *,
    window_start: date,
    window_end: date,
    today: date,
) -> FeedReading:
    """Return what one Bridge account's answer states for the window asked.

    The module docstring is the rule; this is its order: the answer is read
    and refused of everything it can be (:func:`_read_answer`: not in
    dollars, a field missing or not its shape, a line posted outside the
    window -- the claim below needs every line after the first run's end,
    and an answer that ignores the window asked for cannot be trusted to
    have delivered them); the span is capped at yesterday and cut into runs
    around the raw days (:func:`_partition`); and the PRESENT window's
    FIRST run states the claim.  Whether a window is the present one is
    read off the window -- its end is tomorrow -- rather than told, so the
    two cannot disagree.

    Args:
        item: One member of Bridge's ``accounts`` list -- the mapped one,
            which the sync has already found by its id.
        window_start: The first day the sync asked for (its ``start-date``,
            the display-tz midnight).
        window_end: The day AFTER the last day it asked for (its
            ``end-date``): the window is ``[window_start, window_end)``.
        today: The display-tz day at the sync's boundary.  The span is
            capped the day before it, and a window ending tomorrow is the
            present one.

    Returns:
        The :class:`FeedReading`.

    Raises:
        FeedAnswerUnreadable: The account is not in dollars, a field is
            missing or not its shape, or a line is posted outside the window.
    """
    answer = _read_answer(item, window_start=window_start, window_end=window_end)
    cut = _partition(
        answer.lines,
        window_start=window_start,
        cap=min(window_end - _ONE_DAY, today - _ONE_DAY),
    )
    present = window_end == today + _ONE_DAY
    statements = []
    for index, ((first, last), run_lines) in enumerate(zip(cut.runs, cut.in_run)):
        claims = present and index == 0
        # Every line posted after this run's end, held or in a later run,
        # whether or not anything has recorded it (ruling **R-BI35**).
        after = sum(
            (
                feed_line.line.amount for feed_line in answer.lines
                if feed_line.line.posted_on > last
            ),
            Decimal("0.00"),
        )
        statements.append(ParsedStatement(
            external_account_id=answer.external_account_id,
            lines=run_lines,
            declared_start=first,
            declared_end=last,
            stated_balance=answer.balance - after if claims else None,
            stated_balance_on=last if claims else None,
        ))
    return FeedReading(
        runs=tuple(statements),
        holes=cut.holes,
        unknown_categories=cut.unknown_categories,
        held_count=cut.held_count,
        zero_skipped=cut.zero_skipped,
    )
