"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_transfer_recurrence.py``, moved verbatim by ``tools/split``.
"""

import logging
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.models.ref import TransactionType
from app import ref_cache
from app.enums import AmountSourceEnum
from tests._test_helpers import create_account_of_type, make_cadence_rule, shadow_amount
from tests._test_helpers import state_template_price
from tests._test_helpers import transfer_amount


def _assert_shadows_valid(xfer):
    """Assert a transfer has exactly 2 correct shadow transactions.

    **Only valid for a transfer that is NOT a loan payment**, and the amount
    assertion is why: a leg reads its parent through amount rule 5, so
    ``shadow_amount(leg) == xfer.amount`` holds by construction -- but rule 4
    prices a loan payment's leg from the LOAN's installment, which is
    deliberately not the parent's figure.  Every caller here generates plain
    transfers; a loan payment reaching this helper would fail on a rule working
    correctly.

    The amount line therefore carries little on its own, so the DECLARATION is
    asserted beside it: since plan step X-au-g-2c-2 a leg stores no figure and
    names ``PARENT_TRANSFER``, and that pair is the invariant this used to check
    by comparing two stored columns.
    """
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=xfer.id)
        .all()
    )
    assert len(shadows) == 2, (
        f"Transfer {xfer.id} has {len(shadows)} shadows (expected 2)"
    )

    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()
    types = {s.transaction_type_id for s in shadows}
    assert types == {expense_type.id, income_type.id}

    parent_transfer_id = ref_cache.amount_source_id(
        AmountSourceEnum.PARENT_TRANSFER,
    )
    for s in shadows:
        assert s.estimated_amount is None
        assert s.amount_source_id == parent_transfer_id
        assert shadow_amount(s) == transfer_amount(xfer)
        assert s.status_id == xfer.status_id
        assert s.pay_period_id == xfer.pay_period_id
        # due_date mirrors the parent (Transfer Invariant 3).
        assert s.due_date == xfer.due_date

    expense = [s for s in shadows if s.transaction_type_id == expense_type.id][0]
    income = [s for s in shadows if s.transaction_type_id == income_type.id][0]
    assert expense.account_id == xfer.from_account_id
    assert income.account_id == xfer.to_account_id
    return expense, income


def make_template_with_rule(seed_user, cadence, **rule_kwargs):
    """Create a savings account, a transfer template and its cadence rule.

    **ONE copy of what was three byte-identical methods.**  Four other classes
    in this file define a `_make_template_with_rule` of their own and keep it:
    each differs genuinely -- a second endpoint, an explicit bound, a loan
    destination -- so collapsing those would be a shared helper with a flag per
    caller, which is the shape being removed rather than a second instance of
    it.

    Args:
        seed_user: The `seed_user` fixture's mapping.
        cadence: The `RecurrenceUnitEnum` the rule fires on.
        **rule_kwargs: `interval_n`, `day_of_month`, `month_of_year`.

    Returns:
        The refreshed `TransferTemplate`.
    """
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Savings",
        anchor_balance=Decimal("500.00"),
    )

    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        name="Test Transfer",
        default_amount=Decimal("100.00"),
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template)
    # The definition first, then the cadence onto it (plan step R-F6).
    make_cadence_rule(
        template, cadence,
        interval_n=rule_kwargs.get("interval_n", 1),
        fires_on_day=rule_kwargs.get("day_of_month"),
        fires_in_month=rule_kwargs.get("month_of_year"),
    )

    db.session.refresh(template)
    return template


class _LogCapture:
    """Capture log records emitted by ``logger_name`` with propagation off.

    Mirrors the helper in ``tests/test_services/test_service_log_events.py``;
    duplicated here rather than imported so the Commit 34 test class has
    no cross-file dependency on another test module's private helper.
    """

    def __init__(self, logger_name: str, level: int = logging.DEBUG) -> None:
        self._logger = logging.getLogger(logger_name)
        self._level = level
        self.records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.records.append(record)
        self._prior_level: int | None = None
        self._prior_propagate: bool | None = None

    def __enter__(self) -> "_LogCapture":
        self._prior_level = self._logger.level
        self._prior_propagate = self._logger.propagate
        self._logger.addHandler(self._handler)
        self._logger.setLevel(self._level)
        self._logger.propagate = False
        return self

    def __exit__(self, *exc) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prior_level)
        self._logger.propagate = self._prior_propagate

    def find_all(self, event_name: str) -> list[logging.LogRecord]:
        """Return every captured record whose ``event`` field matches."""
        return [
            r for r in self.records
            if getattr(r, "event", None) == event_name
        ]
