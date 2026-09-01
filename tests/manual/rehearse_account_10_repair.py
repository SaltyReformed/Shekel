"""Rehearse the account-10 repair through the app's own HTTP doors.

The instrument behind plan step **balance:X-f3c-2b-2c**, whose product is a
REHEARSED runbook rather than a code change: findings **N-379** and **N-382**
are repaired by an owner clicking through the app, ruling **R-HJ** forbids a
migration writing those money rows, and what a runbook owes before anybody
performs it on real data is evidence that the doors it names accept the acts it
asks for, in the order it asks for them.
``docs/audits/balance_architecture/account_10_repair_runbook.md`` is the
runbook; this is what proves it.

**IT WRITES.**  Every other harness in this directory reads.  This one performs
the repair across three accounts, so it refuses any database it was not
explicitly pointed at, refuses the name ``shekel`` outright (what BOTH the
deployed database and the shared dev runtime are called), and refuses any clone
that is not in the exact PRE-repair state (:func:`_require_unrepaired`).

**It is not the repair and must never become it** (ruling **R-HJ**).  That
ruling rejected "a one-off ``scripts/`` routine driving the services" for the
repair itself: the acts have doors, an owner performs them, and a script that
did it instead would be dead code the moment it ran and would put a second
writer beside every door it drove.

**What is DERIVED and what is STATED, and both are RECONCILED.**  Every figure
the BANK knows is read out of the export at run time: the opening equity is its
own closing for the day the books open, the dividends are its
``DIVIDEND RECEIVED`` lines, and the final assertion is its last stated close.
What cannot be derived is which app row answers which bank line -- the owner's
judgement, ruled in **R-HJ**..**R-HM** -- so the transfer map is stated, and
then checked in BOTH directions before a single write (:func:`_reconcile`).

**Its first draft's reconciliation was one-directional and an adversarial
review broke it.**  Comparing only the STATED delta against the export let a
SWAPPED mapping through: transfers 154 and 156 exchanged, each still naming a
day the export names and an amount it moved, ran to completion and printed
every verification arm green while booking ``-$1,500.00`` on a day the bank
moved ``+$500.00``.  Reading each transfer's own amount from the database is
what closes THAT, which is why this reconciliation needs a connection and runs
after :func:`_connect` rather than before it -- and it closes swaps between
movements of DIFFERENT amounts only.  :func:`_reconcile` states the two limits
that remain.

**Usage** (from the repository root, against a throwaway clone of production
taken to the current alembic head)::

    DATABASE_URL=postgresql://.../shekel_rehearsal \\
        .venv/bin/python tests/manual/rehearse_account_10_repair.py \\
        --clone shekel_rehearsal \\
        --bank ~/Downloads/History_for_Account_Z29868989.csv

Then score the post-state against the same export::

    DATABASE_URL=postgresql://.../shekel_rehearsal \\
        .venv/bin/python tests/manual/measure_cutover_against_bank.py \\
        --account 10 --format fidelity \\
        --bank ~/Downloads/History_for_Account_Z29868989.csv
"""

import argparse
import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser

from app import create_app
from app.extensions import db, login_manager
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.user import User
from app.services import cash_ledger
from app.services.balance_at import BalanceContext, balance_at
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER

_ZERO_MONEY = Decimal("0.00")
_CENT = Decimal("0.01")

#: The day account 10's books open (ruling **R-HK**).  A DECISION -- the export
#: states a closing for every day it names and nothing in it says which day the
#: books should start on -- so it is stated here and its EQUITY is derived from
#: it.
OPENED_ON = date(2026, 3, 26)

#: The archived twin consolidated onto account 10, and the day its own balance
#: is superseded to zero (ruling **R-HK**).
TWIN_ACCOUNT = 2
TWIN_ASSERTED_ON = date(2026, 4, 6)

#: The account under repair, and the account its transfers move against.
SUBJECT_ACCOUNT = 10
CHECKING_ACCOUNT = 1

#: The duplicate ACH (**N-382**): one real transfer recorded twice, once into
#: the twin and once into account 10.  ``DUPLICATE`` is the row with no bank
#: line at all and simply goes; ``ABSORBED`` is the row whose arrival leg the
#: restated opening now holds, so its Checking side is re-recorded as a plain
#: expense and the transfer goes (ruling **R-HK**).  **Both are
#: template-linked, so both can only be SOFT-deleted** -- the limitation
#: **N-386** accepts, measured 2026-08-31 to bind on the second as well as the
#: first, which doubles that finding's standing exposure once this runs.
DUPLICATE_TRANSFER = 102
ABSORBED_TRANSFER = 1

#: What each DELETED transfer must be found to hold, reconciled against its own
#: row before anything is written.  **Stated because the two deletes were the
#: only acts in this repair whose subject was never read** (adversarial review,
#: 2026-09-01): ``_map_problems`` put both ids into ``mapped`` for census
#: membership and checked no figure, while ``_consolidate_twin`` re-recorded a
#: literal ``$500.00`` and ``_expected_class_moves`` expected the same literal
#: -- an equality whose two sides came from ONE constant and could not fail.
#: Both rows are LIVE template-linked recurrences, so their amount is a thing
#: that can drift between this file being written and the repair being
#: performed; if it does, the operator deletes one figure and re-records
#: another with every arm green.  ``(amount, from_account, to_account)``.
DELETED_TRANSFERS = {
    DUPLICATE_TRANSFER: (Decimal("500.00"), CHECKING_ACCOUNT, SUBJECT_ACCOUNT),
    ABSORBED_TRANSFER: (Decimal("500.00"), CHECKING_ACCOUNT, TWIN_ACCOUNT),
}

#: The absorbed transfer's amount, re-recorded as a plain Checking expense.
#: Read from :data:`DELETED_TRANSFERS` rather than spelled a second time, so
#: the figure the act books is the figure the reconciliation checked.
ABSORBED_LEG_AMOUNT = DELETED_TRANSFERS[ABSORBED_TRANSFER][0]

#: The day the absorbed transfer's Checking leg is re-recorded on, and the
#: category it keeps.  **It is NOT the bank's day and cannot be**: SECU posted
#: the ACH on 2026-03-26, which is the day Checking's own books open, and
#: ``cash_ledger._books`` refuses a movement on or before ``opened_on``
#: (ruling **R-HG**).  So the app's own 03-27 stands -- the one date this
#: repair does not move onto the bank's, and the runbook says so.
ABSORBED_LEG_DAY = date(2026, 3, 27)
EXPENSE_CATEGORY = ("Financial", "Emergency Fund")

#: The category the recorded dividends book to (ruling **R-HL**).
DIVIDEND_CATEGORY = ("Income", "Interest & Dividends")

#: ``ref.transaction_types`` ids, and the SETTLED status each type takes.
#: Income settles as Received and an expense as Paid; submitting the other one
#: is refused by the status seam, which is how the first draft found out.
_INCOME, _EXPENSE = 1, 2
_SETTLED_STATUS = {_INCOME: "3", _EXPENSE: "2"}

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
class _Movement:
    """One transfer the bank also records, and the day the bank records it on.

    Attributes:
        transfer_id: The ``budget.transfers`` row.
        bank_day: The day the export names for it.
        delta: What it did to account 10, signed.  CHECKED against the row
            itself and against the export before anything is written.
    """

    transfer_id: int
    bank_day: date
    delta: Decimal


#: Which app transfer answers which bank day, and which way the money moved for
#: account 10.  The mapping is the owner's judgement (**R-HK**); every figure in
#: it is reconciled against the export AND against the row itself by
#: :func:`_reconcile` before a single write.
MOVEMENTS = (
    _Movement(155, date(2026, 4, 9), Decimal("500.00")),
    _Movement(156, date(2026, 4, 23), Decimal("500.00")),
    _Movement(154, date(2026, 4, 29), Decimal("-1500.00")),
    _Movement(157, date(2026, 5, 7), Decimal("500.00")),
    _Movement(346, date(2026, 5, 14), Decimal("250.00")),
    _Movement(409, date(2026, 7, 23), Decimal("-2000.00")),
)


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

    def recorded_dividends(self) -> "list[tuple[date, Decimal]]":
        """Return the dividends this repair records, ascending.

        Returns:
            One ``(day, amount)`` per ``DIVIDEND RECEIVED`` day dated strictly
            after the books open.  The ones on or before it are inside the
            opening equity (ruling **R-HG**) and are not recorded -- on this
            export that is two of the seven lines it carries.
        """
        return [
            (day, self.dividends[day])
            for day in sorted(self.dividends) if day > OPENED_ON
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
    route arm that was dead in a browser.  Each act below fetches the form the
    owner opens, changes only what they type, and submits the rest exactly as
    rendered, including the version pin, the hidden ids and the selects' own
    current values.

    **Six divergences from a browser have been measured and fixed** over two
    review rounds, each of which had this parser posting something no browser
    would.  **Two of the six are reachable on the forms this file actually
    drives** -- the checkbox pair and the first-enabled-option fallback -- and
    the other four are not: none of the five driven templates renders a
    ``<textarea>`` or a ``<select multiple>``.  They are fixed anyway because
    the parser is the thing that makes "the payload comes from the page" true,
    and a parser correct only on today's five pages is a claim about the pages
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

    def __init__(self, app):
        """Forge the owner's session and open a client.

        Args:
            app: The Flask application, already configured for a rehearsal.
        """
        self.client = app.test_client()
        self.user_id = db.session.query(User).order_by(User.id).first().id
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


def _require_unrepaired() -> None:
    """Refuse a clone the repair has already been performed on.

    **Four preconditions, and what they catch is a PARTIALLY repaired clone.**
    A fully repaired one is refused one gate earlier: :func:`_reconcile`'s
    app-to-bank census finds the five recorded dividends answering no act and
    stops with zero submissions.  (An earlier draft of this docstring said the
    run reached the two CREATE acts and wrote a second ``$500.00`` expense
    before tripping -- true of the version before that census arm existed, and
    re-measured false on 2026-09-01.)  What has no other gate is a clone
    interrupted PART way: after act 1 alone the map still reconciles, and it is
    precondition 2 -- transfer 102 already deleted -- that refuses.  Nothing in
    the acts is idempotent, so refusing at act zero is the only safe answer,
    and it doubles as the safety rail a name check cannot be: a database this
    repair has touched is refused whatever it is called.

    Raises:
        SystemExit: When the clone is not in the pre-repair state, naming every
            precondition that failed rather than the first.
    """
    problems = []
    opening = cash_ledger.governing_account_opening(SUBJECT_ACCOUNT)
    if opening.opened_on == OPENED_ON:
        problems.append(
            f"account {SUBJECT_ACCOUNT}'s books already open {OPENED_ON}"
        )
    for transfer_id in (DUPLICATE_TRANSFER, ABSORBED_TRANSFER):
        transfer = db.session.get(Transfer, transfer_id)
        if transfer is None:
            problems.append(f"transfer {transfer_id} does not exist")
        elif transfer.is_deleted:
            problems.append(f"transfer {transfer_id} is already deleted")
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
    if problems:
        raise SystemExit(
            "this clone is not in the pre-repair state:\n  "
            + "\n  ".join(problems)
            + "\nRestore it from an unrepaired snapshot and run again."
        )
    print("precondition: the clone is unrepaired")


def _settled_rows(account_ids):
    """Return every live SETTLED transaction on *account_ids*.

    Args:
        account_ids: The accounts to census.

    Returns:
        One row per settled transaction: its id, account, day, figure and
        parent transfer.
    """
    return db.session.execute(db.text("""
        select t.id, t.account_id, t.settled_on, t.settled_amount,
               t.transfer_id
        from budget.transactions t
        join ref.statuses st on st.id = t.status_id
        where t.account_id = any(:ids) and t.is_deleted = false
          and st.is_settled
        order by t.settled_on, t.id
    """), {"ids": list(account_ids)}).all()


def _transfer_delta(transfer_id: int) -> "Decimal | None":
    """Return what a transfer does to the subject account, signed.

    Args:
        transfer_id: The ``budget.transfers`` row.

    Returns:
        Positive when the money arrives, negative when it leaves, or ``None``
        when the row does not exist, is deleted, or does not touch the account.
    """
    transfer = db.session.get(Transfer, transfer_id)
    if transfer is None or transfer.is_deleted:
        return None
    if transfer.to_account_id == SUBJECT_ACCOUNT:
        return transfer.amount
    if transfer.from_account_id == SUBJECT_ACCOUNT:
        return -transfer.amount
    return None


def _map_problems(export: _Export) -> "list[str]":
    """Return every disagreement between the stated map and the two records.

    Args:
        export: The parsed export.

    Returns:
        One sentence per disagreement, empty when the map reconciles.
    """
    problems: "list[str]" = []
    claimed: "dict[date, int]" = {}
    for movement in MOVEMENTS:
        actual = _transfer_delta(movement.transfer_id)
        if actual is None:
            problems.append(
                f"transfer {movement.transfer_id} does not exist, is deleted, "
                f"or does not touch account {SUBJECT_ACCOUNT}"
            )
        elif actual != movement.delta:
            problems.append(
                f"transfer {movement.transfer_id} moves {actual} on account "
                f"{SUBJECT_ACCOUNT} and the map says {movement.delta}"
            )
        if movement.bank_day not in export.closings:
            problems.append(
                f"transfer {movement.transfer_id} is mapped to "
                f"{movement.bank_day}, a day the export does not name"
            )
        elif export.moved_on(movement.bank_day) != movement.delta:
            problems.append(
                f"transfer {movement.transfer_id} is mapped to "
                f"{movement.bank_day}, where the export moved "
                f"{export.moved_on(movement.bank_day)} and the map says "
                f"{movement.delta}"
            )
        if movement.bank_day in claimed:
            problems.append(
                f"transfers {claimed[movement.bank_day]} and "
                f"{movement.transfer_id} both claim {movement.bank_day}"
            )
        claimed[movement.bank_day] = movement.transfer_id

    answered = set(claimed) | {day for day, _ in export.recorded_dividends()}
    problems.extend(
        f"the export names {day} (closing {export.closings[day]}, moved "
        f"{export.moved_on(day)}) and no act answers it"
        for day in export.named
        if day > OPENED_ON and day not in answered
    )

    for transfer_id, (amount, source, target) in DELETED_TRANSFERS.items():
        transfer = db.session.get(Transfer, transfer_id)
        if transfer is None or transfer.is_deleted:
            problems.append(
                f"transfer {transfer_id} is to be DELETED and does not exist "
                f"or is already deleted"
            )
            continue
        found = (transfer.amount, transfer.from_account_id,
                 transfer.to_account_id)
        if found != (amount, source, target):
            problems.append(
                f"transfer {transfer_id} is to be DELETED holding "
                f"{amount} from account {source} to {target}, and the row "
                f"holds {found[0]} from {found[1]} to {found[2]}"
            )

    mapped = (
        {movement.transfer_id for movement in MOVEMENTS}
        | set(DELETED_TRANSFERS)
    )
    problems.extend(
        f"account {row.account_id} carries a settled row (transaction "
        f"{row.id}, {row.settled_amount} on {row.settled_on}, transfer "
        f"{row.transfer_id}) that no act answers"
        for row in _settled_rows((TWIN_ACCOUNT, SUBJECT_ACCOUNT))
        if row.transfer_id not in mapped
    )
    return problems


def _reconcile(export: _Export) -> None:
    """Refuse to start unless the stated map matches BOTH records.

    Five arms, and the first is what the one-directional version lacked:

    * every mapped transfer EXISTS, is live, and moves the subject account by
      the amount and in the direction the map states -- read off the row;
    * that amount equals what the export moved on the day it is mapped to;
    * no two movements claim one bank day;
    * every day the export names after the books open is answered by exactly
      one act -- a dividend it records or a transfer in the map;
    * every SETTLED row the two accounts already hold is answered by the map,
      by the duplicate, or by the absorbed transfer.

    **The last two run in opposite directions on purpose.**  A census that only
    walks the bank claims the app rows nobody looked at, and one that only
    walks the app claims the bank lines nobody looked at; this project has
    measured a set defined by SUBTRACTION claiming members it never counted.
    Both directions closed is what makes "the export is the census" true.

    **Two limits, stated because a control whose edges are unstated reads as
    stronger than it is** (adversarial review, 2026-09-01).  Reading each row's
    amount closes a swap between movements of DIFFERENT amounts; it cannot see
    a PERMUTATION among identical ones.  The three ``+$500.00`` arrivals
    (transfers 155, 156, 157) can be cycled among 2026-04-09, 04-23 and 05-07
    and every arm passes -- and so does the bank comparison afterwards, because
    the same money lands on the same days.  What such a cycle does leave behind
    is a row filed in a pay PERIOD that no longer contains its settle day,
    since a re-date moves ``settled_on`` and never ``pay_period_id``; that is
    an attribution defect the daily fold cannot see and this reconciliation
    cannot either.  Second: the app-side census covers accounts
    ``TWIN_ACCOUNT`` and ``SUBJECT_ACCOUNT`` only.  Checking is the other
    endpoint of every transfer here and is NOT censused, so a stray settled row
    on it would pass.

    Args:
        export: The parsed export.

    Raises:
        SystemExit: On any disagreement, naming the day and both figures.
    """
    problems = _map_problems(export)
    if problems:
        raise SystemExit(
            "the stated map does not reconcile:\n  " + "\n  ".join(problems)
        )
    bank_days = [day for day in export.named if day > OPENED_ON]
    print(f"map reconciles both ways: {len(MOVEMENTS)} transfers and "
          f"{len(export.recorded_dividends())} dividends answer all "
          f"{len(bank_days)} bank days after {OPENED_ON}, and every settled "
          f"row on accounts {TWIN_ACCOUNT} and {SUBJECT_ACCOUNT} is answered")


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

    Every column that could carry money or attribution is in the digest, and
    the ORDER is fixed by ``ap.id`` so two equal ledgers cannot hash
    differently for a row-order reason.

    Returns:
        A hex SHA-256 over the posted ledger.
    """
    rows = db.session.execute(db.text("""
        select ap.id, ap.journal_entry_id, ap.ledger_account_id,
               ap.posting_kind_id, ap.amount, je.entry_date, je.pay_period_id
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
    export: _Export, modelled_before: Decimal,
) -> "dict[str, Decimal]":
    """Return the class movements this repair's own inputs DERIVE.

    Three of the six are computable from the export and the pre-state, so they
    are asserted rather than printed:

    * **Expense** rises by the Checking leg the absorbed transfer is
      re-recorded as -- and by nothing else, because no other act touches an
      expense row.  **That rise is real spending that did not happen**: the
      deleted transfer's leg posted as a ``transfer`` and touched no expense
      row at all, so the income statement gains a line the owner never spent.
      Ruling **R-HK** names the act and not this consequence; it is reported
      rather than repaired here, and the runbook says so;
    * **Income** falls by every dividend recorded, and RISES by the modelled
      interest those dividends replace -- the corrections that were standing in
      for them, which go to zero (ruling **R-HL**);
    * **Liability** and **Unrealized** do not move at all: no act touches a
      loan or a market value.

    Asset and Equity are NOT derived here.  Both move by the two restatements,
    whose figures are the repair's own subject rather than an input to it, and
    an expectation computed the way the acts compute it would be an equality
    with one head.

    Args:
        export: The parsed export, for the dividends recorded.
        modelled_before: The subject account's ``interest_income`` total before
            the repair, LEDGER-NATIVE (a credit, so negative).

    Returns:
        ``{class name: expected change}`` for the classes this can derive.
    """
    dividends = sum(
        (amount for _, amount in export.recorded_dividends()), _ZERO_MONEY,
    )
    return {
        "Expense": Decimal("500.00"),
        "Income": -dividends - modelled_before,
        "Liability": _ZERO_MONEY,
        "Unrealized": _ZERO_MONEY,
    }


def _snapshot(export: _Export, label: str) -> None:
    """Print what the three touched accounts show, and what corrections stand.

    **After every act, because that is what tells a half-done repair from a
    finished one.**  The acts are separate transactions and an owner may stop
    between them (with one exception the runbook names), so a record that
    described only the end state would leave them no way to answer "did act 3
    land?".  It is also the honest place to see that most acts move a
    CORRECTION rather than a displayed balance.

    **Valued at the export's LAST DAY rather than at today**, which is what
    makes two rehearsals comparable: an interest-bearing account accrues, so
    "the balance now" is a different number every day it is run.

    Args:
        export: The parsed export, for the day to value at.
        label: What just happened.
    """
    db.session.expire_all()
    subject = db.session.get(Account, SUBJECT_ACCOUNT)
    context = BalanceContext.build(
        user_id=subject.user_id, as_of=export.named[-1],
    )
    figures = "  ".join(
        f"a{account_id}="
        f"{balance_at(db.session.get(Account, account_id), context, export.named[-1])}"
        for account_id in (CHECKING_ACCOUNT, TWIN_ACCOUNT, SUBJECT_ACCOUNT)
    )
    print(f"    [{label:<20}] {figures}  "
          f"a{SUBJECT_ACCOUNT} corrections="
          f"{_account_trueup_total(SUBJECT_ACCOUNT)}")


def _restate_opening(op: _Operator, account_id: int, equity: Decimal) -> None:
    """Restate one account's books through the edit page's own card.

    Args:
        op: The owner's session.
        account_id: The account to restate.
        equity: What its books opened holding.

    Raises:
        AssertionError: When the door did not move the governing opening.
    """
    form = op.form_posting_to(
        f"/accounts/{account_id}/edit", f"/accounts/{account_id}/opening",
    )
    op.submit(form, {
        "opened_on": OPENED_ON.isoformat(), "opening_equity": str(equity),
    })
    db.session.expire_all()
    opening = cash_ledger.governing_account_opening(account_id)
    assert (opening.opened_on, opening.opening_equity) == (OPENED_ON, equity), \
        (f"account {account_id} books open {opening.opened_on} at "
         f"{opening.opening_equity}, not {OPENED_ON} at {equity}")
    print(f"  account {account_id} books open {opening.opened_on} holding "
          f"{opening.opening_equity}")


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


def _period_holding(day: date) -> int:
    """Return the pay period whose span contains *day*.

    Args:
        day: A civil day.

    Returns:
        The ``budget.pay_periods`` id.
    """
    return db.session.execute(db.text(
        "select id from budget.pay_periods "
        "where :day between start_date and end_date"
    ), {"day": day}).scalar_one()


def _record(
    op: _Operator, *, account_id: int, category_id: int, type_id: int,
    day: date, amount: Decimal,
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
    Posting the day WITH the settle rehearsed a payload no page emits, which is
    the class this file's own parser exists to avoid.

    **Between the second and third submissions the row is live in the fold at
    TODAY'S date**, for its full figure: measured 2026-09-01, account 10 reads
    ``$3,689.86`` at that moment against ``$3,666.11`` at the export's last
    day.  It is transient and inside one act, so "you may stop between acts"
    does not cover it -- which is why the runbook tells an operator to finish a
    row rather than leave one half-recorded.

    Args:
        op: The owner's session.
        account_id: The account the row belongs to.
        category_id: Its category.
        type_id: ``ref.transaction_types`` -- income or expense.
        day: The day the money moved.
        amount: What moved.

    Returns:
        The new transaction's id.

    Raises:
        AssertionError: When the create renders no cell id, when a Projected
            card already offers a settle-day box (which would mean this
            three-step path is describing a page that no longer exists), or
            when the settle did not record the day and the figure.
    """
    form = op.form(
        f"/transactions/new/full?category_id={category_id}"
        f"&period_id={_period_holding(day)}&account_id={account_id}"
        f"&transaction_type_id={type_id}"
    )
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
    op.submit(settle, {"status_id": _SETTLED_STATUS[type_id]})

    correct = op.form(f"/transactions/{txn_id}/full-edit")
    fields = _payload(correct)
    assert "settled_on" in fields, \
        f"transaction {txn_id} settled but its card renders no settle-day box"
    # Only the DAY makes this third save necessary.  The settle above already
    # stamped ``settled_amount`` from the estimate, so re-posting the figure
    # changes nothing -- it is submitted because the card renders it and an
    # untouched Save posts what it renders, not because it is the correction.
    # The box is present only when the row is ALSO ``correctable`` (a purchase
    # envelope derives its figure and renders none), which is what the guard
    # is for.
    typed = {"settled_on": day.isoformat()}
    if "settled_amount" in fields:
        typed["settled_amount"] = str(amount)
    op.submit(correct, typed)

    db.session.expire_all()
    txn = db.session.get(Transaction, txn_id)
    assert (txn.settled_on, txn.settled_amount) == (day, amount), \
        (f"transaction {txn_id} records {txn.settled_amount} on "
         f"{txn.settled_on}, not {amount} on {day}")
    return txn_id


def _shadow_of(transfer_id: int, account_id: int) -> int:
    """Return one leg of a transfer, the cell the owner opens it from.

    Args:
        transfer_id: The transfer.
        account_id: Which endpoint's shadow to open.

    Returns:
        The shadow transaction's id.
    """
    return db.session.execute(
        db.select(Transaction.id).filter_by(
            transfer_id=transfer_id, account_id=account_id, is_deleted=False,
        )
    ).scalar_one()


def _redate(op: _Operator, movement: _Movement) -> None:
    """Move one transfer's settle day onto the bank's, through the popover.

    Args:
        op: The owner's session.
        movement: The transfer and the day the bank names.

    Raises:
        AssertionError: When the popover renders no settle-day box, or the day
            did not move.
    """
    txn_id = _shadow_of(movement.transfer_id, SUBJECT_ACCOUNT)
    was = db.session.get(Transaction, txn_id).settled_on
    form = op.form(f"/transactions/{txn_id}/full-edit")
    assert "settled_on" in _payload(form), \
        f"transfer {movement.transfer_id}'s card renders no settle-day box"
    op.submit(form, {"settled_on": movement.bank_day.isoformat()})
    db.session.expire_all()
    now = db.session.get(Transaction, txn_id).settled_on
    assert now == movement.bank_day, \
        f"transfer {movement.transfer_id} settles {now}, not {movement.bank_day}"
    print(f"  transfer {movement.transfer_id}: {was} -> {now}")


def _delete_transfer(op: _Operator, transfer_id: int) -> None:
    """Delete one transfer and assert it actually went.

    Args:
        op: The owner's session.
        transfer_id: The transfer to delete.

    Raises:
        AssertionError: When the row is still live afterwards.  The delete door
            answers ``200`` for an already-deleted row, so the follow-up read
            is the only thing that separates a delete from a no-op.
    """
    op.send("delete", f"/transfers/instance/{transfer_id}", {})
    db.session.expire_all()
    transfer = db.session.get(Transfer, transfer_id)
    assert transfer is not None and transfer.is_deleted, \
        f"transfer {transfer_id} is not deleted"
    print(f"  transfer {transfer_id} deleted (soft; N-386)")


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


def _redate_all(op: _Operator) -> None:
    """Move every mapped transfer that is not already on its bank day.

    **Which transfer needs no move is MEASURED, not positional.**  An earlier
    draft skipped ``MOVEMENTS[0]`` by its place in the tuple, so re-ordering
    the map would have silently skipped a different transfer and left the one
    it named unasserted.

    Args:
        op: The owner's session.
    """
    for movement in MOVEMENTS:
        settled = db.session.get(
            Transaction, _shadow_of(movement.transfer_id, SUBJECT_ACCOUNT),
        ).settled_on
        if settled == movement.bank_day:
            print(f"  transfer {movement.transfer_id} already settles "
                  f"{settled}; nothing to move")
            continue
        _redate(op, movement)


def _consolidate_twin(op: _Operator) -> None:
    """Delete the absorbed transfer, re-record its Checking leg, zero the twin.

    **The twin is UNARCHIVED for its own two acts and archived again after,
    because while it is archived neither door has a click path** (finding
    **N-430**, measured 2026-08-31).  The cockpit's archived region offers
    Unarchive and Delete and nothing else -- its own template says "archived
    accounts have no cell kebab or edit-form reach in the cockpit" -- and no
    other surface links the edit page or the detail page for one.  Both doors
    ACCEPT the write when reached directly, so this is reach and not
    capability; the runbook prescribes the round trip rather than a typed URL
    because every other act in it is a click, and the round trip moves no money
    (measured: a byte-identical postings fingerprint either side).

    Args:
        op: The owner's session.
    """
    _delete_transfer(op, ABSORBED_TRANSFER)
    expense = _record(
        op, account_id=CHECKING_ACCOUNT,
        category_id=_category_id(op.user_id, *EXPENSE_CATEGORY),
        type_id=_EXPENSE, day=ABSORBED_LEG_DAY, amount=ABSORBED_LEG_AMOUNT,
    )
    print(f"  Checking expense {expense}: {ABSORBED_LEG_AMOUNT} on "
          f"{ABSORBED_LEG_DAY}")
    _set_archived(op, TWIN_ACCOUNT, archived=False)
    _restate_opening(op, TWIN_ACCOUNT, _ZERO_MONEY)
    _assert_balance(op, TWIN_ACCOUNT, _ZERO_MONEY, TWIN_ASSERTED_ON)
    _set_archived(op, TWIN_ACCOUNT, archived=True)


def _record_dividends(op: _Operator, export: _Export) -> None:
    """Create the income category, then record every dividend the bank states.

    Args:
        op: The owner's session.
        export: The parsed export.
    """
    # The group control on this form is a hidden input whose value SCRIPT sets
    # from a select carrying no name of its own, so what the page renders is
    # whichever group happens to be first.  Typing the group is exactly what
    # the owner does, so it is supplied here rather than scraped -- and said
    # out loud, because a payload that overrides what a page rendered is the
    # thing this file's parser exists to avoid doing silently.
    form = op.form_posting_to("/settings?section=categories", "/categories")
    op.submit(form, {
        "group_name": DIVIDEND_CATEGORY[0], "item_name": DIVIDEND_CATEGORY[1],
    })
    category_id = _category_id(op.user_id, *DIVIDEND_CATEGORY)
    print(f"  category {category_id}: {DIVIDEND_CATEGORY[0]}: "
          f"{DIVIDEND_CATEGORY[1]}")
    for day, amount in export.recorded_dividends():
        txn_id = _record(
            op, account_id=SUBJECT_ACCOUNT, category_id=category_id,
            type_id=_INCOME, day=day, amount=amount,
        )
        print(f"  dividend {txn_id}: {amount} on {day}")


def _perform(op: _Operator, export: _Export) -> None:
    """Perform the repair in R-HL's order -- delete, re-date, restate, record.

    **The order is enforced, and WHERE the refusal lands moved when this file
    started following the operator's real path** (adversarial review,
    2026-09-01).  Recording the 2026-03-31 dividend before act 3 takes three
    submissions, and the first TWO are accepted: the create succeeds, and the
    settle succeeds because it stamps ``display_today()`` rather than the day
    the operator means.  Only the THIRD -- correcting the day to 2026-03-31 --
    meets the books boundary and is refused.  Measured on a pre-repair clone:
    the row is left SETTLED as ``Received`` for ``$13.35``, dated the day the
    operator is working, live in the fold.  So attempting act 5 early does not
    leave an inert Projected row to delete; it leaves recorded income on the
    wrong day, and the runbook says so in those words.

    The rest of the order is about what the intermediate states say: recording
    a dividend first leaves the 2026-05-01 assertion booking a negative
    correction against the account's interest income until the restatement
    lands.

    Args:
        op: The owner's session.
        export: The parsed export, which supplies every bank figure.
    """
    print(f"balances below are valued at {export.named[-1]}, the last day the "
          f"export states")
    _snapshot(export, "before act 1")

    print("act 1 -- delete the duplicate ACH (N-382)")
    _delete_transfer(op, DUPLICATE_TRANSFER)
    _snapshot(export, "after act 1")

    print("act 2 -- re-date onto the bank's own days (N-379)")
    _redate_all(op)
    _snapshot(export, "after act 2")

    print("act 3 -- restate account 10's books (R-HK)")
    _restate_opening(op, SUBJECT_ACCOUNT, export.closings[OPENED_ON])
    _snapshot(export, "after act 3")

    print("act 4 -- consolidate the archived twin onto account 10 (R-HK)")
    _consolidate_twin(op)
    _snapshot(export, "after act 4")

    print("act 5 -- record the dividends the app has never held (R-HL)")
    _record_dividends(op, export)
    _snapshot(export, "after act 5")

    print("act 6 -- assert the bank's last stated close (R-HM)")
    last = export.named[-1]
    _assert_balance(op, SUBJECT_ACCOUNT, export.closings[last], last)
    _snapshot(export, "after act 6")


def _verify(
    export: _Export, before: "dict[str, Decimal]", modelled_before: Decimal,
    checking_before: Decimal,
) -> None:
    """Assert the post-state the repair exists to reach, and print it.

    Seven assertions, and none of them is the balance comparison -- that is
    ``measure_cutover_against_bank.py``'s question, asked with the same export
    by the command this file's docstring names.  What is asked here is whether
    the ACTS landed.

    Args:
        export: The parsed export.
        before: :func:`_class_totals` captured before the first act.
        modelled_before: The subject account's ``interest_income`` total before
            the first act.
        checking_before: :func:`_account_trueup_total` for CHECKING before the
            first act, so the arm can assert the CHANGE rather than a level.

    Raises:
        AssertionError: On any of the seven.
    """
    db.session.expire_all()
    after = _class_totals()
    expected = _expected_class_moves(export, modelled_before)
    print("  posted ledger, by class:")
    for name in sorted(set(before) | set(after)):
        was = before.get(name, _ZERO_MONEY)
        now = after.get(name, _ZERO_MONEY)
        want = expected.get(name)
        said = "" if want is None else (
            f"   expected {want:>+10}" if now - was == want
            else f"   EXPECTED {want:>+10}"
        )
        print(f"    {name:<12}{was:>14} -> {now:>14}  {now - was:>+13}{said}")
    for name, want in expected.items():
        moved = after.get(name, _ZERO_MONEY) - before.get(name, _ZERO_MONEY)
        assert moved == want, (
            f"the {name} class moved {moved}, not the {want} this repair's "
            "own inputs derive"
        )

    subject = db.session.get(Account, SUBJECT_ACCOUNT)
    context = BalanceContext.build(
        user_id=subject.user_id, as_of=export.named[-1],
    )
    twin = balance_at(
        db.session.get(Account, TWIN_ACCOUNT), context, export.named[-1],
    )
    assert twin == _ZERO_MONEY, f"the twin still holds {twin}"
    print(f"  account {TWIN_ACCOUNT} holds {twin}")

    corrections = _account_trueup_total(SUBJECT_ACCOUNT)
    assert corrections == _ZERO_MONEY, (
        f"account {SUBJECT_ACCOUNT}'s posted corrections total {corrections}, "
        "so at least one assertion is not explained by the records"
    )
    print(f"  account {SUBJECT_ACCOUNT}'s posted corrections total "
          f"{corrections}")

    # **CHECKING, the third account this repair touches and the one nothing
    # used to check** (adversarial review, 2026-09-01).  Its displayed balance
    # never moves -- its own 2026-07-31 assertion resets the fold above
    # everything here -- so the repair's whole effect on it is a change in what
    # its records EXPLAIN, which is exactly what the correction total measures.
    # The expected figure is DERIVED, not quoted: deleting the duplicate
    # removes a ``$500.00`` outflow that never happened, so the ledger now
    # holds that much more and the corrections shrink by it.  The absorbed
    # transfer nets to zero here -- its leg leaves as a transfer and returns as
    # an expense of the same amount on the same account.
    checking_moved = _account_trueup_total(CHECKING_ACCOUNT) - checking_before
    assert checking_moved == -DELETED_TRANSFERS[DUPLICATE_TRANSFER][0], (
        f"account {CHECKING_ACCOUNT}'s corrections moved {checking_moved}, "
        f"not the {-DELETED_TRANSFERS[DUPLICATE_TRANSFER][0]} the deleted "
        "duplicate derives"
    )
    print(f"  account {CHECKING_ACCOUNT}'s corrections moved "
          f"{checking_moved:+}")

    modelled = _interest_income_total(SUBJECT_ACCOUNT)
    assert modelled == _ZERO_MONEY, \
        f"the modelled interest row still carries {modelled}"
    print(f"  account {SUBJECT_ACCOUNT}'s modelled interest row carries "
          f"{modelled}")


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
    """Reconcile the map, perform the repair and verify the post-state."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--clone", required=True,
        help="the throwaway database this rehearsal writes to",
    )
    parser.add_argument(
        "--bank", required=True,
        help="path to the Fidelity history CSV export",
    )
    args = parser.parse_args()
    export = _Export.read(args.bank)
    print(f"bank file: {len(export.named)} days, {export.named[0]} .. "
          f"{export.named[-1]}, {len(export.dividends)} dividend day(s), "
          f"{len(export.recorded_dividends())} of them recorded by this repair")
    app = _connect(args.clone)
    with app.app_context():
        _require_unrepaired()
        _reconcile(export)
        before = _class_totals()
        modelled_before = _interest_income_total(SUBJECT_ACCOUNT)
        checking_before = _account_trueup_total(CHECKING_ACCOUNT)
        operator = _Operator(app)
        _perform(operator, export)
        print(f"{operator.acts} door acts performed; verifying")
        _verify(export, before, modelled_before, checking_before)
    print("rehearsal complete")


if __name__ == "__main__":
    main()
