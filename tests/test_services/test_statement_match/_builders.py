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
from app.models.merchant_rule import MerchantRule
from app.models.pay_period import PayPeriod
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services.cash_ledger import amount_basis
from app.services import statement_match
from app.services.statement_match import (
    CreationBars,
    MerchantAnswers,
    MatchSubmission,
    MintedEnvelopes,
    PurchaseCreation,
    RuleSubmission,
    ReviewScope,
    ReviewedRow,
    RowKind,
    as_reviewed,
)
from tests._test_helpers import (
    last_covered_day,
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
    account=None,
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
        account: The account to file it on; the seeded checking one by default.
            **A case needs this where the ACCOUNT is the variable** rather than
            the row -- an account whose books open INSIDE the pay calendar is
            a shape the seeded one cannot be restated into, so a destination on
            it has to be built here.  ``a_scope`` and ``an_import`` beside this
            already take the same parameter for the same reason.

    Returns:
        The staged :class:`~app.models.transaction.Transaction`.
    """
    type_id = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE,
    )
    account_id = (account or seed_user["account"]).id
    category_id = (category or seed_user["categories"]["Groceries"]).id
    template_id = None
    if template:
        definition = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=account_id,
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
        account_id=account_id,
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


def an_import(
    seed_user, account=None, *, line_count=1, recorded_count=1, created_at=None,
):
    """Stage and return one statement import for an account.

    Args:
        seed_user: The seeded user bundle.
        account: The account; the seeded checking one by default.
        line_count: How many lines the FILE held.
        recorded_count: How many of them this import WROTE.  Defaults equal to
            *line_count*'s default, which is the whole-file case; a re-import
            of an overlapping span states a smaller number here, and
            ``ck_statement_imports_recorded_within_file`` refuses a larger one.
            **The builder does not derive it from the lines a test then
            attaches**: the column is what the import DECLARED, and a fixture
            that recomputed it could not stage the disagreement a re-import
            produces.
        created_at: The instant the import ran, or ``None`` to take the
            column's own ``now()`` default.  Stated only by a case whose
            subject is WHICH import is newest or WHICH day one displays on --
            and it has to be stated for the first of those, because ``now()``
            is the TRANSACTION's start time in PostgreSQL, so two imports
            written in one test carry the identical instant.

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
        line_count=line_count,
        recorded_count=recorded_count,
    )
    if created_at is not None:
        statement.created_at = created_at
    db.session.add(statement)
    db.session.flush()
    return statement


def a_merchant(seed_user, name, *, account=None, account_id=None):
    """Stage and return the merchant row *name* names, creating it once.

    **Built through the ORM, like everything else here**, so a broken
    ``statement_import._merchants.resolve_merchants`` cannot also build the
    fixture a reader test would have caught it with -- the rule
    :func:`a_rule` already states.

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
            line with no merchant joins no destination rule, and the label
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


def a_statement(seed_user, merchant, answer, *, account=None, **fields):
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
        answer: The :class:`~app.services.statement_match.RuleAnswer`.
            **Required, and it used to default to ``None`` meaning WITHDRAW**
            (ruling **R-GS**, plan step ``bank_import:X-gd-2``): there is no
            withdrawal, a submission always states one of the four answers, and
            the screen's *I have not said* never reaches this door at all
            because the route drops it.
        account: The account it governs; the seeded checking one by default.
        **fields: ``template_id`` / ``envelope_name`` / ``category_id``, as the
            answer needs them.

    Returns:
        The :class:`~app.services.statement_match.RuleSubmission`.
    """
    return RuleSubmission(
        merchant_id=a_merchant(seed_user, merchant, account=account).id,
        answer=answer, **fields,
    )


def a_rule(
    seed_user, merchant, *, template_id=None, envelope_name=None,
    category_id=None, income_category_id=None, always_ask=False, account=None,
):
    """Stage and return one stated merchant rule.

    Built through the ORM like everything else here, so a broken
    ``state_rules`` cannot also break the fixture a reader test would have
    caught it with -- and the four columns are spelled out rather than taken
    from ``_rules._columns_of``, so a mistake in that mapping cannot arrive
    here and agree with itself.

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
        income_category_id: What a DEPOSIT from this merchant is, for the
            INCOME CATEGORY answer (ruling **R-HT(a)**, plan step
            ``bank_import:X-gj-2a``).  **Its own parameter and not a second use
            of ``category_id``**, for the reason the COLUMN is its own: the two
            answer different questions, and a fixture that shared one field
            could stage a row the CHECK refuses while looking correct.
        always_ask: Which of the two CONTAINER-LESS answers this is
            (ruling **R-GS**) -- ``False`` for *never a purchase*, ``True`` for
            *ask me every time*.  **Read only when no container arm is given**,
            which is the same discipline ``_rules._columns_of`` follows: a
            field belongs to one answer, so naming it beside another answer's
            arm states nothing.
        account: The account it governs; the seeded checking one by default.

    Returns:
        The staged :class:`~app.models.merchant_rule.MerchantRule`.  With no
        arm given at all it is the NEVER answer, which is what every caller
        written before ``always_ask`` existed meant by it.

        **The five columns are spelled out here rather than taken from
        ``_stating._columns_of``**, which is this builder's founding rule and
        matters more now there are five: a mistake in that mapping cannot
        arrive here and agree with itself.  What DOES grade the pair is the
        round trip over ``RuleAnswer.of``.
    """
    names_something = (
        template_id is not None
        or envelope_name is not None
        or income_category_id is not None
    )
    row = MerchantRule(
        user_id=seed_user["user"].id,
        account_id=(account or seed_user["account"]).id,
        merchant_id=a_merchant(seed_user, merchant, account=account).id,
        template_id=template_id,
        envelope_name=envelope_name,
        category_id=category_id,
        income_category_id=income_category_id,
        never_a_purchase=not names_something and not always_ask,
    )
    db.session.add(row)
    db.session.flush()
    return row


def an_envelope(seed_user, name="Groceries"):
    """Return a Projected envelope a purchase may join.

    Args:
        seed_user: The seeded user bundle.
        name: The envelope's name.

    Returns:
        The staged :class:`~app.models.transaction.Transaction`.
    """
    return a_transaction(
        seed_user, name=name, amount="500.00", is_envelope=True,
    )


def an_unexplained_outflow(
    seed_user, merchant="Amazon", amount="-57.96", sequence=0,
    source_category=None,
):
    """Record one unexplained outflow from *merchant*.

    **Shared by the two route modules** since plan step ``bank_import:X-gf-2``
    (finding **N-33**'s shape): the queue asks about a merchant with no answer
    and the register shows one already answered, and both need a line for it.

    Args:
        seed_user: The seeded user bundle.
        merchant: What the bank names the merchant, which is the rule key.
        amount: Signed, negative OUT of the account.
        sequence: The ordinal completing the line's identity.
        source_category: The BANK's own category string, verbatim, or ``None``
            for a source stating none.  Ruling **R-GJ** reads it for one narrow
            purpose: a merchant a source files as a payment to a credit card
            has no create arm until the owner answers for it.

    Returns:
        The staged
        :class:`~app.models.statement_import.BankStatementLine`.
    """
    statement = an_import(seed_user)
    return a_bank_line(
        seed_user, statement, amount=amount,
        posted_on=seed_user["bootstrap_period"].start_date,
        description=f"POINT OF SALE DEBIT L340 THING ({merchant})",
        merchant=merchant, sequence_in_group=sequence,
        source_category=source_category,
    )


def filed_by(seed_user, line, envelope, *, by_rule, scope=None, answers=None):
    """Record one bank *line* as a purchase in *envelope*, and say WHO did it.

    **The one fact under test in several places is ``by_rule``** (ruling
    **bank_import:R-GT**): it decides which settled tab holds the act, and it
    is what the hero's *N filed by rules* counts.  Staged through the real
    create door rather than by writing the two rows, so a case cannot pass
    against a shape the door would never produce.

    **It takes the LINE rather than making one**, because every caller of this
    cares WHICH import recorded it -- :func:`an_unexplained_outflow` stages a
    fresh import per line, which is the opposite of what a case about one
    import's own lines needs.

    Args:
        seed_user: The seeded user bundle.
        line: The recorded :class:`~app.models.statement_import
            .BankStatementLine` to file.  Must already be COMMITTED, because
            the scope is derived from the database.
        envelope: The budget line to file it into.
        by_rule: Whether a STANDING RULE performed the act rather than a
            person ticking it.
        scope: The pass to file against, or ``None`` to derive one.
        bars: The creation bars, or ``None`` to derive them.

            **Both exist so a caller staging MANY acts can derive ONCE**, and
            that is a cost the register's own bound case has already paid for
            in as many words: at 51 rows a per-row derivation ran the pass 53
            times and made that the slowest test in the suite, timing it out in
            CI at the 30 s per-test budget.  A caller staging one line takes
            the default and reads exactly as before.

            **It is also what the app does.**  A render builds ONE
            :class:`~app.services.statement_match._scope.ReviewScope` and files
            every line of a pass against it; a fresh pass per line is a shape
            no door has, so a case built that way is measuring something the
            app never does.

    Returns:
        The created purchase.
    """
    return statement_match.create_purchase_from_line(
        PurchaseCreation(line_id=line.id, transaction_id=envelope.id),
        a_scope(seed_user) if scope is None else scope,
        MintedEnvelopes.none_yet(),
        an_answers(seed_user) if answers is None else answers,
        applied_by_rule=by_rule,
    )


def filed_acts(seed_user, how_many, *, by_rule):
    """Record *how_many* bank lines as purchases, each in its OWN envelope.

    The staging both of ruling **R-GX**'s bound cases need (plan step
    ``bank_import:X-gj-1c``): the settled tabs bound at
    :data:`~app.services.statement_match.REGISTER_LIMIT`, so a case about the
    bound has to hold one act more than that, and it is stated once here
    rather than twice because the two cases differ only in which tier they
    then read -- the service's page value, or the rendered route.

    **Each act gets an envelope of its own, and that is a MEASUREMENT rather
    than a style choice.**  ``posting_service.sync_transaction_postings``
    reconciles a transaction's whole purchase FAMILY on every entry change
    (ruling **R-FM**), so filing the *k*-th purchase into an envelope that
    already holds *k-1* of them calls
    :func:`~app.services._posting_purchases.emit_purchase_deltas` *k* times --
    quadratic in the number of acts.  Measured 2026-08-31 on this box, 51
    acts: **1,326 emit calls (51x52/2) and 4.50 s into one envelope, against
    51 calls and 1.13 s spread across 51**.  CI pins ``-n 12`` on a hosted
    runner with ``nproc`` far under 12, and that oversubscription multiplied
    the concentrated shape past ``pytest.ini``'s 30 s per-test budget: four
    cases timed out at 28.94-29.03 s having taken 4.7 s here.  **The app's
    quadratic is real and is NOT this fixture's to fix** -- it is finding
    **N-406**, filed with this measurement -- but an envelope holding 51
    purchases in one pay period is a shape the app does not produce either:
    measured on a clone of the developer's own database 2026-08-31, the
    largest budget line holds **13** and the mean over the 63 rows carrying
    any is **2.92**.  The fixture was paying for a concentration it never had
    to stage.

    **Nothing about the bound depends on WHERE the money landed**, which is
    why spreading it costs no coverage: the settled tabs render one unnamed
    section whatever the destinations are
    (:func:`~app.services.statement_match._cards.act_sections`), and both
    cases assert over counts of acts.

    Args:
        seed_user: The seeded user bundle.
        how_many: How many acts to stage.
        by_rule: Whether a STANDING RULE performed them rather than a person
            ticking them (ruling **bank_import:R-GT**), which is what decides
            WHICH settled tab holds them.

    Returns:
        The created purchases, in the order they were filed.
    """
    pots = [
        an_envelope(seed_user, name=f"Envelope {ordinal}")
        for ordinal in range(how_many)
    ]
    lines = [
        an_unexplained_outflow(
            seed_user, merchant=f"Shop {ordinal}", amount="-10.00",
            sequence=ordinal,
        )
        for ordinal in range(how_many)
    ]
    db.session.commit()
    # ONE derivation for all of them, which is what the app does and what
    # keeps this affordable -- see :func:`filed_by`.
    scope, answers = a_scope(seed_user), an_answers(seed_user)
    purchases = [
        filed_by(
            seed_user, line, pot, by_rule=by_rule, scope=scope,
            answers=answers,
        )
        for pot, line in zip(pots, lines)
    ]
    db.session.commit()
    return purchases


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
        start_date=last_covered_day(bootstrap) + timedelta(days=1),
    )
    db.session.add(period)
    db.session.flush()
    return period


def a_payday_on(seed_user, start_date):
    """Stage and return one pay period of the owner's, OPENING on *start_date*.

    :func:`a_later_period`'s general form, and it exists for the shape that
    helper cannot build: it opens the next period the day after the bootstrap's
    stored ``end_date``, where the derived end (the day before the NEXT payday)
    and the stored one COINCIDE -- so a case asserting on a derived span and
    built on it would pass against the stored column it is meant to catch.

    Its own stored ``end_date`` is a fortnight, which is what keeps the owner's
    cadence at 14 while this row is the last one: no seeded owner has a
    ``budget.pay_schedule`` row, so
    ``pay_schedule_service.resolve_schedule`` infers the cadence from the last
    period's stored LENGTH.

    ``period_index`` is the next free one rather than a position, because the
    derivation renumbers by payday order anyway and the column only has to
    satisfy ``uq_pay_periods_user_index`` -- which is what lets a caller stage
    paydays whose id order and date order disagree.

    Args:
        seed_user: The seeded user bundle.
        start_date: The payday this period opens on.

    Returns:
        The staged :class:`~app.models.pay_period.PayPeriod`.
    """
    period = PayPeriod(user_id=seed_user["user"].id, start_date=start_date)
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


def accepted_acts(seed_user, account=None):
    """Return the accepted acts the REGISTER lists, whole and unbounded.

    Plan step ``bank_import:X-gf-2``.  These were ``review_set(scope).accepted``
    until the register took them off the review screen (ruling
    **bank_import:R-GX**), and going through the register's own reader rather
    than around it is the point: it is what the surface renders, bound and
    ordered as the surface orders it.

    **Unbounded on purpose.**  A case here stages two or three acts, so the
    bound could never fire -- and passing ``None`` says the assertion is about
    the acts and not about the cut, which is
    ``TestTheRegisterBoundsWhatItRenders``'s own subject.

    Args:
        seed_user: The seeded user bundle.
        account: The account being reviewed; the seeded checking one by
            default.

    Returns:
        The :class:`~app.services.statement_match.AcceptedGroup` values, every
        act that no longer holds first and then newest first.  **That ORDER is
        not what ``review_set(scope).accepted`` gave**, which was plain
        newest-first: a case staging several acts and indexing ``[0]`` is
        asking for a different one than it used to.  No case does today, and
        the register's order is the one the screen renders.
    """
    return statement_match.register_set(
        seed_user["user"].id, (account or seed_user["account"]).id, None,
    ).accepted.shown


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


def an_answers(seed_user, account=None):
    """Return what the owner has said about this account's merchants.

    The :class:`~app.services.statement_match.MerchantAnswers` the pass derives
    once and every act reads -- the stated rules AND ruling **R-GJ**'s bars,
    from one read.  :func:`a_bars` is still here for the cases that assert on
    the bars alone; this is what the write doors take (plan step
    ``bank_import:X-gj-2b-2``).

    Args:
        seed_user: The seeded user bundle.
        account: The account being reviewed; the seeded checking one by
            default.

    Returns:
        The :class:`~app.services.statement_match.MerchantAnswers`.
    """
    return MerchantAnswers.build(
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
    attributed=None,
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
        attributed: Which member carries that difference, as a
            ``(kind, orm_row)`` pair, or ``None`` for the ordinary case where
            the owner named none (plan step ``bank_import:X-gj-3a``).

            **Resolved out of the rows this submission already carries**,
            which is what the pane does: the select's options ARE the ticked
            rows, so the pointer and the row it points at are one value.  A
            pair naming a row this submission does not carry takes the same
            not-offerable fallback the loop above takes -- deliberately, since
            the cases asserting the door refuses such a pointer are the ones
            that need to build it.

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
        attributed_to=_attribution(rows, attributed),
    )


def _attribution(rows, attributed):
    """Return the submitted row an attribution names, or ``None``.

    Args:
        rows: The reviewed rows this submission carries.
        attributed: A ``(kind, orm_row)`` pair, or ``None``.

    Returns:
        The matching :class:`~app.services.statement_match.ReviewedRow` from
        *rows*, so the pointer is one of the submission's own values; or one
        built from the ORM row where the submission does not carry it, which
        is the shape a crafted body has.
    """
    if attributed is None:
        return None
    kind, orm_row = attributed
    for row in rows:
        if row.kind is kind and row.row_id == orm_row.id:
            return row
    return ReviewedRow(
        kind=kind, row_id=orm_row.id, cash_amount=Decimal("0.00"),
        version_id=orm_row.version_id,
    )


def a_reviewed_token(orm_row, kind, scope=None):
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
        scope: The pass to read the reviewed state out of, or ``None`` to
            derive one for this row alone.

            **Passing one is what the SCREEN does**, and deriving per row is a
            shape the app never has: a render builds ONE
            :class:`~app.services.statement_match.ReviewScope` and emits every
            row's token off it.  It is also the twin of :func:`a_submission`,
            which has always taken the scope rather than building its own.

            **The caller owns FRESHNESS, and that is the whole of the
            contract**: the scope must be derived after every row it will be
            asked about is staged.  A row it has not got is not an error here
            -- it takes the not-offerable fallback below, which is deliberate
            and therefore SILENT, so a stale scope downgrades a live row's
            token to ``0.00`` rather than failing.  :func:`a_scope` states the
            same hazard from the other side ("a case that stages a row and then
            re-uses an older scope is asserting against a state the app would
            never have"), which is why this is an explicit parameter and not a
            cache: a cache would make the staleness a property of call order
            that no caller declared.

            **Why it exists**: ``ReviewScope.build`` is the expensive object in
            this package -- 0.59-0.75 s and 202 queries on the developer's own
            account, and the reason ``apply_statement_review`` derives two per
            request rather than one per act (finding **N-306**).  A case
            tokenising many rows and building one scope EACH is the same shape
            at the test tier, and one such case (51 acts) was the slowest test
            in the suite and timed out in CI.

    Returns:
        Its ``"<kind>:<row_id>:<cash_amount>:<version_id>"`` token.
    """
    if scope is None:
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


def an_account_whose_books_hide_a_line(db, seed_user, seed_periods):
    """Return an account whose opening equity already accounts for one line.

    **Built through ``create_account``, not by restating the seeded account.**
    These route fixtures open their pay calendar at ``seed_periods[0]``, years
    after the seeded account's own origination assertion -- so restating THAT
    account's books onto the calendar's first day would move them past an
    assertion, which the restatement door refuses.  A fixture may not assert
    against a state no door can produce, which is the objection an adversarial
    test-quality review raised against exactly this shape.

    An account CREATED with its ``observed_on`` on the calendar's first day
    opens its books there legally, and that is the developer's own Checking
    shape: books opening on the pay calendar's own first day, so a line posted
    that day is inside the calendar and inside the opening equity at once.

    **It lives here because it was BYTE-IDENTICAL in three route modules** --
    the review queue, the reconcile page and the workbench -- and it belongs
    beside :func:`a_bank_line` and :func:`an_import`, which it calls.  Same
    count and same remedy as ``_A_MATCHED_GROUP`` in ``tests/_test_helpers``,
    found by the same adversarial review one pass later.  ``pylint``'s
    ``duplicate-code`` never reads ``tests/``, so nothing but a reader was
    going to find it.

    Args:
        db: The test ``db`` fixture.
        seed_user: The seeded user bundle.
        seed_periods: The seeded pay periods.

    Returns:
        ``(account, day)`` -- the account and the day both its books and the
        line fall on.
    """
    # pylint: disable=import-outside-toplevel  -- ``account_service`` reaches
    # back into this package's subject at import time; the three modules that
    # used to hold their own copy of this fixture deferred it for the same
    # reason.
    from app.services import account_service as _accounts

    day = seed_periods[0].start_date
    account = _accounts.create_account(
        _accounts.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Books Hide A Line",
            anchor_balance=Decimal("689.16"),
            observed_on=day,
        ),
    )
    db.session.flush()
    a_bank_line(
        seed_user, an_import(seed_user, account=account), posted_on=day,
    )
    db.session.commit()
    return account, day
