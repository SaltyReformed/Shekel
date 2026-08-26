"""The rows these tests match against, built without the code under test.

``seed_user`` creates an account, a scenario and categories and **no
transaction at all** -- a fact this arc has already paid for once, when a test
in the previous leaf compared ``[] == []`` and reported it as proof that
nothing moved.  So every test here builds its own rows, through the ORM rather
than through the services the accept door calls, so a broken settle verb cannot
also break the fixture that would have caught it.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import (
    SettlementBasisEnum,
    StatementSourceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.merchant import Merchant
from app.models.merchant_destination import MerchantDestination
from app.models.pay_period import PayPeriod
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services.cash_ledger import amount_basis
from app.services.statement_match import (
    CreationBars,
    MatchSubmission,
    PolicyStatement,
    ReviewScope,
    ReviewedRow,
    RowKind,
    as_reviewed,
)
from tests._test_helpers import (
    settle_day_columns,
)


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
    category=None,
    template=True,
    reconciled_by=None,
    settle_day_basis=None,
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
        category: The category it files under; Groceries by default.
        template: Whether a recurring definition owns it.  ``False`` builds an
            AD-HOC row (``template_id`` NULL), which is what
            ``_create._create_envelope`` produces and therefore the only shape
            a NEW-ENVELOPE merchant answer converges onto -- naming a template
            is a different answer with its own resolution.
        reconciled_by: The :func:`an_assertion` this row was TICKED against on
            the reconcile panel, or ``None`` for a row settled any other way.
            **It no longer decides what KIND of day *settled_on* is** (plan step
            **X-az**): that column answers WHICH statement was seen to show the
            money, and reading it as the day's provenance is finding **N-332**.
            Pass *settle_day_basis* to say what the day is.
        settle_day_basis: WHICH KIND of day *settled_on* is
            (:class:`~app.enums.SettledDayBasisEnum`), or ``None`` for
            ``entered`` -- the owner's own record, which is what a row settled
            through an edit box or a Mark Paid carries.  A fixture standing in
            for a reconcile-panel tick says ``ASSERTED``; one standing in for a
            bank match says ``OBSERVED``.

    Returns:
        The staged :class:`~app.models.transaction.Transaction`.
    """
    type_id = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE,
    )
    category_id = (category or seed_user["categories"]["Groceries"]).id
    template_id = None
    if template:
        definition = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=category_id,
            transaction_type_id=type_id,
            name=name,
            default_amount=Decimal(amount),
            is_envelope=is_envelope,
        )
        db.session.add(definition)
        db.session.flush()
        template_id = definition.id
    txn = Transaction(
        template_id=template_id,
        pay_period_id=(period or seed_user["bootstrap_period"]).id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=ref_cache.status_id(status),
        name=name,
        category_id=category_id,
        transaction_type_id=type_id,
        estimated_amount=Decimal(amount),
        is_envelope=is_envelope,
        **settle_day_columns(settled_on, settle_day_basis),
        settled_amount=Decimal(amount) if settled_on else None,
        settled_basis_id=(
            ref_cache.settlement_basis_id(SettlementBasisEnum.DERIVED)
            if settled_on else None
        ),
        reconciled_by_id=reconciled_by.id if reconciled_by else None,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def a_purchase(
    seed_user, parent, *, amount="25.00", description="Kroger",
    purchased_on=None, settled_on=None, is_credit=False, reconciled_by=None,
    settle_day_basis=None,
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
        settle_day_basis: WHICH KIND of day *settled_on* is
            (:class:`~app.enums.SettledDayBasisEnum`), or ``None`` for
            ``entered``.  See :func:`a_transaction`; a purchase ticked on the
            reconcile panel is ``ASSERTED`` and one the bank stated is
            ``OBSERVED``, and it is the basis rather than *reconciled_by* that
            decides the window (plan step **X-az**).
        reconciled_by: The :func:`an_assertion` this purchase was TICKED
            against on the reconcile panel, or ``None`` for one settled any
            other way.  **It no longer decides what KIND of day *settled_on*
            is** (plan step **X-az**), for the reason :func:`a_transaction`'s
            twin parameter states: that column answers WHICH statement was seen
            to show the money, and reading it as the day's provenance is finding
            **N-332**.  Pass *settle_day_basis*.

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
        **settle_day_columns(settled_on, settle_day_basis),
        is_credit=is_credit,
        reconciled_by_id=reconciled_by.id if reconciled_by else None,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def an_assertion(
    seed_user, *, observed_on=None, balance="1000.00", account=None,
):
    """Stage and return one asserted balance, for a row to be RECONCILED to.

    **What the reconcile panel's tick points a row at**, and the thing that
    makes a settle day a BOUND rather than an observation
    (:attr:`~app.services.statement_match.CandidateRow.expected_window`).  A
    fixture cannot fake the link with a bare integer: ``fk_transaction_entries
    _reconciled_by`` and its transaction twin reference
    ``account_anchor_history (account_id, id)``, so the assertion has to exist.

    Args:
        seed_user: The seeded user bundle.
        observed_on: The civil day this balance is asserted FOR -- the day the
            panel stamps onto every row ticked against it.  The bootstrap
            period's start by default.
        balance: The asserted figure, as a string.  Nothing here reads it; it
            is stated because the column is NOT NULL.
        account: The account it is asserted for; the seeded checking one by
            default.

    Returns:
        The staged :class:`~app.models.account.AccountAnchorHistory`.
    """
    row = AccountAnchorHistory(
        account_id=(account or seed_user["account"]).id,
        anchor_balance=Decimal(balance),
        observed_on=observed_on or seed_user["bootstrap_period"].start_date,
    )
    db.session.add(row)
    db.session.flush()
    return row


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


def a_merchant(seed_user, name, *, account=None, account_id=None):
    """Stage and return the merchant row *name* names, creating it once.

    **Built through the ORM, like everything else here**, so a broken
    ``statement_import._merchants.resolve_merchants`` cannot also build the
    fixture a reader test would have caught it with -- the rule
    :func:`a_policy` already states.

    **Get-or-create, because a merchant is per ACCOUNT and a test states its
    name several times.**  Two lines from one merchant are two lines and ONE
    merchant, and a builder that inserted twice would fail
    ``uq_merchants_account_name`` -- which is the fixture reproducing the
    production identity rather than working around it.

    Args:
        seed_user: The seeded user bundle.
        name: What the source calls the merchant.
        account: The account whose statements name it; the seeded checking one
            by default.
        account_id: Its id, where the caller holds that instead -- a bank line
            carries its import's ``account_id`` and no account object.

    Returns:
        The staged :class:`~app.models.merchant.Merchant`.
    """
    if account_id is None:
        account_id = (account or seed_user["account"]).id
    found = (
        db.session.query(Merchant)
        .filter(Merchant.account_id == account_id, Merchant.name == name)
        .one_or_none()
    )
    if found is not None:
        return found
    row = Merchant(account_id=account_id, name=name)
    db.session.add(row)
    db.session.flush()
    return row


def a_bank_line(
    seed_user, statement, *, amount="-180.00", posted_on=None,
    description="ACH DEBIT DUKEENERGY", sequence_in_group=0,
    transaction_on=None, merchant=None, source_category=None,
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
        merchant: What the source NAMES the merchant, as a string, or ``None``
            for a source naming none -- which is the DEFAULT, and it is a
            fixture decision worth stating.  The string is resolved to a
            :class:`~app.models.merchant.Merchant` row here
            (:func:`a_merchant`), so a test states the name it means and the
            fixture reproduces the production identity.  **It is NOT
            derived from *description* here.**  A
            builder that re-ran the adapter's own parse would move with it, so
            a change to that parse would shift the fixture and the assertion
            together and grade nothing; the parse is graded where it belongs,
            against real CSV bytes, in
            ``tests/test_services/test_statement_import/test_secu_csv.py``.
            What these builders state is the recorded FACT.  ``None`` is the
            default because it is the state every guard here has to survive: a
            line with no merchant joins no destination policy, and the label
            readers fall back to *description*.
        source_category: The BANK's own category string, or ``None`` for a
            source stating none -- which is the DEFAULT, because it is
            provenance and almost nothing reads it.  Ruling **R-GJ** is the one
            thing that does: a merchant whose lines the source files under a
            card-payment category has no create arm until the owner answers for
            it (:class:`~app.services.statement_match.CreationBars`).  Stated
            here VERBATIM as the source spells it, never mapped, for the reason
            *merchant* is not derived from *description*: a builder that
            re-ran a translation would move with it and grade nothing.

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
        merchant_id=(
            None if merchant is None
            else a_merchant(
                seed_user, merchant, account_id=statement.account_id,
            ).id
        ),
        source_category=source_category,
        sequence_in_group=sequence_in_group,
    )
    db.session.add(line)
    db.session.flush()
    return line


def the_merchant_id(seed_user, name, *, account=None):
    """Return the id of the merchant *name* names, refusing to create one.

    **:func:`a_merchant` is get-or-create and it FLUSHES**, so calling it
    inside an assertion writes to the database from a comparison -- and, worse,
    quietly supplies the row whose absence the case may be about.  Both
    adversarial reviews of 2026-08-25 flagged the shape.  This is the read-only
    half: a case ARRANGES with :func:`a_merchant` and ASSERTS with this one, so
    an assertion can only ever observe.

    Args:
        seed_user: The seeded user bundle.
        name: What the source calls the merchant.
        account: The account whose merchants to look in; the seeded checking
            one by default.

    Returns:
        Its :class:`~app.models.merchant.Merchant` id.

    Raises:
        AssertionError: When this account holds no such merchant -- which is
            the failure the case wanted to see, named rather than papered over
            by a fixture minting one.
    """
    account_id = (account or seed_user["account"]).id
    found = (
        db.session.query(Merchant.id)
        .filter(Merchant.account_id == account_id, Merchant.name == name)
        .one_or_none()
    )
    assert found is not None, (
        f"no merchant named {name!r} on account {account_id} -- the code "
        f"under test did not create it"
    )
    return found[0]


def a_statement(seed_user, merchant, answer=None, *, account=None, **fields):
    """Return the submission a test makes ABOUT *merchant*, by name.

    **The door takes a merchant ROW ID** (plan step ``bank_import:X-gd-1``), and
    a case that spelled one inline would be asserting against a number instead
    of against a merchant.  This resolves the name the same way the screen
    does -- to the row -- so a case still reads as *say where Amazon goes*.

    **It CREATES the merchant where the case has staged no line naming it**
    (:func:`a_merchant`), which is what makes it wrong for the two cases that
    grade the scope refusal: those need an id this account does NOT have, and
    they state one directly.

    Args:
        seed_user: The seeded user bundle.
        merchant: What the source calls the merchant.
        answer: The :class:`~app.services.statement_match.PolicyAnswer`, or
            ``None`` to withdraw.
        account: The account it governs; the seeded checking one by default.
        **fields: ``template_id`` / ``envelope_name`` / ``category_id``, as the
            answer needs them.

    Returns:
        The :class:`~app.services.statement_match.PolicyStatement`.
    """
    return PolicyStatement(
        merchant_id=a_merchant(seed_user, merchant, account=account).id,
        answer=answer, **fields,
    )


def a_policy(
    seed_user, merchant, *, template_id=None, envelope_name=None,
    category_id=None, account=None,
):
    """Stage and return one stated merchant destination.

    Built through the ORM like everything else here, so a broken
    ``state_policies`` cannot also break the fixture a reader test would have
    caught it with.

    Args:
        seed_user: The seeded user bundle.
        merchant: What the source calls the merchant.  Resolved to the
            :class:`~app.models.merchant.Merchant` row that IS the key
            (:func:`a_merchant`), creating it where this test has staged
            no line naming it.
        template_id: The recurring definition to file into, for the TEMPLATE
            answer.
        envelope_name: What to call the envelope to create, for the
            NEW ENVELOPE answer.
        category_id: The category to create it under, likewise.
        account: The account it governs; the seeded checking one by default.

    Returns:
        The staged
        :class:`~app.models.merchant_destination.MerchantDestination`.  With no
        arm given it is the NEVER answer, which is a row with all three columns
        NULL rather than the absence of a row.
    """
    row = MerchantDestination(
        user_id=seed_user["user"].id,
        account_id=(account or seed_user["account"]).id,
        merchant_id=a_merchant(seed_user, merchant, account=account).id,
        template_id=template_id,
        envelope_name=envelope_name,
        category_id=category_id,
    )
    db.session.add(row)
    db.session.flush()
    return row


def a_later_period(seed_user):
    """Stage and return the pay period AFTER the bootstrap one.

    On the owner's own calendar, because a period the calendar does not know is
    not a period the offer set can reach -- a case built on one would pass for
    the wrong reason.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The staged :class:`~app.models.pay_period.PayPeriod`.
    """
    bootstrap = seed_user["bootstrap_period"]
    period = PayPeriod(
        user_id=seed_user["user"].id,
        start_date=bootstrap.end_date + timedelta(days=1),
        end_date=bootstrap.end_date + timedelta(days=14),
        period_index=bootstrap.period_index + 1,
    )
    db.session.add(period)
    db.session.flush()
    return period


def a_scope(seed_user, account=None):
    """Return the derived pass these doors act inside.

    **Built at the point of USE, never once per test**, and that is the
    property under test as much as a convenience:
    :class:`~app.services.statement_match.ReviewScope` holds the account's rows
    and their prices as they stood when it was derived, so a case that stages a
    row and then re-uses an older scope is asserting against a state the app
    would never have.  Every call here therefore re-derives.

    Args:
        seed_user: The seeded user bundle.
        account: The account being reviewed; the seeded checking one by
            default.

    Returns:
        The :class:`~app.services.statement_match.ReviewScope`.
    """
    return ReviewScope.build(
        seed_user["user"].id, (account or seed_user["account"]).id,
    )


def a_bars(seed_user, account=None):
    """Return the pass's creation BARS, for a test calling the door directly.

    Ruling **R-GJ**, plan step ``bank_import:X-ga``.
    :func:`~app.services.statement_match.apply_reviewed` derives one per
    REQUEST and threads it, so a case calling
    :func:`~app.services.statement_match.create_purchase_from_line` on its own
    is standing in for that caller and must supply the same thing.

    **DERIVED rather than an empty value**, and that is the property under test
    as much as a convenience: a helper that handed the door a
    ``CreationBars`` with nothing in it would make every case here blind to the
    refusal the step exists to add, which is exactly the *fixture that bypasses
    the production door* shape this suite has been burned by before.  Built at
    the point of USE, for the reason :func:`a_scope` is.

    Args:
        seed_user: The seeded user bundle.
        account: The account being reviewed; the seeded checking one by
            default.

    Returns:
        The :class:`~app.services.statement_match.CreationBars`.
    """
    return CreationBars.build(
        seed_user["user"].id, (account or seed_user["account"]).id,
    )


def a_basis(seed_user):
    """Return the pass's amount basis, for a test calling a producer directly.

    :meth:`~app.services.statement_match.ReviewScope.build` derives one per
    pass and threads it (plan step X-au-j); a case that calls
    :func:`~app.services.statement_match.candidates_for` on its own is standing
    in for that caller and must supply the same thing.  Built from the seeded
    scenario rather than resolved, because these fixtures own it.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The :class:`~app.services.cash_ledger.AmountBasis` for that owner.
    """
    return amount_basis(seed_user["user"].id, seed_user["scenario"].id)


def a_submission(
    scope, *, lines=(), transactions=(), entries=(), residual=None,
):
    """Return the submission a screen rendered from *scope* would post back.

    **It reads the reviewed state out of the SCOPE rather than inventing one**,
    which is what makes these tests exercise the real two-moment flow: the
    screen renders a candidate, the owner ticks it, and the door reconciles
    what was ticked with what it finds (finding **N-336**, plan step
    ``bank_import:X-f6d-3``).  A helper that stamped a figure of its own would
    make every case agree with itself by construction, and the staleness guard
    would be untested by every test that uses it.

    A row the scope does NOT offer is carried as the ORM row states it.  That
    is not a fallback that hides a mistake: those cases are the ones asserting
    the door refuses a row it never offered, and ``resolve_rows`` refuses them
    before the reconciliation is reached -- so what the token says about them
    cannot change the outcome, and a helper that raised here would make the
    refusal untestable.

    Args:
        scope: The pass being submitted against
            (:func:`a_scope`).
        lines: Bank line rows.
        transactions: Transaction rows.
        entries: Purchase rows.
        residual: The difference the screen showed and the owner ticked, as a
            string or a ``Decimal``; ``None`` for the ordinary case where they
            accepted none (plan step ``bank_import:X-f6d-4``).  **Stated by the
            caller rather than computed here**, and that is the point: the door
            reconciles what the screen said against its own arithmetic, so a
            helper that derived the figure would make every case agree by
            construction and leave the reconciliation ungraded -- the same
            reason the reviewed row state is read off the scope rather than
            invented.

    Returns:
        The :class:`~app.services.statement_match.MatchSubmission`.
    """
    offered = {
        (row.kind, row.row_id): row for row in scope.candidates.rows
    }
    wanted = (
        [(RowKind.TRANSACTION, txn) for txn in transactions]
        + [(RowKind.PURCHASE, entry) for entry in entries]
    )
    rows = set()
    for kind, orm_row in wanted:
        candidate = offered.get((kind, orm_row.id))
        if candidate is not None:
            rows.add(as_reviewed(candidate))
            continue
        rows.add(ReviewedRow(
            kind=kind,
            row_id=orm_row.id,
            cash_amount=Decimal("0.00"),
            version_id=orm_row.version_id,
        ))
    return MatchSubmission(
        line_ids=frozenset(line.id for line in lines),
        rows=frozenset(rows),
        accepted_difference=(
            None if residual is None else Decimal(str(residual))
        ),
    )


def a_reviewed_token(orm_row, kind):
    """Return the form value the review screen would emit for *orm_row*.

    **Through the real producer, never composed here** -- a helper that spelled
    the token itself would agree with the schema by construction.

    **What it does NOT grade, stated because a first draft of this docstring
    claimed it did**: it calls
    :func:`~app.services.statement_match.as_reviewed` DIRECTLY, so it never
    touches the ``reviewed_token`` filter and never renders a template.  It
    grades the service against itself.  The pair that has to agree is a Jinja
    filter name and a Marshmallow field name, which nothing in the tree fails
    over, and the only cases that close that loop scrape the rendered page:
    ``test_statement_matches.TestWhatTheTEMPLATEEmittedIsWhatTheDOORAccepts``,
    one per emission site.  Named by adversarial financial review 2026-08-23,
    which measured the hand form's site ungraded at 418 tests green.

    Args:
        orm_row: A ``Transaction`` or ``TransactionEntry``.
        kind: Which of the two it is
            (:class:`~app.services.statement_match.RowKind`).

    Returns:
        Its ``"<kind>:<row_id>:<cash_amount>:<version_id>"`` token.
    """
    account = db.session.get(Account, orm_row.account_id)
    scope = ReviewScope.build(account.user_id, account.id)
    for candidate in scope.candidates.rows:
        if candidate.kind is kind and candidate.row_id == orm_row.id:
            return as_reviewed(candidate).token
    # NOT offerable, which several cases stage deliberately.  The door refuses
    # such a row before it reconciles anything, so the figure here cannot
    # change an outcome; what must be right is the SHAPE, so the token still
    # goes through the same value rather than a literal.
    return ReviewedRow(
        kind=kind, row_id=orm_row.id, cash_amount=Decimal("0.00"),
        version_id=orm_row.version_id,
    ).token
