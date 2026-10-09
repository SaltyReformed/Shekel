"""Rehearse the account-10 repair through the app's own HTTP doors.

The instrument behind plan step **balance:X-f3c-2b-2c**, whose product is a
REHEARSED runbook rather than a code change: ruling **R-HJ** forbids a
migration writing these money rows, so an owner performs the repair by clicking
through the app, and what a runbook owes before anybody does that on real data
is evidence that the doors it names accept the acts it asks for, in the order
it asks for them.
``docs/audits/balance_architecture/account_10_repair_runbook.md`` is the
runbook; this is what proves it.

**The acts are ruling R-BAL3's** (2026-09-05), which replaced the design this
file performed until plan step X-f3c-2b-2c rewrote it: Checking and account 10
BOTH open their books 2026-03-25 at their banks' own closes, the four bank
lines of 2026-03-26 are RECORDED on 2026-03-26 -- transfer 102 among them, KEPT
and re-dated -- and transfer 1, which points at the archived twin, is the row
dropped.  Two rulings of 2026-10-09 amend how: transfer 1 is SET BACK and
CANCELLED rather than deleted (**R-BAL255**: no page renders a single-transfer
delete), and every Fidelity side is typed its own day from Fidelity's record
(**R-BAL256**).  :func:`_perform` states the order and the two points where the
books boundary forces it.

**No production amount and no payee is written in this file** (ruling
**R-BAL249**, applying **R-BAL132**).  Every figure an act types is DERIVED at
run time: Checking's close from the app's own fold of its imported statement
(:func:`app.services.statement_import.fold_bank_balances`), account 10's from
the Fidelity export, the unrecorded line's amount from its own
``budget.bank_statement_lines`` row.  No act types a transfer's figure; each
is RECONCILED, read off the cash fold's own fact for that side.  The run writes
the typed figures, act by act, to a PERFORMANCE SHEET outside every checkout
(``--sheet``, refused inside one), and that sheet is the page the repair is
performed from.  What this file STATES is
structure: row ids, days, and which row answers which bank line.

**IT WRITES.**  Every other harness in this directory reads.  This one performs
the repair across three accounts, so it refuses any database it was not
explicitly pointed at, refuses the name ``shekel`` outright (what BOTH the
deployed database and the shared dev runtime are called), and refuses any clone
whose pre-repair MARKERS have moved (:func:`_require_unrepaired`).

**It is not the repair and must never become it** (ruling **R-HJ**).  That
ruling rejected "a one-off ``scripts/`` routine driving the services" for the
repair itself: the acts have doors, an owner performs them, and a script that
did it instead would be dead code the moment it ran and would put a second
writer beside every door it drove.

**What is DERIVED and what is STATED, and both are RECONCILED.**  Which app row
answers which bank line is the owner's judgement (rulings **R-HJ** to **R-HM**
and **R-BAL3**), so that map is stated -- as ids and days, nothing else -- and
then checked in BOTH directions before a single write (:func:`_reconcile`):
every stated pairing must hold on the figures, and every bank day and every
settled movement in scope must be answered by exactly one act.  *The first
draft of this file's reconciliation was one-directional, and an adversarial
review broke it with two transfers exchanged that ran to completion with every
arm green; the two-directional census is what closed that, and it is kept.*

**Usage** (from the repository root, against a throwaway clone of production
taken the SAME DAY and at the current alembic head; ``LC_ALL`` is the app's
own requirement)::

    LC_ALL=C.UTF-8 PYTHONPATH=. DATABASE_URL=postgresql://.../shekel_rehearsal \\
        python tests/manual/rehearse_account_10_repair.py \\
        --clone shekel_rehearsal \\
        --bank <the Fidelity history CSV> \\
        --residue <the payroll residue ledger row BAL-467 states> \\
        --sheet <handoff folder>/performance_sheet.md

Then score both accounts against their banks::

    ... python tests/manual/measure_cutover_against_bank.py \\
        --account 10 --format fidelity \\
        --bank <the Fidelity history CSV>
    ... python tests/manual/measure_cutover_against_bank.py \\
        --account 1 --bank ~/Downloads/checking/2026_ytd_daily_balances.csv
"""

import argparse
import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

from app import create_app, ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.extensions import db, login_manager
from app.models.account import Account
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import cash_ledger, statement_import
from app.services.balance_at import BalanceContext, balance_at, cash_balance_at
from app.services.row_valuation import settled_figure
from app.services.settle_day import SettleDay, is_evidence, recorded_settle_day
from app.utils.archive_helpers import category_has_usage
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER

_ZERO_MONEY = Decimal("0.00")
_CENT = Decimal("0.01")

#: The day BOTH accounts' books open (ruling **R-BAL3**).  A DECISION -- no
#: bank record says which day the books should start on -- so it is stated
#: here, and each account's opening EQUITY is derived from it: its bank's own
#: close for this day.
BOOKS_OPEN = date(2026, 3, 25)

#: The first day the restated books can hold a movement (ruling **R-HG**: an
#: opening is the CLOSE of its own day), and the day both banks posted the four
#: lines this repair records on Checking.
BOUNDARY_DAY = date(2026, 3, 26)

#: The three accounts the repair touches.  The twin is the archived
#: predecessor of account 10 (ruling **R-HK**: one real Fidelity account).
SUBJECT_ACCOUNT = 10
CHECKING_ACCOUNT = 1
TWIN_ACCOUNT = 2

#: The day the twin's own balance is superseded to zero (ruling **R-HK**).
TWIN_ASSERTED_ON = date(2026, 4, 6)

#: Ruling **R-BAL3**: ONE real ACH left Checking and reached Fidelity on
#: BOUNDARY_DAY, and the app records it twice.  Transfer 102 (Checking ->
#: account 10) is KEPT and re-dated onto that day, inverting finding **N-382**;
#: transfer 1 (Checking -> the archived twin) is DROPPED -- set back to
#: Projected and CANCELLED (ruling **R-BAL255**): no page renders the
#: single-transfer delete, and Cancelled says what is true, that the planned
#: transfer to the old account did not happen.  Not being a soft delete, it
#: does not incur finding **N-386**'s restore exposure.
KEPT_TRANSFER = 102
DROPPED_TRANSFER = 1


@dataclass(frozen=True)
class _BoundaryLine:
    """One of Checking's bank lines on BOUNDARY_DAY, and what answers it.

    Attributes:
        line_id: The ``budget.bank_statement_lines`` row.
        rows: Checking transactions re-dated onto the line's day to answer it.
        transfer_id: A transfer whose CHECKING side answers it instead.
        residue: Whether the answer is KNOWN not to sum to the line.  Only
            finding **BAL-467**'s payroll line: two rows answer one line and
            fall short of it, and which row is short is the owner's
            knowledge, so the residue is measured and stated rather than
            split.
    """

    line_id: int
    rows: "tuple[int, ...]" = ()
    transfer_id: "int | None" = None
    residue: bool = False


#: Checking's four bank lines on BOUNDARY_DAY and what answers each (ruling
#: **R-BAL3**).  The line answered by NOTHING is :data:`UNRECORDED_LINE`.
BOUNDARY_LINES = (
    _BoundaryLine(131, rows=(781, 865), residue=True),
    _BoundaryLine(132, rows=(1069,)),
    _BoundaryLine(134, transfer_id=KEPT_TRANSFER),
)

#: The one line whose answer is KNOWN to fall short of it (BAL-467).
_RESIDUE_LINE = next(entry for entry in BOUNDARY_LINES if entry.residue)

#: The line the app records nowhere (finding **BAL-468**), recorded by act 6
#: as a new Checking expense in the category the developer ruled (2026-09-06,
#: ``Family: Birthday``, id 26), under the name he gave it.  Its AMOUNT is the
#: line's own, read from the row at run time.
UNRECORDED_LINE = 133
UNRECORDED_CATEGORY = 26
UNRECORDED_NAME = "Josh's Birthday"

#: Which transfer answers which day of the Fidelity export, on account 10's
#: side -- the day typed into that side's own box (ruling **R-BAL256**).  Days
#: only (ruling **R-BAL249**): each side's figure is the cash fold's own fact,
#: reconciled against what the export moved that day.
FIDELITY_DAYS = {
    KEPT_TRANSFER: BOUNDARY_DAY,
    155: date(2026, 4, 9),
    156: date(2026, 4, 23),
    154: date(2026, 4, 29),
    157: date(2026, 5, 7),
    346: date(2026, 5, 14),
    409: date(2026, 7, 23),
}

#: The category the recorded dividends book to (ruling **R-HL**), and the
#: category that BECOMES it: ruling **R-BAL250** has act 9 rename the owner's
#: unused ``Financial: Dividend`` (id 33) rather than create a second one.
DIVIDEND_CATEGORY = ("Income", "Interest & Dividends")
DIVIDEND_CATEGORY_ID = 33
DIVIDEND_CATEGORY_WAS = ("Financial", "Dividend")

#: The SETTLED status each transaction type takes.  Income settles as
#: Received and an expense as Paid; submitting the other one is refused by the
#: status seam, which is how the first draft found out.  Members rather than
#: ids: the ids are the reference cache's (:func:`_settled_status_id`).
_SETTLED_STATUS = {
    TxnTypeEnum.INCOME: StatusEnum.RECEIVED,
    TxnTypeEnum.EXPENSE: StatusEnum.DONE,
}

#: The Fidelity history columns this file reads, by their own header text.
_BANK_DAY = "Run Date"
_BANK_BALANCE = "Cash Balance ($)"
_BANK_ACTION = "Action"
_BANK_AMOUNT = "Amount ($)"

#: What a received dividend's ``Action`` says.  The REINVESTMENT line beside it
#: is the same money buying the core position back and is not a second event.
_DIVIDEND_RX = re.compile(r"\bDIVIDEND RECEIVED\b", re.IGNORECASE)

#: The id of a transaction cell the create door just rendered.  Read from the
#: response rather than by re-querying for the newest row in that account and
#: category: that query answers whichever row has the highest id, which is not
#: necessarily the one this request made.
_NEW_CELL_RX = re.compile(r'id="txn-cell-(\d+)"')

#: The flash categories that mean a door REFUSED.  Read after every submission,
#: because two of this repair's doors answer a refusal with ``302`` and a flash
#: rather than with a designed fragment and a 4xx -- ``accounts.restate_opening``
#: is one, and it is the door this whole step exists for.
_REFUSAL_FLASHES = frozenset({"danger", "error", "warning"})


@dataclass(frozen=True)
class _Export:
    """The bank's own record: what it closed at, and what it says moved.

    Attributes:
        closings: ``{day: closing balance}``, one per day the file names.
        dividends: ``{day: amount}`` -- the SUM of that day's
            ``DIVIDEND RECEIVED`` lines, because a day may carry more than one
            and overwriting would drop money.
        named: The days, ascending.  Materialised once: :meth:`moved_on` is
            called per movement and per bank day, and re-sorting the dict on
            every call is this project's DRY violation rather than a cost.
    """

    closings: "dict[date, Decimal]"
    dividends: "dict[date, Decimal]"
    named: "list[date]" = field(default_factory=list)

    @classmethod
    def read(cls, path: str) -> "_Export":
        """Parse a Fidelity transaction-history export.

        The file is BOM'd, carries blank preamble above its header and a
        disclaimer below its rows, and states a running ``Cash Balance ($)``
        per LINE rather than per day.  The header is found by its own column
        names and the columns read by name, so a column added upstream cannot
        silently shift the figures.

        Args:
            path: The CSV file.

        Returns:
            The :class:`_Export`.

        Raises:
            SystemExit: When the file names no day, or its rows are not in date
                order in either direction.
        """
        rows: "list[tuple[date, Decimal, str, str]]" = []
        columns: "dict[str, int] | None" = None
        wanted = (_BANK_DAY, _BANK_BALANCE, _BANK_ACTION, _BANK_AMOUNT)
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for row in csv.reader(handle):
                cells = [cell.strip() for cell in row]
                if columns is None:
                    if all(name in cells for name in wanted):
                        columns = {name: cells.index(name) for name in wanted}
                    continue
                if len(cells) <= max(columns.values()):
                    continue
                day = _parse_day(cells[columns[_BANK_DAY]])
                if day is None or not cells[columns[_BANK_BALANCE]]:
                    continue
                rows.append((
                    day,
                    Decimal(cells[columns[_BANK_BALANCE]]).quantize(_CENT),
                    cells[columns[_BANK_ACTION]],
                    cells[columns[_BANK_AMOUNT]],
                ))
        if not rows:
            raise SystemExit(f"{path} states no daily balance")
        return cls._assemble(path, rows)

    @classmethod
    def _assemble(cls, path: str, rows) -> "_Export":
        """Fold parsed rows into closings and dividends.

        **A day's CLOSING is its chronologically LAST line, and the file's own
        ordering decides which that is.**  An earlier draft required every line
        on a day to state the same balance, which is true of this export only
        because its multi-line days are dividend/reinvestment pairs that net to
        zero on the reported column; an ordinary day with two transfers states
        one closing and one intra-day balance, and that draft aborted on it.
        The direction is MEASURED from the date sequence rather than assumed,
        and a file sorted neither way is refused -- because then no rule picks
        the closing and guessing would be worse than stopping.

        Args:
            path: The file, named in any refusal.
            rows: ``(day, closing, action, amount)`` in FILE order.

        Returns:
            The :class:`_Export`.

        Raises:
            SystemExit: When the rows are not in date order either way.
        """
        days = [row[0] for row in rows]
        ascending = all(a <= b for a, b in zip(days, days[1:]))
        descending = all(a >= b for a, b in zip(days, days[1:]))
        if not ascending and not descending:
            raise SystemExit(
                f"{path} is not in date order in either direction, so no rule "
                "picks a day's CLOSING line out of its intra-day ones"
            )
        chronological = rows if ascending else list(reversed(rows))
        closings: "dict[date, Decimal]" = {}
        dividends: "dict[date, Decimal]" = {}
        for day, closing, action, amount in chronological:
            # Last write wins, and after the reversal above the last write for
            # a day is that day's final line -- its closing balance.
            closings[day] = closing
            if _DIVIDEND_RX.search(action) and amount:
                dividends[day] = dividends.get(day, _ZERO_MONEY) + Decimal(
                    amount
                ).quantize(_CENT)
        return cls(
            closings=closings, dividends=dividends, named=sorted(closings),
        )

    def moved_on(self, day: date) -> Decimal:
        """Return what the bank says moved on *day*, dividends excluded.

        Args:
            day: A day the file names.

        Returns:
            The day's closing less the previous named day's, less any dividend
            the same day credited -- so what is left is the transfer the app
            has a row for.
        """
        prior = [named for named in self.named if named < day]
        opening = self.closings[prior[-1]] if prior else _ZERO_MONEY
        return (self.closings[day] - opening
                - self.dividends.get(day, _ZERO_MONEY))

    def closing_on(self, day: date) -> Decimal:
        """Return the bank's closing balance FOR *day*, carried forward.

        A quiet day is absent from the file, so its closing is the last named
        day's.  BOOKS_OPEN is such a day on this export: nothing moved between
        the line before it and the next one, so the opening equity is that
        earlier line's balance carried forward (ruling **R-BAL3**).

        Args:
            day: The civil day to answer.

        Returns:
            The closing balance.

        Raises:
            SystemExit: When *day* precedes every day the file names.
        """
        prior = [named for named in self.named if named <= day]
        if not prior:
            raise SystemExit(f"the export names no day on or before {day}")
        return self.closings[prior[-1]]

    def recorded_dividends(self) -> "list[tuple[date, Decimal]]":
        """Return the dividends this repair records, ascending.

        Returns:
            One ``(day, amount)`` per ``DIVIDEND RECEIVED`` day dated strictly
            after the books open.  The ones on or before it are inside the
            opening equity (ruling **R-HG**) and are not recorded.
        """
        return [
            (day, self.dividends[day])
            for day in sorted(self.dividends) if day > BOOKS_OPEN
        ]


def _parse_day(raw: str) -> "date | None":
    """Return *raw* as a civil day, or ``None`` when it is not one.

    Args:
        raw: A cell from the export's date column.

    Returns:
        The date, or ``None`` for preamble and disclaimer rows.
    """
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError:
        return None


class _Forms(HTMLParser):
    """Collect every form a response renders: method, url and its own controls.

    **The payload comes from the page, not from this file.**  An HTML form
    submits every control it renders, so a rehearsal that posts a chosen subset
    is not rehearsing the owner's click -- a hand-picked payload once shipped a
    route arm that was dead in a browser.  Each act that submits a FORM fetches
    the form the owner opens, changes only what they type, and submits the rest
    exactly as rendered, including the version pin, the hidden ids and the
    selects' own current values.  The three presses that are buttons rather
    than forms -- Cancel transfer, Unarchive and Archive -- post what their
    buttons carry (Cancel the leg's id, the other two nothing but the CSRF
    token this harness disables), and Cancel only after its button is found
    on the reopened card.

    **Seven divergences from a browser have been measured and fixed** over
    two review rounds, each of which had this parser posting something no
    browser would.  **Three of the seven cannot be reached on the forms this
    file drives** -- the ``<textarea>`` and the two ``<select multiple>``
    cases: none of the seven templates it drives renders either (re-censused
    2026-10-09, when the acts were rewritten for ruling R-BAL3).  They are
    fixed anyway because
    the parser is the thing that makes "the payload comes from the page" true,
    and a parser correct only on today's pages is a claim about the pages
    rather than about the parser.

    * a CHECKED checkbox followed by a hidden partner of the same name -- the
      shape ``grid/_transaction_full_edit.html`` uses for its flags -- came out
      ``false``, because the controls were folded into a dict and the LAST
      value won.  Werkzeug's ``MultiDict`` gives ``request.form[key]`` the
      FIRST, so :func:`_payload` keeps the first and the flag survives;
    * a ``<textarea>`` posted empty whatever it held, because its content was
      never read;
    * a ``<select multiple>`` posted only its last selected option;
    * a ``<select>`` with no ``selected`` fell back to its first option even
      when that option was ``disabled``, which the reset algorithm skips;
    * a ``<select multiple>`` with NOTHING selected posted its first option,
      where a browser posts no value at all;
    * a ``<option selected disabled>`` -- the placeholder idiom
      ``analytics/_income_statement.html`` uses -- was dropped and the next
      option posted in its place, though the markup's own selectedness makes it
      the submitted one;
    * a checked checkbox with no ``value`` posted the empty string rather than
      ``on``.

    Disabled controls are dropped because a browser drops them, which is what
    makes a finalised transfer's locked amount absent from the submission
    rather than re-posted.
    """

    _NOT_A_VALUE = {"submit", "button", "image", "reset"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: "list[dict]" = []
        self._form: "dict | None" = None
        self._select: "dict | None" = None
        self._textarea: "str | None" = None
        self._text: "list[str]" = []

    def handle_starttag(self, tag, attrs):
        """Open a form, or record one control's submitted value."""
        attr = dict(attrs)
        if tag == "form":
            self._open_form(attr)
        elif self._form is None:
            return
        elif tag == "input":
            self._input(attr)
        elif tag == "select":
            self._select = (
                None if "disabled" in attr or not attr.get("name")
                else {
                    "name": attr["name"], "multiple": "multiple" in attr,
                    "selected": [], "first": None,
                }
            )
        elif tag == "option" and self._select is not None:
            self._option(attr)
        elif tag == "textarea" and attr.get("name") and "disabled" not in attr:
            self._textarea = attr["name"]
            self._text = []

    def _open_form(self, attr):
        """Start a form, taking its method and action from HTMX or plain HTML.

        Args:
            attr: The tag's attributes.
        """
        self._form = {
            "method": "post", "url": attr.get("action", ""),
            "controls": [], "multiple": set(),
        }
        for verb in ("patch", "post", "put", "delete", "get"):
            if f"hx-{verb}" in attr:
                self._form["method"] = verb
                self._form["url"] = attr[f"hx-{verb}"]
                break
        self.forms.append(self._form)

    def _input(self, attr):
        """Record one ``<input>``'s submitted value, or drop it.

        Args:
            attr: The tag's attributes.
        """
        if "disabled" in attr or attr.get("type") in self._NOT_A_VALUE:
            return
        if not attr.get("name"):
            return
        ticked = attr.get("type") in ("checkbox", "radio")
        if ticked and "checked" not in attr:
            return
        # A ticked control with no ``value`` submits the string ``on``; every
        # other control with no value submits the empty string.
        default = "on" if ticked else ""
        self._form["controls"].append((attr["name"], attr.get("value", default)))

    def _option(self, attr):
        """Record one ``<option>``'s value against the open select.

        Args:
            attr: The tag's attributes.
        """
        value = attr.get("value", "")
        # **A DISABLED option is still submitted when the markup marks it
        # selected**, and only the no-selection FALLBACK skips it: the reset
        # algorithm gives selectedness to the first option that is not
        # disabled, but an option that already carries ``selected`` keeps it.
        # ``analytics/_income_statement.html`` uses exactly that placeholder
        # idiom, and an earlier version dropped such an option and posted the
        # next one instead (adversarial review, 2026-09-01).
        if "disabled" not in attr and self._select["first"] is None:
            self._select["first"] = value
        if "selected" in attr:
            self._select["selected"].append(value)

    def handle_data(self, data):
        """Accumulate an open textarea's content."""
        if self._textarea is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        """Close a form, a select or a textarea, committing its value."""
        if tag == "form":
            self._form = None
        elif tag == "select" and self._select is not None:
            self._commit_select()
        elif tag == "textarea" and self._textarea is not None:
            if self._form is not None:
                self._form["controls"].append(
                    (self._textarea, "".join(self._text)),
                )
            self._textarea = None

    def _commit_select(self):
        """Append the open select's submitted value or values."""
        if self._form is not None:
            chosen = self._select["selected"]
            if self._select["multiple"]:
                # A MULTI-select with nothing selected submits NOTHING.  The
                # first-option fallback belongs to a single select only, and
                # applying it here posted a value no browser would.
                self._form["multiple"].add(self._select["name"])
            else:
                if not chosen and self._select["first"] is not None:
                    chosen = [self._select["first"]]
                chosen = chosen[:1]
            for value in chosen:
                self._form["controls"].append((self._select["name"], value))
        self._select = None


def _payload(form: dict) -> dict:
    """Return *form*'s controls as the payload a browser would submit.

    **The FIRST value of a repeated name wins**, which is what werkzeug's
    ``MultiDict`` gives ``request.form[key]`` and therefore what every schema
    in this app reads.  A genuinely multi-valued control -- a
    ``<select multiple>`` -- keeps every value as a list, which the test client
    posts as repeated fields.

    Args:
        form: One parsed form.

    Returns:
        ``{name: value}``, with a list for each multi-select.
    """
    payload: dict = {}
    for name, value in form["controls"]:
        if name in form["multiple"]:
            payload.setdefault(name, []).append(value)
        elif name not in payload:
            payload[name] = value
    return payload


def _forms_in(html: str) -> "list[dict]":
    """Return every form in *html*.

    Args:
        html: A rendered page or HTMX fragment.

    Returns:
        One form dict per form, in document order.
    """
    parser = _Forms()
    parser.feed(html)
    return parser.forms


class RefusedError(AssertionError):
    """A door refused a submission this rehearsal expected it to accept."""


class _Operator:
    """One owner's browser session against one clone."""

    def __init__(self, app, sheet: "_Sheet"):
        """Forge the owner's session and open a client.

        Args:
            app: The Flask application, already configured for a rehearsal.
            sheet: The performance sheet every act records into.
        """
        self.sheet = sheet
        self.client = app.test_client()
        self.user_id = db.session.get(Account, SUBJECT_ACCOUNT).user_id
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
            session["_id"] = None
        self.acts = 0

    def _drain_flashes(self) -> "list[tuple[str, str]]":
        """Return and clear whatever the last request flashed.

        Returns:
            ``[(category, message), ...]``, empty when nothing flashed.
        """
        with self.client.session_transaction() as session:
            return list(session.pop("_flashes", []))

    def form(self, url: str) -> dict:
        """Open *url* and return its first form.

        Args:
            url: The page or fragment the owner opens.

        Returns:
            The form dict.

        Raises:
            AssertionError: When the page does not answer, or renders no form.
        """
        response = self.client.get(url, headers={"HX-Request": "true"})
        assert response.status_code == 200, \
            f"GET {url} -> {response.status_code}"
        forms = _forms_in(response.get_data(as_text=True))
        assert forms, f"GET {url} rendered no form"
        return forms[0]

    def form_posting_to(self, page: str, action: str) -> dict:
        """Return the form on *page* whose action ends with *action*.

        Args:
            page: The full page the owner opens.
            action: The tail of the door's URL.

        Returns:
            The form dict.

        Raises:
            AssertionError: When the page does not answer, or carries no such
                form -- which is the card being unreachable, not a payload
                problem.
        """
        response = self.client.get(page)
        assert response.status_code == 200, \
            f"GET {page} -> {response.status_code}"
        for form in _forms_in(response.get_data(as_text=True)):
            if form["url"].endswith(action):
                return form
        raise AssertionError(f"{page} renders no form posting to {action}")

    def submit(self, form: dict, typed: "dict | None" = None):
        """Submit *form* with the owner's edits applied.

        Args:
            form: A form from :meth:`form` or :meth:`form_posting_to`.
            typed: What the owner changed, or supplied where a control is
                driven by script rather than rendered carrying its value.

        Returns:
            The response.
        """
        payload = _payload(form)
        payload.update(typed or {})
        return self.send(form["method"], form["url"], payload)

    def send(self, method: str, url: str, payload: dict):
        """Submit *payload* and assert the door accepted it.

        **A 302 with a danger flash is a REFUSAL, and reading only the status
        missed it.**  ``accounts.restate_opening`` -- the door this whole step
        exists for -- answers an illegal day by flashing and redirecting, so a
        status check alone reported success on a write that never happened
        (adversarial review, 2026-09-01).  The flash is drained after every
        submission and any refusal category raises, which also surfaces the
        refusal SENTENCE -- which is what a rehearsal is for.

        Args:
            method: The form's own method.
            url: The form's own action.
            payload: What the browser would submit.

        Returns:
            The response.

        Raises:
            RefusedError: On a refusing status, or on a refusal flash.
        """
        self.acts += 1
        response = getattr(self.client, method)(
            url, data=payload, headers={"HX-Request": "true"},
        )
        refused = [
            message for category, message in self._drain_flashes()
            if category in _REFUSAL_FLASHES
        ]
        # **A designed error fragment can carry a 2xx, and the status list
        # below would accept it.**  Every refusal this repair's doors produce
        # today answers 4xx or flashes, audited 2026-09-01 -- but that is a
        # convention, and the app stamps a header saying so precisely because
        # the client cannot tell a handled error from a crash page otherwise.
        # Asking the header is one line and does not rest on the audit staying
        # true.
        if DESIGNED_FRAGMENT_HEADER in response.headers:
            refused.append(
                f"a designed error fragment ({DESIGNED_FRAGMENT_HEADER})"
            )
        if response.status_code not in (200, 201, 302):
            raise RefusedError(
                f"{method.upper()} {url} -> {response.status_code}\n"
                f"payload={payload}\n"
                f"{response.get_data(as_text=True)[:800]}"
            )
        if refused:
            raise RefusedError(
                f"{method.upper()} {url} -> {response.status_code} but the "
                f"app refused it: {' | '.join(refused)}"
            )
        return response


class _Sheet:
    """The PERFORMANCE SHEET: every act in order, what it types, what follows.

    **The page the repair is performed from, and the only place its figures
    are written** (ruling **R-BAL249**): the committed runbook names each
    amount by its role, and this run derives the amount and writes it here,
    beside the state the app reaches after the act.  Every line is also
    printed, so the console and the sheet cannot disagree.  It is saved even
    when the run stops early, so a refusal is recorded beside the acts that
    landed before it.
    """

    def __init__(self, path: Path):
        """Hold the sheet's destination.

        Args:
            path: Where to write it; outside every checkout (:func:`_sheet_path`).
        """
        self.path = path
        self.lines: "list[str]" = []

    def say(self, line: str = "") -> None:
        """Print *line* and keep it for the sheet.

        Args:
            line: One line of the record.
        """
        print(line)
        self.lines.append(line)

    def act(self, number: int, title: str, door: str) -> None:
        """Open one act's section.

        Args:
            number: The act's place in the order.
            title: What it does, in the runbook's words.
            door: Where the operator performs it.
        """
        self.say("")
        self.say(f"## Act {number}: {title}")
        self.say(f"Where: {door}")

    def typed(self, what: str, value: object) -> None:
        """Record one value the operator types.

        Args:
            what: The box, as the screen labels it.
            value: What goes in it.
        """
        self.say(f"- TYPE {what}: **{value}**")

    def save(self) -> None:
        """Write the sheet."""
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def _sheet_path(raw: str) -> Path:
    """Return where the performance sheet goes, refusing any git checkout.

    **Any checkout, not only this one**: the harness is run from worktrees, and
    a sheet written into the main checkout or a sibling worktree is as much one
    ``git add`` from a commit as one written beside this file.  The folder is
    also required to EXIST now, because the sheet is written last and a bad
    path found then would lose the record of every act already performed.

    Args:
        raw: The ``--sheet`` argument.

    Returns:
        The resolved path.

    Raises:
        SystemExit: When the path is inside a git checkout, where the real
            figures the sheet carries do not belong (ruling **R-BAL249**), or
            its folder does not exist.
    """
    path = Path(raw).expanduser().resolve()
    checkouts = [
        folder for folder in (path, *path.parents) if (folder / ".git").exists()
    ]
    if checkouts:
        raise SystemExit(
            f"--sheet {path} is inside the git checkout {checkouts[0]}; the "
            "sheet carries real figures and belongs in the handoff folder "
            "(ruling R-BAL249)"
        )
    if path.is_dir() or not path.parent.is_dir():
        raise SystemExit(
            f"--sheet {path} is a folder, or its folder does not exist; it "
            "names the sheet FILE"
        )
    return path


def _require_unrepaired() -> None:
    """Refuse a clone the repair has already been performed on.

    **What these catch is a PARTIALLY repaired clone**, because nothing in the
    acts is idempotent and refusing at act zero is the only safe answer: act 1
    trips the transfer marker (and every act after it runs after act 1), acts
    3 and 4 trip the opening markers, and act 9 the category markers.  These
    are MARKERS, not a proof of the whole pre-repair state -- act 0 trips none
    of them and is not idempotent.  It doubles as the
    safety rail a name check cannot be: a database this repair has touched is
    refused whatever it is called.

    Raises:
        SystemExit: When the clone is not in the pre-repair state, naming every
            precondition that failed rather than the first.
    """
    problems = []
    for account_id in (CHECKING_ACCOUNT, SUBJECT_ACCOUNT):
        opening = cash_ledger.governing_account_opening(account_id)
        if opening.opened_on == BOOKS_OPEN:
            problems.append(
                f"account {account_id}'s books already open {BOOKS_OPEN}"
            )
    for transfer_id in (DROPPED_TRANSFER, KEPT_TRANSFER):
        transfer = db.session.get(Transfer, transfer_id)
        if transfer is None or transfer.is_deleted:
            problems.append(f"transfer {transfer_id} is missing or deleted")
        elif not transfer.status.is_settled:
            problems.append(
                f"transfer {transfer_id} is not settled (already set back or "
                "cancelled?)"
            )
    user_id = db.session.get(Account, SUBJECT_ACCOUNT).user_id
    existing = db.session.execute(
        db.select(Category.id).filter_by(
            user_id=user_id, group_name=DIVIDEND_CATEGORY[0],
            item_name=DIVIDEND_CATEGORY[1],
        )
    ).scalar_one_or_none()
    if existing is not None:
        problems.append(
            f"category {DIVIDEND_CATEGORY[0]}: {DIVIDEND_CATEGORY[1]} "
            f"already exists (id {existing})"
        )
    # The category act 9 renames must be the owner's, still be the one ruling
    # R-BAL250 was asked about, and hold NOTHING -- asked of the app's own
    # in-use rule, because a rename relabels every template, transfer,
    # transaction and merchant rule filed under it.
    renamed = db.session.get(Category, DIVIDEND_CATEGORY_ID)
    if (
        renamed is None or renamed.user_id != user_id
        or (renamed.group_name, renamed.item_name) != DIVIDEND_CATEGORY_WAS
        or category_has_usage(DIVIDEND_CATEGORY_ID, user_id)
    ):
        problems.append(
            f"category {DIVIDEND_CATEGORY_ID} is not the owner's empty "
            f"{DIVIDEND_CATEGORY_WAS[0]}: {DIVIDEND_CATEGORY_WAS[1]}"
        )
    if problems:
        raise SystemExit(
            "this clone is not in the pre-repair state:\n  "
            + "\n  ".join(problems)
            + "\nRestore it from an unrepaired snapshot and run again."
        )
    print("precondition: the clone is unrepaired")


def _facts(account_id: int) -> list:
    """Return the cash fold's own settled movements for one account.

    **The figures this repair reconciles and verifies are the FOLD'S**, read
    off :func:`app.services.cash_ledger.walk_cash_ledger` -- the walk every
    balance is folded from (ruling **R-BAL80**: the fold reads movements and no
    row) -- rather than off a transfer's stored amount or a row's columns,
    either of which is a second statement of what moved.

    Args:
        account_id: The account.

    Returns:
        Its :class:`~app.services.cash_ledger.CashSourceFact` values.
    """
    db.session.expire_all()
    owner = db.session.get(Account, account_id).user_id
    scenario = db.session.execute(
        db.select(Scenario.id).filter_by(user_id=owner, is_baseline=True)
    ).scalar_one()
    return list(
        cash_ledger.walk_cash_ledger(account_id, scenario).source_facts
    )


class _Moved(NamedTuple):
    """What one row or one transfer side moved, summed from its facts.

    Attributes:
        delta: The signed total.
        day: The day of its LAST movement -- meaningful only when
            :attr:`movements` is one, which every row in this repair's scope is
            asserted to be (:func:`_checking_problems`).
        movements: How many movements it holds.
    """

    delta: Decimal
    day: date
    movements: int


def _moved_by(facts: list) -> "tuple[dict, dict]":
    """Return what each transaction and each transfer moved, from *facts*.

    Args:
        facts: One account's :func:`_facts`.

    Returns:
        ``({transaction id: _Moved}, {transfer id: _Moved})``.
    """
    rows: dict = {}
    transfers: dict = {}
    for fact in facts:
        for key, into in ((fact.transaction_id, rows), (fact.transfer_id, transfers)):
            if key is None:
                continue
            was = into.get(key, _Moved(_ZERO_MONEY, fact.settled_on, 0))
            into[key] = _Moved(
                was.delta + fact.delta, fact.settled_on, was.movements + 1,
            )
    return rows, transfers


def _side_day(transfer_id: int, account_id: int) -> SettleDay | None:
    """Return the day one transfer side records, and how it is known.

    Read off the side's own movement (:func:`app.services.settle_day
    .recorded_settle_day`), which is what the popover's box for that side
    prefills from and what decides whether it reads as "a guess".

    Args:
        transfer_id: The transfer.
        account_id: Which side.

    Returns:
        The side's :class:`~app.services.settle_day.SettleDay`, or ``None``
        when the fold counts no movement for it.
    """
    entries = [
        fact.entry_id for fact in _facts(account_id)
        if fact.transfer_id == transfer_id
    ]
    if len(entries) != 1:
        return None
    return recorded_settle_day(db.session.get(TransactionEntry, entries[0]))


def _movement_key(fact) -> "tuple[str, int]":
    """Return what one movement IS, for a census: a transfer side or a row.

    A transfer side is named by its TRANSFER, not by the row that carries it
    today: a leg's ``transaction_id`` is documented to go NULL once transfer
    legs lose their shadow rows (plan step ``balance:X-bi-6-4d``), and a NULL
    would collapse every leg into one member of the set.

    Args:
        fact: A :class:`~app.services.cash_ledger.CashSourceFact`.

    Returns:
        ``("transfer", transfer id)`` or ``("row", transaction id)``.
    """
    if fact.transfer_id is not None:
        return ("transfer", fact.transfer_id)
    return ("row", fact.transaction_id)


def _next_bank_day() -> date:
    """Return the first day after BOUNDARY_DAY on which SECU posted a line.

    Checking's rows the repair moves sit between BOUNDARY_DAY and this day
    today (the app dated them a day late), and nothing else may, so it bounds
    the census in :func:`_checking_problems` and the verification in
    :func:`_verify_checking_boundary`.

    Returns:
        The day.

    Raises:
        SystemExit: When no line follows BOUNDARY_DAY.
    """
    day = db.session.execute(
        db.select(db.func.min(BankStatementLine.posted_on)).filter(
            BankStatementLine.account_id == CHECKING_ACCOUNT,
            BankStatementLine.posted_on > BOUNDARY_DAY,
        )
    ).scalar_one()
    if day is None:
        raise SystemExit(f"Checking has no bank line after {BOUNDARY_DAY}")
    return day


def _subject_problems(export: _Export) -> "list[str]":
    """Return every disagreement between the Fidelity map and the records.

    Four arms.  **Each day**: what the export moved on every day it names
    after the books open equals what the mapped transfers' account-10 sides
    move on it -- zero on a day only a dividend answers, so a transfer line on
    a dividend day is caught.  **Each transfer**: it has a settled movement on
    account 10, is mapped to a day the export names, and no two claim one day.
    **Each Checking side**: every mapped transfer but the kept one carries its
    Checking side on the day SECU's import OBSERVED, and that is the day the
    map gives Fidelity's -- these ACHs post at both banks the same day
    (ruling R-BAL3 for the two in March; the other five measured so on
    2026-10-09).  It is the arm that separates transfers of EQUAL amounts.  **Each movement**: every one accounts 2 and 10 hold, on
    or before the export's last day, is a mapped transfer's or the dropped
    one's.

    Args:
        export: The parsed export.

    Returns:
        One sentence per disagreement, empty when the map reconciles.
    """
    problems: "list[str]" = []
    _, moved = _moved_by(_facts(SUBJECT_ACCOUNT))
    claimed: "dict[date, int]" = {}
    for transfer_id, day in FIDELITY_DAYS.items():
        if transfer_id not in moved:
            problems.append(
                f"transfer {transfer_id} has no settled movement on account "
                f"{SUBJECT_ACCOUNT}"
            )
        if day not in export.closings:
            problems.append(
                f"transfer {transfer_id} is mapped to {day}, a day the export "
                "does not name"
            )
        if day in claimed:
            problems.append(
                f"transfers {claimed[day]} and {transfer_id} both claim {day}"
            )
        claimed[day] = transfer_id
        if transfer_id == KEPT_TRANSFER:
            continue  # nothing observed its Checking side; act 5 types it
        side = _side_day(transfer_id, CHECKING_ACCOUNT)
        if (
            side is None or side.day != day
            or side.basis is not SettledDayBasisEnum.OBSERVED
        ):
            problems.append(
                f"transfer {transfer_id} is mapped to Fidelity's {day}, and "
                f"its Checking side records {side}"
            )

    dividends = {day for day, _ in export.recorded_dividends()}
    for day in export.named:
        if day <= BOOKS_OPEN:
            continue
        mapped = sum(
            (moved[t].delta for t, d in FIDELITY_DAYS.items()
             if d == day and t in moved),
            _ZERO_MONEY,
        )
        if export.moved_on(day) != mapped:
            problems.append(
                f"the export moved {export.moved_on(day)} on {day} and the "
                f"transfers mapped there move {mapped}"
            )
        if day not in claimed and day not in dividends:
            problems.append(f"the export names {day} and no act answers it")

    answered = set(FIDELITY_DAYS) | {DROPPED_TRANSFER}
    for account_id in (TWIN_ACCOUNT, SUBJECT_ACCOUNT):
        problems.extend(
            f"account {account_id} carries a settled movement (transaction "
            f"{fact.transaction_id}, {fact.delta} on {fact.settled_on}) that "
            "no act answers"
            for fact in _facts(account_id)
            if fact.settled_on <= export.named[-1]
            and fact.transfer_id not in answered
        )
    return problems


def _line_problems(
    posted: dict, rows: dict, transfers: dict,
) -> "tuple[list[str], Decimal]":
    """Grade each boundary line against what answers it.

    Args:
        posted: ``{line id: BankStatementLine}`` on BOUNDARY_DAY.
        rows: Checking's ``{transaction id: _Moved}``.
        transfers: Checking's ``{transfer id: _Moved}``.

    Returns:
        ``(problems, residue)``: one sentence per disagreement, and what the
        residue line's rows move less what the bank posted, books minus bank.
    """
    problems: "list[str]" = []
    residue = _ZERO_MONEY
    for entry in BOUNDARY_LINES:
        line = posted.get(entry.line_id)
        if line is None:
            continue
        if entry.transfer_id is not None:
            found = transfers.get(entry.transfer_id)
            answered = None if found is None else found.delta
            dropped = transfers.get(DROPPED_TRANSFER)
            if dropped is None or dropped.delta != line.amount:
                problems.append(
                    f"transfer {DROPPED_TRANSFER}, the second record of the "
                    f"ACH bank line {entry.line_id} posted, moves "
                    f"{None if dropped is None else dropped.delta} on Checking"
                )
        elif all(row in rows for row in entry.rows):
            answered = sum((rows[row].delta for row in entry.rows), _ZERO_MONEY)
        else:
            answered = None
        if answered is None:
            problems.append(
                f"bank line {entry.line_id} is answered by a row or side with "
                "no settled movement on Checking"
            )
        elif entry.residue:
            residue = answered - line.amount
        elif answered != line.amount:
            problems.append(
                f"bank line {entry.line_id} moved {line.amount} and what "
                f"answers it moves {answered}"
            )
    return problems, residue


def _checking_problems() -> "tuple[list[str], Decimal]":
    """Return every disagreement on Checking's boundary, and BAL-467's residue.

    Graded against the app's OWN record of SECU's lines,
    ``budget.bank_statement_lines``:

    * the lines SECU posted on BOUNDARY_DAY are exactly the ones the map
      answers, and none is matched already (this repair answers them by hand);
    * **the census**: Checking's movements dated before the next day SECU
      posted anything are exactly the mapped rows and the dropped transfer's
      side, each row holding one movement.  This is what makes the residue
      BAL-467's and nothing else: with the rows fixed, and every other line
      answered to the cent, the residue line's shortfall is the day's whole
      gap, and a row left off the map or borrowed from another day is refused
      here rather than absorbed there;
    * each line other than the residue line equals what answers it;
    * the dropped transfer runs Checking -> the twin and moved what the kept
      one moved, which is what the line both answer says.

    Returns:
        ``(problems, residue)``, where *residue* is what the residue line's
        rows move less what the bank posted -- books minus bank.
    """
    problems: "list[str]" = []
    posted = {
        line.id: line
        for line in db.session.query(BankStatementLine).filter_by(
            account_id=CHECKING_ACCOUNT, posted_on=BOUNDARY_DAY,
        )
    }
    stated = {entry.line_id for entry in BOUNDARY_LINES} | {UNRECORDED_LINE}
    if set(posted) != stated:
        problems.append(
            f"Checking's bank lines on {BOUNDARY_DAY} are {sorted(posted)} "
            f"and the map answers {sorted(stated)}"
        )
    problems.extend(
        f"bank line {row[0]} is already matched; this repair answers it by "
        "hand"
        for row in db.session.query(
            StatementMatchMember.bank_statement_line_id,
        ).filter(StatementMatchMember.bank_statement_line_id.in_(stated))
    )

    facts = _facts(CHECKING_ACCOUNT)
    rows, transfers = _moved_by(facts)
    next_day = _next_bank_day()
    in_window = {_movement_key(f) for f in facts if f.settled_on < next_day}
    mapped = {row for entry in BOUNDARY_LINES for row in entry.rows}
    expected = {("row", row) for row in mapped} | {("transfer", DROPPED_TRANSFER)}
    if in_window != expected:
        problems.append(
            f"Checking's movements before {next_day} are {sorted(in_window)}; "
            f"the map answers {sorted(expected)}"
        )
    problems.extend(
        f"transaction {row} holds {rows[row].movements} movements, not one"
        for row in mapped if row in rows and rows[row].movements != 1
    )

    line_problems, residue = _line_problems(posted, rows, transfers)
    problems.extend(line_problems)
    dropped = db.session.get(Transfer, DROPPED_TRANSFER)
    if dropped is None or (dropped.from_account_id, dropped.to_account_id) != (
        CHECKING_ACCOUNT, TWIN_ACCOUNT,
    ):
        problems.append(
            f"transfer {DROPPED_TRANSFER} is to be DROPPED as Checking -> "
            f"account {TWIN_ACCOUNT}, and it is not that"
        )
    return problems, residue


def _reconcile(export: _Export, sheet: _Sheet, stated: Decimal) -> Decimal:
    """Refuse to start unless the stated map matches BOTH banks' records.

    :func:`_subject_problems` grades account 10 against Fidelity's export and
    :func:`_checking_problems` grades Checking's boundary against SECU's lines.
    **The census arms run in opposite directions on purpose**: one that only
    walks the bank claims the app rows nobody looked at, and one that only
    walks the app claims the bank lines nobody looked at.

    **BAL-467's residue is checked against the figure that ledger row STATES**
    (*stated*, typed at run time so no committed file holds it, ruling
    **R-BAL249**).  That is what closes the hole the census cannot: the census
    sees only Checking's movements dated before SECU's next posted day, so a
    row the residue line should name, dated LATER, would otherwise be absorbed
    into the residue -- measured by adversarial review 2026-10-09 with a whole
    paycheck row moved out of the window.

    **The limits, stated because a control whose edges are unstated reads as
    stronger than it is**: two transfers whose Checking sides fall on the same
    day AND move the same amount could still be exchanged (the census refuses
    a day two transfers claim, and none do today); and the residue arm is only
    as good as the figure typed for it.

    Args:
        export: The parsed export.
        sheet: The performance sheet.
        stated: The residue ledger row BAL-467 states, books less bank.

    Returns:
        BAL-467's residue, for :func:`_verify_checking_boundary`.

    Raises:
        SystemExit: On any disagreement, naming the day and both figures.
    """
    problems = _subject_problems(export)
    checking, residue = _checking_problems()
    problems.extend(checking)
    if residue != stated:
        problems.append(
            f"the payroll residue on bank line {_RESIDUE_LINE.line_id} measures "
            f"{residue}, and ledger row BAL-467 states {stated}"
        )
    if problems:
        raise SystemExit(
            "the stated map does not reconcile:\n  " + "\n  ".join(problems)
        )
    for account_id in (TWIN_ACCOUNT, SUBJECT_ACCOUNT):
        beyond = [
            fact for fact in _facts(account_id)
            if fact.settled_on > export.named[-1]
        ]
        sheet.say(
            f"account {account_id} holds {len(beyond)} settled movement(s) "
            f"after {export.named[-1]}, the export's last day; the export "
            "cannot speak for them and no act touches them"
        )
    bank_days = [day for day in export.named if day > BOOKS_OPEN]
    sheet.say(
        f"map reconciles both ways: {len(FIDELITY_DAYS)} transfers and "
        f"{len(export.recorded_dividends())} dividends answer all "
        f"{len(bank_days)} Fidelity days after {BOOKS_OPEN}; Checking's "
        f"{len(BOUNDARY_LINES) + 1} lines on {BOUNDARY_DAY} are each answered"
    )
    sheet.say(
        f"BAL-467 residue on bank line {_RESIDUE_LINE.line_id} (books less "
        f"bank): **{residue}**"
    )
    return residue


def _class_totals() -> "dict[str, Decimal]":
    """Return the posted ledger's total by account class.

    **This is what replaced a trial-balance assertion that could not fail.**
    ``budget.account_postings`` carries a deferred trigger refusing any journal
    entry whose legs do not sum to zero, so ``sum(amount)`` is ``0.00`` in
    every state the database can hold and asserting it measures the TRIGGER
    rather than the repair (adversarial review, 2026-09-01).  What the acts
    actually move is the balance BETWEEN classes, so that is what is captured
    before and diffed after -- and :func:`_expected_class_moves` turns three of
    those classes into an assertion, because a printed diff nothing compares
    against is not a replacement for a check, only for a claim.

    Returns:
        ``{class name: total}``.
    """
    return {
        row.name: row.total
        for row in db.session.execute(db.text("""
            select cl.name, coalesce(sum(ap.amount), 0) as total
            from budget.account_postings ap
            join budget.ledger_accounts la on la.id = ap.ledger_account_id
            join ref.ledger_account_classes cl on cl.id = la.class_id
            group by cl.name
        """)).all()
    }


def _account_trueup_total(account_id: int) -> Decimal:
    """Return what the posted ledger books as corrections for one account.

    The APP's own answer, read off ``budget.account_postings``, rather than a
    replay written here: an earlier version hand-rolled the correction fold and
    disagreed with the seam on three of four assertions, because it expressed
    neither the RESET (ruling **R-S**) nor which assertion clears which source
    (ruling **R-FL**).  A verification that re-implements the rule it checks is
    an equality whose two sides come from one head.

    Args:
        account_id: The account.

    Returns:
        The sum of its ASSET-side ``trueup`` postings.
    """
    return db.session.execute(db.text("""
        select coalesce(sum(ap.amount), 0)
        from budget.account_postings ap
        join budget.ledger_accounts la on la.id = ap.ledger_account_id
        join ref.posting_kinds k on k.id = ap.posting_kind_id
        join ref.ledger_account_classes cl on cl.id = la.class_id
        where la.account_id = :account and k.name = 'trueup'
          and cl.name = 'Asset'
    """), {"account": account_id}).scalar_one()


def _postings_fingerprint() -> str:
    """Return a digest of every posted-ledger row, ordered.

    **Written because the runbook made a claim nothing graded.**  It said the
    twin's archive round trip "moves no money (measured: a byte-identical
    postings fingerprint either side)" and no fingerprint existed anywhere in
    the instruments -- a one-off measurement quoted as a standing property,
    which is this project's "a fix describes itself ungraded" (adversarial
    review, 2026-09-01).  Now the round trip asserts it.

    Every column that could carry money or attribution is in the digest --
    the posting's, and its journal entry's date, period, source, scenario,
    owner, description and the row, transfer and movement it books -- and the
    ORDER is fixed
    by ``ap.id`` so two equal ledgers cannot hash differently for a row-order
    reason.

    Returns:
        A hex SHA-256 over the posted ledger.
    """
    rows = db.session.execute(db.text("""
        select ap.id, ap.journal_entry_id, ap.ledger_account_id,
               ap.posting_kind_id, ap.amount, je.entry_date, je.pay_period_id,
               je.source_kind_id, je.scenario_id, je.user_id,
               je.transaction_id, je.transfer_id, je.transaction_entry_id,
               je.description
        from budget.account_postings ap
        join budget.journal_entries je on je.id = ap.journal_entry_id
        order by ap.id
    """)).all()
    digest = hashlib.sha256()
    for row in rows:
        digest.update("|".join(str(cell) for cell in row).encode())
    return f"{digest.hexdigest()[:16]} over {len(rows)} postings"


def _interest_income_total(account_id: int) -> Decimal:
    """Return the account's modelled interest-income chart row.

    Args:
        account_id: The account.

    Returns:
        Its total, or ``0`` when the row does not exist.
    """
    return db.session.execute(db.text("""
        select coalesce(sum(ap.amount), 0)
        from budget.account_postings ap
        join budget.ledger_accounts la on la.id = ap.ledger_account_id
        join ref.ledger_account_kinds k on k.id = la.kind_id
        where la.account_id = :account and k.name = 'interest_income'
    """), {"account": account_id}).scalar_one()


def _expected_class_moves(
    export: _Export, modelled_before: Decimal, recorded_expense: Decimal,
) -> "dict[str, Decimal]":
    """Return the class movements this repair's own inputs DERIVE.

    Four of the six classes are asserted rather than printed:

    * **Expense** rises by the one bank line act 7 records (finding
      **BAL-468**).  The figure is the line's own, which act 7 also typed, so
      this does not grade the typing; what it grades is that NOTHING ELSE
      reached an expense row -- act 6 re-dates one without changing its
      figure, and a transfer leg posts as a transfer;
    * **Income** falls by every dividend recorded, and RISES by the modelled
      interest those dividends replace -- the corrections that were standing in
      for them, which go to zero (ruling **R-HL**);
    * **Liability** and **Unrealized** do not move at all: no act touches a
      loan or a market value.

    Asset and Equity are NOT derived here.  Both move by the three
    restatements, whose figures are the repair's own subject rather than an
    input to it, and an expectation computed the way the acts compute it would
    be an equality with one head.

    Args:
        export: The parsed export, for the dividends recorded.
        modelled_before: The twin's and account 10's ``interest_income``
            totals before the repair, summed, LEDGER-NATIVE (a credit, so
            negative); both rows end at zero.
        recorded_expense: What act 7 records, positive.

    Returns:
        ``{class name: expected change}`` for the four classes this derives.
    """
    dividends = sum(
        (amount for _, amount in export.recorded_dividends()), _ZERO_MONEY,
    )
    return {
        "Expense": recorded_expense,
        "Income": -dividends - modelled_before,
        "Liability": _ZERO_MONEY,
        "Unrealized": _ZERO_MONEY,
    }


def _snapshot(export: _Export, sheet: _Sheet, label: str = "AFTER") -> None:
    """Record what the three touched accounts show, before or after an act.

    **After every act, because that is what tells a half-done repair from a
    finished one**, and what an operator compares the screen against before
    taking the next act.  It is also the honest place to see that most acts
    move a CORRECTION rather than a displayed balance.  Ruling **R-FO** sends
    an interest-bearing account's true-up to its ``interest_income`` row, and
    two such rows move here: the TWIN's books interest that never happened
    from act 1 until act 2 clears it, and account 10's -- nonzero before act
    1, standing in for dividends the app never recorded -- grows at act 3 and
    empties at act 9, when the dividends are recorded.  Both accounts'
    corrections and modelled interest are printed, act by act.

    **Valued at the export's LAST DAY rather than at today**, which is what
    makes two rehearsals comparable: an interest-bearing account accrues, so
    "the balance now" is a different number every day it is run.

    Args:
        export: The parsed export, for the day to value at.
        sheet: The performance sheet.
        label: ``BEFORE`` for the state no act has touched yet.
    """
    db.session.expire_all()
    subject = db.session.get(Account, SUBJECT_ACCOUNT)
    last = export.named[-1]
    context = BalanceContext.build(user_id=subject.user_id, as_of=last)
    figures = ", ".join(
        f"account {account_id} {balance_at(db.session.get(Account, account_id), context, last)}"
        for account_id in (CHECKING_ACCOUNT, TWIN_ACCOUNT, SUBJECT_ACCOUNT)
    )
    windows = "; ".join(
        f"account {account_id} corrections "
        f"{_account_trueup_total(account_id)}, modelled interest "
        f"{_interest_income_total(account_id)}"
        for account_id in (TWIN_ACCOUNT, SUBJECT_ACCOUNT)
    )
    sheet.say(f"- {label} (valued {last}): {figures}; {windows}")


def _restate_opening(
    op: _Operator, account_id: int, opened_on: date, equity: Decimal,
) -> None:
    """Restate one account's books through the edit page's own card.

    Args:
        op: The owner's session, which carries the performance sheet.
        account_id: The account to restate.
        opened_on: The day its books open.
        equity: What its books opened holding.

    Raises:
        AssertionError: When the door did not move the governing opening.
    """
    form = op.form_posting_to(
        f"/accounts/{account_id}/edit", f"/accounts/{account_id}/opening",
    )
    op.sheet.typed("Books opening: opened on", opened_on.isoformat())
    op.sheet.typed("Books opening: balance", equity)
    op.submit(form, {
        "opened_on": opened_on.isoformat(), "opening_equity": str(equity),
    })
    db.session.expire_all()
    opening = cash_ledger.governing_account_opening(account_id)
    assert (opening.opened_on, opening.opening_equity) == (opened_on, equity), \
        (f"account {account_id} books open {opening.opened_on} at "
         f"{opening.opening_equity}, not {opened_on} at {equity}")


def _assert_balance(
    op: _Operator, account_id: int, balance: Decimal, observed_on: date,
) -> None:
    """Assert a balance through the account's own true-up editor.

    Args:
        op: The owner's session.
        account_id: The account.
        balance: What the bank says it held.
        observed_on: The day it held it.

    Raises:
        AssertionError: When the editor renders no date or balance box, or the
            appended assertion does not govern afterwards.
    """
    form = op.form(f"/accounts/{account_id}/anchor-form")
    fields = _payload(form)
    boxes = {key for key in fields if "balance" in key or "anchor" in key}
    days = {key for key in fields if "observed" in key or key.endswith("_on")}
    assert boxes and days, f"anchor form fields: {sorted(fields)}"
    op.submit(form, {
        **{key: str(balance) for key in boxes},
        **{key: observed_on.isoformat() for key in days},
    })
    db.session.expire_all()
    governing = cash_ledger.governing_anchor_on(account_id, observed_on)
    assert (governing.balance, governing.observed_on) == (balance, observed_on), \
        (f"account {account_id} governs at {governing.balance} on "
         f"{governing.observed_on}, not {balance} on {observed_on}")
    print(f"  account {account_id} asserts {balance} on {observed_on}")


def _category_id(user_id: int, group: str, item: str) -> int:
    """Return one category's id.

    Args:
        user_id: The owner.
        group: Its group name.
        item: Its item name.

    Returns:
        The ``budget.categories`` id.
    """
    return db.session.execute(
        db.select(Category.id).filter_by(
            user_id=user_id, group_name=group, item_name=item,
        )
    ).scalar_one()


def _period_holding(account_id: int, day: date) -> int:
    """Return the pay period whose span contains *day*, for that account's owner.

    **It asks the owner's CALENDAR since plan step ``pay_calendar:C4-c``.**  It
    was ``select id from budget.pay_periods where :day between start_date and
    end_date`` -- a raw span read off the row -- and that step dropped
    ``end_date``, because a period's last covered day is the day before the
    NEXT payday and is a property of the whole payday set rather than of one
    row.  ``period_containing`` is the application's own answer to this
    question, which is what the door this id is posted to will use.

    It also gained the account, and that is a repair rather than plumbing: the
    query it replaced was scoped by nothing at all, so on a database holding a
    second owner it would have raised ``MultipleResultsFound`` -- or, worse,
    have been made to work by dropping ``scalar_one`` and filed the row under
    someone else's paycheck.

    Args:
        account_id: The account the row will belong to; its owner is whose
            calendar answers.
        day: A civil day.

    Returns:
        The ``budget.pay_periods`` id.

    Raises:
        AssertionError: No paycheck of that owner covers *day*, which would
            otherwise post a ``period_id`` of ``None`` to the create door.
    """
    # Pylint: ``import-outside-toplevel`` -- this module is a runbook script
    # rather than an importable unit, and its own imports are grouped at the
    # top for the reader; the calendar is needed by this one helper.
    from app.services.pay_calendar import calendar_for  # pylint: disable=import-outside-toplevel

    owner_id = db.session.get(Account, account_id).user_id
    period = calendar_for(owner_id).period_containing(day)
    assert period is not None and period.period_id is not None, (
        f"no saved paycheck of owner {owner_id} covers {day}, so this row "
        f"has no period to be filed in"
    )
    return period.period_id


def _cell(account_id: int, category_id: int, type_id: int, day: date) -> str:
    """Return the create card's URL for one grid cell: the owner's click.

    Args:
        account_id: The account the row will belong to.
        category_id: Its category -- the grid row.
        type_id: ``ref.transaction_types`` -- income or expense.
        day: The day the money moved, whose paycheck is the grid column.

    Returns:
        The URL the cell opens.
    """
    return (
        f"/transactions/new/full?category_id={category_id}"
        f"&period_id={_period_holding(account_id, day)}&account_id={account_id}"
        f"&transaction_type_id={type_id}"
    )


def _settled_status_id(txn_id: int) -> str:
    """Return the status a row of *txn_id*'s type settles as, as the form posts it.

    Args:
        txn_id: The transaction.

    Returns:
        The ``ref.statuses`` id, from the reference cache.
    """
    type_id = db.session.get(Transaction, txn_id).transaction_type_id
    kind = next(
        member for member in _SETTLED_STATUS
        if ref_cache.txn_type_id(member) == type_id
    )
    return str(ref_cache.status_id(_SETTLED_STATUS[kind]))


def _record(
    op: _Operator, cell: str, day: date, amount: Decimal,
    name: "str | None" = None,
) -> int:
    """Create a row, settle it, then correct the day -- the owner's own path.

    **Three submissions, not two, and the third is the one an earlier draft
    skipped.**  The create card renders no status control and no Actual box,
    and the full-edit card renders "Money moved on" ONLY for a row that is
    already settled -- ``grid/_transaction_full_edit.html`` gates it on
    ``txn.status.is_settled``, and gates the "Actual" box beside it on
    ``is_settled and correctable``.  So a browser cannot state the day while
    the row is Projected; the operator settles first -- **which stamps
    TODAY** -- and then reopens the now-settled card and corrects the day.
    The create card renders no Name box either, so a NAME is typed in that
    same third save, where the card renders one for a placed row.

    **Between the second and third submissions the row is live in the fold at
    TODAY'S date**, for its full figure.  It is transient and inside one act,
    which is why the runbook tells an operator to finish a row rather than
    leave one half-recorded.

    Args:
        op: The owner's session, which carries the performance sheet.
        cell: The grid cell the row is created from (:func:`_cell`).
        day: The day the money moved.
        amount: What moved.
        name: What to call it, or ``None`` to keep the name the create door
            gives it.

    Returns:
        The new transaction's id.

    Raises:
        AssertionError: When the create renders no cell id, when a Projected
            card already offers a settle-day box (which would mean this
            three-step path is describing a page that no longer exists), when
            the settled card renders no Name box for a *name*, or when the
            settle did not record the day and the figure.
    """
    op.sheet.typed("Amount", amount)
    form = op.form(cell)
    created = op.submit(form, {"estimated_amount": str(amount)})
    found = _NEW_CELL_RX.search(created.get_data(as_text=True))
    assert found, \
        "the create door rendered no transaction cell to read an id from"
    txn_id = int(found.group(1))

    settle = op.form(f"/transactions/{txn_id}/full-edit")
    assert "settled_on" not in _payload(settle), (
        f"transaction {txn_id} is Projected and its card already renders a "
        "settle-day box; this rehearsal's three-step path assumes it does not"
    )
    op.submit(settle, {"status_id": _settled_status_id(txn_id)})

    correct = op.form(f"/transactions/{txn_id}/full-edit")
    fields = _payload(correct)
    assert "settled_on" in fields, \
        f"transaction {txn_id} settled but its card renders no settle-day box"
    # Only the DAY (and a NAME) makes this third save necessary.  The settle
    # above already stamped the figure from the estimate, so re-posting it
    # changes nothing -- it is submitted because the card renders it and an
    # untouched Save posts what it renders.
    typed = {"settled_on": day.isoformat()}
    op.sheet.typed("Money moved on", day.isoformat())
    if "settled_amount" in fields:
        typed["settled_amount"] = str(amount)
    if name is not None:
        assert "name" in fields, \
            f"transaction {txn_id}'s settled card renders no Name box"
        typed["name"] = name
        op.sheet.typed("Name", name)
    op.submit(correct, typed)

    db.session.expire_all()
    txn = db.session.get(Transaction, txn_id)
    assert (txn.settled_on, settled_figure(txn)) == (day, amount), \
        (f"transaction {txn_id} records {settled_figure(txn)} on "
         f"{txn.settled_on}, not {amount} on {day}")
    return txn_id


def _redate_row(op: _Operator, txn_id: int, day: date) -> None:
    """Move one settled row's day through its own card.

    Args:
        op: The owner's session, which carries the performance sheet.
        txn_id: The transaction.
        day: The day the bank posted it.

    Raises:
        AssertionError: When the card renders no settle-day box, or the fold
            does not then count the row on *day*.
    """
    form = op.form(f"/transactions/{txn_id}/full-edit")
    assert "settled_on" in _payload(form), \
        f"transaction {txn_id}'s card renders no settle-day box"
    op.sheet.typed(f"transaction {txn_id}: Money moved on", day.isoformat())
    op.submit(form, {"settled_on": day.isoformat()})
    rows, _ = _moved_by(_facts(db.session.get(Transaction, txn_id).account_id))
    assert rows[txn_id].day == day, \
        f"transaction {txn_id} is counted on {rows[txn_id].day}, not {day}"


def _redate_sides(
    op: _Operator, transfer_id: int, days: "dict[int, date]",
) -> None:
    """Type each named side's day into its own box, in ONE save.

    **Each side keeps its own day** (plan step ``balance:X-bi-6-4c-3``, ruling
    **R-BAL142**), so the popover renders one "Money moved on" box per side,
    and a side whose day is only BORROWED renders empty and reads "a guess".
    Typing a side's box makes the day that side's own, ENTERED; leaving a box
    as rendered leaves its side alone.  Ruling **R-BAL256** has every
    Fidelity side typed from Fidelity's own record, so after this the side
    must carry the day as evidence, not merely land on it.

    Args:
        op: The owner's session, which carries the performance sheet.
        transfer_id: The transfer.
        days: ``{account id: the day that account's bank posted it}``.

    Raises:
        AssertionError: When the popover renders no box for a side, or a side
            does not then record its day as its own.
    """
    transfer = db.session.get(Transfer, transfer_id)
    boxes = {
        transfer.from_account_id: "settled_on_from",
        transfer.to_account_id: "settled_on_to",
    }
    # Opened from the grid, as the owner reaches it: the only page that draws
    # a transfer cell is an account's grid, whose card carries the leg's id.
    leg = next(iter(days))
    form = op.form(f"/transfers/{transfer_id}/full-edit?leg_account_id={leg}")
    fields = _payload(form)
    typed = {}
    for account_id, day in days.items():
        assert boxes[account_id] in fields, (
            f"transfer {transfer_id}'s popover renders no box for account "
            f"{account_id}"
        )
        typed[boxes[account_id]] = day.isoformat()
        op.sheet.typed(
            f"transfer {transfer_id}: Money moved on, account {account_id}",
            day.isoformat(),
        )
    op.submit(form, typed)
    for account_id, day in days.items():
        side = _side_day(transfer_id, account_id)
        assert side is not None and side.day == day and is_evidence(side), (
            f"transfer {transfer_id}'s side on account {account_id} records "
            f"{side}, not {day} as its own"
        )


def _drop_transfer(op: _Operator, transfer_id: int) -> None:
    """Set a settled transfer back to Projected, then Cancel it.

    **Ruling R-BAL255, and why it is not a delete**: no page renders the
    single-transfer delete door (``routes/transfers/mutations.py``
    ``delete_transfer`` says so itself), so a delete could never be clicked;
    and Cancelled is the true statement about this row -- the planned transfer
    to the old account did not happen, because the one real ACH is the kept
    transfer's.  The card offers Cancel only for a Projected transfer, so the
    owner sets Status to Projected and saves -- which CLOSES the card -- then
    reopens it from Checking's grid and presses the red "Cancel transfer"
    quick action.  **The reopened card has TWO buttons reading "Cancel"**: the
    grey one only closes the card, and pressing it leaves transfer 1 a stale
    Projected plan that no balance check can see (measured by adversarial
    review 2026-10-09), so :func:`_verify_ledger` asserts Cancelled.  Between
    the save and the press the row is a PLAN again, so the two are one act.

    Args:
        op: The owner's session, which carries the performance sheet.
        transfer_id: The transfer to drop.

    Raises:
        AssertionError: When the set-back does not render Cancel, the transfer
            does not end Cancelled, or either account's fold still counts it.
    """
    projected = ref_cache.status_id(StatusEnum.PROJECTED)
    card = f"/transfers/{transfer_id}/full-edit?leg_account_id={CHECKING_ACCOUNT}"
    form = op.form(card)
    op.sheet.typed("Status", "Projected, then Save (the card closes)")
    op.submit(form, {"status_id": str(projected)})
    cancel = f"/transfers/instance/{transfer_id}/cancel"
    page = op.client.get(card, headers={"HX-Request": "true"}).get_data(
        as_text=True,
    )
    assert cancel in page, \
        f"transfer {transfer_id}'s reopened card renders no Cancel transfer"
    op.sheet.say(
        "- REOPEN the card from Checking's grid, then PRESS the red 'Cancel "
        "transfer' quick action -- NOT the grey Cancel, which only closes the "
        "card. The cell must then read Cancelled."
    )
    op.send("post", cancel, {"leg_account_id": str(CHECKING_ACCOUNT)})
    db.session.expire_all()
    transfer = db.session.get(Transfer, transfer_id)
    assert transfer.status_id == ref_cache.status_id(StatusEnum.CANCELLED), \
        f"transfer {transfer_id} is status {transfer.status_id}, not Cancelled"
    for account_id in (transfer.from_account_id, transfer.to_account_id):
        _, moved = _moved_by(_facts(account_id))
        assert transfer_id not in moved, \
            f"account {account_id}'s fold still counts transfer {transfer_id}"


def _set_archived(op: _Operator, account_id: int, archived: bool) -> None:
    """Archive or unarchive an account and assert the flag moved.

    **The posted ledger is fingerprinted either side, so "the round trip moves
    no money" is GRADED rather than claimed.**  The runbook asserted that
    property and nothing measured it (adversarial review, 2026-09-01).
    Archiving is a visibility flag: the balance sheet reads the posted ledger
    over the whole chart and filters no account (**N-384**), so a flip that
    moved a posting would move net worth silently.

    Args:
        op: The owner's session.
        account_id: The account.
        archived: The state to reach.

    Raises:
        AssertionError: When the flag did not move, or the flip moved money.
    """
    door = "archive" if archived else "unarchive"
    before = _postings_fingerprint()
    op.send("post", f"/accounts/{account_id}/{door}", {})
    db.session.expire_all()
    assert db.session.get(Account, account_id).is_active is not archived, \
        f"account {account_id} did not {door}"
    after = _postings_fingerprint()
    assert after == before, (
        f"{door} of account {account_id} MOVED THE POSTED LEDGER: "
        f"{before} -> {after}"
    )
    print(f"  account {account_id} {door}d; postings unchanged ({after})")


def _add_earlier_paychecks(op: _Operator, count: int) -> None:
    """Record paychecks before the first one ("Add earlier paychecks").

    Plan step ``pay_calendar:C18-b``.  NOT one of ruling R-BAL3's acts: it is
    rehearsed so the runbook can say whether performing it first changes the
    repair (the step waits on ``pay_calendar:C18`` because the rehearsal must
    run against the calendar the repair is performed on).

    Args:
        op: The owner's session, which carries the performance sheet.
        count: How many paychecks to add.

    Raises:
        AssertionError: When the owner's paycheck count did not grow by *count*.
    """
    def periods() -> int:
        return db.session.execute(
            db.text("select count(*) from budget.pay_periods where user_id = :u"),
            {"u": op.user_id},
        ).scalar_one()

    was = periods()
    form = op.form_posting_to(
        "/settings?section=pay-periods", "/pay-periods/earlier",
    )
    op.sheet.typed("Add earlier paychecks: Paychecks to add", count)
    op.submit(form, {"num_periods": str(count)})
    db.session.expire_all()
    assert periods() == was + count, \
        f"paychecks went {was} -> {periods()}, not up by {count}"


def _type_fidelity_days(op: _Operator) -> None:
    """Type Fidelity's day into account 10's box on every other mapped transfer.

    Ruling **R-BAL256**: each Fidelity side carries its own day from
    Fidelity's record rather than borrowing Checking's.  **Which side needs it
    is MEASURED on both terms, the day and how it is known**: a side already
    on its day as its OWN is skipped, and one merely borrowing the right day is
    typed, since "a guess" that happens to agree would follow any later
    change to its Checking side.  Checking's sides are not touched here: each
    is SECU's to state, and the reconcile that grades them is plan step X-bk-2.

    Args:
        op: The owner's session, which carries the performance sheet.
    """
    for transfer_id, day in FIDELITY_DAYS.items():
        if transfer_id == KEPT_TRANSFER:
            continue
        side = _side_day(transfer_id, SUBJECT_ACCOUNT)
        if side is not None and side.day == day and is_evidence(side):
            op.sheet.say(
                f"- transfer {transfer_id}: account {SUBJECT_ACCOUNT}'s side "
                f"already records {day} as its own; nothing to type"
            )
            continue
        _redate_sides(op, transfer_id, {SUBJECT_ACCOUNT: day})


def _consolidate_twin(op: _Operator) -> None:
    """Zero the archived twin in ONE sitting: unarchive, restate, assert, archive.

    **It follows act 1 directly, and that placement is the reason it is act
    2.**  Dropping transfer 1 takes its arrival off the twin while the twin's
    2026-04-06 assertion still stands, so the gap books a correction that
    ruling **R-FO** reports as INTEREST on this interest-bearing account -- a
    gain that never happened, measured by adversarial review 2026-10-09 -- and
    zeroing the twin is what clears it.  Nothing else forces this act's place.

    **The twin is UNARCHIVED for its own two acts and archived again after.**
    Its restatement door is reachable while archived (plan step
    ``balance:X-f3c-2b-2d`` closed finding **N-430**), but its BALANCE editor
    is not reachable by clicking (finding **N-453**, ruled to
    ``balance:X-f4``), and the runbook is a click procedure, so the round trip
    stays.  **Do not stop while it is unarchived, nor between the restatement
    and the assertion**: zeroing the opening while the 2026-04-06 assertion
    stands books that whole figure as the same false interest until the
    assertion that follows supersedes it.  The round trip moves no money, which
    :func:`_set_archived` grades with a postings fingerprint either side.

    Args:
        op: The owner's session, which carries the performance sheet.
    """
    _set_archived(op, TWIN_ACCOUNT, archived=False)
    opened_on = cash_ledger.governing_account_opening(TWIN_ACCOUNT).opened_on
    _restate_opening(op, TWIN_ACCOUNT, opened_on, _ZERO_MONEY)
    op.sheet.typed("Balance", _ZERO_MONEY)
    op.sheet.typed("As of", TWIN_ASSERTED_ON.isoformat())
    _assert_balance(op, TWIN_ACCOUNT, _ZERO_MONEY, TWIN_ASSERTED_ON)
    _set_archived(op, TWIN_ACCOUNT, archived=True)


def _record_dividends(op: _Operator, export: _Export) -> None:
    """Rename the empty category into R-HL's, then record every dividend.

    Args:
        op: The owner's session, which carries the performance sheet.
        export: The parsed export.
    """
    # The group control is a hidden input that SCRIPT keeps in step with a
    # select carrying no name of its own; the page renders it holding the
    # category's CURRENT group, so the owner's new choice reaches the payload
    # only through that script.  Supplying the group here is exactly what the
    # owner's pick does -- and said out loud, because a payload that overrides
    # what a page rendered is the thing this file's parser exists to avoid
    # doing silently.
    form = op.form_posting_to(
        "/settings?section=categories",
        f"/categories/{DIVIDEND_CATEGORY_ID}/edit",
    )
    op.sheet.typed(f"category {DIVIDEND_CATEGORY_ID}: Group",
                   DIVIDEND_CATEGORY[0])
    op.sheet.typed(f"category {DIVIDEND_CATEGORY_ID}: Item Name",
                   DIVIDEND_CATEGORY[1])
    op.submit(form, {
        "group_name": DIVIDEND_CATEGORY[0], "item_name": DIVIDEND_CATEGORY[1],
    })
    category_id = _category_id(op.user_id, *DIVIDEND_CATEGORY)
    assert category_id == DIVIDEND_CATEGORY_ID, \
        f"the dividends' category is {category_id}, not the renamed one"
    for day, amount in export.recorded_dividends():
        op.sheet.say(f"- dividend of {day}, account {SUBJECT_ACCOUNT}:")
        _record(
            op,
            _cell(SUBJECT_ACCOUNT, category_id,
                  ref_cache.txn_type_id(TxnTypeEnum.INCOME), day),
            day, amount,
        )


#: What the operator must hold to, on the sheet as well as here.
_STOP_RULES = (
    "STOP RULES. Perform acts 1 to 10 in ONE sitting. Act 1 opens a window in "
    "which the twin books interest that never happened, and act 2 closes it. "
    "Account 10's corrections already stand in, as modelled interest, for "
    "dividends the app never recorded; act 3 enlarges them and act 9 records "
    "the dividends and empties them (both accounts are on every AFTER line). "
    "Finish each row's saves before the next: between a row's settle and its "
    "day correction it counts on TODAY. Do not stop while account 2 is "
    "unarchived (act 2)."
)


def _equities(export: _Export) -> "dict[int, Decimal]":
    """Return what each restated account's books open holding: its bank's close.

    Args:
        export: The parsed Fidelity export.

    Returns:
        ``{account id: equity}`` for account 10 and Checking.

    Raises:
        SystemExit: When the app's own record of SECU's statement does not
            price Checking's close on BOOKS_OPEN.
    """
    checking_close = statement_import.fold_bank_balances(
        CHECKING_ACCOUNT, [BOOKS_OPEN],
    ).balances.get(BOOKS_OPEN)
    if checking_close is None:
        raise SystemExit(
            f"no imported statement prices Checking's close on {BOOKS_OPEN}"
        )
    return {
        SUBJECT_ACCOUNT: export.closing_on(BOOKS_OPEN),
        CHECKING_ACCOUNT: checking_close,
    }


def _perform(
    op: _Operator, export: _Export, earlier: int,
) -> "tuple[int, dict[int, Decimal]]":
    """Perform the repair in ruling R-BAL3's order, as amended.

    **The order is FORCED at two points, not chosen.**  The books boundary
    refuses a movement dated on or before its account's ``opened_on`` (ruling
    **R-HG**), so nothing can be dated onto BOUNDARY_DAY until the books it
    belongs to open on BOOKS_OPEN -- which is why both restatements (acts 3
    and 4) precede every re-date and the new row (acts 5 to 7).  And transfer
    102's re-date touches BOTH endpoints, so BOTH openings must already be
    restated before that one save; a per-account "restate, then re-date" walk
    is one click from a refusal mid-act.

    **The dividends follow the restatements** (ruling **R-HL**): recorded
    first, the account's later assertions book corrections against its
    ``interest_income`` row, a negative interest income nobody earned, until
    the rest lands.  **The twin is zeroed directly after act 1**, which opens
    the window it closes (:func:`_consolidate_twin`).  The windows that remain
    are on :data:`_STOP_RULES` and measured act by act by :func:`_snapshot`.

    Args:
        op: The owner's session, which carries the performance sheet.
        export: The parsed export, which supplies every Fidelity figure.
        earlier: How many paychecks act 0 adds before the first; ``0`` skips
            it.

    Returns:
        ``(the id act 7 created, {account id: the equity it opened with})``.

    Raises:
        SystemExit: From :func:`_equities`.
    """
    equities = _equities(export)
    unrecorded = db.session.get(BankStatementLine, UNRECORDED_LINE)
    lines = {
        entry.line_id: db.session.get(BankStatementLine, entry.line_id)
        for entry in BOUNDARY_LINES
    }

    op.sheet.say("")
    op.sheet.say(_STOP_RULES)
    op.sheet.say("")
    op.sheet.say(f"## Before act 1 (balances valued {export.named[-1]}, the "
                 "last day the export states)")
    _snapshot(export, op.sheet, "BEFORE")

    if earlier:
        op.sheet.act(0, "add earlier paychecks (NOT an R-BAL3 act; measured)",
                     "Settings > Pay periods > Add earlier paychecks")
        _add_earlier_paychecks(op, earlier)
        _snapshot(export, op.sheet)

    op.sheet.act(1, f"drop transfer {DROPPED_TRANSFER}: set it back, then "
                 "cancel it (R-BAL3, R-BAL255)",
                 f"transfer {DROPPED_TRANSFER}'s popover: Status, Save; Cancel")
    _drop_transfer(op, DROPPED_TRANSFER)
    _snapshot(export, op.sheet)

    op.sheet.act(2, f"zero the archived twin (account {TWIN_ACCOUNT}) in ONE "
                 "sitting", f"Accounts > archived > account {TWIN_ACCOUNT}: "
                 "Unarchive; Edit > Books opening; its balance editor; Archive")
    _consolidate_twin(op)
    _snapshot(export, op.sheet)

    op.sheet.act(3, f"restate account {SUBJECT_ACCOUNT}'s books to "
                 f"{BOOKS_OPEN} at Fidelity's close for that day",
                 f"Accounts > account {SUBJECT_ACCOUNT} > Edit > Books opening")
    _restate_opening(op, SUBJECT_ACCOUNT, BOOKS_OPEN, equities[SUBJECT_ACCOUNT])
    _snapshot(export, op.sheet)

    op.sheet.act(4, f"restate Checking's books to {BOOKS_OPEN} at SECU's "
                 "close for that day", f"Accounts > account {CHECKING_ACCOUNT}"
                 " > Edit > Books opening")
    _restate_opening(op, CHECKING_ACCOUNT, BOOKS_OPEN,
                     equities[CHECKING_ACCOUNT])
    _snapshot(export, op.sheet)

    op.sheet.act(5, f"type transfer {KEPT_TRANSFER}'s day into BOTH sides' "
                 "boxes, each from its own bank (R-BAL3, R-BAL256)",
                 f"transfer {KEPT_TRANSFER}'s popover, one box per side, ONE save")
    kept_line = next(e for e in BOUNDARY_LINES if e.transfer_id == KEPT_TRANSFER)
    _redate_sides(op, KEPT_TRANSFER, {
        CHECKING_ACCOUNT: lines[kept_line.line_id].posted_on,
        SUBJECT_ACCOUNT: FIDELITY_DAYS[KEPT_TRANSFER],
    })
    _snapshot(export, op.sheet)

    op.sheet.act(6, f"re-date Checking's rows onto {BOUNDARY_DAY}, the day "
                 "SECU posted them", "each row's card, Money moved on")
    for entry in BOUNDARY_LINES:
        for row in entry.rows:
            _redate_row(op, row, lines[entry.line_id].posted_on)
    _snapshot(export, op.sheet)

    op.sheet.act(7, f"record bank line {UNRECORDED_LINE}, which no row answers "
                 "(BAL-468)", f"Checking's grid, category {UNRECORDED_CATEGORY},"
                 " new expense; settle; then correct the day and name")
    created = _record(
        op,
        _cell(CHECKING_ACCOUNT, UNRECORDED_CATEGORY,
              ref_cache.txn_type_id(TxnTypeEnum.EXPENSE), unrecorded.posted_on),
        unrecorded.posted_on, -unrecorded.amount, UNRECORDED_NAME,
    )
    op.sheet.say(f"- created transaction {created}")
    _snapshot(export, op.sheet)

    op.sheet.act(8, f"type Fidelity's day into account {SUBJECT_ACCOUNT}'s box "
                 f"on the {len(FIDELITY_DAYS) - 1} other transfers (R-BAL256)",
                 f"each transfer's popover, account {SUBJECT_ACCOUNT}'s box only")
    _type_fidelity_days(op)
    _snapshot(export, op.sheet)

    op.sheet.act(9, "rename the empty category, then record the dividends the "
                 "app has never held (R-HL, R-BAL250)",
                 f"Settings > Categories > category {DIVIDEND_CATEGORY_ID}'s "
                 "pencil, then account 10's grid per dividend")
    _record_dividends(op, export)
    _snapshot(export, op.sheet)

    last = export.named[-1]
    op.sheet.act(10, f"assert Fidelity's last stated close on {last} (R-HM)",
                 f"account {SUBJECT_ACCOUNT}'s balance editor")
    op.sheet.typed("Balance", export.closings[last])
    op.sheet.typed("As of", last.isoformat())
    _assert_balance(op, SUBJECT_ACCOUNT, export.closings[last], last)
    _snapshot(export, op.sheet)
    return created, equities


def _verify_class_moves(
    sheet: _Sheet, before: "dict[str, Decimal]",
    expected: "dict[str, Decimal]",
) -> None:
    """Record every class's move and assert the ones the inputs derive.

    Args:
        sheet: The performance sheet.
        before: :func:`_class_totals` captured before the first act.
        expected: :func:`_expected_class_moves`.

    Raises:
        AssertionError: When a derived class moved by anything else.
    """
    after = _class_totals()
    sheet.say("- posted ledger, by class (before -> after, moved):")
    for name in sorted(set(before) | set(after)):
        was = before.get(name, _ZERO_MONEY)
        now = after.get(name, _ZERO_MONEY)
        want = expected.get(name)
        said = "" if want is None else (
            f", expected {want:+}" if now - was == want
            else f", EXPECTED {want:+}"
        )
        sheet.say(f"  - {name}: {was} -> {now}, {now - was:+}{said}")
    for name, want in expected.items():
        moved = after.get(name, _ZERO_MONEY) - before.get(name, _ZERO_MONEY)
        assert moved == want, (
            f"the {name} class moved {moved}, not the {want} this repair's "
            "own inputs derive"
        )


def _verify_ledger(
    export: _Export, sheet: _Sheet, before: "dict[str, Decimal]",
    modelled_before: Decimal,
) -> None:
    """Assert what the acts did to the posted ledger, and record it.

    Neither this nor :func:`_verify_boundary` is the day-by-day comparison with
    the banks -- that is ``measure_cutover_against_bank.py``'s question, asked
    of both accounts by the commands this file's docstring names.  What they
    ask is whether the ACTS landed.

    Args:
        export: The parsed export.
        sheet: The performance sheet.
        before: :func:`_class_totals` captured before the first act.
        modelled_before: The twin's and account 10's ``interest_income``
            totals before the first act, summed.

    Raises:
        AssertionError: On the class moves, the twin's balance, either
            account's corrections or modelled interest, or the dropped
            transfer's state.
    """
    db.session.expire_all()
    recorded_expense = -db.session.get(BankStatementLine, UNRECORDED_LINE).amount
    sheet.say("")
    sheet.say("## After the last act: what to check")
    _verify_class_moves(
        sheet, before,
        _expected_class_moves(export, modelled_before, recorded_expense),
    )
    subject = db.session.get(Account, SUBJECT_ACCOUNT)
    last = export.named[-1]
    context = BalanceContext.build(user_id=subject.user_id, as_of=last)
    twin = balance_at(db.session.get(Account, TWIN_ACCOUNT), context, last)
    assert twin == _ZERO_MONEY, f"the twin still holds {twin}"
    sheet.say(f"- account {TWIN_ACCOUNT} holds {twin}")
    for account_id in (TWIN_ACCOUNT, SUBJECT_ACCOUNT):
        corrections = _account_trueup_total(account_id)
        modelled = _interest_income_total(account_id)
        assert (corrections, modelled) == (_ZERO_MONEY, _ZERO_MONEY), (
            f"account {account_id}'s posted corrections total {corrections} "
            f"and its modelled interest row {modelled}: an assertion is not "
            "explained by the records"
        )
        sheet.say(f"- account {account_id}'s corrections total {corrections}, "
                  f"its modelled interest row {modelled}")
    cancelled = ref_cache.status_id(StatusEnum.CANCELLED)
    states = {db.session.get(Transfer, DROPPED_TRANSFER).status_id} | {
        row.status_id for row in db.session.query(Transaction).filter_by(
            transfer_id=DROPPED_TRANSFER, is_deleted=False,
        )
    }
    assert states == {cancelled}, (
        f"transfer {DROPPED_TRANSFER} and its rows hold statuses "
        f"{sorted(states)}, not Cancelled: was the card's grey Cancel pressed?"
    )
    sheet.say(f"- transfer {DROPPED_TRANSFER} and its rows are Cancelled")


def _verify_boundary(
    export: _Export, sheet: _Sheet, residue: Decimal, created: int,
    equities: "dict[int, Decimal]",
) -> None:
    """Assert both books open where the banks close, and record it.

    Account 10 reads Fidelity's own close on BOUNDARY_DAY and every Fidelity
    side carries Fidelity's day as its own (ruling **R-BAL256**); Checking is
    :func:`_verify_checking_boundary`.  **The opening figure itself cannot
    fail here** -- the act typed it from the same record this compares against.
    After a HUMAN performs the repair, what grades the typing is the scorer,
    ``measure_cutover_against_bank.py``, which prints each account's opening
    day against its bank on a line of its own.

    Args:
        export: The parsed export.
        sheet: The performance sheet.
        residue: BAL-467's residue, from :func:`_reconcile`.
        created: The transaction act 7 created.
        equities: What each restated account opened with.

    Raises:
        AssertionError: On any arm.
    """
    for account_id, equity in equities.items():
        opening = cash_ledger.governing_account_opening(account_id)
        assert (opening.opened_on, opening.opening_equity) == (BOOKS_OPEN, equity), \
            f"account {account_id} opens {opening.opened_on} at {opening.opening_equity}"
        sheet.say(f"- account {account_id}'s books open {opening.opened_on} "
                  f"holding {opening.opening_equity}")
    for transfer_id, day in FIDELITY_DAYS.items():
        side = _side_day(transfer_id, SUBJECT_ACCOUNT)
        assert side is not None and side.day == day and is_evidence(side), \
            f"transfer {transfer_id}'s Fidelity side records {side}, not {day}"
    sheet.say(f"- all {len(FIDELITY_DAYS)} Fidelity sides record Fidelity's "
              "day as their own")
    subject = db.session.get(Account, SUBJECT_ACCOUNT)
    context = BalanceContext.build(
        user_id=subject.user_id, as_of=export.named[-1],
    )
    subject_books = cash_balance_at(subject, context, BOUNDARY_DAY)
    assert subject_books == export.closing_on(BOUNDARY_DAY), (
        f"account {SUBJECT_ACCOUNT}'s books read {subject_books} on "
        f"{BOUNDARY_DAY}, Fidelity {export.closing_on(BOUNDARY_DAY)}"
    )
    sheet.say(f"- account {SUBJECT_ACCOUNT} on {BOUNDARY_DAY}: books "
              f"{subject_books}, Fidelity {export.closing_on(BOUNDARY_DAY)}")
    _verify_checking_boundary(sheet, context, residue, created)


def _verify_checking_boundary(
    sheet: _Sheet, context: BalanceContext, residue: Decimal, created: int,
) -> None:
    """Assert Checking's boundary holds exactly what the map names.

    Every Checking movement dated before the next day SECU posted anything is
    a mapped row, the row act 7 created, or the kept transfer's side -- all on
    BOUNDARY_DAY -- and on BOUNDARY_DAY the books stand off SECU's close by
    BAL-467's residue and nothing else.  The census half is what makes the
    residue mean something: a row left behind, or one moved in from another
    day, changes the set before it can change the gap.  **The level is
    compared on BOUNDARY_DAY alone, deliberately**: the owner asserted a
    balance on the day after, and the cash fold RESETS at an assertion (ruling
    R-S), so a later day's gap would grade the typed figure, not the rows.
    Between BOUNDARY_DAY and SECU's next posted day neither record moves --
    the census says so for the app, :func:`_next_bank_day` for the bank.

    Args:
        sheet: The performance sheet.
        context: The verification's read pass.
        residue: BAL-467's residue, from :func:`_reconcile`.
        created: The transaction act 7 created.

    Raises:
        AssertionError: When the stretch holds any other movement, or the
            books stand off SECU's close by anything but the residue.
    """
    next_day = _next_bank_day()
    facts = _facts(CHECKING_ACCOUNT)
    in_window = {
        (fact.transaction_id, fact.settled_on)
        for fact in facts if fact.settled_on < next_day
    }
    kept = {
        fact.transaction_id for fact in facts
        if fact.transfer_id == KEPT_TRANSFER
    }
    wanted = {
        (txn, BOUNDARY_DAY)
        for txn in {row for entry in BOUNDARY_LINES for row in entry.rows}
        | {created} | kept
    }
    assert in_window == wanted, (
        f"Checking's movements before {next_day} are {sorted(in_window)}; the "
        f"map names {sorted(wanted)}"
    )
    books = cash_balance_at(
        db.session.get(Account, CHECKING_ACCOUNT), context, BOUNDARY_DAY,
    )
    bank = statement_import.fold_bank_balances(
        CHECKING_ACCOUNT, [BOUNDARY_DAY],
    ).balances[BOUNDARY_DAY]
    assert books - bank == residue, (
        f"Checking's books read {books} on {BOUNDARY_DAY} against SECU's "
        f"{bank}: {books - bank}, where BAL-467's residue is {residue}"
    )
    sheet.say(f"- Checking on {BOUNDARY_DAY}: books {books}, SECU {bank}, "
              f"apart by BAL-467's residue {residue} and nothing else; every "
              f"Checking movement before {next_day} is one of the "
              f"{len(wanted)} the map names")


def _connect(clone: str):
    """Build an app pointed at *clone*, refusing anything it is not.

    **A money-moving harness names its own target.**  ``DATABASE_URL`` is an
    environment variable and an environment variable is inherited: a shell that
    last exported the dev runtime's URL would silently rehearse against a
    database other sessions are working in, and ``shekel`` is what BOTH the
    deployed database and that runtime are called.  So the target is a required
    ARGUMENT, checked against the connection the app actually opened, and the
    one name that is never a rehearsal clone is refused outright.

    **The name check is the weaker half and :func:`_require_unrepaired` is the
    stronger**: a name can only refuse the databases somebody thought of, while
    the pre-state refuses every database this repair has already run against,
    whatever it is called.

    Args:
        clone: The database this rehearsal is for.

    Returns:
        The configured Flask application.

    Raises:
        SystemExit: When *clone* is the shared name, or the connection is not
            to it.
    """
    if clone == "shekel":
        raise SystemExit(
            "'shekel' is the deployed database AND the shared dev runtime; "
            "clone one and rehearse against the copy"
        )
    app = create_app()
    # CSRF and strong session protection are both disabled for the rehearsal
    # and neither is under test: this forges a session because it has no
    # password for the database it is pointed at, exactly as
    # ``verify_render_surfaces`` does, and strong protection refuses a session
    # whose identifier was not minted inside a request.
    app.config["WTF_CSRF_ENABLED"] = False
    login_manager.session_protection = None
    with app.app_context():
        live = db.session.execute(
            db.text("select current_database()")
        ).scalar_one()
    if live != clone:
        raise SystemExit(
            f"--clone says {clone!r} and DATABASE_URL opened {live!r}"
        )
    return app


def main() -> None:
    """Reconcile the map, perform the repair, verify, and write the sheet."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--clone", required=True,
        help="the throwaway database this rehearsal writes to",
    )
    parser.add_argument(
        "--bank", required=True,
        help="path to the Fidelity history CSV export",
    )
    parser.add_argument(
        "--sheet", required=True,
        help="where to write the performance sheet (outside every checkout)",
    )
    parser.add_argument(
        "--residue", required=True, type=Decimal,
        help="the payroll residue ledger row BAL-467 states, books less bank",
    )
    parser.add_argument(
        "--add-earlier-paychecks", type=int, default=0, metavar="N",
        help="perform 'Add earlier paychecks' for N first (act 0; measured)",
    )
    args = parser.parse_args()
    sheet = _Sheet(_sheet_path(args.sheet))
    export = _Export.read(args.bank)
    app = _connect(args.clone)
    sheet.say(f"# Account-10 repair: performance sheet ({args.clone}, "
              f"written {datetime.now():%Y-%m-%d %H:%M})")
    sheet.say(f"Fidelity export: {len(export.named)} days, {export.named[0]} "
              f"to {export.named[-1]}; {len(export.recorded_dividends())} "
              "dividends recorded by this repair")
    # **A run that ends early still records why.**  A refusal raised as one of
    # the two kinds this file raises is written with its sentence; anything
    # else leaves the STOPPED line below, and its traceback is on the console.
    completed = False
    try:
        with app.app_context():
            _require_unrepaired()
            residue = _reconcile(export, sheet, args.residue)
            before = _class_totals()
            modelled_before = sum(
                (_interest_income_total(a) for a in (TWIN_ACCOUNT, SUBJECT_ACCOUNT)),
                _ZERO_MONEY,
            )
            operator = _Operator(app, sheet)
            created, equities = _perform(
                operator, export, args.add_earlier_paychecks,
            )
            sheet.say("")
            sheet.say(f"{operator.acts} door submissions performed; verifying")
            _verify_ledger(export, sheet, before, modelled_before)
            _verify_boundary(export, sheet, residue, created, equities)
        sheet.say("")
        sheet.say("rehearsal complete")
        completed = True
    except (AssertionError, SystemExit) as error:
        sheet.say("")
        sheet.say(f"STOPPED: {error}")
        raise
    finally:
        if not completed and not sheet.lines[-1].startswith("STOPPED"):
            sheet.say("")
            sheet.say("STOPPED before the end: the console shows why")
        sheet.save()


if __name__ == "__main__":
    main()
