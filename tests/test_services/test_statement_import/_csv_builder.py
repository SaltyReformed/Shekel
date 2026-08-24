"""Build SECU-shaped CSV payloads for the importer's tests.

**Shaped from the developer's real export rather than invented**, because a
fixture that does not reproduce the file's actual quirks cannot grade a parser
written for them: the two header lines before the column row, the masked account
number beside the account NAME, the newest-first ordering, the separate Credit
and Debit columns with the debit pre-signed, the optional running-balance
column, and the trailing ``Totals:`` summary that is not a transaction.

**The byte shape is the real one, and it was wrong here first.**  An adversarial
review compared this builder against the two exports on disk: they carry NO
byte-order mark and LF line endings, where this wrote a BOM and CRLF and its
docstring claimed that was "as SECU writes it".  Nothing failed -- the parser
decodes ``utf-8-sig`` and ``csv`` is indifferent -- so the fixture was merely
stricter than reality, which is the direction that hides nothing but still made
the docstring false.  It writes what the bank writes now.

The builder writes the summary CORRECTLY by default (its counts and sums are
computed from the rows given), so a test that wants a file disagreeing with
itself has to say so explicitly -- which keeps "the summary matches" from being
an accident of the fixture.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

#: The masked account number SECU writes, as it appears in the real export.
ACCOUNT_NUMBER = "******3820"

#: The account NAME SECU writes beside it.  Part of the identity the adapter
#: records, because the mask alone cannot tell two of one owner's accounts
#: apart when their numbers end alike.
ACCOUNT_NAME = "Checking"

#: What the adapter returns as "what this file calls its account".
ACCOUNT_IDENTITY = f"{ACCOUNT_NAME} {ACCOUNT_NUMBER}"

#: The column header row, verbatim from a real export with running balances.
HEADER = [
    "Date", "Account", "Account Number", "Account Type", "Description",
    "Check #", "Category", "Memo", "Credit", "Debit", "Running Balance",
]


def row(day, amount, description, category="", memo="", running=None,
        account_number=ACCOUNT_NUMBER, account_name=ACCOUNT_NAME):
    """Return one data row in SECU's own column order.

    Args:
        day: ``date`` the line posted.
        amount: Signed ``Decimal`` or string, positive INTO the account.
        description: What the bank called it.
        category: The bank's own category cell.
        memo: The memo cell (empty on every real data row measured).
        running: The running balance after this line, or ``None`` to omit the
            column's value.
        account_number: What the file calls the account.

    Returns:
        The row as a list of cells.
    """
    value = Decimal(str(amount))
    return [
        day.strftime("%m/%d/%Y"),
        account_name,
        account_number,
        "Checking",
        description,
        "",
        category,
        memo,
        f"{value:.2f}" if value > 0 else "",
        f"{value:.2f}" if value < 0 else "",
        "" if running is None else f"{Decimal(str(running)):.2f}",
    ]


def totals_row(rows, item_count=None, credit=None, debit=None):
    """Return the trailing summary row.

    Computed from *rows* unless a test overrides a figure, so a file that
    disagrees with its own summary is always a deliberate construction.

    Args:
        rows: The data rows the summary describes.
        item_count: Override the stated count.
        credit: Override the stated credit total.
        debit: Override the stated debit total.

    Returns:
        The summary row as a list of cells.
    """
    # Tolerant of an unparseable cell: several tests plant a malformed amount
    # deliberately, and the summary is scaffolding for those rather than their
    # subject.  The adapter refuses the row before it reads the summary.
    amounts = []
    for cell in rows:
        try:
            value = Decimal(cell[8] or cell[9])
        except (InvalidOperation, ValueError):
            continue
        # NaN CONSTRUCTS without raising and then raises on comparison, which
        # is the very defect one of the adapter tests plants -- so the fixture
        # has to skip it here or it cannot build the file that proves it.
        if value.is_finite():
            amounts.append(value)
    credit_total = (
        sum((a for a in amounts if a > 0), Decimal("0.00"))
        if credit is None else Decimal(str(credit))
    )
    debit_total = (
        sum((a for a in amounts if a < 0), Decimal("0.00"))
        if debit is None else Decimal(str(debit))
    )
    count = len(rows) if item_count is None else item_count
    return [
        "", "", "", "", "", "", "Totals:", f"{count} items",
        f"{credit_total:.2f}", f"{debit_total:.2f}", "",
    ]


#: The header day a file gets when its own rows state no readable date -- the
#: shape a test planting a malformed Date cell builds.
DEFAULT_BALANCE_AS_OF = "08/16/2026"


def _implied_as_of(rows):
    """Return the LATEST day *rows* state, in SECU's own format, or ``None``.

    A bank writes its balance as of the moment it exported, so the header's day
    is at or after the file's last line on every real export.  Deriving it
    keeps a fixture from building the one file no bank can write -- a balance
    stated for a day the statement has not reached, which
    ``ck_statement_imports_effective_day_within_file`` refuses.

    Args:
        rows: Data rows in FILE order.

    Returns:
        ``MM/DD/YYYY`` for the latest parseable Date cell, or ``None`` when no
        row carries one -- which is a file a test built to be refused, and
        inventing a day for it would be the fixture deciding its subject.
    """
    days = []
    for cell in rows:
        try:
            month, day, year = (int(part) for part in str(cell[0]).split("/"))
        except (ValueError, IndexError):
            continue
        days.append((year, month, day))
    if not days:
        return None
    year, month, day = max(days)
    return f"{month:02d}/{day:02d}/{year:04d}"


#: The header figure a file with no running-balance chain gets by default.
#: Arbitrary and free to be: with no chain and no recorded history nothing can
#: contradict it, so :func:`~app.services.statement_import.resolve_anchor`
#: records it as ``assumed_last_day`` whatever it says.
DEFAULT_STATED_BALANCE = "1000.00"


def _implied_closing(rows):
    """Return the closing balance *rows* state, or ``None`` when they state none.

    Args:
        rows: Data rows in FILE order, newest first.

    Returns:
        The chronologically LAST row's running balance -- which is the file's
        own closing -- or ``None`` when the rows carry no running-balance
        column.  Rows are newest-first, so the last chronologically is the
        FIRST cell here.
    """
    if not rows or len(rows[0]) <= 10 or not str(rows[0][10]).strip():
        return None
    return str(rows[0][10]).strip()


def build(rows, *, with_totals=True, totals=None, balance_as_of=None,
          stated_balance=None, header=None):
    """Return a complete SECU-shaped CSV as bytes.

    Args:
        rows: Data rows, in the order they should appear IN THE FILE.  Real
            exports are newest-first, and passing them that way is what
            exercises the adapter's reversal.
        with_totals: Whether to append the summary row.
        totals: An explicit summary row, overriding the computed one.
        balance_as_of: The date in the file's second header line.  ``None``,
            the default, takes the LATEST day the rows themselves state, so
            the header cannot claim a balance for a day the statement has
            not reached.
        stated_balance: The figure in that line.  ``None``, the default, makes
            the file SELF-CONSISTENT: the header takes the closing the rows'
            own running-balance chain implies, or
            :data:`DEFAULT_STATED_BALANCE` when they carry no chain.  Pass a
            figure to plant a disagreement deliberately.
        header: Override the column header row.

    Returns:
        The file's bytes: UTF-8, no BOM, LF line endings, as SECU writes them.

    **The default changed at plan step ``bank_import:X-f6e-1``, and it was
    scaffolding rather than an assertion that changed.**  It used to be a flat
    ``"1000.00"`` on the reasoning that a header *"is deliberately allowed to
    disagree with the lines, because it does on a real export"*.  Measured on
    the developer's nine real exports, that is backwards: **eight state a
    balance that follows exactly from their own lines**, and the ninth
    (2026-08-16) states 2026-08-13's closing over a file listing two 08-14
    lines -- a lag the anchor solve now RESOLVES rather than tolerates
    (ruling **R-GF**).  So a chained fixture whose header said ``1000.00``
    was not modelling a real export; it was modelling a file no bank writes,
    and the door now refuses it correctly.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    stated = (
        (_implied_closing(rows) or DEFAULT_STATED_BALANCE)
        if stated_balance is None else stated_balance
    )
    as_of = (
        (_implied_as_of(rows) or DEFAULT_BALANCE_AS_OF)
        if balance_as_of is None else balance_as_of
    )
    writer.writerow(["Checking", ACCOUNT_NUMBER])
    writer.writerow([f"Balance as of {as_of}", stated])
    writer.writerow(HEADER if header is None else header)
    for data_row in rows:
        writer.writerow(data_row)
    if with_totals:
        writer.writerow(totals if totals is not None else totals_row(rows))
    return buffer.getvalue().encode("utf-8")


def chained(start_balance, entries, account_number=ACCOUNT_NUMBER,
            account_name=ACCOUNT_NAME, with_running=True):
    """Return rows whose running balances FOLLOW from a starting balance.

    The convenience most tests want: give it an opening balance and
    ``(day, amount, description)`` triples in CHRONOLOGICAL order and it
    produces newest-first rows with a correct chain, so a test about something
    else is not silently also a test about arithmetic.

    Args:
        start_balance: The balance before the first entry.
        entries: ``(day, amount, description)`` triples, oldest first.
        account_number: What the file calls the account.
        account_name: The account's name cell.
        with_running: Whether to write the running-balance column.  False
            reproduces the 10-column export, which is what SECU gives you
            unless the option is ticked -- and is the file the developer
            actually downloaded.

    Returns:
        Rows in FILE order (newest first).
    """
    balance = Decimal(str(start_balance))
    built = []
    for day, amount, description in entries:
        balance += Decimal(str(amount))
        built.append(row(
            day, amount, description,
            running=balance if with_running else None,
            account_number=account_number, account_name=account_name,
        ))
    built.reverse()
    return built
