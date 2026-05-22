"""
Shekel Budget App -- Balance Resolver Anchor Tests (Commit 4 / E-19)

Tests for ``app.services.balance_resolver.resolve_anchor``.

The resolver reads the most recent ``AccountAnchorHistory`` row as the
dated source of truth for E-19; the ``Account.current_anchor_*``
columns are treated as a denormalized cache of that latest row.  These
tests lock the contract:

  * the latest history row wins, even when more than one exists;
  * the history row wins over the cache when they disagree, with a
    ``EVT_ANCHOR_CACHE_RECONCILED`` log emitted;
  * the resolver never returns ``None`` for a factory-built account
    (Commit 3 invariant);
  * the ``scenario_id`` parameter is accepted but does not currently
    affect the returned anchor (anchors are per-account at the storage
    tier);
  * the returned ``balance`` is a 2-decimal-place ``Decimal``;
  * a zero anchor balance is preserved verbatim per E-12 (zero is a
    value, not "missing").

Test IDs match the remediation plan's Commit 4 specification (C4-1
through C4-6).
"""

import logging
from dataclasses import FrozenInstanceError
from datetime import date as _date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.services.balance_resolver import AnchorPoint, resolve_anchor


class _LogCapture:
    """Context manager that captures log records on a named logger.

    Mirrors the helper in ``tests/test_utils/test_log_events.py`` so
    the reconciliation-log assertion does not have to depend on
    ``caplog`` fixture quirks under xdist parallelisation.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self.records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = self.records.append  # type: ignore[assignment]

    def __enter__(self) -> "_LogCapture":
        self._logger.addHandler(self._handler)
        self._logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc) -> None:
        self._logger.removeHandler(self._handler)


def _make_anchor_history(
    *,
    account_id: int,
    pay_period_id: int,
    anchor_balance: Decimal,
    notes: str,
) -> AccountAnchorHistory:
    """Insert and flush an ``AccountAnchorHistory`` row.

    Used by the multi-event tests to layer additional true-up rows on
    top of the origination row that ``account_service.create_account``
    writes.  Returns the inserted row so the caller can read
    ``created_at`` (the resolver's tiebreaker is ``created_at desc``).
    """
    history = AccountAnchorHistory(
        account_id=account_id,
        pay_period_id=pay_period_id,
        anchor_balance=anchor_balance,
        notes=notes,
    )
    db.session.add(history)
    db.session.flush()
    return history


class TestResolveAnchor:
    """Tests for ``resolve_anchor``."""

    # ── C4-1 -----------------------------------------------------------

    def test_resolve_anchor_from_latest_history(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-1: with two history rows, the resolver returns the newest.

        Setup: seed_user gives one origination row (anchor 1000.00 on
        the bootstrap period).  We add a second pay period and a
        second history row (anchor 1234.56 on the new period).

        Expected: ``resolve_anchor`` returns the 1234.56 row.
        Arithmetic: the latest row is the dated SoT; 1234.56 is the
        most recent ``anchor_balance`` and its ``pay_period_id`` is
        the new period's id.
        """
        with app.app_context():
            account = seed_user["account"]
            scenario = seed_user["scenario"]

            second_period = PayPeriod(
                user_id=seed_user["user"].id,
                start_date=seed_user["bootstrap_period"].end_date,
                end_date=seed_user["bootstrap_period"].end_date.replace(day=28),
                period_index=1,
            )
            db.session.add(second_period)
            db.session.flush()

            new_balance = Decimal("1234.56")
            _make_anchor_history(
                account_id=account.id,
                pay_period_id=second_period.id,
                anchor_balance=new_balance,
                notes="true-up #2",
            )
            # Bring the cache in line so this test exercises only the
            # latest-wins path, not the cache-mismatch path.
            account.current_anchor_balance = new_balance
            account.current_anchor_period_id = second_period.id
            db.session.commit()

            result = resolve_anchor(account, scenario.id)

            assert isinstance(result, AnchorPoint)
            # 1234.56: the most recent AccountAnchorHistory row's
            # anchor_balance.  Hand-computed equality, not "> 0".
            assert result.balance == Decimal("1234.56")
            assert result.period.id == second_period.id

    # ── C4-2 -----------------------------------------------------------

    def test_resolve_anchor_history_wins_over_stale_column(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-2: when the cache disagrees with the latest history row,
        the history row wins and the divergence is logged.

        Setup: seed_user origination history row is 1000.00 on the
        bootstrap period.  We engineer the cache columns to a
        deliberately-wrong value (777.77 on a non-existent period_id
        would violate the FK; instead we use the legitimate bootstrap
        period but a wrong balance).

        Expected: returned balance is 1000.00 (history wins);
        ``EVT_ANCHOR_CACHE_RECONCILED`` is emitted at WARNING level.
        Arithmetic: 1000.00 is the seed_user fixture's origination
        history-row balance; the cache value 777.77 is stale and is
        ignored by the resolver.
        """
        with app.app_context():
            account = seed_user["account"]
            scenario = seed_user["scenario"]
            # Stale the cache: keep period_id legal but mis-set
            # balance to a value the history row does not carry.
            account.current_anchor_balance = Decimal("777.77")
            db.session.commit()

            with _LogCapture(logging.getLogger(
                "app.services.balance_resolver",
            )) as cap:
                result = resolve_anchor(account, scenario.id)

            # 1000.00: seed_user origination row's anchor_balance.
            # Hand-computed: the cache value of 777.77 is what we
            # wrote into the column; the history row predates that
            # write and stays at 1000.00; history wins.
            assert result.balance == Decimal("1000.00")

            reconciliation_records = [
                r for r in cap.records
                if getattr(r, "event", None) == "anchor_cache_reconciled"
            ]
            assert len(reconciliation_records) == 1, (
                "Expected exactly one anchor_cache_reconciled event; "
                f"saw {len(reconciliation_records)}."
            )
            rec = reconciliation_records[0]
            assert rec.levelno == logging.WARNING
            assert rec.category == "business"
            assert rec.account_id == account.id
            assert rec.scenario_id == scenario.id
            assert rec.cached_balance == "777.77"
            assert rec.history_balance == "1000.00"

    # ── C4-3 -----------------------------------------------------------

    def test_resolve_anchor_never_none(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-3: a freshly factory-built account resolves to an
        ``AnchorPoint``, never ``None``.

        The Commit 3 invariant is that every account row has at
        least one matching ``AccountAnchorHistory`` row from the
        moment of creation -- the canonical factory writes both in
        one transaction.  This test exercises that invariant via the
        resolver.
        """
        with app.app_context():
            account = seed_user["account"]
            scenario = seed_user["scenario"]
            result = resolve_anchor(account, scenario.id)
            assert result is not None
            assert isinstance(result, AnchorPoint)
            # 1000.00: seed_user fixture's origination anchor.
            assert result.balance == Decimal("1000.00")
            assert result.period.id == seed_user["bootstrap_period"].id

    # ── C4-4 -----------------------------------------------------------

    def test_resolve_anchor_scenario_scoped(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-4: ``resolve_anchor`` returns the account's anchor and
        does not leak data from a sibling scenario.

        The current data model is per-account: ``AccountAnchorHistory``
        carries no ``scenario_id`` column, so two scenarios for the
        same user share the same per-account anchor.  This test
        proves that calling the resolver with either scenario's id
        returns the same per-account anchor -- i.e. no cross-account
        leakage and no scenario-id confusion.  When E-NN eventually
        adds per-scenario anchors, this test will need to be
        re-pinned to assert "scenario 2's anchor" rather than
        "account's anchor regardless of scenario"; until then, the
        contract is "no leakage between scenarios" and that is what
        is locked here.

        Arithmetic: 1000.00 is the seed_user origination anchor; it
        belongs to ``seed_user["account"]``, not to any specific
        scenario.  Both scenario ids resolve to the same value
        because the anchor is per-account.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = seed_user["account"]
            baseline = seed_user["scenario"]
            sibling = Scenario(
                user_id=user_id,
                name="What-If",
                is_baseline=False,
            )
            db.session.add(sibling)
            db.session.commit()

            from_baseline = resolve_anchor(account, baseline.id)
            from_sibling = resolve_anchor(account, sibling.id)

            # 1000.00: per-account anchor; consistent across scenarios.
            assert from_baseline.balance == Decimal("1000.00")
            assert from_sibling.balance == Decimal("1000.00")
            assert from_baseline.period.id == from_sibling.period.id
            # Per-account invariant: distinct scenarios cannot return
            # one another's data because anchors are not scenario-
            # scoped at the storage tier today.  The equality below
            # is the load-bearing guarantee.
            assert from_baseline.period.id == account.current_anchor_period_id

    # ── C4-5 -----------------------------------------------------------

    def test_resolve_anchor_decimal_type(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-5: ``AnchorPoint.balance`` is a ``Decimal`` quantized to
        two decimal places.

        ``Numeric(12, 2)`` is the storage type, so the SQLAlchemy
        adapter already returns ``Decimal`` rows with two fractional
        digits.  ``Decimal(str(...))`` preserves that representation.
        Arithmetic: a Decimal with exponent ``-2`` is the canonical
        cents-precision form; this is the contract every consumer in
        Commits 5-10 relies on.
        """
        with app.app_context():
            account = seed_user["account"]
            scenario = seed_user["scenario"]
            result = resolve_anchor(account, scenario.id)
            assert isinstance(result.balance, Decimal)
            # Exponent of -2 means two digits after the decimal point.
            assert result.balance.as_tuple().exponent == -2

    # ── C4-6 -----------------------------------------------------------

    def test_resolve_anchor_zero_balance_is_value(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """C4-6: an anchor of ``Decimal("0.00")`` is honored as a
        value, not coerced to a default or treated as "missing".

        Setup: appending a true-up history row with anchor 0.00 on
        the bootstrap period and aligning the cache columns.  The
        coding-standard rule (E-12 / CLAUDE.md "do not rely on
        truthiness for business logic") is the regression lock: code
        that wrote ``or Decimal("0.00")`` or ``if not balance:`` would
        silently swap the zero for something else; the resolver must
        preserve the zero verbatim.
        """
        with app.app_context():
            account = seed_user["account"]
            scenario = seed_user["scenario"]
            bootstrap_period_id = seed_user["bootstrap_period"].id

            _make_anchor_history(
                account_id=account.id,
                pay_period_id=bootstrap_period_id,
                anchor_balance=Decimal("0.00"),
                notes="true-up to zero",
            )
            account.current_anchor_balance = Decimal("0.00")
            account.current_anchor_period_id = bootstrap_period_id
            db.session.commit()

            result = resolve_anchor(account, scenario.id)

            # 0.00 (E-12): zero is a value; the resolver returns it
            # verbatim instead of falling back to a non-zero default.
            assert result.balance == Decimal("0.00")
            assert result.balance.as_tuple().exponent == -2
            # Make the "value, not missing" assertion explicit: the
            # resolver MUST distinguish Decimal("0.00") from None.
            assert result.balance is not None


class TestResolveAnchorMissingHistory:
    """The defensive ``RuntimeError`` for the no-history edge case.

    Commit 3 makes this state unreachable in production; the
    resolver's loud failure here is the regression trap for any
    future code path that constructs an ``Account`` row outside the
    canonical factory.  Deleting the origination row simulates that
    regression.
    """

    def test_runtime_error_when_no_history_exists(
        self, app, db, seed_user,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """Resolver raises ``RuntimeError`` (not silently None) when
        zero history rows exist.

        Arithmetic: not applicable -- this is the defensive failure
        path.  The assertion is that the error message names the
        account id and points at the canonical factory.
        """
        with app.app_context():
            account = seed_user["account"]
            scenario = seed_user["scenario"]
            (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .delete()
            )
            db.session.commit()

            with pytest.raises(RuntimeError, match=str(account.id)):
                resolve_anchor(account, scenario.id)


class TestAnchorPointDataclass:
    """Static contract tests for the :class:`AnchorPoint` dataclass."""

    def test_anchor_point_is_frozen(self):
        """``AnchorPoint`` is immutable -- writes to its fields raise.

        Frozen dataclasses are the project's chosen shape for
        canonical-producer return values: a consumer cannot mutate
        the resolver's output and have that mutation silently affect
        a sibling consumer.  ``PayPeriod`` here is a transient ORM
        instance not attached to a session; the dataclass does not
        re-load relationships, so this stays a pure-Python test.
        """
        period = PayPeriod(
            user_id=1,
            start_date=_date(2026, 1, 1),
            end_date=_date(2026, 1, 14),
            period_index=0,
        )
        anchor = AnchorPoint(
            balance=Decimal("100.00"),
            period=period,
            as_of_date=_date(2026, 1, 1),
        )
        with pytest.raises(FrozenInstanceError):
            anchor.balance = Decimal("999.00")  # type: ignore[misc]
