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


def build(rows, *, with_totals=True, totals=None, balance_as_of="08/16/2026",
          stated_balance="1000.00", header=None):
    """Return a complete SECU-shaped CSV as bytes.

    Args:
        rows: Data rows, in the order they should appear IN THE FILE.  Real
            exports are newest-first, and passing them that way is what
            exercises the adapter's reversal.
        with_totals: Whether to append the summary row.
        totals: An explicit summary row, overriding the computed one.
        balance_as_of: The date in the file's second header line.
        stated_balance: The figure in that line.  Deliberately allowed to
            disagree with the lines, because it does on a real export.
        header: Override the column header row.

    Returns:
        The file's bytes: UTF-8, no BOM, LF line endings, as SECU writes them.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Checking", ACCOUNT_NUMBER])
    writer.writerow([f"Balance as of {balance_as_of}", stated_balance])
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
