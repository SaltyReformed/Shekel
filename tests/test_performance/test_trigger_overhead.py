"""Performance tests for audit trigger overhead on the recurrence engine (Phase 8B WU-6).

These tests measure the execution time of recurrence engine operations
with and without audit triggers enabled.  The 20% overhead threshold
is specified in the Phase 8 master plan.

Run explicitly (not included in default pytest suite):
    pytest tests/test_performance/ -v -s
"""
import time
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern, Status, TransactionType
from app.services import recurrence_engine

# Overhead threshold from the Phase 8 plan.
MAX_OVERHEAD_PERCENT = 20

# Number of timing iterations for more stable measurements.
ITERATIONS = 5
# Warmup iterations discarded before timing.
WARMUP = 2


def _create_template(perf_user, pattern_name="every_period"):
    """Create a template with a recurrence rule for benchmarking."""
    expense_type = db.session.query(TransactionType).filter_by(name="expense").one()
    pattern = db.session.query(RecurrencePattern).filter_by(name=pattern_name).one()

    rule = RecurrenceRule(
        user_id=perf_user["user"].id,
        pattern_id=pattern.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=perf_user["user"].id,
        account_id=perf_user["account"].id,
        category_id=perf_user["category"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Benchmark Expense",
        default_amount=Decimal("150.00"),
    )
    db.session.add(template)
    db.session.flush()

    # Reload to get relationships populated.
    db.session.refresh(template)
    return template


def _delete_generated_transactions(template_id):
    """Delete all transactions generated from a template."""
    db.session.execute(
        db.text("DELETE FROM budget.transactions WHERE template_id = :tid"),
        {"tid": template_id},
    )
    db.session.flush()


def _disable_triggers():
    """Disable audit triggers on budget.transactions."""
    db.session.execute(
        db.text("ALTER TABLE budget.transactions DISABLE TRIGGER audit_transactions")
    )


def _enable_triggers():
    """Re-enable audit triggers on budget.transactions."""
    db.session.execute(
        db.text("ALTER TABLE budget.transactions ENABLE TRIGGER audit_transactions")
    )


def _time_generate(template, periods, scenario_id, iterations=ITERATIONS):
    """Time generate_for_template over multiple iterations, return median ms."""
    # Warmup to stabilize caches and connection pools.
    for _ in range(WARMUP):
        _delete_generated_transactions(template.id)
        db.session.commit()
        recurrence_engine.generate_for_template(template, periods, scenario_id)
        db.session.flush()
        db.session.commit()

    times = []
    for _ in range(iterations):
        _delete_generated_transactions(template.id)
        db.session.commit()

        start = time.perf_counter()
        recurrence_engine.generate_for_template(
            template, periods, scenario_id
        )
        db.session.flush()
        elapsed_ms = (time.perf_counter() - start) * 1000
        times.append(elapsed_ms)

    times.sort()
    return times[len(times) // 2]  # median


class TestRecurrenceEngineOverhead:
    """Benchmark recurrence engine with and without audit triggers."""

    def test_generate_for_template_overhead(self, app, db, perf_user, perf_periods):
        """generate_for_template() overhead with triggers is under 20%.

        Steps:
        1. Create a template with 'every_period' recurrence (52 txns).
        2. Time generate_for_template() with triggers enabled.
        3. Disable triggers on budget.transactions.
        4. Time generate_for_template() without triggers.
        5. Re-enable triggers.
        6. Assert overhead is under MAX_OVERHEAD_PERCENT.
        """
        template = _create_template(perf_user)
        scenario_id = perf_user["scenario"].id

        # Time with triggers enabled.
        time_with = _time_generate(template, perf_periods, scenario_id)

        # Time without triggers.
        _disable_triggers()
        try:
            time_without = _time_generate(template, perf_periods, scenario_id)
        finally:
            _enable_triggers()

        overhead_pct = ((time_with - time_without) / time_without) * 100

        print(f"\n  generate_for_template (52 periods):")
        print(f"    With triggers:    {time_with:.1f} ms")
        print(f"    Without triggers: {time_without:.1f} ms")
        print(f"    Overhead:         {overhead_pct:.1f}%")

        assert overhead_pct < MAX_OVERHEAD_PERCENT, (
            f"Trigger overhead {overhead_pct:.1f}% exceeds {MAX_OVERHEAD_PERCENT}% threshold"
        )

    def test_regenerate_for_template_overhead(self, app, db, perf_user, perf_periods):
        """regenerate_for_template() overhead with triggers is under 20%.

        Measures the delete + recreate cycle.
        """
        template = _create_template(perf_user)
        scenario_id = perf_user["scenario"].id

        def _time_regenerate(iterations=ITERATIONS):
            # Warmup.
            for _ in range(WARMUP):
                _delete_generated_transactions(template.id)
                recurrence_engine.generate_for_template(
                    template, perf_periods, scenario_id
                )
                db.session.commit()
                recurrence_engine.regenerate_for_template(
                    template, perf_periods, scenario_id
                )
                db.session.flush()
                db.session.commit()

            times = []
            for _ in range(iterations):
                # Ensure transactions exist to be regenerated.
                _delete_generated_transactions(template.id)
                recurrence_engine.generate_for_template(
                    template, perf_periods, scenario_id
                )
                db.session.commit()

                start = time.perf_counter()
                recurrence_engine.regenerate_for_template(
                    template, perf_periods, scenario_id
                )
                db.session.flush()
                elapsed_ms = (time.perf_counter() - start) * 1000
                times.append(elapsed_ms)
            times.sort()
            return times[len(times) // 2]

        time_with = _time_regenerate()

        _disable_triggers()
        try:
            time_without = _time_regenerate()
        finally:
            _enable_triggers()

        overhead_pct = ((time_with - time_without) / time_without) * 100

        print(f"\n  regenerate_for_template (52 periods):")
        print(f"    With triggers:    {time_with:.1f} ms")
        print(f"    Without triggers: {time_without:.1f} ms")
        print(f"    Overhead:         {overhead_pct:.1f}%")

        assert overhead_pct < MAX_OVERHEAD_PERCENT, (
            f"Trigger overhead {overhead_pct:.1f}% exceeds {MAX_OVERHEAD_PERCENT}% threshold"
        )

    def test_bulk_transaction_insert_overhead(self, app, db, perf_user, perf_periods):
        """Bulk INSERT of 100 transactions: overhead with triggers under 20%.

        Direct ORM inserts to isolate trigger overhead from recurrence logic.
        """
        projected = db.session.query(Status).filter_by(name="projected").one()
        expense = db.session.query(TransactionType).filter_by(name="expense").one()
        scenario_id = perf_user["scenario"].id
        category_id = perf_user["category"].id

        def _bulk_insert():
            for i, period in enumerate(perf_periods[:100]):
                txn = Transaction(
                    pay_period_id=period.id,
                    scenario_id=scenario_id,
                    status_id=projected.id,
                    name=f"Bulk Txn {i}",
                    category_id=category_id,
                    transaction_type_id=expense.id,
                    estimated_amount=Decimal("50.00"),
                )
                db.session.add(txn)
            db.session.flush()

        def _time_bulk(iterations=ITERATIONS):
            # Warmup.
            for _ in range(WARMUP):
                db.session.execute(
                    db.text("DELETE FROM budget.transactions WHERE name LIKE 'Bulk Txn%'")
                )
                db.session.commit()
                _bulk_insert()
                db.session.commit()

            times = []
            for _ in range(iterations):
                db.session.execute(
                    db.text("DELETE FROM budget.transactions WHERE name LIKE 'Bulk Txn%'")
                )
                db.session.commit()

                start = time.perf_counter()
                _bulk_insert()
                elapsed_ms = (time.perf_counter() - start) * 1000
                times.append(elapsed_ms)
            times.sort()
            return times[len(times) // 2]

        time_with = _time_bulk()

        _disable_triggers()
        try:
            time_without = _time_bulk()
        finally:
            _enable_triggers()

        # Skip if baseline is too fast for reliable measurement.
        if time_without < 1.0:
            pytest.skip("Baseline too fast for reliable overhead measurement")

        overhead_pct = ((time_with - time_without) / time_without) * 100

        print(f"\n  Bulk INSERT (52 transactions):")
        print(f"    With triggers:    {time_with:.1f} ms")
        print(f"    Without triggers: {time_without:.1f} ms")
        print(f"    Overhead:         {overhead_pct:.1f}%")

        assert overhead_pct < MAX_OVERHEAD_PERCENT, (
            f"Trigger overhead {overhead_pct:.1f}% exceeds {MAX_OVERHEAD_PERCENT}% threshold"
        )

    def test_bulk_update_trigger_overhead(self, app, db, perf_user, perf_periods):
        """Bulk UPDATE of 100 transactions: overhead with triggers under 20%.

        UPDATEs are the most common write operation in a budgeting app
        (editing amounts, marking done, changing statuses).
        """
        projected = db.session.query(Status).filter_by(name="projected").one()
        expense = db.session.query(TransactionType).filter_by(name="expense").one()
        scenario_id = perf_user["scenario"].id
        category_id = perf_user["category"].id
        batch_size = min(len(perf_periods), 100)

        # Pre-insert rows to update.
        for i, period in enumerate(perf_periods[:batch_size]):
            txn = Transaction(
                pay_period_id=period.id,
                scenario_id=scenario_id,
                status_id=projected.id,
                name=f"Update Txn {i}",
                category_id=category_id,
                transaction_type_id=expense.id,
                estimated_amount=Decimal("50.00"),
            )
            db.session.add(txn)
        db.session.flush()
        db.session.commit()

        def _bulk_update(amount_val):
            """Update all benchmark transactions to a new amount."""
            db.session.execute(
                db.text(
                    "UPDATE budget.transactions "
                    "SET estimated_amount = :amt "
                    "WHERE name LIKE 'Update Txn%'"
                ),
                {"amt": amount_val},
            )
            db.session.flush()

        def _time_update(amount_val, iterations=ITERATIONS):
            """Time bulk UPDATE over multiple iterations, return median ms."""
            for _ in range(WARMUP):
                _bulk_update(amount_val)
                db.session.commit()

            times = []
            for _ in range(iterations):
                start = time.perf_counter()
                _bulk_update(amount_val)
                elapsed_ms = (time.perf_counter() - start) * 1000
                times.append(elapsed_ms)
                db.session.commit()
            times.sort()
            return times[len(times) // 2]

        # Time with triggers enabled.
        time_with = _time_update(Decimal("75.00"))

        # Time without triggers.
        _disable_triggers()
        try:
            time_without = _time_update(Decimal("100.00"))
        finally:
            _enable_triggers()

        if time_without < 1.0:
            pytest.skip("Baseline too fast for reliable overhead measurement")

        overhead_pct = ((time_with - time_without) / time_without) * 100

        print(f"\n  Bulk UPDATE ({batch_size} transactions):")
        print(f"    With triggers:    {time_with:.1f} ms")
        print(f"    Without triggers: {time_without:.1f} ms")
        print(f"    Overhead:         {overhead_pct:.1f}%")

        assert overhead_pct < MAX_OVERHEAD_PERCENT, (
            f"Trigger overhead {overhead_pct:.1f}% exceeds {MAX_OVERHEAD_PERCENT}% threshold"
        )

    def test_bulk_delete_trigger_overhead(self, app, db, perf_user, perf_periods):
        """Bulk DELETE of transactions: overhead with triggers under 20%."""
        projected = db.session.query(Status).filter_by(name="projected").one()
        expense = db.session.query(TransactionType).filter_by(name="expense").one()
        scenario_id = perf_user["scenario"].id
        category_id = perf_user["category"].id
        batch_size = min(len(perf_periods), 100)

        def _insert_batch(label):
            """Insert a batch of transactions for deletion benchmarking."""
            for i, period in enumerate(perf_periods[:batch_size]):
                txn = Transaction(
                    pay_period_id=period.id,
                    scenario_id=scenario_id,
                    status_id=projected.id,
                    name=f"Delete {label} {i}",
                    category_id=category_id,
                    transaction_type_id=expense.id,
                    estimated_amount=Decimal("50.00"),
                )
                db.session.add(txn)
            db.session.flush()
            db.session.commit()

        def _time_delete(label, iterations=ITERATIONS):
            """Time bulk DELETE over multiple iterations, return median ms."""
            for warmup_idx in range(WARMUP):
                _insert_batch(f"{label}_w{warmup_idx}")
                db.session.execute(
                    db.text(
                        "DELETE FROM budget.transactions "
                        "WHERE name LIKE :pattern"
                    ),
                    {"pattern": f"Delete {label}_w{warmup_idx}%"},
                )
                db.session.flush()
                db.session.commit()

            times = []
            for iteration in range(iterations):
                batch_label = f"{label}_{iteration}"
                _insert_batch(batch_label)
                start = time.perf_counter()
                db.session.execute(
                    db.text(
                        "DELETE FROM budget.transactions "
                        "WHERE name LIKE :pattern"
                    ),
                    {"pattern": f"Delete {batch_label}%"},
                )
                db.session.flush()
                elapsed_ms = (time.perf_counter() - start) * 1000
                times.append(elapsed_ms)
                db.session.commit()
            times.sort()
            return times[len(times) // 2]

        # Time with triggers enabled.
        time_with = _time_delete("trig")

        # Time without triggers.
        _disable_triggers()
        try:
            time_without = _time_delete("notr")
        finally:
            _enable_triggers()

        if time_without < 1.0:
            pytest.skip("Baseline too fast for reliable overhead measurement")

        overhead_pct = ((time_with - time_without) / time_without) * 100

        print(f"\n  Bulk DELETE ({batch_size} transactions):")
        print(f"    With triggers:    {time_with:.1f} ms")
        print(f"    Without triggers: {time_without:.1f} ms")
        print(f"    Overhead:         {overhead_pct:.1f}%")

        assert overhead_pct < MAX_OVERHEAD_PERCENT, (
            f"Trigger overhead {overhead_pct:.1f}% exceeds {MAX_OVERHEAD_PERCENT}% threshold"
        )
