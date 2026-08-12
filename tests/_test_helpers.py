"""
Shekel Budget App -- shared test helper utilities.

Underscore-prefixed module name keeps pytest from collecting it as a
test file.  Import functions from here in test modules that need them.
"""

import importlib.util
import os
import pathlib
import re
import sys
import weakref

from collections import namedtuple
from datetime import (
    date as _real_date,
    datetime as _real_datetime,
    timedelta as _real_timedelta,
    timezone as _real_timezone,
)
from decimal import Decimal


# The synthetic split-loan fixture shared verbatim by the three parallel
# loan-posting suites (unit / reconciliation-oracle / wiring): a $250,000 loan
# originated 2025-01-01 at 6%, trued up to $100,000 on 2026-01-10.  The trueup
# balance deliberately differs from origination so a correct interest figure
# proves the walk's anchor reset (a payment before the trueup accrues on
# $250,000, one after on $100,000).  ``p1`` / ``p2`` / ``p3`` are the
# ``seed_periods`` indices whose monthly due dates (payment_day=1) land in
# distinct months after the anchor (02-01 / 03-01 / 04-01).  Single-sourced here
# so a fixture change touches one place, not three; each suite unpacks it into
# its own module-level names.
_SplitLoan = namedtuple(
    "_SplitLoan",
    "origination_principal origination_date rate anchor_balance anchor_date "
    "p1 p2 p3",
)
SPLIT_LOAN = _SplitLoan(
    origination_principal=Decimal("250000.00"),
    origination_date=_real_date(2025, 1, 1),
    rate=Decimal("0.06000"),
    anchor_balance=Decimal("100000.00"),
    anchor_date=_real_date(2026, 1, 10),
    p1=1, p2=3, p3=5,
)


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


def freeze_today(monkeypatch, target_date, modules=None, at_time=None):
    """Patch ``date.today()`` and ``datetime.now()`` to ``target_date``.

    Production services import ``date`` (and sometimes ``datetime``) at
    module load time (e.g. ``from datetime import date``), so
    monkeypatching ``datetime.date`` globally does not affect them.
    Each module that uses ``date.today()`` or ``datetime.now()`` must
    be patched individually.  This helper hides that boilerplate.

    Patches BOTH ``date`` and ``datetime`` so that test helpers using
    ``datetime.now() - timedelta(days=N)`` align with production code
    using ``date.today()``.  ``datetime.now()`` returns NOON of
    ``target_date`` (timezone-aware when ``tz`` is passed).

    **Noon, not midnight, and the DATABASE half of this freeze has always
    said so.**  :func:`_freeze_db_clock` pins PostgreSQL's clock with
    :func:`settle_instant_on` -- noon UTC -- precisely "so the frozen date the
    test reads from ``date.today()`` and the date the app RENDERS for these
    rows are the same date".  This half used MIDNIGHT UTC, which is the
    previous EVENING in ``America/New_York``: under ruling R-DH (b) every
    display-timezone reader (``app.utils.dates.display_today``, the analytics
    tax year, the loan YTD chips, and since plan step 2 the day an anchor
    assertion is filed under) then answered ``target_date - 1``.  Two halves of
    one freeze, twelve hours and one civil date apart, with the rule stated
    correctly in one of them.  It is finding N-132's shape a sixth time --
    midnight UTC used to MEAN a civil day -- and it is fixed here rather than
    worked around at each reader.

    **Both halves now take the SAME instant, ``at_time`` included** (plan step
    X-f1b).  The database half derived its own noon from ``target_date`` until
    then, so a caller that named an EVENING instant -- the only kind that can
    tell a display-timezone rule apart from a UTC one, which is what finding
    **N-182** needed -- re-opened the same split the paragraph above closed, in
    the direction nothing had exercised.  Nothing moves at the default: noon UTC
    on ``target_date`` is exactly what the database half computed for itself.

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
        at_time:
            Optional wall time on ``target_date`` for the frozen ``now``,
            overriding the noon default.  For a test that means a specific
            INSTANT rather than a day -- the New Year boundary cases, where
            ``time.min`` (midnight UTC) is deliberately the previous EVENING in
            the display timezone.  Saying it beats relying on the default: the
            boundary tests read midnight out of this helper as an undocumented
            implementation detail, and one of their own siblings already hand-
            rolled a stub because the helper offered no way to ask.
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

    # Noon by default, so this half agrees with ``_freeze_db_clock``'s -- see
    # the docstring.  Naive here because ``now(tz=None)`` must stay naive; the
    # ``tz``-aware branch below stamps it without shifting the wall time, which
    # is the same instant ``settle_instant_on`` builds for a UTC caller.
    target_datetime = _real_datetime.combine(
        target_date,
        at_time if at_time is not None
        else _real_datetime.min.time().replace(hour=12),
    )

    class _DateTimeMeta(type(_real_datetime)):
        """Metaclass that treats real datetimes as _FrozenDateTime instances."""

        def __instancecheck__(cls, instance):
            return isinstance(instance, _real_datetime)

    class _FrozenDateTime(_real_datetime, metaclass=_DateTimeMeta):
        """Datetime subclass with a fixed ``now()`` aligned to target_date."""

        @classmethod
        def now(cls, tz=None):
            """Return noon of target_date (with tz if provided)."""
            if tz is None:
                return target_datetime
            return target_datetime.replace(tzinfo=tz)

        @classmethod
        def utcnow(cls):
            """Return noon UTC of target_date (naive, like real utcnow)."""
            return target_datetime

        @classmethod
        def today(cls):
            """Return noon of target_date."""
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

    # BOTH halves of the freeze take the SAME instant, including when the caller
    # named one with ``at_time``.  Handing this half ``target_date`` (and letting
    # it re-derive noon) is the exact defect the docstring above records: two
    # halves of one freeze, hours and a civil date apart.  With the default
    # ``at_time`` this value IS ``settle_instant_on(target_date)``, so nothing
    # moves for a test that does not ask for an instant.
    _freeze_db_clock(
        monkeypatch, target_datetime.replace(tzinfo=_real_timezone.utc),
    )


# ── the DATABASE half of the frozen clock (finding N-65) ──────────────

#: The frozen instant every database-clock value is issued from, or ``None``
#: when no test has frozen its clock.  Set through ``monkeypatch`` by
#: :func:`freeze_today`, so it reverts with the test that set it, and read by
#: the two listeners below -- which are therefore inert outside a frozen test
#: and never need removing.
_FROZEN_DB_CLOCK = None

#: Monotonic counter for :meth:`_FrozenDbClock.stamp`, held at MODULE scope so
#: it survives a re-freeze.  It used to live on the clock instance, and
#: ``freeze_today`` builds a new instance every call -- measured across the
#: suite: 133 re-freezes, 27 of them after stamps had already been issued.  No
#: two of those re-froze to the same civil date, so nothing tied; a test that
#: did would have produced exactly the ``ORDER BY created_at DESC`` coin flip
#: the microsecond step exists to prevent.
_DB_CLOCK_ISSUED = 0

#: ``mapper class -> (attribute name, stamp kind)`` for every column whose
#: INSERT value comes from the database clock.  DERIVED from the mapped columns
#: rather than listed.  Populated lazily and never invalidated: a model's
#: column defaults do not change at runtime.
_DB_CLOCK_INSERT_ATTRS = {}

#: Guard so the ``Session`` listener is attached exactly once per process.
_DB_CLOCK_LISTENERS_INSTALLED = False

#: Engines the statement rewriter is bound to.  Weak, so a discarded engine is
#: not pinned in memory.
_DB_CLOCK_BOUND_ENGINES = weakref.WeakSet()

#: A statement asking PostgreSQL what time it is, in each spelling this schema
#: can produce, so the rule tests the QUESTION and not one way of writing it.
#: ``now()`` and ``current_timestamp`` yield an instant; ``current_date`` yields
#: a DATE and must be answered with one (``transaction_entries.purchased_on``).
#:
#: Each alternative carries only the word boundary it can actually have.  The
#: first draft wrapped the group in ``\b...\b`` and matched nothing at all: the
#: group's last character is ``)``, and the next character in real SQL is ``,``
#: or a space -- two non-word characters, so the trailing ``\b`` can never
#: assert.  It was a rule that could not fire, and it was caught only because
#: it was MADE to fire on demand rather than reasoned about.
_DB_CLOCK_CALL_RX = re.compile(
    r"\bnow\s*\(\s*\)|\bcurrent_timestamp\b|\bcurrent_date\b", re.IGNORECASE,
)

#: The same question as :data:`_DB_CLOCK_CALL_RX`, asked of a ``server_default``
#: written as raw SQL text rather than as a SQLAlchemy function.
#: ``transaction_entries.purchased_on`` is ``db.text("CURRENT_DATE")``, which is a
#: ``TextClause`` and therefore invisible to an ``isinstance(..., now)`` test --
#: the gap this arm closes (found by plan step X-h's adversarial review, which
#: caught the row landing on the real wall date while its siblings were frozen).
_DB_CLOCK_TEXT_DEFAULT_RX = _DB_CLOCK_CALL_RX

#: Statement kinds the rewriter leaves alone.  DDL legitimately mentions
#: ``now()`` -- it is how the NOT NULL defaults are DECLARED -- and rewriting a
#: ``CREATE TABLE`` would bake a frozen instant into the SCHEMA.  ``DO`` is here
#: because this app's audit and posting infrastructure ships ``DO $$ ... $$``
#: blocks (``audit_infrastructure.py``, ``posting_infrastructure.py``).
_DB_CLOCK_EXEMPT_VERBS = frozenset(
    {"CREATE", "ALTER", "DROP", "COMMENT", "SET", "GRANT", "REVOKE", "TRUNCATE",
     "DO"},
)


class _FrozenDbClock:
    """The frozen instant, issued strictly increasing so write ORDER survives.

    A flat instant would give every row in a test the identical timestamp, and
    the app resolves an account's current anchor by ``ORDER BY created_at
    DESC`` -- ties there are broken arbitrarily by PostgreSQL, which would turn
    a deterministic fixture into a coin flip.  Each stamp is therefore one
    microsecond past the last, which preserves the order rows were written in
    while leaving every one of them on the frozen civil DATE.

    The counter is :data:`_DB_CLOCK_ISSUED`, at module scope, so a re-freeze
    inside one test cannot reissue an instant this process has already used.
    """

    def __init__(self, instant):
        """Store the base instant every stamp is measured from."""
        self._instant = instant

    def stamp(self):
        """Return the next instant: the frozen one, plus one microsecond."""
        global _DB_CLOCK_ISSUED  # pylint: disable=global-statement
        _DB_CLOCK_ISSUED += 1
        return self._instant + _real_timedelta(microseconds=_DB_CLOCK_ISSUED)


def _db_clock_insert_attrs(model_class):
    """Return ``(attribute, is_date)`` for every clock-defaulted column.

    DERIVED from SQLAlchemy's own mapper rather than listed by hand, which is
    the whole point: the app has 61 columns whose INSERT value comes from a
    ``NOW()`` server default plus one from a ``CURRENT_DATE`` text default
    (measured 2026-07-28), and a hand-written list of 62 entries is a copy that
    goes stale the first time someone adds a model.  A bundle hand-synchronised
    with the thing it mirrors is the same defect plan step X-r deleted from
    ``_projections``.

    **Both spellings of a default are read**, because the app uses both: a
    SQLAlchemy ``now()`` function, and raw SQL text (``db.text("CURRENT_DATE")``
    on ``transaction_entries.purchased_on``).  An ``isinstance(..., now)`` test
    alone is blind to the second, which is how that column kept landing on the
    real wall date while every timestamp beside it was frozen.

    ``is_date`` carries the column's TYPE, because a ``DATE`` column must be
    answered with a date: handing it an instant is a different value, not a
    formatting detail.

    Args:
        model_class: A mapped model class taken off a session's unit of work.

    Returns:
        A tuple of ``(attribute name, is_date)`` pairs PostgreSQL would fill if
        the INSERT omitted them.
    """
    cached = _DB_CLOCK_INSERT_ATTRS.get(model_class)
    if cached is not None:
        return cached
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import Date, DateTime, inspect as sa_inspect
    from sqlalchemy.sql.elements import TextClause
    from sqlalchemy.sql.functions import now as sa_now

    resolved = []
    for prop in sa_inspect(model_class).column_attrs:
        column = prop.columns[0]
        default = column.server_default
        if default is None:
            continue
        arg = getattr(default, "arg", None)
        is_clock = isinstance(arg, sa_now) or (
            isinstance(arg, TextClause)
            and _DB_CLOCK_TEXT_DEFAULT_RX.search(str(arg)) is not None
        )
        if not is_clock:
            continue
        is_date = isinstance(column.type, Date) and not isinstance(
            column.type, DateTime,
        )
        resolved.append((prop.key, is_date))
    resolved = tuple(resolved)
    _DB_CLOCK_INSERT_ATTRS[model_class] = resolved
    return resolved


def _stamp_omitted_db_defaults(session, _flush_context, _instances):
    """Fill the clock-defaulted columns an INSERT would otherwise omit.

    The first of finding N-65's two mechanisms, and the one the statement
    rewriter below CANNOT cover: a column left unset is simply absent from the
    INSERT, so PostgreSQL applies its ``DEFAULT NOW()`` (or ``DEFAULT
    CURRENT_DATE``) and the clock call never appears in any SQL text to
    rewrite.  ``AccountAnchorHistory.created_at`` -- the column N-65 names --
    is exactly this shape.

    Every clock column of one row takes the SAME stamp, so ``created_at ==
    updated_at`` on insert, exactly as the database would have written them.

    Args:
        session: The flushing :class:`~sqlalchemy.orm.Session`.
        _flush_context: SQLAlchemy's internal flush context (unused).
        _instances: The deprecated instances argument (unused).
    """
    if _FROZEN_DB_CLOCK is None:
        return
    for obj in list(session.new):
        attrs = _db_clock_insert_attrs(type(obj))
        if not attrs:
            continue
        stamp = _FROZEN_DB_CLOCK.stamp()
        for attr, is_date in attrs:
            # ``__dict__`` rather than ``getattr``: an unset column is simply
            # absent from it, and reading through the instrumented attribute
            # would emit a load for a value that does not exist yet.
            if obj.__dict__.get(attr) is None:
                setattr(obj, attr, stamp.date() if is_date else stamp)


def _db_clock_literal(match, stamp):
    """Return the SQL literal that answers one matched clock call.

    A ``current_date`` call yields a DATE and is answered with one; ``now()``
    and ``current_timestamp`` yield an instant.  Answering a date question with
    a timestamp would change the value's TYPE in the statement, which is a
    different defect from the one being fixed.

    Args:
        match: The regex match for the clock call.
        stamp: The frozen instant to answer with.

    Returns:
        A PostgreSQL literal, typed to match the call it replaces.
    """
    if match.group(0).lower().startswith("current_date"):
        return f"DATE '{stamp.date().isoformat()}'"
    return f"TIMESTAMPTZ '{stamp.isoformat()}'"


def _rewrite_db_clock_calls(
    _conn, _cursor, statement, parameters, _context, _executemany,
):
    """Answer every rendered clock call with the frozen instant.

    The second of finding N-65's two mechanisms, and it is one rule covering
    what would otherwise be several.  A clock call reaches the database more
    than one way, and only some of them are column defaults:

    * a modified row's ``onupdate=NOW()`` columns render ``updated_at=now()``
      into the UPDATE;
    * a BULK update (``carry_forward_service``'s ``query.update(...)``) renders
      the same thing while bypassing the ORM unit of work entirely, so no
      session-level listener can reach it.

    All three render the call into the SQL, so rewriting the SQL is the ONE
    place that answers all three.  An earlier draft stamped the first two on
    the mapped objects instead and left the third: the full suite reported 41
    failures, every one of them a bulk UPDATE, which is how the third was found
    rather than reasoned about.

    Every occurrence in one statement takes the same instant, so a row's
    ``created_at`` and ``updated_at`` do not disagree by a microsecond.

    DDL is exempt: DECLARING a ``DEFAULT now()`` column is not asking the time,
    and rewriting a ``CREATE TABLE`` would bake a frozen instant into the
    SCHEMA.  Bound parameters are untouched -- the rewrite is on the statement
    TEXT, and a value that happens to read ``now()`` travels as a parameter,
    never as SQL.

    Args:
        _conn: The DBAPI connection wrapper (unused).
        _cursor: The DBAPI cursor (unused).
        statement: The SQL about to be executed.
        parameters: Bound parameters, returned unchanged.
        _context: The execution context (unused).
        _executemany: Whether this is an executemany (unused).

    Returns:
        ``(statement, parameters)`` -- the statement with every clock call
        replaced by a typed literal, or the original pair when there is nothing
        to rewrite.
    """
    if _FROZEN_DB_CLOCK is None:
        return statement, parameters
    verb = statement.split(None, 1)
    if verb and verb[0].upper() in _DB_CLOCK_EXEMPT_VERBS:
        return statement, parameters
    if not _DB_CLOCK_CALL_RX.search(statement):
        return statement, parameters
    stamp = _FROZEN_DB_CLOCK.stamp()
    return (
        _DB_CLOCK_CALL_RX.sub(
            lambda match: _db_clock_literal(match, stamp), statement,
        ),
        parameters,
    )


def bind_db_clock_rewriter(engine):
    """Attach the statement rewriter to *engine*, once per engine.

    **Called from the session-scoped ``setup_database`` fixture, before any
    test runs**, and that timing is the whole point.  It used to be called
    lazily from the flush listener, which made the rewriter's INSTALLATION
    depend on some earlier ORM flush having happened under a frozen clock in
    the same worker process -- so a frozen test whose only writes were bulk
    ``query.update(...)`` never bound it and silently got the real wall clock.
    Measured by plan step X-h's adversarial review, same test, same assertion:

        fresh process                     -> updated_at 2026-07-28 (WRONG)
        after another test had flushed     -> updated_at 2026-03-20 (right)

    A fail-OPEN gate whose result depends on test ORDER, which is the exact
    class the sibling commit deleted from the checker suite (finding N-45).
    Binding eagerly removes the dependency: the listener is always attached and
    reads the frozen clock at call time, so it is inert until a test freezes.

    A class-level ``Engine`` listener is NOT an alternative: it does not reach
    an engine that already exists -- measured, ``event.contains(Engine, ...)``
    reported True while the live engine's own dispatch did not hold the
    listener and it never ran.

    Args:
        engine: The :class:`~sqlalchemy.engine.Engine` the suite writes through.
    """
    # Pylint: ``import-outside-toplevel`` -- see :func:`_db_clock_insert_attrs`.
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import event

    if engine in _DB_CLOCK_BOUND_ENGINES:
        return
    event.listen(
        engine, "before_cursor_execute", _rewrite_db_clock_calls, retval=True,
    )
    _DB_CLOCK_BOUND_ENGINES.add(engine)


def _freeze_db_clock(monkeypatch, frozen_instant):
    """Make the DATABASE's clock agree with the clock the test just froze.

    The structural half of finding N-65 (balance plan step X-h).  Freezing
    ``date.today()`` alone leaves PostgreSQL's clock untouched, and the
    database answers it in several places: 61 columns take their INSERT value
    from a ``NOW()`` server default, one from a ``CURRENT_DATE`` text default,
    and 23 of them re-stamp on UPDATE.  So a fixture that settled a row "now"
    stamped it months outside the pay periods the test seeded, and the balance
    fold -- which dates every event -- replayed it outside the window entirely.
    Nothing noticed while the shipping producers read the LATEST anchor row and
    ignored its date; the fold made the instant load-bearing.

    **One of the FOUR reaches this was written for is gone, closed
    structurally rather than contained** (plan step X-f1, ruling R-EC).
    ``status_seam`` assigned ``db.func.now()`` to ``Transaction.settled_on``
    outright -- the one reach that was not a schema derivation, so nothing but
    this rewriter could see it.  The seam stamps ``display_today()`` into a
    ``DATE`` column now, which is a Python value the ``date.today()`` freeze
    already governs.  The rewriter stays for the other reaches, which are
    schema-level and not going anywhere.

    Two mechanisms cover it, and each covers what the other structurally
    cannot: :func:`_stamp_omitted_db_defaults` for a default that never appears
    in any SQL, and :func:`_rewrite_db_clock_calls` for every call that does --
    including the bulk updates no session listener can see.  The rewriter is
    bound eagerly by the ``setup_database`` fixture
    (:func:`bind_db_clock_rewriter`), never lazily, so neither mechanism
    depends on the other having run.

    The instant is the one :func:`freeze_today` froze ``datetime.now()`` to, so
    the two halves cannot disagree about WHEN "now" is.  By default that is noon
    UTC -- :func:`settle_instant_on`'s value, this suite's existing primitive for
    "a deterministic instant on this civil day", the same civil day in the
    display timezone, so the frozen date the test reads from ``date.today()`` and
    the date the app RENDERS for these rows are the same date.  A caller that
    names an instant with ``at_time`` gets THAT instant here too: an
    evening-Eastern freeze (the only kind that can tell a display-timezone rule
    apart from a UTC one) would otherwise leave the database eleven hours and one
    civil date away from the app, which is the split this docstring's sibling
    paragraph in :func:`freeze_today` was written about.

    Production is unchanged, and deliberately so.  The database is the right
    authority for a production timestamp -- two app workers with skewed clocks
    would stamp inconsistent instants -- so the fix belongs in the harness, not
    in the models.

    Stated boundary: a timestamp written by a database TRIGGER (the
    ``system.audit_log`` rows) still comes from the real clock, because the
    trigger's own clock call is never rendered into a statement this sees.  No
    balance producer reads those rows, and freezing them would take a
    ``search_path`` override that fails OPEN the moment anything resets the
    path.

    Args:
        monkeypatch: pytest's ``monkeypatch`` fixture, so the frozen clock
            reverts with the test that set it.
        frozen_instant: The aware-UTC instant :func:`freeze_today` froze
            ``datetime.now()`` to -- noon UTC on the target date unless the
            caller named another with ``at_time``.
    """
    global _DB_CLOCK_LISTENERS_INSTALLED  # pylint: disable=global-statement
    # Pylint: ``import-outside-toplevel`` -- see :func:`_db_clock_insert_attrs`.
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not _DB_CLOCK_LISTENERS_INSTALLED:
        event.listen(Session, "before_flush", _stamp_omitted_db_defaults)
        _DB_CLOCK_LISTENERS_INSTALLED = True
    monkeypatch.setattr(
        f"{__name__}._FROZEN_DB_CLOCK",
        _FrozenDbClock(frozen_instant),
    )


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


def _sync_loan_ledger(loan_account_id):
    """Reconcile a loan's genesis ledger, exactly as every production writer does.

    The shared write-through step of the three loan fixtures below
    (:func:`create_loan_account`, :func:`insert_trueup_event`,
    :func:`insert_tracking_start_event`).  Every production path that creates or
    re-bases a loan's anchors reconciles the genesis postings in the SAME
    transaction as the source row:

    * ``loan.create_params`` / ``loan.update_params`` call
      :func:`~app.services.loan_posting_service.sync_loan_postings_all_scenarios`
      (``app/routes/loan/params.py:125`` and ``:177``).
    * The balance true-up and the tracking-start opening both route through
      :func:`app.services.anchor_service._append_loan_anchor_and_sync`, which
      appends the event, re-syncs, and commits (``anchor_service.py:390``).

    The property that matters, and the one this helper reproduces, is that the
    event and its postings land in the SAME transaction -- never an event alone.
    This helper flushes rather than commits, keeping the existing fixture contract
    (the caller owns the transaction and commits with the rest of its setup).

    Without this, a fixture-built loan carries anchor rows but NO opening
    posting -- a state production cannot produce, and the one every loan built
    through these helpers used to be in (so it exercised the no-ledger fallback
    while production always took the ledger path).

    **Scope, stated plainly so nobody over-trusts it.**  This reaches only loans
    built through :func:`create_loan_account` / :func:`create_loan_with_trueup` /
    :func:`insert_trueup_event` / :func:`insert_tracking_start_event`.  Roughly
    twenty test modules still hand-roll a loan (a bare ``LoanParams(...)`` insert;
    e.g. ``tests/test_services/test_balance_at.py::_make_mortgage``, a near-copy of
    :func:`create_loan_account`).  Such a loan gets a ledger only if one of the
    anchor helpers above happens to be called on it afterwards, and otherwise has
    none at all -- ``test_year_end_summary_service.py``'s hand-built loan is in
    exactly that state.  That coupling is invisible at the call site and is a real
    trap; routing the hand-rolled builders through this factory is a follow-up
    (and a prerequisite for the fail-loud read seam, which turns a ledger-less loan
    into a raise).

    Deliberately calls the PLAIN sync rather than production's
    :func:`~app.services.loan_posting_service.sync_all_scenarios_or_duplicate`:
    the duplicate-translating wrapper exists to turn a user's double-click into
    idempotent success, and it does so by ROLLING BACK.  In a fixture that would
    silently discard the test's setup; a duplicate anchor written by a fixture is
    a bug in the fixture and must fail loud.

    Flushes but does NOT commit -- the caller owns the transaction boundary, the
    same contract the production chokepoints keep.

    **No future-anchor guard, because there is nothing left to guard.**  This
    helper used to assert that no user-asserted anchor was dated after the sync's
    as-of, because the walk DROPPED such an anchor and left the loan half-opened
    (opening present, true-up missing) -- a state that looked ledger-backed and
    was not.  The walk no longer reads a clock: it records every anchor the loan
    carries, whatever its date, and the readers decide what has happened
    (``loan_ledger.walk_loan_ledger``).  So a fixture's anchor
    posts whatever its date, and a fixture can no longer build the half-opened
    loan by accident.

    Args:
        loan_account_id: The loan whose genesis ledger to reconcile.
    """
    # Pylint: ``import-outside-toplevel`` -- same circular-dep avoidance as every
    # other helper in this module: the services package must not load at
    # tests/_test_helpers import time.
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.services import loan_posting_service

    loan_posting_service.sync_loan_postings_all_scenarios(loan_account_id)
    db.session.flush()


def clear_loan_ledger(loan_account_id):
    """Delete a loan's genesis postings -- the BROKEN state production cannot make.

    The exact inverse of :func:`_sync_loan_ledger`: removes the journal entries
    the loan sync produces (``loan_opening``, ``loan_trueup``, and the
    ``loan_payment`` split corrections) on any of the loan's own ledger accounts,
    leaving the Step-2/3 CASH entries untouched.  The posting ledger is
    append-only (the ORM blocks deletes on ``budget.journal_entries`` /
    ``budget.account_postings``), so it clears them via raw SQL -- the same
    mechanism, and the same rationale, as :func:`clear_postings_for_transfer`.

    A configured loan with no opening posting is NOT a legitimate state: the
    opening is written in the same transaction as the ``LoanParams``
    (``app/routes/loan/params.py:125``), the Step-4 migration backfilled every
    pre-existing loan, and ``pay_period_admin.reset_pay_periods`` re-syncs.  This
    helper exists so the handful of tests that pin behaviour ON that broken state
    -- the readers' ``None`` contract, and the seam's fail-loud raise -- can
    construct it EXPLICITLY and say so at the call site.

    It is deliberately the only way to build a ledger-less loan THROUGH THESE
    HELPERS (a hand-rolled ``LoanParams`` insert still yields one by omission --
    see :func:`_sync_loan_ledger`).  A boolean ``open_ledger=False`` knob on
    :func:`create_loan_account` would leave a casual escape hatch back onto the
    fallback path this arc exists to delete; a call to something named
    ``clear_loan_ledger`` cannot be made by accident.

    Commits (mirroring :func:`clear_postings_for_transfer`).

    Args:
        loan_account_id: The loan whose genesis ledger to remove.
    """
    # Pylint: ``import-outside-toplevel`` -- same lazy-app-import convention every
    # helper in this module follows.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import PostingSourceEnum
    from app.extensions import db

    # ID-based, never a name-string compare (the project's ref-table rule): the
    # three source kinds the loan sync emits.
    genesis_source_ids = [
        ref_cache.posting_source_id(source)
        for source in (
            PostingSourceEnum.LOAN_OPENING,
            PostingSourceEnum.LOAN_TRUEUP,
            PostingSourceEnum.LOAN_PAYMENT,
        )
    ]
    # A loan owns two shapes of ledger account: the LINKED one (``account_id``)
    # and its derived opening-equity / interest / escrow / refund ones
    # (``loan_account_id``).  Scope to entries of a genesis kind touching either.
    #
    # The entry ids are resolved to a Python list FIRST, deliberately: the
    # predicate reaches the entry THROUGH its postings, so deleting the postings
    # would empty the predicate and strand every journal-entry header behind it.
    entry_ids = [
        row[0] for row in db.session.execute(db.text("""
            SELECT DISTINCT je.id
            FROM budget.journal_entries je
            JOIN budget.account_postings p ON p.journal_entry_id = je.id
            JOIN budget.ledger_accounts la ON la.id = p.ledger_account_id
            WHERE je.source_kind_id = ANY(:src)
              AND (la.account_id = :a OR la.loan_account_id = :a)
        """), {"a": loan_account_id, "src": genesis_source_ids}).all()
    ]
    if not entry_ids:
        return
    # Legs before the header, for explicitness -- the FK CASCADE would do it
    # either way, and the ordering carries no safety property: the balanced-entry
    # constraint trigger fires AFTER INSERT OR UPDATE only
    # (``app/posting_infrastructure.py:151``), never on DELETE, so an entry cannot
    # be caught mid-delete with too few legs.
    db.session.execute(db.text(
        "DELETE FROM budget.account_postings "
        "WHERE journal_entry_id = ANY(:ids)"
    ), {"ids": entry_ids})
    db.session.execute(db.text(
        "DELETE FROM budget.journal_entries WHERE id = ANY(:ids)"
    ), {"ids": entry_ids})
    db.session.commit()


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

    Like production, the event is RECONCILED INTO POSTINGS in the same
    transaction (:func:`_sync_loan_ledger`): ``apply_loan_anchor_true_up`` appends
    the row and re-syncs every scenario
    (``anchor_service._append_loan_anchor_and_sync``, which then commits; this
    helper leaves the commit to its caller).  An un-reconciled true-up
    does not exist as far as the ledger is concerned -- and the ledger is what
    every loan surface now reads -- so a fixture that only wrote the event left
    the loan reporting its pre-true-up balance on every page.

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already
            flushed (``account_id`` populated).
        anchor_balance: The asserted balance (Decimal); ``0.00`` marks
            the loan paid off.
        anchor_date: The date the balance was asserted.  Defaults to
            ``origination_date + 1 day`` so it sorts strictly after the
            origination event and becomes the resolver's latest anchor.

    Returns:
        The newly added :class:`LoanAnchorEvent` (added and reconciled into
        postings, not committed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as insert_origination_rate above.
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
    _sync_loan_ledger(loan_params.account_id)
    return event


def insert_tracking_start_event(loan_params, anchor_balance, anchor_date):
    """Append a ``tracking_start`` :class:`LoanAnchorEvent` (mid-life import).

    Mirrors the production tracking-start path
    (:func:`app.services.anchor_service.record_loan_tracking_start`): the operator
    began tracking an already-amortizing loan and asserts its real balance as of a
    date at/before the first recorded payment.  It is loaded as an ordinary
    ``is_opening=False`` balance ASSERTION
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`) that RESETS the
    genesis walk's running balance at its own date -- the loan still opens at its
    origination (step C1), so a date at/after the tracking-start reads this
    asserted balance and a date before it reads the origination opening held flat.

    Like production, the event is RECONCILED INTO POSTINGS in the same
    transaction (:func:`_sync_loan_ledger`): ``record_loan_tracking_start``
    appends the row and re-syncs every scenario (it shares
    ``anchor_service._append_loan_anchor_and_sync`` with the true-up, which then
    commits; this helper leaves the commit to its caller).

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already flushed.
        anchor_balance: The asserted opening balance (Decimal).
        anchor_date: The date the balance was asserted (at/before the first
            recorded payment).

    Returns:
        The newly added :class:`LoanAnchorEvent` (added and reconciled into
        postings, not committed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as insert_trueup_event above.
    from app import ref_cache
    from app.enums import LoanAnchorSourceEnum
    from app.extensions import db
    from app.models.loan_anchor_event import LoanAnchorEvent

    event = LoanAnchorEvent(
        account_id=loan_params.account_id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.TRACKING_START,
        ),
    )
    db.session.add(event)
    _sync_loan_ledger(loan_params.account_id)
    return event


def create_loan_account(
    seed_user, db_session, name="Test Loan",
    principal=None, rate=None, term=24,
    origination_date=None, payment_day=1,
    *, account_type=None, anchor_balance=None,
):
    """Create a loan account with LoanParams, origination event, and rate.

    The ONE shared loan-account builder for every DB-backed loan in the suite.
    Routes the account through the canonical ``account_service.create_account``
    factory (so it gets its origination ``AccountAnchorHistory`` row), inserts a
    ``LoanParams`` row, seeds the origination ``RateHistory`` the loan resolver
    requires, and OPENS the genesis posting ledger -- so a caller never has to
    repeat that dance, and cannot accidentally omit a step.

    Seventeen suites used to hand-roll this block (a ``LoanParams(...)`` insert
    beside a copy of the account-factory call).  Every one of them omitted the
    ledger open, so their loans silently exercised the no-ledger FALLBACK that
    production never takes -- which is exactly how a $3,455.79 divergence between
    the two balance producers stayed invisible to the whole suite.  They differed
    from this factory in only two respects, which are the two knobs below:
    the account TYPE and the anchor PERIOD.  Everything else was duplication.

    **Always OPENS the loan's genesis ledger** (:func:`_sync_loan_ledger`), which
    is what ``loan.create_params`` does in the same transaction as the
    ``LoanParams`` insert (``app/routes/loan/params.py:125``).  There is no
    opt-out: a configured loan without an opening posting is a state production
    cannot produce, and a suite built on it exercises the no-ledger fallback that
    production never takes.  A test that needs that broken loan on purpose builds
    it explicitly with :func:`clear_loan_ledger`.

    The sync runs after the ``RateHistory`` insert because the genesis walk
    resolves the loan's rate periods, and the resolver raises on an empty
    rate-change feed.

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
        account_type: The :class:`~app.enums.AcctTypeEnum` member to create the
            account as; defaults to ``AUTO_LOAN``.  Keyword-only.  Resolved to its
            id through :mod:`app.ref_cache` -- the project's ref-table rule is IDs
            for logic, name strings for display only, so this takes the enum and
            never a name string.  Any amortizing type works (``MORTGAGE`` is the
            other one the suite uses).
        (It took an ``anchor_period`` until plan step X-f1c3c.  **It was LIVE,
        not vestigial** -- it reached ``AccountSpec.anchor_period_id``, which
        ``account_service.create_account`` wrote to BOTH
        ``accounts.current_anchor_period_id`` and the origination assertion's
        own ``pay_period_id``.  Rulings R-EH and R-EO deleted both
        destinations, so the parameter had nowhere left to reach.  A loan's
        balance is ledger-derived from dated ``LoanAnchorEvent`` rows
        regardless, which is why no caller loses an assertion by dropping it.
        *A first version of this note said the parameter had "zero loads in its
        body", which was measured AFTER its use had already been deleted in the
        same pass -- a measurement of a state the author had just created, and
        the reason a claims audit caught it.*)
        anchor_balance: The ACCOUNT's anchor balance, when it must differ from the
            loan's ``principal``; defaults to *principal*.  Keyword-only.  These
            are genuinely two different facts, and production can set them apart:
            the account is created first (with a user-entered anchor) and the loan
            params are configured afterwards, through a separate route.  A test
            that needs to prove the loan path is driving a balance -- rather than
            the generic account-anchor path -- makes them differ, so the anchor is
            a distinguishable decoy rather than the same number twice.

    Returns:
        The created loan :class:`~app.models.account.Account`.  Its
        :class:`LoanParams` row is reachable via :func:`loan_params_for` when a
        caller needs to append an anchor event to it.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above; these pull the models/services
    # package, which must not load at tests/_test_helpers import time.
    from app import ref_cache
    from app.enums import AcctTypeEnum
    from app.models.loan_params import LoanParams
    from app.services import account_service

    if principal is None:
        principal = Decimal("1000.00")
    if rate is None:
        rate = Decimal("0.05000")
    if origination_date is None:
        origination_date = _real_date(2026, 1, 1)
    if account_type is None:
        account_type = AcctTypeEnum.AUTO_LOAN
    if anchor_balance is None:
        anchor_balance = principal

    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=ref_cache.acct_type_id(account_type),
            name=name,
            anchor_balance=anchor_balance,
            # ``observed_on`` is left to the factory (today).  A loan's balance
            # is ledger-derived from dated ``LoanAnchorEvent`` rows, not from
            # this cash assertion, so the civil-day partition R-DH governs does
            # not reach it -- and the suites that DO care restamp the opening
            # themselves.  Recorded rather than "fixed": a blanket day-before
            # default here trips the create-time floor for a fixture whose pay
            # periods start today or later.
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
    _sync_loan_ledger(account.id)
    db_session.commit()
    return account


def loan_params_for(db_session, account_id):
    """Return a loan account's :class:`LoanParams` row.

    :func:`create_loan_account` returns the ACCOUNT, but the anchor helpers
    (:func:`insert_trueup_event`, :func:`insert_tracking_start_event`) take its
    params, so callers were each re-writing the same one-line query.  Holds it in
    one place.

    Args:
        db_session: The test ``db.session``.
        account_id: The loan account whose params to load.

    Returns:
        The loan's :class:`~app.models.loan_params.LoanParams` row.

    Raises:
        NoResultFound: If the account has no ``LoanParams`` (not a configured
            loan) -- a fixture bug, surfaced rather than papered over with None.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.models.loan_params import LoanParams

    return db_session.query(LoanParams).filter_by(account_id=account_id).one()


def add_escrow_line(
    db_session, account_id, name, annual_amount, *,
    effective_date=None, inflation_rate=None,
):
    """Create an escrow LINE with one opening version (supersession model).

    The shared escrow-setup builder for tests that exercise app paths reading
    escrow (the loan-payment split, PITI, savings-dashboard PITI, the loan-card
    breakdown).  Inserts the ``EscrowLine`` + ``EscrowComponentVersion`` pair the
    app reads (the supersession escrow model).  When
    ``effective_date`` is omitted it defaults to the loan's ``origination_date``,
    so the escrow is active for the whole loan life (the standing-charge case
    every escrow test intends); pass an explicit date to build a version that
    supersedes an earlier one.  Flushes so ids are assigned; the caller commits.

    Args:
        db_session: The test ``db.session``.
        account_id: The loan account the escrow belongs to.
        name: The escrow line display name.
        annual_amount: The opening version's stored annual amount (Decimal).
        effective_date: The version's effective date; defaults to the loan's
            origination date when omitted.
        inflation_rate: Optional decimal-fraction inflation rate for the version.

    Returns:
        The created :class:`~app.models.escrow_line.EscrowComponentVersion` (its
        ``line`` relationship carries the parent line), so a caller can mutate
        ``annual_amount`` to exercise a live escrow change.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.models.escrow_line import EscrowComponentVersion, EscrowLine
    from app.models.loan_params import LoanParams

    if effective_date is None:
        params = (
            db_session.query(LoanParams).filter_by(account_id=account_id).one()
        )
        effective_date = params.origination_date

    line = EscrowLine(account_id=account_id, name=name)
    db_session.add(line)
    db_session.flush()
    version = EscrowComponentVersion(
        line_id=line.id, effective_date=effective_date,
        annual_amount=annual_amount, inflation_rate=inflation_rate,
    )
    db_session.add(version)
    db_session.flush()
    return version


def create_loan_with_trueup(
    seed_user, db_session, *, origination_principal, anchor_balance,
    anchor_date, rate, origination_date, name="Split Loan", term=360,
    payment_day=1, escrow_annual=None, account_type=None,
):
    """Create an amortizing loan carrying an origination AND a user-trueup anchor.

    The shared "resolvable loan with a controlled latest anchor" builder for the
    Build-Order Step 4 split-service and wiring suites (a duplicate-code finding
    otherwise): routes through :func:`create_loan_account` (origination anchor +
    rate), appends a ``user_trueup`` :class:`LoanAnchorEvent` at
    *anchor_balance* / *anchor_date* -- the LATEST event, so both the split walk
    and the resolver seed their running balance from it -- and an optional
    escrow component effective from origination (so every post-anchor payment's
    date falls in its active range).  Commits, so the loan is fully resolvable.

    Keeping ``origination_principal`` distinct from ``anchor_balance`` lets a
    caller prove the split seeds from the trueup anchor, not origination.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        origination_principal: The original principal (the origination anchor).
        anchor_balance: The user-trueup balance the split walk seeds from.
        anchor_date: The user-trueup anchor date (the post-anchor lower bound).
        rate: The origination annual rate as a Decimal fraction.
        origination_date: The loan origination date.
        name: The account name (default ``"Split Loan"``).
        term: The loan term in months (default 360).
        payment_day: The day-of-month payment day (default 1).
        escrow_annual: Optional annual escrow amount (Decimal); when given, one
            escrow component effective from ``origination_date`` is added.
        account_type: The :class:`~app.enums.AcctTypeEnum` member to create the
            account as; defaults to :func:`create_loan_account`'s own default
            (``AUTO_LOAN``).  Pass ``MORTGAGE`` when the test's assertions depend
            on the loan's KIND rather than just its amortization -- Schedule A's
            mortgage-interest deduction is the case that does, and a fixture that
            takes the default silently pins an auto loan's interest into a
            home-mortgage figure.

    Returns:
        The created loan :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- same circular-dep avoidance as the
    # loan helpers above; these pull the models/services package, which must not
    # load at tests/_test_helpers import time.
    from app.models.loan_params import LoanParams  # pylint: disable=import-outside-toplevel

    loan = create_loan_account(
        seed_user, db_session, name=name, principal=origination_principal,
        rate=rate, term=term, origination_date=origination_date,
        payment_day=payment_day, account_type=account_type,
    )
    params = (
        db_session.query(LoanParams).filter_by(account_id=loan.id).one()
    )
    insert_trueup_event(params, anchor_balance, anchor_date)
    if escrow_annual is not None:
        add_escrow_line(
            db_session, loan.id, "Tax & Insurance", escrow_annual,
            effective_date=origination_date,
        )
    db_session.commit()
    return loan


def create_savings_account(seed_user, db_session, name, anchor_balance):
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

    **It took an ``anchor_period_id`` until plan step X-f1c3c** (ruling R-EH):
    an account no longer references a pay period at all, so callers that pinned
    one now pin the assertion's DAY or take the factory's default (today).

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
        # ``observed_on`` is left to the factory (today).  See
        # ``create_loan_account`` for why this helper does not take
        # ``create_account_of_type``'s day-before default.
    }
    account = account_service.create_account(
        account_service.AccountSpec(**spec_kwargs),
    )
    db_session.add(account)
    db_session.flush()
    return account


def create_hysa_account(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    seed_user, db_session, anchor_period, balance,
    apy=Decimal("0.05000"), name="HYSA", compounding=None,
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

    **The opening assertion is stamped at the anchor period's first day**
    (via :func:`restamp_opening_assertion`).  Since plan step X-c2a, modelled
    interest accrues only forward of an account's latest balance assertion
    (ruling R-L), and ``account_service.create_account`` writes that row with
    the WALL CLOCK.  A suite that freezes ``today`` inside its own
    seeded period range -- ``tests/test_services`` freezes it to 2026-03-20 --
    would otherwise build an account asserted months AFTER its own last pay
    period, a state production cannot reach (a true-up files against
    ``get_current_period``) and one in which the account accrues nothing
    anywhere.  Pinning the instant to the period's own start makes the fixture
    deterministic, reachable (an account opened on day 1 of its period), and
    clock-independent, and it keeps every hand-computed interest figure in the
    suites valid: the accrual window is then the full anchor period, exactly
    what it was before the rule existed.  A test that needs a MID-period
    assertion (the shape the rule exists for) restamps it itself.

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
        compounding: The :class:`~app.enums.CompoundingFrequencyEnum` member,
            or ``None`` for DAILY.  Parameterised at plan step X-g4b: the
            helper hardcoded DAILY, so no test anywhere ran a MONTHLY or
            QUARTERLY account through a balance PRODUCER -- and the real Money
            Market compounds MONTHLY, so a regression hardcoding the frequency
            in the replay's rate resolver would have misspriced a live account
            with the whole suite green.

    Returns:
        The created HYSA :class:`~app.models.account.Account`.

    Pylint: ``too-many-arguments`` (7/5) / ``too-many-positional-arguments``
    (7/5) -- a test FACTORY whose parameters are the account's independent
    configurable facts (owner, session, anchor period, balance, rate,
    name, compounding frequency); bundling them would put a
    parameter object between every suite and its fixture.
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
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(InterestParams(
        account_id=account.id,
        apy=apy,
        compounding_frequency_id=ref_cache.compounding_frequency_id(
            compounding or CompoundingFrequencyEnum.DAILY,
        ),
    ))
    restamp_opening_assertion(
        db_session, account, settle_instant_on(anchor_period.start_date),
    )
    db_session.commit()
    return account


# Default opening anchor balance for ledger-account-suite accounts.  The
# Build-Order Step 2 suites never assert on a balance (Commit 2 touches no
# balance math), so a single fixed value keeps the shared factory at four
# parameters and the call sites free of an irrelevant amount.
_LEDGER_SUITE_ANCHOR_BALANCE = Decimal("100.00")


def create_account_of_type(
    seed_user, db_session, type_name, name, anchor_balance=None,
    observed_on=None,
):
    """Create an account of any built-in type via the canonical factory.

    The shared "build an account of type X" helper for the ledger-account
    (Build-Order Step 2) suites, so the stereotyped ``AccountType`` lookup +
    ``AccountSpec`` + ``create_account`` block is not copied per file (a
    duplicate-code finding).  ``create_account`` fires the Step-2
    ledger-account sync hook, so the returned account already carries its
    paired ``budget.ledger_accounts`` row.  The opening anchor balance
    defaults to a fixed sentinel (the ledger-pairing suites assert on
    pairing, never on balance); the Step-5 account-anchor suites pass an
    explicit ``anchor_balance`` because the opening correction posts exactly
    that value.  The anchor period is resolved by the factory from the day the
    opening is observed on.

    **The opening defaults to the day BEFORE today, and that default is the
    point** (ruling R-DH (a), finding N-133 / F1).  An assertion is the CLOSING
    balance for its civil day, so a settle dated that same day is INSIDE it --
    and the ordinary settle idiom in these suites is the seam's own
    ``settled_on = display_today()``, which under a frozen clock is TODAY.  An account opened
    "today" therefore swallows every settle the test then records, and the
    fixture stops exercising the thing it names.  Opening the account
    yesterday is the production shape (an account exists before money moves in
    it) and makes "and then things happened" true in the data rather than only
    in the docstring.  A test that specifically needs a settle on the opening's
    OWN day passes ``observed_on`` explicitly, which is the honest way to ask
    for the case rather than inheriting it by accident.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        type_name: The ``ref.account_types`` name (e.g. ``"Checking"``,
            ``"Mortgage"``, ``"401(k)"``).
        name: The account name.
        anchor_balance: Optional opening anchor balance (``Decimal``);
            ``None`` uses the ledger-suite sentinel.
        observed_on: Optional civil day the opening balance was TRUE.
            Defaults to the DAY BEFORE the frozen today -- see below.

    Returns:
        The created :class:`~app.models.account.Account` (flushed,
        uncommitted).
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models / service lazily, the same way the factory helpers above do.
    # pylint: disable=import-outside-toplevel
    from datetime import timedelta
    from app.models.ref import AccountType
    from app.services import account_service
    from app.utils.dates import display_today

    acct_type = (
        db_session.query(AccountType).filter_by(name=type_name).one()
    )
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=name,
            anchor_balance=(
                _LEDGER_SUITE_ANCHOR_BALANCE if anchor_balance is None
                else anchor_balance
            ),
            observed_on=(
                display_today() - timedelta(days=1)
                if observed_on is None else observed_on
            ),
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
        list[:class:`~app.models.ledger_account.LedgerAccount`] -- every
        row carrying this ``account_id``.  Since Step 5's
        ``uq_ledger_accounts_account_kind`` re-key, TWO rows are normal
        once an account has anchor corrections: the ``linked`` row plus
        its ``anchor_equity`` twin (one per account-linked kind).  Callers
        asserting counts or shapes must say WHICH kinds they expect.
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


def ledger_account_of_kind(db_session, account_id, kind_enum):
    """Return an account's ledger row of a given kind (linked / anchor_equity), or None.

    The shared kind-scoped lookup behind :func:`linked_ledger_account` and the
    account-anchor suites' anchor-equity lookups.  Since Step 5's
    ``(account_id, kind_id)`` re-key an account can carry TWO rows sharing its
    ``account_id`` (the ``linked`` row plus its ``anchor_equity`` equity twin),
    so any "the account's ledger account" lookup must say WHICH kind -- a bare
    ``[0]`` on :func:`ledger_accounts_for_account` is insertion-order-dependent.
    Holding the ``(account_id, kind_id)`` query in one place keeps the several
    kind-scoped lookups the posting suites use from drifting (a duplicate-code
    finding otherwise).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id.
        kind_enum: The :class:`~app.enums.LedgerAccountKindEnum` member to
            resolve (e.g. ``LINKED`` or ``ANCHOR_EQUITY``).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` of that kind for
        the account, or ``None`` when the account has no such row yet.
    """
    # pylint: disable=import-outside-toplevel  -- same collection-time-safety
    # convention every helper in this module follows (no app symbols at module
    # load).
    from app import ref_cache
    from app.models.ledger_account import LedgerAccount

    return (
        db_session.query(LedgerAccount)
        .filter_by(
            account_id=account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind_enum),
        )
        .one_or_none()
    )


def linked_ledger_account(db_session, account_id):
    """Return the account's LINKED ledger row, never its anchor-equity twin.

    Since Step 5's ``(account_id, kind_id)`` re-key an account with posted
    anchor corrections carries TWO ledger rows sharing its ``account_id``
    (the ``linked`` row plus the ``anchor_equity`` equity twin), so any
    helper that grabs "the account's ledger account" must say WHICH kind --
    a bare ``[0]`` on :func:`ledger_accounts_for_account` is
    insertion-order-dependent.  This is the shared linked-kind lookup the
    posting suites key their cash-leg assertions off (the ``LINKED`` case of
    :func:`ledger_account_of_kind`).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id.

    Returns:
        The LINKED :class:`~app.models.ledger_account.LedgerAccount`, or
        ``None`` when the account has no pairing yet.
    """
    # pylint: disable=import-outside-toplevel  -- same collection-time-safety
    # convention as the helpers above (no app symbols at module load).
    from app.enums import LedgerAccountKindEnum

    return ledger_account_of_kind(
        db_session, account_id, LedgerAccountKindEnum.LINKED,
    )


def make_balanced_entry(
    session, seed_user, *, from_ledger_id, to_ledger_id,
    amount=Decimal("100.00"), transfer_id=None, transaction_id=None,
    source_kind=None, posting_kind=None, period_id=None,
):
    """Create and commit one balanced journal entry (two legs summing to zero).

    The from leg is ``-amount`` (credit), the to leg is ``+amount`` (debit),
    so the deferred balanced trigger passes at commit.  Returns the committed
    :class:`~app.models.journal_entry.JournalEntry`.  Shared by the
    journal-entry model suite and the ledger append-only role-privilege suite
    (previously module-local to the former -- a duplicate-code finding once
    the R4 tests needed the same shape).

    ``source_kind`` / ``transfer_id`` / ``transaction_id`` / ``posting_kind``
    default to the transfer shape (Step 2); a Step-3 transaction-sourced
    entry passes ``source_kind=PostingSourceEnum.TRANSACTION``,
    ``transaction_id=<id>``, and ``posting_kind=PostingKindEnum.EXPENSE`` (or
    ``INCOME``).  Both concrete FKs default to ``None`` so a caller sets
    exactly the one its source kind implies.  ``source_kind`` /
    ``posting_kind`` default to ``None`` (resolved lazily to the TRANSFER
    members) so the enums are not imported at module load, matching this
    module's collection-time-safety convention.

    Args:
        session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies the user,
            scenario, and bootstrap period).
        from_ledger_id: Ledger account id for the ``-amount`` leg.
        to_ledger_id: Ledger account id for the ``+amount`` leg.
        amount: The leg magnitude (Decimal).
        transfer_id: Optional ``budget.transfers`` back-link.
        transaction_id: Optional ``budget.transactions`` back-link.
        source_kind: :class:`~app.enums.PostingSourceEnum` member, or
            ``None`` for TRANSFER.
        posting_kind: :class:`~app.enums.PostingKindEnum` member, or
            ``None`` for TRANSFER.
        period_id: Pay period id, or ``None`` for the bootstrap period.

    Returns:
        The committed :class:`~app.models.journal_entry.JournalEntry`.
    """
    # Pylint: ``import-outside-toplevel`` -- same collection-time-safety
    # convention as the helpers above (no app symbols imported at module
    # load); load the enums, cache, and models lazily here.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import PostingKindEnum, PostingSourceEnum
    from app.models.journal_entry import JournalEntry, Posting

    if source_kind is None:
        source_kind = PostingSourceEnum.TRANSFER
    if posting_kind is None:
        posting_kind = PostingKindEnum.TRANSFER
    if period_id is None:
        period_id = seed_user["bootstrap_period"].id
    entry = JournalEntry(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        pay_period_id=period_id,
        entry_date=_real_date(2026, 1, 15),
        source_kind_id=ref_cache.posting_source_id(source_kind),
        transfer_id=transfer_id,
        transaction_id=transaction_id,
        description="Test entry",
    )
    session.add(entry)
    session.flush()
    kind_id = ref_cache.posting_kind_id(posting_kind)
    session.add(Posting(
        journal_entry_id=entry.id, ledger_account_id=from_ledger_id,
        amount=-amount, posting_kind_id=kind_id,
    ))
    session.add(Posting(
        journal_entry_id=entry.id, ledger_account_id=to_ledger_id,
        amount=amount, posting_kind_id=kind_id,
    ))
    session.commit()
    return entry


def loan_correction_entries(db_session, shadow_id):
    """Return the Build-Order Step 4 loan_payment corrections under a shadow.

    The ``budget.journal_entries`` rows the loan-payment posting service books
    under an income shadow's ``transaction_id`` (``source_kind = loan_payment``),
    ordered by id.  Shared by the Step-4 split-service suite and the Step-4
    wiring suite so both read a payment's corrections the same way (a
    duplicate-code finding otherwise).

    Args:
        db_session: The test ``db.session``.
        shadow_id: The loan-side income shadow's id whose corrections to fetch.

    Returns:
        list[:class:`~app.models.journal_entry.JournalEntry`] -- the correction
        entries booked under *shadow_id*, ascending by id (empty when none).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import PostingSourceEnum
    from app.models.journal_entry import JournalEntry

    return (
        db_session.query(JournalEntry)
        .filter_by(
            transaction_id=shadow_id,
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT
            ),
        )
        .order_by(JournalEntry.id)
        .all()
    )


def ledger_net(db_session, ledger_account_id, scenario_id):
    """Return the net of every posting leg on a ledger account in a scenario.

    Sums ``budget.account_postings.amount`` over *ledger_account_id* for journal
    entries in *scenario_id*.  Shared by the Step-4 split-service and wiring
    suites so both read a ledger's net the same way.

    Args:
        db_session: The test ``db.session``.
        ledger_account_id: The ledger account whose legs to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0")`` when none posted).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting

    return (
        db_session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def correction_net_in_period(
    db_session, ledger_account_id, scenario_id, source_enum, period_id,
):
    """Return one source kind's posted net on a ledger account in ONE pay period.

    The per-PERIOD reading of the ledger the R2 attribution rule is about (plan
    step X-ai-r): a reversal must net the period whose postings it corrects back
    to what the surviving facts say, rather than leaving that period overstated
    and filing the correction against a different one.  ``ledger_net`` is the
    scalar-total counterpart and ``linked_net_by_date`` the per-DATE one; this is
    the third axis, and it is the one an anchor correction is attributed on.

    Scoped the way the reconcile scopes itself -- one source kind, one ledger
    account, one scenario -- so the figure asserted here is the figure the
    reconcile reasons about.  Shared by the cash and loan anchor suites, whose
    only real difference is where an assertion's period comes from.

    Args:
        db_session: The test ``db.session``.
        ledger_account_id: The ledger account whose legs to sum (the account's
            LINKED ledger, for an anchor correction).
        scenario_id: The scenario to scope to.
        source_enum: The :class:`~app.enums.PostingSourceEnum` member naming the
            correction kind (``ACCOUNT_TRUEUP``, ``LOAN_TRUEUP``, ...).
        period_id: The pay period to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0.00")`` when the period
        holds no leg of that kind -- which is what a fully reversed period reads
        as, and the assertion most of these tests make).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting

    return (
        db_session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0.00"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.pay_period_id == period_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                source_enum,
            ),
        )
        .scalar()
    )


def linked_net_by_date(db_session, ledger_account_id, scenario_id):
    """Return a ledger account's posted net keyed by journal-entry ``entry_date``.

    Sums ``budget.account_postings.amount`` over *ledger_account_id* for journal
    entries in *scenario_id*, grouped by each entry's ``entry_date``.  A date whose
    legs net to zero (a reversed forgery) is still present, at ``Decimal("0.00")``.
    This is an INDEPENDENT re-implementation of the grouped read the
    checked-projection assert performs (production groups via ``_visible_nets``), so
    a suite comparing against it keeps teeth if that production query drifts.  Shared
    by the posting-service checked-projection suite and the loan-route escrow-sync
    suite so both read a ledger's per-date net the same way (``ledger_net`` is the
    scalar-total counterpart).

    Args:
        db_session: The test ``db.session``.
        ledger_account_id: The ledger account whose legs to sum.
        scenario_id: The scenario to scope to.

    Returns:
        A ``{entry_date: Decimal}`` mapping (empty when none posted).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting

    rows = (
        db_session.query(
            JournalEntry.entry_date,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .group_by(JournalEntry.entry_date)
        .all()
    )
    return dict(rows)


def _posted_loan_linked_ledger(loan_account_id, scenario_id):
    """Return a loan's linked ledger row, or None when it has no OPENING posting.

    The entry guard the posting window rests on, held in one place so the
    scalar and the per-period form cannot disagree about which loans the ledger
    can answer for.  A loan gets exactly one OPENING-kind leg on its linked
    ledger per scenario (the origination anchor correction, whose linked leg is
    ``-original_principal``), so its absence means the loan is not configured in
    this scenario -- an unconfigured loan, or a what-if the opening was never
    posted into.

    Args:
        loan_account_id: The loan account whose linked ledger to resolve.
        scenario_id: The budget scenario to scope to.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`, or ``None``
        when no OPENING leg is posted on it in *scenario_id*.

    Raises:
        PostingError: If the loan account has no linked ledger account at all (a
            broken chart-of-accounts pairing).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import PostingKindEnum
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting
    from app.services.posting_reads import _ledger_account_for

    linked = _ledger_account_for(loan_account_id)
    has_opening = db.session.query(
        db.session.query(Posting.id)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            Posting.posting_kind_id == ref_cache.posting_kind_id(
                PostingKindEnum.OPENING,
            ),
            JournalEntry.scenario_id == scenario_id,
        )
        .exists()
    ).scalar()
    return linked if has_opening else None


def posted_loan_balance_at(loan_account_id, scenario_id, as_of):
    """Return what the POSTED ledger says a loan owed on a date, or None.

    ``owed(as_of) = round_money(-(sum of the loan's linked-ledger postings,
    scenario-scoped, whose ``entry_date`` is on or before *as_of*))`` -- the
    opening (``-original_principal``), every settled payment's net principal (the
    Step-2 cash leg plus the Step-4 split correction), and every true-up.

    **This is the test suite's own window onto the general ledger, and that is
    deliberate.**  Plan step E1e deleted the production readers this replaces
    (``loan_posting_service.confirmed_loan_balance_at`` / ``_map``): the balance
    seam answers a loan from the event FOLD (steps C3b1 / C3b3 / E1d-b) and the
    balance sheet reads the postings through
    :mod:`app.services.ledger_report_service`, so the sum-of-postings
    balance-at-T had no production caller left -- its only remaining job was to be
    the counterparty the fold and the resolver are graded against.  An oracle's
    window belongs on the oracle's side: the reconciliation suite already states
    the rule (``_independent_loan_linked_net`` reads through a DIFFERENT join
    shape than the production readers "so the two cannot share a lookup bug"), and
    a window living here cannot be re-exported into a screen.

    Two deliberate differences from the deleted reader, both stated so a caller is
    not surprised:

    * it answers ANY date, where the reader raised for ``as_of > today``.  That
      raise was a DOMAIN guard forcing production callers to route a future date
      to the projection; there are no such callers now.  A future date sums the
      same postings (every posted entry is dated today or earlier), so it carries
      the confirmed balance FLAT -- which is exactly what the deleted per-period
      reader did, and what the early-settle parallel run reads at period ends
      beyond today.
    * the per-period form (:func:`posted_loan_balance_map`) is a plain
      per-period call, not a prefix-sum over one grouped load.  The batching
      existed for production read cost; an oracle wants the simplest derivation
      that can be checked by eye.

    Args:
        loan_account_id: The loan account whose posted balance to read.
        scenario_id: The budget scenario to scope to (postings are
            scenario-scoped via ``journal_entries.scenario_id``).
        as_of: The evaluation date.  Only postings whose ``entry_date`` is on or
            before it are summed.

    Returns:
        The posted balance owed as a cent-quantized ``Decimal``, or ``None`` when
        the loan has no OPENING posting in the scenario.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting
    from app.utils.money import round_money

    linked = _posted_loan_linked_ledger(loan_account_id, scenario_id)
    if linked is None:
        return None
    net = (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0.00"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.entry_date <= as_of,
        )
        .scalar()
    )
    # Debit-positive ledger: the linked net is -(owed).  Written ``0 - net``
    # rather than ``-net`` so a zero net (a loan read before its opening's date)
    # yields ``0.00``, never ``-0.00``.
    return round_money(Decimal("0.00") - net)


def posted_loan_balance_map(loan_account_id, scenario_id, periods):
    """Return each period's posted loan balance, keyed by period id, or None.

    The per-period form of :func:`posted_loan_balance_at`, evaluated at each
    period's END date.  A posting can fall mid-period under the one clock (step
    C2), so a payment settled during period P must count in P's balance, which
    ``entry_date <= period.end_date`` selects.

    **It is the scalar per period, not an independent derivation.**  Comparing
    this against :func:`posted_loan_balance_at` at the same date is therefore
    ``f(x) == f(x)`` -- pin a VALUE, never that identity.  Nor is it directly
    comparable to the production map
    (:func:`app.services.balance_at.positions_period_map`) except over ELAPSED
    periods: that map clamps a BEGUN period to ``min(period.end, ctx.as_of)``
    and answers a FUTURE period from the forward plan, where this window carries
    the confirmed sum flat.  The keys line up; the values only do in the past.

    Returns ``None`` -- not a map of zeros -- when the loan has no OPENING
    posting in the scenario, so a caller can tell "the ledger cannot answer for
    this loan" from "the ledger says zero".

    Args:
        loan_account_id: The loan account whose per-period balances to read.
        scenario_id: The budget scenario to scope to.
        periods: The pay periods to key by (any order; the result keys by
            ``period.id`` in the given order).  Postings in periods outside this
            list are still counted -- each period's value is a cumulative, not a
            slice.

    Returns:
        A ``{period.id: Decimal}`` mapping, or ``None`` when the loan has no
        OPENING posting in the scenario.
    """
    if _posted_loan_linked_ledger(loan_account_id, scenario_id) is None:
        return None
    return {
        period.id: posted_loan_balance_at(
            loan_account_id, scenario_id, period.end_date,
        )
        for period in periods
    }


def find_loan_ledger_account(db_session, loan_account_id, kind):
    """Return the per-loan ledger account of *kind*, or None if not created.

    The per-loan interest / escrow / refund ledger account
    (``loan_account_id`` + the ``kind`` discriminator), lazily minted by the
    Step-4 chart resolver on first use.  Shared by the Step-4 split-service and
    wiring suites.

    Args:
        db_session: The test ``db.session``.
        loan_account_id: The loan whose per-loan ledger account to find.
        kind: The :class:`~app.enums.LedgerAccountKindEnum` member
            (``LOAN_INTEREST`` / ``LOAN_ESCROW`` / ``LOAN_REFUND``).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount`, or ``None`` when
        the resolver has not minted it yet.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.models.ledger_account import LedgerAccount

    return (
        db_session.query(LedgerAccount)
        .filter_by(
            loan_account_id=loan_account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind),
        )
        .one_or_none()
    )


def loan_income_shadow(db_session, transfer_id, loan_account_id):
    """Return the loan-side income shadow of a transfer.

    The income (to-account) shadow of *transfer_id* on *loan_account_id* -- the
    row the Step-4 correction books under.  Shared by the Step-4 split-service
    and wiring suites.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The parent transfer's id.
        loan_account_id: The loan (to-)account the income shadow lives on.

    Returns:
        The loan-side income :class:`~app.models.transaction.Transaction`.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import TxnTypeEnum
    from app.models.transaction import Transaction

    return (
        db_session.query(Transaction)
        .filter_by(
            transfer_id=transfer_id,
            account_id=loan_account_id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
        )
        .one()
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


def load_init_database_module():
    """Load ``scripts/init_database.py`` by path (it is not a package member).

    ``scripts`` has no ``__init__``, so the deploy host is loaded by absolute
    path -- the same importlib idiom :func:`load_migration_module` uses -- so a
    test can call its post-migration backfill hooks directly.  The script
    mutates ``DATABASE_URL_APP`` to ``""`` at import time (its deploy-host
    owner-role override, which must run BEFORE the ``app`` import), a
    process-global side effect this restores around the load so it never leaks
    into the test session.  Shared by the loan and account backfill suites so
    the importlib + env-restore boilerplate lives in one place (a duplicate-code
    finding otherwise).

    Returns:
        The loaded ``init_database`` module object, exposing the deploy hooks
        (``backfill_loan_payment_postings_after_migration`` /
        ``backfill_all_account_anchor_postings_after_migration``).
    """
    script_path = (
        pathlib.Path(__file__).resolve().parents[1] / "scripts" / "init_database.py"
    )
    saved = os.environ.get("DATABASE_URL_APP")
    spec = importlib.util.spec_from_file_location(
        script_path.stem, str(script_path),
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            os.environ.pop("DATABASE_URL_APP", None)
        else:
            os.environ["DATABASE_URL_APP"] = saved
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


_UNSET_SETTLED_ON = object()


def settle_instant_on(day):
    """Return a deterministic event instant on a given civil date (noon UTC).

    A test-side helper for pinning an ASSERTION's recording instant to a
    specific day without a wall-clock read -- :func:`restamp_opening_assertion`,
    which :func:`create_hysa_account` uses to pin an account's opening.  Noon
    UTC is the same civil day in the display zone (Eastern), so a day pinned
    this way reads back as that day.

    **It no longer serves a SETTLE, and that is plan step X-f1** (ruling R-EC).
    A settled transaction stores its civil day in ``transactions.settled_on``,
    so :func:`create_settled_transfer` and
    :func:`create_settled_cash_transaction` take the DAY directly and a caller
    that used to wrap it here passes it plain.  What remains is the assertion
    side, where ``created_at`` is genuinely an instant.

    Args:
        day: The civil :class:`datetime.date` to place the event on.

    Returns:
        A timezone-aware :class:`datetime.datetime` at noon UTC on *day*.
    """
    from datetime import time, timezone  # pylint: disable=import-outside-toplevel
    return _real_datetime.combine(day, time(12, 0), tzinfo=timezone.utc)


def register_form_data(**overrides):
    """Return a complete, valid POST body for ``/register``.

    **One place that knows what the registration form requires**, added at plan
    step X-ad-a when that grew from four fields to seven: the form now asks for
    the owner's real pay schedule because registration stopped inventing one
    (ruling **R-DB**, finding **N-123**).  Twenty-odd call sites across four
    test modules post this body, and a field added to
    ``RegisterSchema`` without a home here would otherwise be twenty-odd edits
    -- which is the shape of test churn that ends in a suite where half the
    posts are subtly different for no stated reason.

    ``last_payday`` is the USER's today (``display_today``, ruling R-DH (b)),
    never ``date.today()``: the service bounds it against the same clock, and a
    process pinned to UTC is already tomorrow at 8pm Eastern -- so a
    process-clock default would be a FUTURE payday, refused, for four hours
    every day.  Today is the safest point in the accepted window
    ``[today - cadence + 1, today]`` because it is valid at every cadence.

    Args:
        **overrides: Field values to replace or add.  Pass ``email`` and
            ``display_name`` per test; pass a field explicitly to exercise its
            refusal (e.g. ``last_payday=""`` for the missing-date path).

    Returns:
        A ``dict`` suitable for ``client.post("/register", data=...)``.
    """
    # pylint: disable=import-outside-toplevel
    from app.config import BaseConfig
    from app.utils.dates import display_today
    body = {
        "email": "newuser@example.com",
        "display_name": "New User",
        "password": "securepass123",
        "confirm_password": "securepass123",
        "last_payday": display_today().isoformat(),
        "cadence_days": str(BaseConfig.DEFAULT_PAY_CADENCE_DAYS),
        "num_periods": str(BaseConfig.DEFAULT_PAY_PERIOD_HORIZON),
    }
    body.update(overrides)
    return body


def registration_spec(**overrides):
    """Return a complete, valid :class:`RegistrationSpec` for service tests.

    The service-tier twin of :func:`register_form_data`, and it exists for the
    same reason: ``auth_service.register_user`` takes one value object whose
    pay-calendar half arrived at plan step X-ad-a, and the tests that call it
    directly should not each restate what a valid sign-up looks like.

    Args:
        **overrides: Spec fields to replace.  ``first_payday`` defaults to the
            user's today -- see :func:`register_form_data` for why the clock
            matters.

    Returns:
        The :class:`~app.services.auth_service.RegistrationSpec`.
    """
    # pylint: disable=import-outside-toplevel
    from app.config import BaseConfig
    from app.services.auth_service import RegistrationSpec
    from app.utils.dates import display_today
    fields = {
        "email": "newuser@example.com",
        "password": "securepass123",
        "display_name": "New User",
        "first_payday": display_today(),
        "cadence_days": BaseConfig.DEFAULT_PAY_CADENCE_DAYS,
        "num_periods": BaseConfig.DEFAULT_PAY_PERIOD_HORIZON,
    }
    fields.update(overrides)
    return RegistrationSpec(**fields)


def seam_confirmed_view(loan_account_id, scenario_id, as_of):
    """Return a loan's seam CONFIRMED view at an explicit scenario and as-of.

    The walk-built :class:`~app.services.loan_resolver.ConfirmedLedgerView`
    (:func:`app.services.balance_at.confirmed_view`) -- the balance the loan's
    recorded events fold to, plus its confirmed schedule rows -- which since plan
    step E1d-b is what the resolver's confirmed slice seeds from, replacing the
    deleted posted-ledger reader ``loan_payment_service.confirmed_loan_view``.

    Several suites build an INDEPENDENT ``compute_payoff_scenarios`` reference to
    grade a seam figure against, and every one of them needs this same seed: a
    reference composed WITHOUT it would be the un-seeded anchor replay, which is a
    different trajectory (it is blind to how much cash a payment actually moved).
    Sharing one helper is what keeps those references honest and identical.

    Pins the scenario and the as-of EXPLICITLY rather than through
    ``BalanceContext.build``, because the callers are frozen-clock or
    named-scenario tests whose baseline lookup must not be re-derived.

    Args:
        loan_account_id: The loan account whose confirmed view to build.
        scenario_id: The scenario to scope the walk to.
        as_of: The evaluation date (the resolver's NOW).

    Returns:
        The ``ConfirmedLedgerView``, or ``None`` when the seam withholds one (not
        a configured loan, no scenario, a future date, or not yet originated).
    """
    # Pylint: import-outside-toplevel -- helper-local, matching this module's
    # convention of importing app symbols inside the helper that needs them.
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.account import Account  # pylint: disable=import-outside-toplevel
    from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
    from app.services import balance_at  # pylint: disable=import-outside-toplevel

    loan = db.session.get(Account, loan_account_id)
    return balance_at.confirmed_view(loan, balance_at.BalanceContext(
        user_id=loan.user_id,
        scenario=db.session.get(Scenario, scenario_id),
        as_of=as_of,
    ))


def seam_cash_balance_at(account, scenario_id, as_of):
    """Return what the SEAM says an account's cash balance is on a date.

    :func:`app.services.balance_at.cash_balance_at` -- the cash FOLD sampled at
    one date, and since plan step X-c2b2 the figure every cash-flow surface
    renders.  Shared by the five pay-period CRUD suites, which need a balance
    PROBE rather than a balance test: what they assert is that extending,
    truncating, regenerating, resetting or topping up a schedule leaves the
    projection marching correctly, and the balance is how they read that.

    They probed ``_cash_engine.balance_as_of_date`` until plan step X-c2b3
    deleted it.  Routing them here rather than at a replacement producer is the
    point: a schedule-mutation suite should read the figure a SCREEN shows, so a
    mutation that corrupts the projection fails these tests instead of passing
    them against a producer no screen reads.

    **The reader's NOW and the valuation date are DIFFERENT dates here, and
    conflating them would silently zero every probe.**  ``cash_balance_at``
    takes both: ``ctx.as_of`` is the reader's now -- the floor a still-projected
    row's landing day is clamped UP to, because a plan cannot already have
    happened (ruling R-G) -- while the trailing argument is the date being
    valued.  Passing the valuation date as both would clamp every projected row
    to ``valuation + 1`` and answer with the assertions alone.  So the now comes
    from :meth:`BalanceContext.build`, which is the app's own default
    (``date.today()`` inside ``balance_at._context``) and therefore the date a
    ``freeze_today`` suite has patched -- a helper-local ``date.today()`` would
    read the WALL clock instead, which is the N-65 / N-8 trap.

    ``scenario_id`` is load-bearing rather than decorative: the context resolves
    the BASELINE, so a caller probing any other scenario is refused here instead
    of being answered about a scenario it did not write to.

    Args:
        account: The cash account to value (session-attached).
        scenario_id: The scenario the caller wrote its rows under.  Must be the
            user's baseline, which is what every current caller uses.
        as_of: The calendar date to value at, a civil ``date``.

    Returns:
        The cent-quantized ``Decimal`` cash-flow balance at *as_of*.
    """
    # Pylint: import-outside-toplevel -- helper-local, matching this module's
    # convention of importing app symbols inside the helper that needs them.
    from app.services import balance_at  # pylint: disable=import-outside-toplevel

    ctx = balance_at.BalanceContext.build(account.user_id)
    assert ctx.scenario_id == scenario_id, (
        f"seam_cash_balance_at probes the BASELINE scenario "
        f"({ctx.scenario_id}), but was asked for {scenario_id}"
    )
    return balance_at.cash_balance_at(account, ctx, as_of)


def create_transfer(
    seed_user, db_session, from_account, to_account, period,
    amount=Decimal("100.00"), *, due_date=None, name=None, scenario=None,
):
    """Create a PROJECTED transfer with its two shadows, via the real service.

    The create half of :func:`create_settled_transfer`, which now builds on this
    one -- so both helpers route through ``transfer_service.create_transfer``,
    the sole transfer-creation chokepoint, and a test cannot accidentally
    construct a transfer that bypasses a write guard production enforces.

    Exposed separately because a test may need a transfer that is NOT settled
    (a projection), or one carrying an explicit ``due_date``: on a LOAN payment
    the due date is the installment the payment satisfies, which decides whether
    the write is even allowed (ruling R-C, plan step C9b) and which installment
    the fold splits it against.

    Flushes via the service; the caller commits.

    Args:
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id`` and the
            baseline scenario).
        db_session: The test ``db.session`` (unused directly -- the service owns
            the session -- but accepted so call sites read uniformly).
        from_account: The account money leaves (the expense shadow lands here).
        to_account: The account money enters (the income shadow lands here).
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transfer (and both shadows) in.
        amount: The transfer amount (Decimal).  Defaults to
            ``Decimal("100.00")``.
        due_date: Optional due date stored on the transfer and mirrored to both
            shadows.  ``None`` (the default) leaves it unset, which is what the
            ad-hoc transfer form produces; a loan payment's installment then
            falls back to its pay-period start.
        name: Optional transfer display name.
        scenario: The :class:`~app.models.scenario.Scenario` to place the
            transfer in.  Defaults to the seed user's baseline.

    Returns:
        The created (Projected) :class:`~app.models.transfer.Transfer`.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import StatusEnum
    from app.services import transfer_service

    scenario_id = (
        seed_user["scenario"].id if scenario is None else scenario.id
    )
    return transfer_service.create_transfer(
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
            due_date=due_date,
        ),
    )


def create_settled_transfer(
    seed_user, db_session, from_account, to_account, period,
    amount=Decimal("100.00"), actual_amount=None,
    settled_on=_UNSET_SETTLED_ON, name=None, scenario=None,
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
        settled_on: The civil DAY written to both shadows.  Defaults to the
            user's today (what the seam derives, and the realistic ``mark_done``
            value).  A loan test reading a PAST balance must pin this to the day
            it wants the payment visible from -- balance step C2 keys visibility
            on the SETTLED date -- typically the period's ``start_date``.  It
            took an INSTANT until plan step X-f1 and callers wrapped their day in
            ``settle_instant_on``; the column stores the day now, so the day is
            passed directly.  **Passing ``None`` EXPLICITLY is now REFUSED**, and
            this paragraph promised the opposite until a neutral review caught
            it: the sentinel is ``_UNSET_SETTLED_ON``, not ``None``, so an
            explicit ``None`` reaches ``update_transfer`` and, since finding
            **N-183** routed that write through the status seam, raises
            ``ValidationError`` ("the settle day cannot be cleared") rather than
            NULLing a settled pair.  A fixture that genuinely needs the broken
            settled-with-no-day row builds it with the bare :func:`add_txn`
            instead, which is what that builder is for.
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

    transfer = create_transfer(
        seed_user, db_session, from_account, to_account, period,
        amount=amount, name=name, scenario=scenario,
    )
    update_kwargs = {"status_id": ref_cache.status_id(StatusEnum.DONE)}
    if settled_on is not _UNSET_SETTLED_ON:
        update_kwargs["settled_on"] = settled_on
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
    settled_on=_UNSET_SETTLED_ON,
):
    """Create an ordinary (non-transfer) transaction and settle it go-forward.

    The cash analog of :func:`create_settled_transfer` for the Build-Order
    Step 3 posting-ledger oracle: it builds a Projected transaction, then settles
    it through the two REAL go-forward production primitives -- the status seam
    (``status_seam.apply_status_change``) and the posting builder
    (``posting_service.sync_transaction_postings``) -- in the same order the
    mark-done route applies them (seam, then the optional manual ``actual_amount``,
    then the reconcile as the last step).  So the returned transaction is genuinely
    settled (``status.is_settled``, ``settled_on`` stamped) AND its confirmed cash
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
        settled_on: The settle DAY.  Defaults to the seam-derived
            ``display_today()`` (the realistic ``mark_done`` value); pass an
            explicit ``date`` to pin the attribution day for a past-balance
            read.  Pinned BEFORE the ledger emission -- mirroring
            :func:`create_settled_transfer`'s ``settled_on`` -- so the posted
            ``entry_date`` and the Step-5 walk's attribution day agree, exactly
            as production produces them.  There is no "settle with no day"
            option: that state is the broken invariant
            ``balance_predicates.settled_day`` refuses.

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
    # stamps settled_on, the optional manual actual is applied AFTER (as the route
    # does), and the builder reconciles the ledger to the confirmed effect last.
    settled_status = StatusEnum.RECEIVED if is_income else StatusEnum.DONE
    status_seam.apply_status_change(
        txn, ref_cache.status_id(settled_status),
    )
    if settled_on is not _UNSET_SETTLED_ON:
        txn.settled_on = settled_on
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


def add_entry(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db_session, seed_user, txn, amount, purchased_on,
    *, is_credit=False, settled_on=None, description="purchase",
):
    """Attach one :class:`TransactionEntry` of ``amount`` to ``txn``.

    The shared entry builder so the stereotyped entry construction is not
    copied per suite (a duplicate-code finding).  Flushes; the caller commits.

    **The three bucket flags are parameters (plan step X-c2c2a).**  It built a
    DEBIT-only, outstanding entry until the per-row valuation tests moved to
    ``test_cash_amounts.py``, which needs all three buckets of
    ``cash_ledger._amounts._entry_checking_impact`` -- settled debit,
    outstanding debit, and credit.  Narrowness is why the helper had not
    displaced the hand-rolled copies it exists to prevent: seven suites still
    define their own ``_add_entry`` because this one could not express their
    shape.  Widening it is additive; those suites are not touched here (scope).

    **The reconciled bucket is a DATE, not a flag** (plan step S1-c, ruling
    R-DH (d)).  The stored ``is_cleared`` boolean this replaced was written by
    a bulk UPDATE at anchor true-up, so which bucket a purchase fell in
    depended on the order two buttons were pressed.  A fixture that wants the
    "already inside the asserted balance" bucket therefore passes a
    ``settled_on`` on or before the account's latest ``observed_on`` -- which
    makes the precondition it depends on VISIBLE at the call site instead of
    hidden behind a boolean.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id``).
        txn: The parent :class:`~app.models.transaction.Transaction`.
        amount: The entry amount (Decimal).
        purchased_on: The day the purchase was made.  Never after the user's
            today, which the service refuses (ruling R-M).
        is_credit: True for a credit-card purchase, which never hits checking
            directly (it leaves via the CC Payback sibling) and so only
            REDUCES the reservation whatever its dates say.
        settled_on: The day the bank took the money, or ``None`` (the default)
            for a purchase not yet seen on a statement.  A purchase whose
            ``settled_on`` is at or before the account's latest asserted day is
            already inside that balance and is SUBTRACTED from the reservation;
            every other purchase acts as its floor.  Must not precede
            *purchased_on* (``ck_transaction_entries_settled_not_before_purchase``).
        description: The entry description; the default suits a test that
            does not assert on it.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.transaction_entry import TransactionEntry

    db_session.add(TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=amount,
        description=description,
        purchased_on=purchased_on,
        settled_on=settled_on,
        is_credit=is_credit,
    ))
    db_session.flush()


def mark_purchase_settled(db_session, account, entry, settled_on=None):
    """Record the day the bank took *entry*, and CHECK the anchor covers it.

    The successor to ``add_entry(..., is_cleared=True)`` (plan step S1-c,
    ruling R-DH (d)).  The retired boolean asserted "this purchase is already
    inside the anchor balance" unconditionally, so a fixture could set it on an
    account whose only assertion PREDATED the purchase -- a state production
    cannot reach, because the way a purchase gets inside a declared balance is
    that the user declared the balance after it posted.  That is finding
    N-132 / R8's shape: a fixture asserting a state the app cannot produce
    passes for years and stops discriminating the case it names.

    So this helper does what the flag did AND states the precondition the flag
    let fixtures skip.  It fails loudly rather than silently building an
    unreachable row, and the failure message names both days.

    **The bound is TWO-SIDED, and the upper half was added after the guard's
    first pass let an unreachable row through anyway.**  Checking only
    ``settled_on <= observed_on`` invites the obvious escape: move the
    ASSERTION forward until it covers the purchase.  But an assertion is itself
    bounded -- ``anchor_service.resolve_observation_day`` refuses an
    ``observed_on`` after the user's today, because a balance you have not read
    yet is not an observation -- so an assertion dated into the app's future is
    exactly as unreachable as the anchor-months-earlier row the guard was
    written to stop.  A one-sided guard turns finding N-132's defect into a
    different N-132 defect, which is what the S1-c conversion's own review
    measured on two ``test_cash_fold`` fixtures.  Both halves are checked here
    so neither escape is open.

    The clock is :func:`app.utils.dates.display_today` -- the same one the
    service guard reads, and the one a suite's ``freeze_today`` moves -- never
    ``date.today()``, which would judge a fixture on the process timezone.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` the purchase's parent
            transaction belongs to -- the account whose assertions decide
            whether the purchase is inside a declared balance.
        entry: The :class:`~app.models.transaction_entry.TransactionEntry` to
            settle.
        settled_on: The day the bank took it.  Defaults to the entry's own
            ``purchased_on`` -- "it posted the day I bought it", the shape a
            same-day debit has.

    Returns:
        The updated entry (flushed).

    Raises:
        AssertionError: When the account has never asserted a balance, when its
            latest assertion is for a day BEFORE *settled_on*, or when that
            assertion is dated after the user's today.  In the first two the
            purchase reads as OUTSTANDING however this helper is spelled; in
            the third the account is in a state its own write door refuses.
            Either way a fixture expecting the settled bucket is asking for
            something production cannot produce.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.services.cash_ledger import reconciled_through
    from app.utils.dates import display_today

    settled_on = settled_on or entry.purchased_on
    # The guard asks the PRODUCTION rule, not a lookalike: if this helper
    # spelled the comparison itself it could come to disagree with the
    # reservation it exists to set up, which is the whole shape plan step X-f
    # is about.
    boundary = reconciled_through(account.id)
    observed_on = boundary.observed_day
    assert observed_on is not None, (
        f"account id={account.id} has asserted no balance, so no purchase can "
        f"be inside one; give it an assertion before settling entry "
        f"id={entry.id}"
    )
    assert boundary.covers(settled_on), (
        f"entry id={entry.id} settled {settled_on} is AFTER account "
        f"id={account.id}'s latest asserted day {observed_on}, so the "
        f"projection reads it as outstanding.  Move the account's assertion to "
        f"{settled_on} or later, or the fixture is asserting a state "
        f"production cannot produce."
    )
    today = display_today()
    assert observed_on <= today, (
        f"account id={account.id}'s latest assertion is dated {observed_on}, "
        f"which is AFTER the user's today ({today}); "
        f"anchor_service.resolve_observation_day refuses that at the "
        f"write door, so no production account can be in this state.  Move the "
        f"assertion (and entry id={entry.id}'s dates) back inside the clock "
        f"this suite runs on rather than forward past it."
    )
    entry.settled_on = settled_on
    db_session.flush()
    return entry


def default_settle_day(period, status_id):
    """Return the settle day a BARE-built fixture row takes for *status_id*.

    **One statement of the rule, for the two bare ``Transaction`` builders that
    need it** (:func:`add_txn` and ``test_calendar_service``'s module-local
    twin).  A row built in a settled status must carry a settle day -- a settled
    row without one is the state
    :func:`app.utils.balance_predicates.settled_day` refuses -- and the day is
    the pay period's ``start_date``, because that is precisely what every reader
    derived for such a row before plan step X-f1: these builders never set
    ``paid_at``, so ``to_display_civil_date(None, period.start_date)`` answered
    the period start.  Stamping ``display_today()`` instead would silently move
    that cash to today and re-date whatever figure the fixture was built to
    grade.

    Args:
        period: The :class:`~app.models.pay_period.PayPeriod` the row sits in.
        status_id: The ``ref.statuses.id`` the row is built in.

    Returns:
        ``period.start_date`` for a settled status, ``None`` for any other.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.utils.balance_predicates import settled_status_ids

    return period.start_date if status_id in settled_status_ids() else None


def add_txn(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db_session, seed_user, period, name, amount,
    status_enum=None, is_income=False,
    due_date=None, category_key=None, is_deleted=False,
    actual_amount=None, account=None, scenario=None,
    settled_on=_UNSET_SETTLED_ON,
):
    """Create a projected (default) transaction on the seed user's account.

    The shared bare-Transaction builder for the dashboard suites (the
    route, shared-helper, and pulse-producer tests all built an identical
    ``_add_txn`` -- a duplicate-code finding).  Builds an income or expense
    row with optional actual amount, due date, category, soft-delete flag,
    and status.  Flushes; the caller commits.

    **A row built in a SETTLED status gets a settle day here, and the default
    is the pay period's ``start_date``** (plan step X-f1).  It is deliberately
    the period start and not today: this builder never set ``paid_at``, so
    every settled row it produced used to reach the readers as
    ``to_display_civil_date(None, period.start_date)`` -- the period start --
    which is the day every hand-computed figure in the suites that consume it
    was written against.  It is also the day migration ``a3f7c8e21b64``'s
    backfill gave the real rows in the same shape.  Stamping ``display_today()``
    instead would silently move that cash to today and re-date whatever
    balance, period subtotal or ledger entry the fixture was built to grade.

    It stays a BARE builder -- it does not route through
    ``status_seam.apply_status_change`` -- because that is its purpose: several
    suites need "a settled row the posting writer has never seen", and the seam
    would additionally refuse ``Projected -> Settled``, which is not a legal
    transaction transition but is a legitimate STARTING state for a fixture.
    What it may not produce is an INCOHERENT row: a settled row with no settle
    day is the state ``balance_predicates.settled_day`` refuses, and every
    reader of it would raise.

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
        account: The :class:`~app.models.account.Account` the row lands on;
            defaults to ``seed_user["account"]`` (the Checking account).
        scenario: The :class:`~app.models.scenario.Scenario` the row lives in;
            defaults to the seed user's baseline.  Pass a non-baseline scenario
            to exercise multi-scenario isolation.
        settled_on: The civil day the row's money moved.  Omit it for the
            default described above (``period.start_date`` for a settled
            status, ``None`` for any other).  Pass a day to place the cash
            somewhere specific -- the shape a fixture needs when it grades WHICH
            day a balance steps on.  Passing ``None`` explicitly builds the
            broken settled-with-no-day row on purpose, which is what a negative
            control for :func:`app.utils.balance_predicates.settled_day` needs.

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
    account = seed_user["account"] if account is None else account
    scenario = seed_user["scenario"] if scenario is None else scenario
    status_id = ref_cache.status_id(status_enum)
    if settled_on is _UNSET_SETTLED_ON:
        settled_on = default_settle_day(period, status_id)
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    cat_id = None
    if category_key and category_key in seed_user["categories"]:
        cat_id = seed_user["categories"][category_key].id

    txn = Transaction(
        account_id=account.id,
        pay_period_id=period.id,
        scenario_id=scenario.id,
        status_id=status_id,
        name=name,
        category_id=cat_id,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        actual_amount=Decimal(str(actual_amount)) if actual_amount is not None else None,
        due_date=due_date,
        is_deleted=is_deleted,
        settled_on=settled_on,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def require_assertion_instant(at):
    """Return *at*, refusing a plain ``date`` where an INSTANT is required.

    **The mirror of the settle column's type guard, and it exists because the
    X-f1 conversion tripped over exactly this** (finding **N-179**'s other
    direction).  A settle records a civil DAY; an ASSERTION records the instant
    it was typed (``AccountAnchorHistory.created_at``, a ``timestamptz``) and
    DERIVES its business day from it.  A bulk conversion that turned settle
    instants into days caught five assertion sites in the same pass, and each
    then failed deep inside ``to_display_tz`` with ``'date' object has no
    attribute 'tzinfo'`` -- or, had it reached the column, would have been
    stored as MIDNIGHT UTC, which is the previous Eastern evening and therefore
    the previous business day.

    Every assertion builder in this module routes through it, so handing one a
    day fails at the call site with a message that says what to pass.

    Args:
        at: The candidate recording instant.

    Returns:
        *at* unchanged when it is a ``datetime``.

    Raises:
        TypeError: When *at* is a ``date`` that is not a ``datetime``.
    """
    if isinstance(at, _real_date) and not isinstance(at, _real_datetime):
        raise TypeError(
            f"An assertion is pinned by the INSTANT it was recorded at, got "
            f"the civil day {at!r}.  Its business day (``observed_on``) is "
            "DERIVED from that instant in the display timezone, and a bare "
            "date reaches the timestamptz column as midnight UTC -- the "
            "PREVIOUS Eastern evening, so the assertion would be filed a day "
            "early.  Pass an aware-UTC datetime: the suite's own "
            "``_instant(...)`` helper, or ``settle_instant_on(day)``."
        )
    return at


def observed_day_of(instant):
    """Return the civil day an assertion stamped at ``instant`` is ABOUT.

    The ONE place the test helpers state how a pinned recording instant maps to
    an ``AccountAnchorHistory.observed_on``, and every anchor builder below
    routes through it.

    **Why the helpers derive it rather than take it.**  ``observed_on`` is a
    stored, user-supplied column since plan step 2 (ruling R-DH), so a fixture
    CAN set the two independently -- and a handful deliberately should, because
    "the user corrected an anchor's date" is now a reachable state.  But every
    fixture written before the column existed meant "an assertion recorded at
    instant X, about the day X falls on", and each of their hand-computed
    figures depends on that.  Deriving keeps all of them valid unchanged;
    a fixture that wants the two APART says so explicitly, which is exactly the
    right way round for a state that used to be unreachable.

    It is the display-timezone day, not the UTC one (ruling R-DH (b)): midnight
    UTC is the previous EVENING in Eastern, and a fixture that took the UTC day
    would file a late-evening assertion in the next pay period -- the N-132
    shape this suite has now been bitten by five times.

    Args:
        instant: The aware-UTC instant the assertion is stamped at.

    Returns:
        The :class:`datetime.date` the assertion is the closing balance for.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.utils.dates import to_display_date

    return to_display_date(instant)


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
        anchor_balance=Decimal(str(balance)),
        created_at=created,
        observed_on=observed_day_of(created),
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def override_anchor(db_session, account, period, balance, *, at=None):
    """Replace ``account``'s current anchor with ``balance`` on ``period``.

    Appends an :class:`AccountAnchorHistory` row -- the source of truth, and
    since ruling R-EH the ONLY place an asserted balance lives.  It used to
    sync the ``accounts.current_anchor_*`` cache columns alongside, so the
    resolver's cache-reconciliation log would not fire; there is no cache and
    no such log.  The shared form of the
    ``_override_anchor`` helper five suites had each written for themselves --
    a duplicate-code cluster that mattered once the fold made the row's INSTANT
    load-bearing, because a fix applied to one copy would have left the other
    four asserting against a state production cannot reach.

    **The instant defaults to the period's first day IN THE USER'S ZONE**, and
    that default is the point (plan step X-c2b2, the N-8 / X-c2a fixture shape a
    third time; the zone is ruling R-DH (b), 2026-07-31).
    ``AccountAnchorHistory.created_at`` server-defaults to the WALL CLOCK,
    while ``tests/test_services`` freezes today to 2026-03-20 -- so a row
    written by a helper that did not stamp it was asserted MONTHS after its own
    pay period, a state production cannot reach (a true-up files against
    ``get_current_period``).  The shipping producers never noticed: they read
    the LATEST row and ignored its date.  The fold replays every assertion on
    the day it was made, so such a row lands past the end of the window and no
    period ever sees the balance the test asserted.  Pinning it to the period's
    own start makes the fixture deterministic, clock-independent, and shaped
    like a true-up filed on day one of its period -- which keeps every
    hand-computed figure in the suites valid unchanged.

    A test that needs a MID-period assertion (the shape ruling R-L and the
    walk's instant partition exist for) passes ``at`` explicitly.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to re-anchor.
        period: The :class:`~app.models.pay_period.PayPeriod` the new anchor is
            recorded against; its ``start_date`` is the default instant.
        balance: The new anchor balance (Decimal -- construct from a string).
        at: Optional aware-UTC instant to stamp the row with, overriding the
            period-start default.

    **It took a required ``notes`` label until ruling R-ES** (plan step
    X-f1e2), so a failing suite could tell which fixture wrote the assertion it
    was looking at.  The column is deleted -- nothing in ``app/`` read it -- and
    the diagnostic it served is answered better by the row's own ``id``,
    ``observed_on`` and ``created_at``, which the fixture controls and the
    engine actually orders on.

    Returns:
        The created :class:`AccountAnchorHistory` row (flushed, not committed
        -- the caller owns the transaction boundary).
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from datetime import datetime, time, timezone
    from app.models.account import AccountAnchorHistory
    from app.utils.dates import DISPLAY_TIMEZONE

    # Day one of the period IN THE USER'S ZONE, converted to UTC for storage.
    # Midnight UTC is the previous EVENING in Eastern, so since ruling R-DH (b)
    # -- which reads an instant's display civil day -- it would file this
    # true-up in the PREVIOUS period and empty the anchor column's remainder of
    # the very assertion the fixture just made.
    asserted_at = at if at is not None else datetime.combine(
        period.start_date, time.min, tzinfo=DISPLAY_TIMEZONE,
    ).astimezone(timezone.utc)
    history = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=balance,
        created_at=asserted_at,
        observed_on=observed_day_of(asserted_at),
    )
    db_session.add(history)
    db_session.flush()
    return history


def restamp_opening_assertion(db_session, account, at):
    """Pin the factory-written OPENING assertion's instant to ``at``.

    ``account_service.create_account`` writes the opening
    :class:`~app.models.account.AccountAnchorHistory` row with a wall-clock
    ``created_at``, which would sort AFTER every controlled instant a cash-ledger
    test uses.  Re-stamping it makes the whole event stream deterministic.

    The instant-precise counterpart of :func:`add_anchor_history` (which dates
    relative to now, in whole days): the cash walk partitions settles against an
    assertion by INSTANT, so its suites need second precision and an absolute
    moment.  Shared rather than copied per suite -- the walk (plan step X-a) and
    the fold (X-b) both build their streams this way.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` whose opening to pin.
        at: The aware-UTC instant to stamp it with.

    Returns:
        The re-stamped :class:`AccountAnchorHistory` row (flushed).
    """
    return _restamp_assertion(db_session, account, at, newest=False)


def restamp_latest_assertion(db_session, account, at):
    """Pin the account's NEWEST assertion instant to ``at``.

    The twin of :func:`restamp_opening_assertion` for a true-up written through
    the production path (``anchor_service.stage_anchor_true_up``, which sets the
    ``current_anchor_*`` cache AND appends the history row): that row also
    carries a wall-clock ``created_at``, and since plan step X-c2a modelled
    interest begins at the LATEST assertion's UTC civil day (ruling R-L), so a
    suite that needs a controlled accrual window has to pin it.

    It FLUSHES first rather than relying on autoflush: the caller has just
    staged the true-up in the session, and resolving the row without flushing
    would silently restamp the previous assertion instead -- a test helper
    quietly pinning the wrong row is worse than one that fails.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` whose latest
            assertion to pin.
        at: The aware-UTC instant to stamp it with.

    Returns:
        The re-stamped :class:`AccountAnchorHistory` row (flushed).
    """
    return _restamp_assertion(db_session, account, at, newest=True)


def _restamp_assertion(db_session, account, at, *, newest):
    """Pin the oldest or newest assertion's instant -- the shared core.

    One query with one ordering flag rather than two near-identical copies:
    both wrappers answer "which stored assertion am I pinning", and the row
    they resolve must be selected the same way
    :func:`~app.services.cash_ledger.resolve_anchor` selects the latest
    (``created_at`` then ``id``), or a same-instant pair would restamp a
    different row than the producer reads.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to pin.
        at: The aware-UTC instant to stamp with.
        newest: ``True`` for the latest assertion, ``False`` for the opening.

    Returns:
        The re-stamped :class:`AccountAnchorHistory` row (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.account import AccountAnchorHistory

    require_assertion_instant(at)
    db_session.flush()
    order = (AccountAnchorHistory.created_at, AccountAnchorHistory.id)
    if newest:
        order = tuple(column.desc() for column in order)
    row = (
        db_session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(*order)
        .first()
    )
    row.created_at = at
    # The BUSINESS day moves with the recording instant.  Pinning one and
    # leaving the other is now a reachable state (``observed_on`` is a stored
    # column since plan step 2), and it is not what any caller of a *restamp*
    # helper means: they are placing the whole assertion, not editing its date.
    row.observed_on = observed_day_of(at)
    db_session.flush()
    return row


def append_balance_assertion(
    db_session, account, period, balance, at, recorded_at=None,
):
    """Append one balance ASSERTION (a true-up) at a pinned instant.

    The instant-precise true-up builder the cash-ledger suites share.  See
    :func:`restamp_opening_assertion` for why the instant (not the day) is the
    thing being pinned.

    The row is inserted and then re-stamped, because ``created_at`` carries a
    server default that the INSERT would otherwise fill with the wall clock.

    **The row carries TWO clocks and this helper can now separate them**
    (*recorded_at*).  ``observed_on`` is the BUSINESS day the balance was true
    for and ``created_at`` is when it was typed; since plan step 2 made
    ``observed_on`` user-supplied they can disagree, which is precisely the
    shape the retired third statement of "the latest assertion" got wrong
    (finding N-133 / F4).  Defaulting *recorded_at* to *at* keeps every
    existing caller's two clocks equal -- and an adversarial review found that
    default silently disarming the one test written to exercise the
    disagreement, which is why the parameter exists.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` asserting.
        period: The :class:`~app.models.pay_period.PayPeriod` the assertion is
            filed against.
        balance: The asserted balance (str or Decimal-coercible).
        at: The aware-UTC instant whose display-timezone day becomes the
            BUSINESS day (``observed_on``).
        recorded_at: The aware-UTC instant to stamp as ``created_at`` -- when
            the row was TYPED.  Defaults to *at*, which makes the two clocks
            agree; pass it to build a back-dated assertion, where the business
            day precedes the recording instant.

    Returns:
        The inserted :class:`AccountAnchorHistory` row (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.account import AccountAnchorHistory

    require_assertion_instant(at)
    require_assertion_instant(at if recorded_at is None else recorded_at)
    row = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        observed_on=observed_day_of(at),
    )
    db_session.add(row)
    db_session.flush()
    row.created_at = at if recorded_at is None else recorded_at
    db_session.flush()
    return row


def open_calendar_hole(db_session, period, last_covered_day):
    """Shorten one period's stored ``end_date`` so a calendar hole opens after it.

    **The hole is HAND-BUILT, and since plan step C3-b that is the only way to
    build one.**  Five suites need a schedule with a day no pay period covers,
    because that is the state ledger row D7 / finding **P2** describes.  They
    used to reach it through the REAL writer -- append a batch starting later
    than the current coverage ends -- deliberately, so that "can this state
    exist?" was proven rather than assumed.

    **What a hole MEANS to a reader changed at plan step C2-b2**, and the
    callers changed with it.  The recurrence engine used to answer
    ``PlacementOutcome.SCHEDULE_GAP`` and log the orphaned dates; it now reads
    the DERIVED calendar, in which the preceding paycheck runs to the day
    before the next payday and so absorbs them.  The state is reported by
    ``scripts/integrity_check.py`` **BA-07** instead, which reads the very
    column this writes.

    ``pay_period_write`` closed that door: it materialises the payday
    derivation, in which a period ends the day before the next payday, so an
    append now ABSORBS the days it used to leave behind.  What the suites are
    about is unchanged -- how a READER behaves when a day belongs to no
    paycheck -- and that state is still reachable in the wild, from rows written
    before C3-b.  So the fixture writes the column directly, and the writer's
    own tests carry the other half: that no door can produce this any more, and
    that the next write through one REPAIRS it.

    Args:
        db_session: The test ``db.session``.
        period: The :class:`~app.models.pay_period.PayPeriod` to shorten -- the
            one immediately before the intended hole.
        last_covered_day: The new stored ``end_date``.  Must be on or after
            *period*'s ``start_date`` (``ck_pay_periods_date_order`` requires
            strictly after) and before the next period's payday, or no hole
            opens.

    Returns:
        The inclusive ``(first_uncovered_day, last_uncovered_day)`` span, so a
        caller asserts against the fixture's own arithmetic rather than
        restating it.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.pay_period import PayPeriod

    # Re-read by primary key rather than writing through the handed-in object.
    # Callers typically hold a period from a FIXTURE built in an earlier app
    # context, which is DETACHED: assigning to it writes nothing, the hole
    # silently fails to open, and the test then measures a contiguous schedule
    # while claiming to measure a hole.  ``Session.get`` returns the
    # identity-mapped instance without copying the detached one's (possibly
    # stale) state over it, which ``merge`` would.
    period = db_session.get(PayPeriod, period.id)
    assert last_covered_day > period.start_date, (
        f"a period must cover at least two days "
        f"(ck_pay_periods_date_order); {period.start_date} .. "
        f"{last_covered_day} does not"
    )
    following = (
        db_session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == period.user_id,
            PayPeriod.start_date > period.start_date,
        )
        .order_by(PayPeriod.start_date)
        .first()
    )
    assert following is not None, (
        "no period follows the one being shortened, so this opens no hole -- "
        "it moves the schedule's horizon"
    )
    period.end_date = last_covered_day
    db_session.flush()
    first_uncovered = last_covered_day + _real_timedelta(days=1)
    last_uncovered = following.start_date - _real_timedelta(days=1)
    assert first_uncovered <= last_uncovered, "the fixture built no hole"
    return first_uncovered, last_uncovered


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
    from app.models.account import Account, AccountAnchorHistory
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

    # 4. Anchor integrity: every account carries at least one balance
    #    ASSERTION (E-19 / Commit 3).  It used to assert that the account's
    #    ``current_anchor_period_id`` named one of the user's live periods;
    #    rulings R-EH and R-EO deleted both that column and the assertion's own
    #    pay period, so what survives is the invariant those columns existed to
    #    serve -- an account the resolver can answer for.  A period wipe can no
    #    longer strand an anchor, which is the whole point of the deletion.
    for account in db_session.query(Account).filter_by(user_id=user_id):
        assert db_session.query(AccountAnchorHistory).filter_by(
            account_id=account.id,
        ).count() > 0, (
            f"user {user_id}: account {account.id} carries no balance "
            "assertion, so no producer can resolve a balance for it"
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

    **The opening assertion is stamped at the anchor period's first day**
    (via :func:`restamp_opening_assertion`) -- finding N-77, fixed at plan step
    X-g2a for the reason :func:`create_hysa_account` was at X-c2a, and read
    there for the full argument.  ``account_service.create_account`` writes that
    row with the WALL CLOCK, and from plan step X-g2b a Property's appreciation
    accrues only forward of its LATEST assertion (ruling R-Y), so an unpinned
    opening is the newest assertion, lands past the suite's seeded horizon, and
    the account then appreciates NOTHING anywhere -- a state production cannot
    reach.  A test that needs a MID-period or later assertion restamps it
    itself, exactly as the interest suites do.

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
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(AssetAppreciationParams(
        account_id=account.id, annual_appreciation_rate=rate,
    ))
    restamp_opening_assertion(
        db_session, account, settle_instant_on(anchor_period.start_date),
    )
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

    **The opening assertion is stamped at the anchor period's first day**
    (via :func:`restamp_opening_assertion`) -- finding N-77, fixed at plan step
    X-g2a; see :func:`make_appreciating_account` beside it and
    :func:`create_hysa_account` for the full argument.  From plan step X-g2b an
    investment's growth accrues only forward of its LATEST assertion (ruling
    R-Y), and the factory writes that row with the WALL CLOCK, so an unpinned
    opening leaves the account growing nowhere.

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
    restamp_opening_assertion(
        db_session, account, settle_instant_on(anchor_period.start_date),
    )
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


def net_posted_by_day(filter_clause):
    """Return ``{entry_date: net posted magnitude}`` for one source row.

    The LEDGER half of every settle-day assertion in the X-f1c suites, shared
    because the transaction and transfer versions differed only in their filter
    clause (``JournalEntry.transaction_id == id`` vs ``.transfer_id == id``) --
    identical ``group_by``, identical reduction, identical rationale.  ``tests/``
    is outside R0801's reach, so that duplication had to be found by reading;
    it was, by a neutral review.

    **The NET is what separates "re-dated" from "posted twice".**  A settle-day
    correction leaves the original day's entries in place as history and adds a
    reversal (the R2 attribution rule), so the raw list of ``entry_date`` values
    still contains the old day and a membership test grades nothing.  Summed per
    ``(day, ledger account)`` the reconciled-away day collapses to ``$0.00`` and
    only the live day carries the effect.

    The reduction is per LEDGER ACCOUNT, not per day: netting across accounts
    within a day would collapse to ``$0.00`` for ANY balanced entry, which is
    every entry this project writes -- an assertion that could never fail.  The
    per-day value returned is the LARGEST per-account absolute residue, not a
    sum across accounts; every leg of a balanced entry carries the same
    magnitude, so it reads as "the size of this row's effect on that day".  An
    earlier draft of this paragraph said "the sum", which the code never did.

    Args:
        filter_clause: The SQLAlchemy clause selecting the journal entries of
            one source row, e.g. ``JournalEntry.transaction_id == txn.id``.

    Returns:
        ``{entry_date: Decimal}`` for the days that still carry a non-zero net
        posted effect.  A day whose entries net to zero is DROPPED, so the
        mapping reads as "where this row's money is posted right now" and an
        exact-dict assertion grades both halves of a correction at once.
    """
    # Imported here rather than at module scope: this helper module is imported
    # by conftest-adjacent code before the app's models are configured in some
    # collection orders, and the ledger models are needed only by this function.
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.journal_entry import (  # pylint: disable=import-outside-toplevel
        JournalEntry, Posting,
    )

    rows = (
        db.session.query(
            JournalEntry.entry_date,
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
        )
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(filter_clause)
        .group_by(JournalEntry.entry_date, Posting.ledger_account_id)
        .all()
    )
    net = {}
    for entry_date, _ledger_account_id, total in rows:
        residue = abs(Decimal(str(total)))
        net[entry_date] = max(net.get(entry_date, Decimal("0.00")), residue)
    return {day: amount for day, amount in net.items() if amount != 0}


def capture_sql_statements(fn):
    """Run ``fn`` while recording every SQL statement the engine emits.

    The statement-capture idiom the advisory-lock suites grade against: it is
    the only way to assert, at the unit level, that a given path DOES or does
    NOT take a lock, and -- since plan step X-f1c3c -- that it takes it BEFORE
    the read it protects.  Ordering is the half a presence check cannot see: a
    lock taken after the reconcile has already read what is posted serialises
    nothing.

    It lived as a private ``_capture_statements`` in
    ``tests/test_services/test_pay_period_topup.py`` until the ledger reconciles
    needed the same assertion; ``tests/`` is outside R0801's reach, so a second
    copy would not have been flagged.

    **It captures PARAMETERS as well as text, and that is load-bearing.**
    SQLAlchemy BINDS the lock key, so every acquisition emits the byte-identical
    string ``SELECT pg_advisory_xact_lock(%(pg_advisory_xact_lock_2)s,
    %(pg_advisory_xact_lock_3)s)`` whatever it is locking.  A test that graded
    statement text alone could not tell a per-user lock from a per-account one,
    a per-scenario one, or a constant -- which is exactly what two independent
    adversarial reviews caught it doing.  See :func:`advisory_lock_keys`.

    Args:
        fn: A zero-argument callable to run under capture.

    Returns:
        ``(result, statements)`` -- *fn*'s return value and a list of
        ``(statement, parameters)`` pairs in emission order.
    """
    # Imported here rather than at module scope: this helper module is imported
    # before the app's extensions are configured in some collection orders.
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from sqlalchemy import event  # pylint: disable=import-outside-toplevel

    statements: list[tuple] = []

    def _cap(_conn, _cursor, statement, parameters, *_args, **_kwargs):
        statements.append((statement, parameters))

    event.listen(db.engine, "before_cursor_execute", _cap)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _cap)
    return result, statements


def took_advisory_lock(statements):
    """Return whether any captured statement acquired the per-user write lock.

    Args:
        statements: The ``(statement, parameters)`` list from
            :func:`capture_sql_statements`.

    Returns:
        ``True`` when at least one statement called ``pg_advisory_xact_lock``.
    """
    return any("pg_advisory_xact_lock" in text for text, _params in statements)


def advisory_lock_keys(statements):
    """Return the ``(namespace, key)`` pairs the captured run actually locked.

    The half :func:`took_advisory_lock` cannot see.  The two-argument
    ``pg_advisory_xact_lock(namespace, key)`` form binds BOTH arguments, so the
    emitted SQL is identical for every key and only the parameters distinguish
    "this locked user 7" from "this locked account 7" or "this locked a
    constant".  Every assertion about WHAT is locked has to come through here.

    The bind names carry SQLAlchemy's positional suffixes
    (``pg_advisory_xact_lock_2`` / ``_3``), so the two values are ordered by
    that trailing integer rather than by dict iteration order.

    Args:
        statements: The ``(statement, parameters)`` list from
            :func:`capture_sql_statements`.

    Returns:
        A list of ``(namespace, key)`` tuples, one per acquisition, in
        emission order (duplicates kept -- a re-entrant double-take is two
        entries, which is itself worth asserting).
    """
    def _suffix(name):
        tail = str(name).rsplit("_", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    keys = []
    for text, params in statements:
        if "pg_advisory_xact_lock" not in text:
            continue
        if isinstance(params, dict):
            values = [params[name] for name in sorted(params, key=_suffix)]
        else:
            values = list(params or ())
        keys.append(tuple(values))
    return keys


def advisory_lock_precedes(statements, *table_names):
    """Return whether the write lock was taken BEFORE any of *table_names* was read.

    The ordering half of the lock assertion.  A reconcile that takes its lock
    after reading what is posted has serialised nothing: the second transaction
    has already read the same pre-state and will compute the same delta against
    it.  Grading presence alone would pass such a build.

    Args:
        statements: The statement list from :func:`capture_sql_statements`.
        *table_names: Substrings identifying the tables whose first read must
            come after the lock, e.g. ``"journal_entries"``.  Matched as
            substrings of the emitted SQL, so a bare table name is enough and a
            schema-qualified one also works.

    Returns:
        ``True`` when a ``pg_advisory_xact_lock`` statement is emitted and every
        named table's FIRST appearance is at a later index; ``False`` when the
        lock is absent, or when any named table was read first.  A table that
        is never read does not falsify the ordering -- there is nothing to
        order against -- so callers assert the read happened separately.
    """
    texts = [text for text, _params in statements]
    lock_at = next(
        (i for i, text in enumerate(texts) if "pg_advisory_xact_lock" in text),
        None,
    )
    if lock_at is None:
        return False
    for table in table_names:
        read_at = next(
            (i for i, text in enumerate(texts) if table in text), None,
        )
        if read_at is not None and read_at < lock_at:
            return False
    return True


def linked_ledger_total(account_id):
    """Return the summed posting amount on one account's LINKED ledger account.

    The account's ledger-native balance as the double-entry ledger holds it:
    the anchor reconcile's whole job is to keep this equal to the account's
    resolved balance assertion plus the settled movements recorded after it.
    Two concurrent reconciles that both computed their delta against the same
    posted state leave the two permanently apart, and this is the left-hand
    side of the assertion that catches it.

    **NOT scenario-scoped, and its one caller depends on that being harmless.**
    It sums every posting on the linked ledger across ALL scenarios, so the
    invariant "linked ledger total == resolved assertion" holds only while the
    database has a single scenario -- which is true today (production creates
    baselines only) and stops being true the moment scenario clone ships.  Said
    here rather than discovered later, because the failure would look like a
    concurrency regression and would not be one.

    Args:
        account_id: The account whose linked ledger to total.

    Returns:
        The sum of every posting on that ledger account as a
        :class:`~decimal.Decimal` (``Decimal("0.00")`` when nothing is posted).

    Raises:
        PostingError: When the account has no linked ledger account.
    """
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.journal_entry import (  # pylint: disable=import-outside-toplevel
        Posting,
    )
    from app.services.posting_reads import (  # pylint: disable=import-outside-toplevel
        _ledger_account_for,
    )

    # ``_ledger_account_for`` RAISES ``PostingError`` for an unpaired account;
    # it never returns ``None``.  An earlier version of this helper guarded for
    # ``None`` and returned ``$0.00``, which was dead code that would also have
    # reported "nothing posted" for a broken chart of accounts.
    linked = _ledger_account_for(account_id)
    rows = (
        db.session.query(Posting.amount)
        .filter(Posting.ledger_account_id == linked.id)
        .all()
    )
    return sum((amount for (amount,) in rows), Decimal("0.00"))


def _tax_config_models():
    """Return the session and the ``ref`` / tax models the seeders below need.

    Imported lazily, like every other helper in this module: importing the ORM
    at module scope would make this file unimportable outside an app context.

    Returns:
        ``(db, {name: model})`` for the four tax tables and the two ``ref``
        lookups the seeders resolve by name.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred, like every other
    # helper in this module: importing the ORM at module scope would bind the
    # mappers before the test app configures them, so this file would be
    # unimportable outside an app context.
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    # Pylint: ``import-outside-toplevel`` -- deferred; see above.
    from app.models.ref import (  # pylint: disable=import-outside-toplevel
        FilingStatus,
        TaxType,
    )
    # Pylint: ``import-outside-toplevel`` -- deferred; see above.
    from app.models.tax_config import (  # pylint: disable=import-outside-toplevel
        FicaConfig,
        StateTaxConfig,
        TaxBracket,
        TaxBracketSet,
    )

    return db, {
        "FilingStatus": FilingStatus,
        "TaxType": TaxType,
        "FicaConfig": FicaConfig,
        "StateTaxConfig": StateTaxConfig,
        "TaxBracket": TaxBracket,
        "TaxBracketSet": TaxBracketSet,
    }


# --- Tax configuration -------------------------------------------------------
#
# The three rows ``paycheck_calculator.calculate_paycheck`` needs before it can
# answer at all: a federal bracket set, a state config and a FICA config.  They
# lived as private helpers inside ``tests/test_routes/test_salary.py`` until
# plan step R4b-1, whose own tests need a REAL paycheck computed through
# generation; copying them would have made a financial fixture exist twice.
# Values match the shipped seeds closely enough to be recognisable and are
# otherwise arbitrary -- the assertions that use them compute their expected
# figures from these same rows.

def seed_state_tax_config(user_id, rate, tax_year=2026, state_code="NC"):
    """Create a flat state tax config for testing.

    Args:
        user_id: The owning user's ID.
        rate: Decimal flat rate in decimal form (e.g. 0.0399).
        tax_year: Tax year for the config.
        state_code: Two-letter state code.

    Returns:
        StateTaxConfig: The created config.
    """
    db, models = _tax_config_models()
    flat_type = db.session.query(models["TaxType"]).filter_by(name="flat").one()
    # T-P5: state configs are filing-status-keyed.  These net-biweekly tests
    # all use single-filer profiles, so the config carries the single status
    # (matching the withholding path's filing-status-scoped lookup).
    single_status = (
        db.session.query(models["FilingStatus"]).filter_by(name="single").one()
    )
    config = models["StateTaxConfig"](
        user_id=user_id,
        state_code=state_code,
        tax_year=tax_year,
        tax_type_id=flat_type.id,
        filing_status_id=single_status.id,
        flat_rate=rate,
        standard_deduction=Decimal("25500.00"),
    )
    db.session.add(config)
    db.session.flush()
    return config


def seed_fica_config(user_id, tax_year=2026):
    """Create a standard FICA config for testing.

    Returns:
        FicaConfig: The created config.
    """
    db, models = _tax_config_models()
    config = models["FicaConfig"](
        user_id=user_id,
        tax_year=tax_year,
        ss_rate=Decimal("0.0620"),
        ss_wage_base=Decimal("176100.00"),
        medicare_rate=Decimal("0.0145"),
        medicare_surtax_rate=Decimal("0.0090"),
        medicare_surtax_threshold=Decimal("200000.00"),
    )
    db.session.add(config)
    db.session.flush()
    return config


def seed_tax_bracket_set(user_id, tax_year=2026):
    """Create a bracket set with sample brackets for testing.

    Seeds a 'single' filing status bracket set with two brackets so
    that the federal brackets section renders with visible data.

    Args:
        user_id: The owning user's ID.
        tax_year: Tax year for the bracket set.

    Returns:
        TaxBracketSet: The created bracket set with two brackets.
    """
    db, models = _tax_config_models()
    filing_status = (
        db.session.query(models["FilingStatus"]).filter_by(name="single").one()
    )
    bracket_set = models["TaxBracketSet"](
        user_id=user_id,
        filing_status_id=filing_status.id,
        tax_year=tax_year,
        standard_deduction=Decimal("14600.00"),
        child_credit_amount=Decimal("2000.00"),
        other_dependent_credit_amount=Decimal("500.00"),
    )
    db.session.add(bracket_set)
    db.session.flush()

    brackets = [
        models["TaxBracket"](
            bracket_set_id=bracket_set.id,
            min_income=Decimal("0.00"),
            max_income=Decimal("11600.00"),
            rate=Decimal("0.1000"),
            sort_order=1,
        ),
        models["TaxBracket"](
            bracket_set_id=bracket_set.id,
            min_income=Decimal("11600.00"),
            max_income=Decimal("47150.00"),
            rate=Decimal("0.1200"),
            sort_order=2,
        ),
    ]
    db.session.add_all(brackets)
    db.session.flush()
    return bracket_set
