"""
Shekel Budget App -- shared test helper utilities.

Underscore-prefixed module name keeps pytest from collecting it as a
test file.  Import functions from here in test modules that need them.
"""

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
