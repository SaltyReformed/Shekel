"""
Shekel Budget App -- shared test helper utilities.

Underscore-prefixed module name keeps pytest from collecting it as a
test file.  Import functions from here in test modules that need them.
"""

import re
import sys

from datetime import date as _real_date, datetime as _real_datetime


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
