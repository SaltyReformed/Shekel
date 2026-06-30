"""
Shekel Budget App -- shared test helper utilities.

Underscore-prefixed module name keeps pytest from collecting it as a
test file.  Import functions from here in test modules that need them.
"""

import importlib.util
import pathlib
import re
import sys

from datetime import date as _real_date, datetime as _real_datetime
from decimal import Decimal


def select_option_values(html: str, select_key: str) -> list[str]:
    """Return the ``value`` attributes of every ``<option>`` inside a named ``<select>``.

    Locates the ``<select>`` element identified by ``select_key``
    (matched against either its ``id`` or its ``name`` attribute --
    Shekel's templates are inconsistent: some forms set only
    ``name=`` while others set both ``id=`` and ``name=``) and
    returns the ``value="..."`` of each ``<option>`` child it
    contains, in document order.  Returns an empty list when no
    matching select is present or it carries no option children with
    value attributes.

    Use this helper to assert dropdown contents without falsely
    matching ``value="N"`` attributes from unrelated elements
    elsewhere in the page (transaction-type IDs, pay-period IDs,
    hardcoded month numbers, recurrence-pattern IDs, etc.).  A naive
    ``f'value="{model.id}" not in html`` check fails when ``model.id``
    happens to collide with any of those siblings -- a deterministic
    bug masquerading as a flake until the colliding sequence values
    align.

    Args:
        html: The full HTML response body to search.
        select_key: The ``id`` or ``name`` attribute of the
            ``<select>`` element to scope the search to.
            Case-sensitive, matched against the literal attribute
            value.

    Returns:
        Ordered list of ``value`` strings from the named select's
        ``<option>`` children.  Empty list when the select is not
        present in ``html``.  Returned values are the raw attribute
        strings (e.g. ``"2"`` not ``2``) so callers compare against
        ``str(model.id)`` rather than the int.
    """
    select_block = re.search(
        r'<select[^>]*\b(?:id|name)="'
        + re.escape(select_key)
        + r'"[^>]*>(.*?)</select>',
        html,
        re.DOTALL,
    )
    if select_block is None:
        return []
    return re.findall(
        r'<option\b[^>]*\bvalue="([^"]*)"',
        select_block.group(1),
    )


def field_is_disabled(html: str, field_name: str) -> bool:
    """Return True if the ``<input>``/``<select>`` named ``field_name`` is disabled.

    Slices from the ``name="<field_name>"`` attribute to the end of that
    opening tag (the next ``>``) and reports whether the ``disabled``
    attribute appears there.  The grid edit popovers append ``disabled``
    after ``name=`` on a finalised row's locked money / period / category
    / due-date fields (#26), so this distinguishes a locked field from the
    still-editable Status dropdown and Notes input in the same form.

    Args:
        html: The full HTML response body to search.
        field_name: The ``name`` attribute of the input/select to inspect.

    Returns:
        True when the named field's opening tag carries ``disabled``.

    Raises:
        AssertionError: The field is absent, so a typo'd name fails loud
            rather than silently reporting an editable field as locked.
    """
    marker = f'name="{field_name}"'
    idx = html.find(marker)
    assert idx != -1, f"{marker} not found in rendered HTML"
    tag_end = html.find(">", idx)
    return "disabled" in html[idx:tag_end]


def freeze_today(monkeypatch, target_date, modules=None):
    """Patch ``date.today()`` and ``datetime.now()`` to ``target_date``.

    Production services import ``date`` (and sometimes ``datetime``) at
    module load time (e.g. ``from datetime import date``), so
    monkeypatching ``datetime.date`` globally does not affect them.
    Each module that uses ``date.today()`` or ``datetime.now()`` must
    be patched individually.  This helper hides that boilerplate.

    Patches BOTH ``date`` and ``datetime`` so that test helpers using
    ``datetime.now() - timedelta(days=N)`` align with production code
    using ``date.today()``.  ``datetime.now()`` returns midnight UTC
    of ``target_date`` (timezone-aware when ``tz`` is passed).

    Args:
        monkeypatch:
            pytest's ``monkeypatch`` fixture.  Required so the patch is
            torn down at end of test automatically.
        target_date:
            The ``datetime.date`` instance that ``date.today()`` should
            return inside the patched modules.
        modules:
            Iterable of dotted module paths whose ``date`` and
            ``datetime`` symbols to replace.  When omitted, every
            loaded module that has a top-level ``date`` or ``datetime``
            symbol bound to the real class is patched -- this covers
            production services (``app.services.*``), test modules
            that imported ``date`` inline, and ``tests.conftest``
            itself, ensuring all layers see the same frozen "today".
            Pass an explicit tuple to patch only specific modules.
    """
    # Custom metaclass so ``isinstance(real_date_obj, _FrozenDate)``
    # returns True.  Without this, production code that does
    # ``isinstance(start_date, date)`` -- where ``date`` has been
    # replaced by ``_FrozenDate`` -- rejects real ``datetime.date``
    # instances and raises spurious ValidationError.
    class _DateMeta(type(_real_date)):
        """Metaclass that treats real dates as _FrozenDate instances."""

        def __instancecheck__(cls, instance):
            """Real ``datetime.date`` objects pass ``isinstance`` checks."""
            return isinstance(instance, _real_date)

    class _FrozenDate(_real_date, metaclass=_DateMeta):
        """Date subclass with a fixed ``today()`` for test isolation."""

        @classmethod
        def today(cls):
            """Return the frozen date instead of the wall-clock date."""
            return target_date

    target_datetime = _real_datetime.combine(
        target_date, _real_datetime.min.time()
    )

    class _DateTimeMeta(type(_real_datetime)):
        """Metaclass that treats real datetimes as _FrozenDateTime instances."""

        def __instancecheck__(cls, instance):
            return isinstance(instance, _real_datetime)

    class _FrozenDateTime(_real_datetime, metaclass=_DateTimeMeta):
        """Datetime subclass with a fixed ``now()`` aligned to target_date."""

        @classmethod
        def now(cls, tz=None):
            """Return midnight of target_date (with tz if provided)."""
            if tz is None:
                return target_datetime
            return target_datetime.replace(tzinfo=tz)

        @classmethod
        def utcnow(cls):
            """Return midnight UTC of target_date (naive, like real utcnow)."""
            return target_datetime

        @classmethod
        def today(cls):
            """Return midnight of target_date."""
            return target_datetime

    date_modules = None
    datetime_modules = None

    if modules is None:
        # Auto-discover every loaded module whose top-level ``date`` or
        # ``datetime`` symbol is the real class OR a previous frozen
        # subclass left over from an earlier ``freeze_today``
        # invocation.  Including the subclass case lets a later
        # ``freeze_today`` call (e.g. a file-level autouse) override an
        # earlier one (e.g. a conftest-level soak fixture) without
        # leaving any module holding the stale frozen value.
        date_modules = []
        datetime_modules = []
        for module_name, module in list(sys.modules.items()):
            if module is None:
                continue
            try:
                mod_date = getattr(module, "date", None)
            except (ImportError, AttributeError):
                mod_date = None
            try:
                mod_dt = getattr(module, "datetime", None)
            except (ImportError, AttributeError):
                mod_dt = None
            if mod_date is _real_date or (
                isinstance(mod_date, type)
                and mod_date is not _real_date
                and issubclass(mod_date, _real_date)
            ):
                date_modules.append(module_name)
            if mod_dt is _real_datetime or (
                isinstance(mod_dt, type)
                and mod_dt is not _real_datetime
                and issubclass(mod_dt, _real_datetime)
            ):
                datetime_modules.append(module_name)
    else:
        # Caller passed an explicit module list -- patch both date and
        # datetime in each so callers don't need to maintain two lists.
        date_modules = list(modules)
        datetime_modules = list(modules)

    for module_path in date_modules:
        try:
            monkeypatch.setattr(f"{module_path}.date", _FrozenDate)
        except (AttributeError, TypeError):
            # Module may have been unloaded or the attribute may have
            # changed shape since enumeration.  Best-effort patching.
            pass

    for module_path in datetime_modules:
        try:
            monkeypatch.setattr(f"{module_path}.datetime", _FrozenDateTime)
        except (AttributeError, TypeError):
            pass


def insert_origination_event(loan_params):
    """Append the origination :class:`LoanAnchorEvent` for a loan.

    Mirrors the production-code pattern in
    :func:`app.routes.loan.create_params` (E-18 / Commit 15) so test
    fixtures that build :class:`LoanParams` directly remain
    compatible with the resolver-routed display surfaces.  The
    resolver raises ``ValueError`` on an empty anchor-event list, so
    every fixture that hits the loan dashboard / debt strategy /
    /savings debt card / year-end net-worth liability MUST call
    this helper after inserting :class:`LoanParams`.

    Uses ``original_principal`` as the anchor balance and
    ``origination_date`` as the anchor date, matching both the
    Commit-12 migration backfill and the production setup-flow
    insert pattern.

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already
            flushed (``loan_params.account_id`` populated).

    Returns:
        The newly added :class:`LoanAnchorEvent` instance,
        ``db.session.add()``'d but not committed.  The caller's
        existing ``db.session.commit()`` carries the event into the
        same transaction.
    """
    # pylint: disable=import-outside-toplevel  -- avoid module-load
    # circular deps via models package; tests/_test_helpers loads
    # early enough that an unconditional top-level import would
    # snowball into ref_cache / Flask app bootstrapping.
    from app import ref_cache
    from app.enums import LoanAnchorSourceEnum
    from app.extensions import db
    from app.models.loan_anchor_event import LoanAnchorEvent

    event = LoanAnchorEvent(
        account_id=loan_params.account_id,
        anchor_date=loan_params.origination_date,
        anchor_balance=loan_params.original_principal,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    )
    db.session.add(event)
    return event


def insert_origination_rate(loan_params, interest_rate):
    """Append the origination :class:`RateHistory` row for a loan.

    Mirrors the production-code pattern in
    :func:`app.routes.loan.create_params` (DH-#56): the loan's base /
    period-0 rate lives in the :class:`RateHistory` row effective at
    origination, not the retired ``LoanParams.interest_rate`` column.
    The resolver raises ``ValueError`` when a loan's rate-change feed is
    empty (no origination row), so every fixture that builds
    :class:`LoanParams` directly and then resolves it (loan dashboard,
    debt strategy, /savings debt card, year-end liability, contractual
    P&I) MUST call this helper after inserting :class:`LoanParams`.

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already
            flushed (``loan_params.account_id`` populated).
        interest_rate: The origination annual rate as a Decimal fraction
            (e.g. ``Decimal("0.06875")`` for 6.875%).

    Returns:
        The newly added :class:`RateHistory` instance,
        ``db.session.add()``'d but not committed.  The caller's existing
        ``db.session.commit()`` carries the row into the same transaction.
    """
    # pylint: disable=import-outside-toplevel  -- avoid module-load
    # circular deps via models package; tests/_test_helpers loads
    # early enough that an unconditional top-level import would
    # snowball into ref_cache / Flask app bootstrapping.
    from app.extensions import db
    from app.models.loan_features import RateHistory

    row = RateHistory(
        account_id=loan_params.account_id,
        effective_date=loan_params.origination_date,
        interest_rate=interest_rate,
        monthly_pi=None,
    )
    db.session.add(row)
    return row


def insert_trueup_event(loan_params, anchor_balance, anchor_date=None):
    """Append a user-trueup :class:`LoanAnchorEvent` asserting a balance.

    Mirrors the production balance-trueup path
    (:func:`app.services.anchor_service.apply_loan_anchor_true_up`,
    E-18 / Commit 16): the operator asserts a new dated balance and the
    resolver replays forward from this latest event.  Under the
    contractual-schedule balance model, a cash overpayment does NOT
    auto-reduce the balance, so a fixture that needs a loan in a known
    state -- in particular paid off (``anchor_balance`` of
    ``Decimal("0.00")``) -- records it as the explicit operator action
    it now is: a balance true-up, exactly as the user does after making
    an extra or lump-sum payment.

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already
            flushed (``account_id`` populated).
        anchor_balance: The asserted balance (Decimal); ``0.00`` marks
            the loan paid off.
        anchor_date: The date the balance was asserted.  Defaults to
            ``origination_date + 1 day`` so it sorts strictly after the
            origination event and becomes the resolver's latest anchor.

    Returns:
        The newly added :class:`LoanAnchorEvent` (added, not committed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as insert_origination_event above.
    from datetime import timedelta
    from app import ref_cache
    from app.enums import LoanAnchorSourceEnum
    from app.extensions import db
    from app.models.loan_anchor_event import LoanAnchorEvent

    if anchor_date is None:
        anchor_date = loan_params.origination_date + timedelta(days=1)
    event = LoanAnchorEvent(
        account_id=loan_params.account_id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        ),
    )
    db.session.add(event)
    return event


def create_loan_account(
    seed_user, db_session, name="Test Loan",
    principal=None, rate=None, term=24,
    origination_date=None, payment_day=1,
):
    """Create a loan account with LoanParams, origination event, and rate.

    The single shared loan-account builder for service tests (the
    savings-dashboard, debt-summary, debt-principal-progress, and
    dashboard-pulse suites all need a resolvable loan).  Routes the
    account through the canonical ``account_service.create_account``
    factory (so it gets its origination ``AccountAnchorHistory`` row),
    inserts a ``LoanParams`` row, then seeds the origination
    ``LoanAnchorEvent`` and ``RateHistory`` the loan resolver requires --
    so a caller never has to repeat that four-step dance (DRY; the
    per-suite ``_create_small_loan`` copies were a duplicate-code finding).

    Commits before returning so the loan is fully resolvable.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        name: The account name.
        principal: The original principal (and the account anchor); both
            ``original_principal`` and ``current_principal`` are seeded
            to it.  Defaults to ``Decimal("1000.00")``.
        rate: The origination annual rate as a Decimal fraction.  Defaults
            to ``Decimal("0.05000")`` (5%).
        term: The loan term in months (default 24).
        origination_date: The loan origination date (default
            ``date(2026, 1, 1)``).
        payment_day: The day-of-month payment day (default 1).

    Returns:
        The created loan :class:`~app.models.account.Account`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above; these pull the models/services
    # package, which must not load at tests/_test_helpers import time.
    from app.models.loan_params import LoanParams
    from app.models.ref import AccountType
    from app.services import account_service

    if principal is None:
        principal = Decimal("1000.00")
    if rate is None:
        rate = Decimal("0.05000")
    if origination_date is None:
        origination_date = _real_date(2026, 1, 1)

    loan_type = (
        db_session.query(AccountType).filter_by(name="Auto Loan").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name=name,
            anchor_balance=principal,
        ),
    )
    db_session.add(account)
    db_session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=principal,
        current_principal=principal,
        term_months=term,
        origination_date=origination_date,
        payment_day=payment_day,
    )
    db_session.add(params)
    db_session.flush()
    insert_origination_rate(params, rate)
    insert_origination_event(params)
    db_session.commit()
    return account


def create_savings_account(
    seed_user, db_session, name, anchor_balance, anchor_period_id=None,
):
    """Create a Savings account via the canonical factory (flushed, uncommitted).

    The shared liquid-account builder for goal-track / savings tests, so
    the stereotyped ``AccountSpec`` + ``create_account`` + ``flush`` block
    is not copied per suite (a duplicate-code finding).  The caller
    commits with its own goal/transaction inserts.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        name: The account name.
        anchor_balance: The opening anchor balance (Decimal).
        anchor_period_id: Optional anchor period id (defaults to the
            factory's resolution when omitted).

    Returns:
        The created Savings :class:`~app.models.account.Account`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.ref import AccountType
    from app.services import account_service

    savings_type = (
        db_session.query(AccountType).filter_by(name="Savings").one()
    )
    spec_kwargs = {
        "user_id": seed_user["user"].id,
        "account_type_id": savings_type.id,
        "name": name,
        "anchor_balance": anchor_balance,
    }
    if anchor_period_id is not None:
        spec_kwargs["anchor_period_id"] = anchor_period_id
    account = account_service.create_account(
        account_service.AccountSpec(**spec_kwargs),
    )
    db_session.add(account)
    db_session.flush()
    return account


def create_hysa_account(
    seed_user, db_session, anchor_period, balance,
    apy=Decimal("0.05000"), name="HYSA",
):
    """Create an HYSA account (INTEREST) with InterestParams (default 5% APY daily).

    The shared interest-bearing-account builder (promoted from the
    balance-seam suite's per-file ``_make_hysa`` copy) so the dashboard,
    seam, and net-worth suites build an INTEREST account through one home
    rather than each re-inlining the ``AccountType`` lookup +
    ``create_account`` + ``InterestParams`` block.  Routes the account
    through the canonical ``account_service.create_account`` factory (so it
    gets its origination ``AccountAnchorHistory`` row), then attaches the
    ``InterestParams`` row (APY + daily compounding) that makes the account
    classify INTEREST.  Commits before returning so the account is fully
    resolvable.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        anchor_period: The :class:`~app.models.pay_period.PayPeriod` to
            anchor the account against; its ``id`` becomes the account's
            ``current_anchor_period_id``.
        balance: The opening anchor balance (Decimal -- construct from a
            string per the coding standard).
        apy: The annual percentage yield as a Decimal fraction (default
            ``Decimal("0.05000")`` for 5%).
        name: The account name (default ``"HYSA"``).

    Returns:
        The created HYSA :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way the loan / investment helpers do.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import CompoundingFrequencyEnum
    from app.models.interest_params import InterestParams
    from app.models.ref import AccountType
    from app.services import account_service

    hysa_type = db_session.query(AccountType).filter_by(name="HYSA").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=hysa_type.id,
            name=name,
            anchor_balance=balance,
            anchor_period_id=anchor_period.id,
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(InterestParams(
        account_id=account.id,
        apy=apy,
        compounding_frequency_id=ref_cache.compounding_frequency_id(
            CompoundingFrequencyEnum.DAILY,
        ),
    ))
    db_session.commit()
    return account


# Default opening anchor balance for ledger-account-suite accounts.  The
# Build-Order Step 2 suites never assert on a balance (Commit 2 touches no
# balance math), so a single fixed value keeps the shared factory at four
# parameters and the call sites free of an irrelevant amount.
_LEDGER_SUITE_ANCHOR_BALANCE = Decimal("100.00")


def create_account_of_type(seed_user, db_session, type_name, name):
    """Create an account of any built-in type via the canonical factory.

    The shared "build an account of type X" helper for the ledger-account
    (Build-Order Step 2) suites, so the stereotyped ``AccountType`` lookup +
    ``AccountSpec`` + ``create_account`` block is not copied per file (a
    duplicate-code finding).  ``create_account`` fires the Step-2
    ledger-account sync hook, so the returned account already carries its
    paired ``budget.ledger_accounts`` row.  The opening anchor balance is a
    fixed sentinel (the suites assert on ledger pairing, never on balance)
    and the anchor period is resolved by the factory from the user's pay
    periods.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        type_name: The ``ref.account_types`` name (e.g. ``"Checking"``,
            ``"Mortgage"``, ``"401(k)"``).
        name: The account name.

    Returns:
        The created :class:`~app.models.account.Account` (flushed,
        uncommitted).
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models / service lazily, the same way the factory helpers above do.
    # pylint: disable=import-outside-toplevel
    from app.models.ref import AccountType
    from app.services import account_service

    acct_type = (
        db_session.query(AccountType).filter_by(name=type_name).one()
    )
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=name,
            anchor_balance=_LEDGER_SUITE_ANCHOR_BALANCE,
        ),
    )


def ledger_accounts_for_account(db_session, account_id):
    """Return every ``LedgerAccount`` linked to *account_id*.

    Shared by the ledger-account model / service / backfill suites so the
    one-line lookup is not re-inlined per file (a duplicate-code finding).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id whose linked ledger accounts to
            fetch.

    Returns:
        list[:class:`~app.models.ledger_account.LedgerAccount`] -- the
        linked rows (zero or one in normal operation; the partial unique
        index permits at most one).
    """
    # Pylint: ``import-outside-toplevel`` -- same collection-time-safety
    # convention as the helpers above (no app symbols imported at module
    # load); load the model lazily here.
    # pylint: disable=import-outside-toplevel
    from app.models.ledger_account import LedgerAccount

    return (
        db_session.query(LedgerAccount)
        .filter_by(account_id=account_id)
        .all()
    )


def load_migration_module(filename):
    """Load an Alembic migration module by filename via importlib.

    ``migrations/versions`` has no ``__init__``, so a migration cannot be
    imported as an ordinary package member.  This loads one by absolute path so
    a test can invoke a migration's module-level helpers directly (e.g. the
    posting-ledger backfill's ``_backfill_settled_transfers``).  Shared by the
    posting-ledger backfill suite and the Commit-6 reconciliation oracle so the
    importlib boilerplate lives in one place (a duplicate-code finding).

    Args:
        filename: The migration module's filename (e.g.
            ``"db239773c2fd_create_journal_entries_account_postings_.py"``),
            resolved under ``<repo>/migrations/versions``.

    Returns:
        The loaded migration module object.
    """
    versions_dir = (
        pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
    )
    path = versions_dir / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clear_postings_for_transfer(transfer_id):
    """Delete a transfer's posted journal entries and legs (raw SQL).

    The posting ledger is append-only (the ORM blocks deletes on
    ``budget.journal_entries`` / ``budget.account_postings``), so this clears a
    transfer's auto-posted entries via raw SQL.  Used by the posting-ledger
    suites to reproduce the pre-ledger historical state the Commit-3 backfill
    targets: Commit 5 auto-posts on settle, so a settled transfer already
    carries go-forward postings; clearing them lets the backfill genuinely
    re-post (rather than no-opping on its ``NOT EXISTS`` guard).  Legs are
    deleted before the header for explicitness (the FK CASCADE would do it
    either way).  Commits.

    Args:
        transfer_id: The transfer whose posted entries and legs to remove.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db

    db.session.execute(db.text(
        "DELETE FROM budget.account_postings WHERE journal_entry_id IN "
        "(SELECT id FROM budget.journal_entries WHERE transfer_id = :t)"
    ), {"t": transfer_id})
    db.session.execute(db.text(
        "DELETE FROM budget.journal_entries WHERE transfer_id = :t"
    ), {"t": transfer_id})
    db.session.commit()


def clear_postings_for_transaction(transaction_id):
    """Delete an ordinary transaction's posted journal entries and legs (raw SQL).

    The transaction analog of :func:`clear_postings_for_transfer`: the posting
    ledger is append-only (the ORM blocks deletes on ``budget.journal_entries``
    / ``budget.account_postings``), so this clears a transaction's auto-posted
    entries via raw SQL, keyed on the ``journal_entries.transaction_id`` linkage.
    Used by the Commit-8 cash reconciliation oracle to reproduce the pre-ledger
    historical state the Commit-7 backfill targets: the go-forward poster
    auto-posts on settle, so a settled transaction already carries go-forward
    postings; clearing them lets the backfill genuinely re-post (rather than
    no-opping on its ``NOT EXISTS`` guard), so the two producers can be compared
    leg-for-leg.  Legs are deleted before the header for explicitness (the FK
    CASCADE would do it either way); the category/fallback ledger accounts the
    postings referenced are left in place (the backfill's ``ON CONFLICT`` reuses
    them).  Commits.

    Args:
        transaction_id: The transaction whose posted entries and legs to remove.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db

    db.session.execute(db.text(
        "DELETE FROM budget.account_postings WHERE journal_entry_id IN "
        "(SELECT id FROM budget.journal_entries WHERE transaction_id = :t)"
    ), {"t": transaction_id})
    db.session.execute(db.text(
        "DELETE FROM budget.journal_entries WHERE transaction_id = :t"
    ), {"t": transaction_id})
    db.session.commit()


_UNSET_PAID_AT = object()


def create_settled_transfer(
    seed_user, db_session, from_account, to_account, period,
    amount=Decimal("100.00"), actual_amount=None,
    paid_at=_UNSET_PAID_AT, name=None, scenario=None,
):
    """Create an ad-hoc transfer and settle it (Paid), returning the parent.

    The shared "settled transfer with two real shadows" builder for the
    posting-ledger (Build-Order Step 2) backfill / lifecycle suites.  Routes
    the whole thing through ``transfer_service`` -- the sole transfer writer --
    so the parent transfer plus its expense/income shadow transactions obey
    every transfer invariant (two balanced shadows, amounts/status/period
    mirrored), exactly as production produces them.  The transfer is created
    Projected, then transitioned to Paid via ``update_transfer`` (the same
    ``mark_done`` chokepoint the route uses).

    Flushes via the service; the caller commits.

    Args:
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id`` and
            the baseline scenario).
        db_session: The test ``db.session`` (unused directly -- the service
            owns the session -- but accepted so call sites read uniformly).
        from_account: The :class:`~app.models.account.Account` money leaves
            (the expense shadow lands here).
        to_account: The account money enters (the income shadow lands here).
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transfer (and both shadows) in.
        amount: The transfer amount (Decimal); also the shadows'
            ``estimated_amount``.  Defaults to ``Decimal("100.00")``.
        actual_amount: When not ``None``, the settled actual amount mirrored
            to both shadows (so their ``effective_amount`` becomes this, not
            ``amount``).  Defaults to ``None`` (effective == estimated ==
            amount).
        paid_at: The settle timestamp written to both shadows.  Defaults to
            ``db.func.now()`` (the realistic ``mark_done`` value); pass
            ``None`` explicitly to settle with a NULL ``paid_at`` (the
            historical state the backfill's period-start fallback covers).
        name: Optional transfer display name.
        scenario: The :class:`~app.models.scenario.Scenario` to place the
            transfer (and both shadows) in.  Defaults to ``None``, which uses
            the seed user's baseline scenario (``seed_user["scenario"]``);
            pass a non-baseline scenario to exercise multi-scenario isolation
            (the Commit-6 reconciliation oracle).

    Returns:
        The settled (Paid) parent :class:`~app.models.transfer.Transfer`.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import StatusEnum
    from app.extensions import db
    from app.services import transfer_service

    scenario_id = (
        seed_user["scenario"].id if scenario is None else scenario.id
    )
    transfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=from_account.id,
            to_account_id=to_account.id,
            pay_period_id=period.id,
            scenario_id=scenario_id,
            amount=amount,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
            name=name,
        ),
    )
    update_kwargs = {"status_id": ref_cache.status_id(StatusEnum.DONE)}
    update_kwargs["paid_at"] = (
        db.func.now() if paid_at is _UNSET_PAID_AT else paid_at
    )
    if actual_amount is not None:
        update_kwargs["actual_amount"] = actual_amount
    transfer_service.update_transfer(
        transfer.id, seed_user["user"].id, **update_kwargs
    )
    return transfer


def create_settled_cash_transaction(
    seed_user, db_session, period, amount,
    *, account=None, scenario=None, is_income=False,
    category=None, actual_amount=None, name="Cash Txn",
):
    """Create an ordinary (non-transfer) transaction and settle it go-forward.

    The cash analog of :func:`create_settled_transfer` for the Build-Order
    Step 3 posting-ledger oracle: it builds a Projected transaction, then settles
    it through the two REAL go-forward production primitives -- the status seam
    (``status_seam.apply_status_change``) and the posting builder
    (``posting_service.sync_transaction_postings``) -- in the same order the
    mark-done route applies them (seam, then the optional manual ``actual_amount``,
    then the reconcile as the last step).  So the returned transaction is genuinely
    settled (``status.is_settled``, ``paid_at`` stamped) AND its confirmed cash
    effect is posted to the double-entry ledger, exactly as production produces it
    when a user marks a transaction Paid / Received.

    Income settles to Received and expenses to Paid (Done) -- the same split the
    mark-done route applies (``mutations.py``).  A plain transaction carries no
    entries, so its effect is ``effective_amount`` (``actual`` over ``estimated``);
    callers needing the envelope debit-only effect attach credit entries
    separately (the backfill / lifecycle suites cover that path).

    Flushes via the builder; the caller commits.

    Args:
        seed_user: The ``seed_user`` (or ``seed_second_user``) fixture dict --
            supplies the default account / scenario and the owning ``user_id``.
        db_session: The test ``db.session``.
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transaction in.
        amount: The estimated amount (Decimal) -- the confirmed effect when no
            ``actual_amount`` is given.
        account: The :class:`~app.models.account.Account` the cash leg lands on;
            defaults to ``seed_user["account"]`` (the Checking account).
        scenario: The :class:`~app.models.scenario.Scenario` to place the
            transaction in; defaults to the seed user's baseline scenario.  Pass
            a non-baseline scenario to exercise multi-scenario isolation.
        is_income: When True, an Income transaction settling to Received (cash
            leg positive); otherwise an Expense settling to Paid (cash leg
            negative).
        category: The :class:`~app.models.category.Category` the counter leg
            books into, or ``None`` for the per-(owner, class) Uncategorized
            fallback.
        actual_amount: The settled actual amount (Decimal) when it diverges from
            the estimate, or ``None`` (effective == estimated == amount).
        name: The transaction display name (becomes the journal entry
            description).

    Returns:
        The settled (Paid / Received) :class:`~app.models.transaction.Transaction`,
        with its go-forward postings flushed.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import StatusEnum, TxnTypeEnum
    from app.models.transaction import Transaction
    from app.services import posting_service, status_seam

    account = seed_user["account"] if account is None else account
    scenario = seed_user["scenario"] if scenario is None else scenario
    type_id = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE
    )
    txn = Transaction(
        account_id=account.id,
        pay_period_id=period.id,
        scenario_id=scenario.id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=name,
        category_id=None if category is None else category.id,
        transaction_type_id=type_id,
        estimated_amount=amount,
    )
    db_session.add(txn)
    db_session.flush()

    # Settle through the real go-forward path: the seam flips the status and
    # stamps paid_at, the optional manual actual is applied AFTER (as the route
    # does), and the builder reconciles the ledger to the confirmed effect last.
    settled_status = StatusEnum.RECEIVED if is_income else StatusEnum.DONE
    status_seam.apply_status_change(
        txn, ref_cache.status_id(settled_status),
    )
    if actual_amount is not None:
        txn.actual_amount = actual_amount
    posting_service.sync_transaction_postings(txn, settled=True)
    return txn


def set_default_grid_account(db_session, user_id, account_id):
    """Point a user's default grid account at *account_id* (re-queried, flushed).

    Sets ``UserSettings.default_grid_account_id`` so
    ``account_resolver.resolve_grid_account``'s tier-1 picks this account --
    the way a test makes the dashboard / grid render a NON-checking account.
    Re-queries the live ``UserSettings`` row rather than mutating the
    ``seed_user["settings"]`` object directly: the account factories above
    (:func:`create_hysa_account`, :func:`make_investment_account`, ...) commit
    internally, which detaches the fixture's settings instance, so a write to
    it would be silently dropped.  Flushes so the change is visible to the
    producer under test in the same session.

    Args:
        db_session: The test ``db.session``.
        user_id: The user whose default grid account to set.
        account_id: The account id to point the default at.

    Returns:
        The live :class:`~app.models.user.UserSettings` row, updated.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention the factory helpers above follow.
    from app.models.user import UserSettings

    settings = (
        db_session.query(UserSettings).filter_by(user_id=user_id).first()
    )
    settings.default_grid_account_id = account_id
    db_session.flush()
    return settings


def make_salary_profile(
    seed_user, db_session, name="Test Salary",
    annual_salary=None, state_code="NC",
):
    """Build and add an active SalaryProfile for the seed user (uncommitted).

    The shared salary-profile builder so the stereotyped
    ``FilingStatus`` lookup + ``SalaryProfile`` construction block is not
    copied per suite (a duplicate-code finding).  The caller commits.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        name: The profile name.
        annual_salary: The annual salary (Decimal); defaults to
            ``Decimal("75000.00")``.
        state_code: The state code (default ``"NC"``).

    Returns:
        The added :class:`~app.models.salary_profile.SalaryProfile`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.ref import FilingStatus
    from app.models.salary_profile import SalaryProfile

    if annual_salary is None:
        annual_salary = Decimal("75000.00")
    filing = db_session.query(FilingStatus).first()
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=filing.id,
        name=name,
        annual_salary=annual_salary,
        state_code=state_code,
    )
    db_session.add(profile)
    return profile


def create_envelope_txn(seed_user, db_session, period, name, estimated):
    """Create an entry-tracked (is_envelope) projected expense (flushed).

    Builds a minimal Every-Period envelope template plus a Projected
    expense instance in ``period`` on the seed user's account, so the
    stereotyped template + instance construction is not copied per suite
    (a duplicate-code finding).  The caller attaches entries via
    :func:`add_entry` and commits.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            instance in.
        name: The template / transaction name.
        estimated: The envelope's estimated (budget) amount (Decimal).

    Returns:
        The created :class:`~app.models.transaction.Transaction` (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app import ref_cache
    from app.enums import StatusEnum, TxnTypeEnum
    from app.models.recurrence_rule import RecurrenceRule
    from app.models.ref import RecurrencePattern
    from app.models.transaction import Transaction
    from app.models.transaction_template import TransactionTemplate

    every_period = (
        db_session.query(RecurrencePattern)
        .filter_by(name="Every Period").one()
    )
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db_session.add(rule)
    db_session.flush()
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type_id,
        name=name,
        default_amount=estimated,
        is_envelope=True,
    )
    db_session.add(template)
    db_session.flush()
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type_id,
        estimated_amount=estimated,
        template_id=template.id,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def add_entry(db_session, seed_user, txn, amount, entry_date):
    """Attach one debit :class:`TransactionEntry` of ``amount`` to ``txn``.

    The shared single-debit-entry builder so the stereotyped entry
    construction is not copied per suite (a duplicate-code finding).
    Flushes; the caller commits.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id``).
        txn: The parent :class:`~app.models.transaction.Transaction`.
        amount: The entry amount (Decimal).
        entry_date: The entry's ``entry_date``.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.transaction_entry import TransactionEntry

    db_session.add(TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=amount,
        description="purchase",
        entry_date=entry_date,
    ))
    db_session.flush()


def add_txn(
    db_session, seed_user, period, name, amount,
    status_enum=None, is_income=False,
    due_date=None, category_key=None, is_deleted=False,
    actual_amount=None,
):
    """Create a projected (default) transaction on the seed user's account.

    The shared bare-Transaction builder for the dashboard suites (the
    route, shared-helper, and pulse-producer tests all built an identical
    ``_add_txn`` -- a duplicate-code finding).  Builds an income or expense
    row with optional actual amount, due date, category, soft-delete flag,
    and status.  Flushes; the caller commits.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies the account,
            scenario, and categories).
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transaction in.
        name: The transaction name.
        amount: The estimated amount (str or Decimal-coercible).
        status_enum: The :class:`~app.enums.StatusEnum` member; defaults to
            ``StatusEnum.PROJECTED`` (resolved lazily to avoid importing
            the enum at module-load time).
        is_income: When True, an income row; otherwise an expense row.
        due_date: The transaction's due date, or ``None``.
        category_key: A key into ``seed_user["categories"]`` to set the
            category, or ``None`` for no category.
        is_deleted: The soft-delete flag.
        actual_amount: The actual (tier-3) amount, or ``None``.

    Returns:
        The created :class:`~app.models.transaction.Transaction` (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app import ref_cache
    from app.enums import StatusEnum, TxnTypeEnum
    from app.models.transaction import Transaction

    if status_enum is None:
        status_enum = StatusEnum.PROJECTED
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    cat_id = None
    if category_key and category_key in seed_user["categories"]:
        cat_id = seed_user["categories"][category_key].id

    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(status_enum),
        name=name,
        category_id=cat_id,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        actual_amount=Decimal(str(actual_amount)) if actual_amount is not None else None,
        due_date=due_date,
        is_deleted=is_deleted,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def add_anchor_history(db_session, account, period, balance, days_ago=0):
    """Append an :class:`AccountAnchorHistory` row ``days_ago`` before now.

    The shared anchor-history builder for the dashboard route and
    pulse-producer suites (both built an identical ``_add_anchor_history``
    -- a duplicate-code finding).  The ``created_at`` is set to
    ``datetime.now(UTC) - days_ago``; under ``freeze_today`` the patched
    ``datetime.now`` returns the frozen today, so a positive ``days_ago``
    is that many days before the frozen reference.  Flushes; the caller
    commits.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` the anchor
            belongs to.
        period: The :class:`~app.models.pay_period.PayPeriod` the anchor
            is recorded against.
        balance: The anchor balance (str or Decimal-coercible).
        days_ago: How many days before now to date the row (default 0).

    Returns:
        The created :class:`AccountAnchorHistory` row (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from datetime import datetime, timedelta, timezone
    from app.models.account import AccountAnchorHistory

    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    entry = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=period.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created,
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def _pp_assert_structure(periods, user_id):
    """Assert the index/calendar invariants over an ordered period list.

    Invariants 1-3 of :func:`assert_pay_period_invariants`, factored out
    because they are pure in-memory checks over the already-loaded,
    index-ordered ``periods`` and need no database access.

    Args:
        periods: The user's :class:`PayPeriod` rows ordered by
            ``period_index`` ascending.
        user_id: The owning user's id, used only in diagnostics.
    """
    # 1. Index uniqueness (the schema enforces this; re-checking catches
    #    any path that bypasses the ORM).
    indices = [p.period_index for p in periods]
    assert len(indices) == len(set(indices)), (
        f"user {user_id}: duplicate period_index among {indices}"
    )

    for prev, cur in zip(periods, periods[1:]):
        # 2. Index order == calendar order (strictly ascending dates).
        assert cur.start_date > prev.start_date, (
            f"user {user_id}: period_index {cur.period_index} starts "
            f"{cur.start_date}, not after index {prev.period_index} "
            f"({prev.start_date}) -- index order != calendar order"
        )
        assert cur.end_date > prev.end_date, (
            f"user {user_id}: period_index {cur.period_index} ends "
            f"{cur.end_date}, not after index {prev.period_index} "
            f"({prev.end_date}) -- index order != calendar order"
        )
        # 3a. No index gaps (contiguous sequence).
        assert cur.period_index - prev.period_index == 1, (
            f"user {user_id}: period_index gap between {prev.period_index} "
            f"and {cur.period_index}"
        )
        # 3b. No date overlap (each period starts after the prior ends).
        assert cur.start_date > prev.end_date, (
            f"user {user_id}: period {cur.period_index} ({cur.start_date}) "
            f"overlaps period {prev.period_index} (ends {prev.end_date})"
        )


def assert_pay_period_invariants(db_session, user_id):
    """Assert a user's pay-period structure is not corrupt (Discipline 1).

    The single source of truth for "this user's period structure is
    sound," called after EVERY pay-period mutation test (extend /
    truncate / regenerate / top-up / reset).  A pay period is the spine
    of every financial number in Shekel, and period corruption produces
    a silently wrong balance rather than an error, so this helper exists
    to make that corruption impossible to ship undetected.  See
    ``docs/plans/implementation_plan_pay_period_crud.md`` (Test plan,
    Discipline 1).

    Raises ``AssertionError`` (with a diagnostic) on the first violated
    invariant:

      1. ``period_index`` is unique per user.
      2. ``period_index`` order == calendar order (strictly ascending
         ``start_date`` AND ``end_date``) -- the exact property the
         balance resolver walks and trusts.
      3. No ``period_index`` gaps and no date overlaps (the BA-03 /
         BA-04 anomalies the production integrity checker flags).
      4. Every account's anchor points at a live period owned by the user.
      5. Every transfer has exactly two shadow transactions, both in the
         transfer's (still-existing) period.
      6. No transaction references a pay period that no longer exists.

    Args:
        db_session: The test ``db.session``.
        user_id: The user whose pay-period structure to validate.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way every helper above does.
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from app.models.pay_period import PayPeriod
    from app.models.transaction import Transaction
    from app.models.transfer import Transfer

    periods = (
        db_session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.period_index)
        .all()
    )
    _pp_assert_structure(periods, user_id)

    period_ids = {p.id for p in periods}

    # 4. Anchor integrity: every account anchors to one of the user's
    #    live periods.
    for account in db_session.query(Account).filter_by(user_id=user_id):
        assert account.current_anchor_period_id in period_ids, (
            f"user {user_id}: account {account.id} anchor points at period "
            f"{account.current_anchor_period_id}, not among the user's periods"
        )

    # 5. Transfer invariant: exactly two shadows, both in the transfer's
    #    own (surviving) period.
    for transfer in db_session.query(Transfer).filter_by(user_id=user_id):
        shadows = transfer.shadow_transactions
        assert len(shadows) == 2, (
            f"user {user_id}: transfer {transfer.id} has {len(shadows)} "
            f"shadow transactions, expected exactly 2"
        )
        assert transfer.pay_period_id in period_ids, (
            f"user {user_id}: transfer {transfer.id} is in period "
            f"{transfer.pay_period_id}, not among the user's periods"
        )
        for shadow in shadows:
            assert shadow.pay_period_id == transfer.pay_period_id, (
                f"user {user_id}: shadow {shadow.id} of transfer "
                f"{transfer.id} is in period {shadow.pay_period_id}, not the "
                f"transfer's period {transfer.pay_period_id}"
            )

    # 6. No transaction (scoped via its account) references a period that
    #    no longer exists -- the CASCADE FK enforces this; re-checking
    #    catches an ORM bypass after a bulk delete.
    orphans = (
        db_session.query(Transaction.id)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(Account.user_id == user_id, PayPeriod.id.is_(None))
        .count()
    )
    assert orphans == 0, (
        f"user {user_id}: {orphans} transaction(s) reference a deleted "
        f"pay period"
    )


def make_every_period_rule(db_session, user_id):
    """Create and flush an ``Every Period`` recurrence rule for the user.

    The shared rule builder for the pay-period CRUD test suites (extend /
    truncate / regenerate), so the template builders below and their
    callers do not each re-derive it.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.models.recurrence_rule import RecurrenceRule
    from app.models.ref import RecurrencePattern

    pattern = (
        db_session.query(RecurrencePattern).filter_by(name="Every Period").one()
    )
    rule = RecurrenceRule(user_id=user_id, pattern_id=pattern.id)
    db_session.add(rule)
    db_session.flush()
    return rule


def make_expense_template(db_session, seed_user, amount="1200.00", is_active=True):
    """Create and flush an every-period expense template on the seed account.

    Shared by the pay-period CRUD test suites so the
    ``RecurrenceRule`` + ``TransactionTemplate`` construction block is
    defined once.  The caller commits.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.models.ref import TransactionType
    from app.models.transaction_template import TransactionTemplate

    rule = make_every_period_rule(db_session, seed_user["user"].id)
    expense_type = (
        db_session.query(TransactionType).filter_by(name="Expense").one()
    )
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Rent",
        default_amount=Decimal(amount),
        is_active=is_active,
    )
    db_session.add(template)
    db_session.flush()
    return template


def make_transfer_template(db_session, seed_user, to_account, amount="200.00"):
    """Create and flush an every-period transfer template (checking -> to).

    Shared by the pay-period CRUD test suites so the
    ``RecurrenceRule`` + ``TransferTemplate`` construction block is
    defined once.  The caller commits.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.models.transfer_template import TransferTemplate

    rule = make_every_period_rule(db_session, seed_user["user"].id)
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        recurrence_rule_id=rule.id,
        name="To Savings",
        default_amount=Decimal(amount),
    )
    db_session.add(template)
    db_session.flush()
    return template


def make_appreciating_account(seed_user, db_session, anchor_period, balance, rate):
    """Create a Property account (APPRECIATING) with AssetAppreciationParams.

    The shared appreciating-asset builder for the balance-seam parity
    suite and the cross-page balance-equality lock (promoted from the
    per-suite ``_make_property`` copies).  Routes the account through the
    canonical ``account_service.create_account`` factory (so it gets its
    origination ``AccountAnchorHistory`` row), then attaches the
    ``AssetAppreciationParams`` row that carries the annual appreciation
    rate so the account classifies APPRECIATING.  Commits before
    returning so the account is fully resolvable.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        anchor_period: The :class:`~app.models.pay_period.PayPeriod` to
            anchor the account against; its ``id`` becomes the account's
            ``current_anchor_period_id``.
        balance: The user-set market value, used as the anchor balance
            (Decimal -- construct from a string per the coding standard).
        rate: The annual appreciation rate as a Decimal fraction (e.g.
            ``Decimal("0.03000")`` for 3%).

    Returns:
        The created Property :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way the loan helpers above do.
    # pylint: disable=import-outside-toplevel
    from app.models.asset_appreciation_params import AssetAppreciationParams
    from app.models.ref import AccountType
    from app.services import account_service

    property_type = (
        db_session.query(AccountType).filter_by(name="Property").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=property_type.id,
            name="House",
            anchor_balance=balance,
            anchor_period_id=anchor_period.id,
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(AssetAppreciationParams(
        account_id=account.id, annual_appreciation_rate=rate,
    ))
    db_session.commit()
    return account


def make_investment_account(
    seed_user, db_session, anchor_period, balance, name="401k",
    employer_type="none", match_pct=None, match_cap_pct=None,
):
    """Create a 401(k) account (INVESTMENT) with InvestmentParams (7% return).

    The shared investment-account builder for the balance-seam parity
    suite and the cross-page balance-equality lock (promoted from the
    per-suite ``_make_401k`` copies).  Routes the account through the
    canonical ``account_service.create_account`` factory, then attaches an
    ``InvestmentParams`` row (7% assumed annual return) so the account
    classifies INVESTMENT.  Commits before returning so the account is
    fully resolvable.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        anchor_period: The :class:`~app.models.pay_period.PayPeriod` to
            anchor the account against; its ``id`` becomes the account's
            ``current_anchor_period_id``.
        balance: The opening anchor balance (Decimal -- construct from a
            string per the coding standard).
        name: The account name (default ``"401k"``); parameterised so a
            caller can seed two investment accounts for one user without
            colliding on the ``(user_id, name)`` unique constraint.
        employer_type: The :class:`~app.enums.EmployerContributionTypeEnum`
            value (default ``"none"`` -- no employer contribution).
        match_pct: Employer match percentage (Decimal) or ``None``.
        match_cap_pct: Employer match cap percentage (Decimal) or ``None``.

    Returns:
        The created 401(k) :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way the loan helpers above do.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import EmployerContributionTypeEnum
    from app.models.investment_params import InvestmentParams
    from app.models.ref import AccountType
    from app.services import account_service

    inv_type = db_session.query(AccountType).filter_by(name="401(k)").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=inv_type.id,
            name=name,
            anchor_balance=balance,
            anchor_period_id=anchor_period.id,
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(InvestmentParams(
        account_id=account.id,
        assumed_annual_return=Decimal("0.07000"),
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum(employer_type),
        ),
        employer_match_percentage=match_pct,
        employer_match_cap_percentage=match_cap_pct,
    ))
    db_session.commit()
    return account


# The frozen 7d63 counter-account column list, and the same list with the
# Step-4 ``kind_id`` added.  Module-level so :func:`inject_cash_backfill_kind_id`
# reuses a single source for both Pass-A INSERTs.
_FROZEN_COUNTER_COLUMNS = "(user_id, class_id, category_id, is_fallback, name) "
_KIND_INJECTED_COUNTER_COLUMNS = (
    "(user_id, class_id, category_id, is_fallback, name, kind_id) "
)


def _inject_pass_a_kind(frozen_sql, name_expr_tail, kind_name):
    """Add the kind_id column + its name-resolving subquery to a Pass-A INSERT.

    Reuses the frozen, immutable shipped SQL as the single source of every other
    column; appends the kind subquery to the end of the SELECT list (right after
    ``name_expr_tail`` -- the frozen text closing that INSERT's ``name``
    expression).  Asserts the transform fired so a future change to the shipped
    constant fails loudly here rather than silently emitting kind-less SQL that
    trips the NOT NULL at insert.

    Args:
        frozen_sql: The frozen Pass-A INSERT ... SELECT statement.
        name_expr_tail: The exact frozen text that closes the SELECT's ``name``
            expression (the anchor the kind subquery is appended after).
        kind_name: The ``ref.ledger_account_kinds`` name this INSERT's rows take
            (``"category"`` or ``"fallback"``).

    Returns:
        str -- the INSERT with ``kind_id`` added to the column list and the kind
        subquery added to the SELECT list.
    """
    subquery = (
        f"(SELECT id FROM ref.ledger_account_kinds WHERE name = '{kind_name}')"
    )
    injected = (
        frozen_sql
        .replace(_FROZEN_COUNTER_COLUMNS, _KIND_INJECTED_COUNTER_COLUMNS)
        .replace(name_expr_tail, f"{name_expr_tail.rstrip()}, {subquery} ")
    )
    assert _KIND_INJECTED_COUNTER_COLUMNS in injected and subquery in injected, (
        "kind_id injection did not fire -- the frozen 7d63 Pass-A SQL changed; "
        "update the anchors in tests/_test_helpers.py"
    )
    return injected


def inject_cash_backfill_kind_id(monkeypatch, migration_module):
    """Inject the Step-4 ``kind_id`` into a 7d63 migration's frozen Pass-A SQL.

    Step 4, Commit 2 (``efca4315bf81``) added a NOT NULL
    ``budget.ledger_accounts.kind_id``, so the frozen 7d63 Pass-A INSERTs --
    which predate that column and omit it -- can no longer run standalone at
    HEAD.  In production 7d63 ran at its own revision (before ``kind_id``
    existed) and the Step-4 migration then backfilled each row's kind from its
    column shape (category rows -> ``category``, fallback rows -> ``fallback``);
    at HEAD the two are fused because ``kind_id`` is already NOT NULL.

    This swaps the two frozen, immutable Pass-A SQL constants on
    *migration_module* for kind-injected equivalents -- reusing the shipped
    mapping SQL as the single source -- so the migration's real
    ``_backfill_settled_transactions`` orchestration runs unchanged at HEAD with
    Pass A carrying the kind the Step-4 backfill would assign.  Shared by the
    cash-backfill suite and the cash reconciliation oracle, the two suites that
    invoke that backfill (a duplicate-code finding).  ``monkeypatch``
    auto-reverts the patched constants after each test.

    Args:
        monkeypatch: The test's ``monkeypatch`` fixture.
        migration_module: The loaded 7d63 migration module (each suite loads its
            own via :func:`load_migration_module`, so each patches its own copy).
    """
    monkeypatch.setattr(
        migration_module, "_CREATE_CATEGORY_LEDGER_ACCOUNTS_SQL",
        _inject_pass_a_kind(
            migration_module._CREATE_CATEGORY_LEDGER_ACCOUNTS_SQL,
            "LEFT(c.group_name || ': ' || c.item_name, 100) ",
            "category",
        ),
    )
    monkeypatch.setattr(
        migration_module, "_CREATE_FALLBACK_LEDGER_ACCOUNTS_SQL",
        _inject_pass_a_kind(
            migration_module._CREATE_FALLBACK_LEDGER_ACCOUNTS_SQL,
            "ELSE 'Uncategorized Expense' END ",
            "fallback",
        ),
    )
