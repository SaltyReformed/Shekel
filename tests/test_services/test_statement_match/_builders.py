"""The rows these tests match against, built without the code under test.

``seed_user`` creates an account, a scenario and categories and **no
transaction at all** -- a fact this arc has already paid for once, when a test
in the previous leaf compared ``[] == []`` and reported it as proof that
nothing moved.  So every test here builds its own rows, through the ORM rather
than through the services the accept door calls, so a broken settle verb cannot
also break the fixture that would have caught it.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import (
    SettlementBasisEnum,
    StatementSourceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate


def a_transaction(
    seed_user,
    *,
    name="Electricity",
    amount="180.00",
    income=False,
    settled_on=None,
    status=StatusEnum.PROJECTED,
    is_envelope=False,
    period=None,
):
    """Stage and return one transaction on the seeded checking account.

    Args:
        seed_user: The seeded user bundle.
        name: The row's name; also its template's, so each call is distinct.
        amount: Its estimated amount, as a string.
        income: Whether it is an income row (positive into the account).
        settled_on: Its recorded settle day, or ``None``.  A row carrying one
            also gets the SETTLEMENT RECORD the schema now requires beside it
            (plan step ``balance:X-au-c3``): ``ck_transactions_settle_day_needs
            _basis`` refuses a settle day with no basis, so a fixture writing
            the day alone is a row production cannot produce.  ``derived`` is
            the basis a settle through the ordinary door writes, and the figure
            is the row's own estimate -- which is what
            ``transaction_service.settle_transaction`` resolves for a row that
            owns its amount.
        status: Its status.
        is_envelope: Whether it tracks purchases.
        period: The pay period to file it under; the bootstrap one by default.

    Returns:
        The staged :class:`~app.models.transaction.Transaction`.
    """
    type_id = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE,
    )
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=type_id,
        name=name,
        default_amount=Decimal(amount),
        is_envelope=is_envelope,
    )
    db.session.add(template)
    db.session.flush()
    txn = Transaction(
        template_id=template.id,
        pay_period_id=(period or seed_user["bootstrap_period"]).id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=ref_cache.status_id(status),
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=type_id,
        estimated_amount=Decimal(amount),
        is_envelope=is_envelope,
        settled_on=settled_on,
        settled_amount=Decimal(amount) if settled_on else None,
        settled_basis_id=(
            ref_cache.settlement_basis_id(SettlementBasisEnum.DERIVED)
            if settled_on else None
        ),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def a_purchase(
    seed_user, parent, *, amount="25.00", description="Kroger",
    purchased_on=None, settled_on=None, is_credit=False,
):
    """Stage and return one purchase under *parent*.

    Args:
        seed_user: The seeded user bundle.
        parent: The envelope transaction it belongs to.
        amount: Its amount, as a string.
        description: What it is called.
        purchased_on: The day it was made.
        settled_on: The day the bank took it, or ``None``.
        is_credit: Whether it went on a card (and so never touches checking).

    Returns:
        The staged :class:`~app.models.transaction_entry.TransactionEntry`.
    """
    entry = TransactionEntry(
        transaction_id=parent.id,
        account_id=parent.account_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or seed_user["bootstrap_period"].start_date,
        settled_on=settled_on,
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def an_import(seed_user, account=None):
    """Stage and return one statement import for an account.

    Args:
        seed_user: The seeded user bundle.
        account: The account; the seeded checking one by default.

    Returns:
        The staged :class:`~app.models.statement_import.StatementImport`.
    """
    statement = StatementImport(
        account_id=(account or seed_user["account"]).id,
        user_id=seed_user["user"].id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name="statement.csv",
        file_digest="c" * 64,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        line_count=1,
        recorded_count=1,
    )
    db.session.add(statement)
    db.session.flush()
    return statement


def a_bank_line(
    seed_user, statement, *, amount="-180.00", posted_on=None,
    description="ACH DEBIT DUKEENERGY", sequence_in_group=0,
    transaction_on=None,
):
    """Stage and return one recorded bank line under *statement*.

    Args:
        seed_user: The seeded user bundle.
        statement: The import that recorded it.
        amount: Signed, positive INTO the account.
        posted_on: The day the bank posted it.
        description: What the bank called it.
        sequence_in_group: The ordinal completing its identity.
        transaction_on: The day the bank STATED the transaction happened, or
            ``None`` for a source stating none -- which is the DEFAULT here
            because it is the majority case on the developer's own statement
            (179 of 361 lines) and because a fixture that always states one
            would never exercise :attr:`~._offers.BankLine.happened_on`'s
            fallback.

    Returns:
        The staged
        :class:`~app.models.statement_import.BankStatementLine`.
    """
    day = posted_on or seed_user["bootstrap_period"].start_date
    line = BankStatementLine(
        account_id=statement.account_id,
        import_id=statement.id,
        posted_on=day,
        transaction_on=transaction_on,
        amount=Decimal(amount),
        description=description,
        sequence_in_group=sequence_in_group,
    )
    db.session.add(line)
    db.session.flush()
    return line
