"""
Shekel Budget App -- Audit Trigger Benchmark Script

Measures recurrence engine performance with and without audit triggers
across multiple template configurations and period counts.

Usage:
    python scripts/benchmark_triggers.py

Output:
    Table of results with timing, row counts, and overhead percentages.

This script is for manual benchmarking, not automated CI.
"""
import os
import sys
import time
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ITERATIONS = 5


def run_benchmark():
    """Execute the benchmark suite and print results."""
    from app import create_app
    from app.extensions import db
    from app.models.user import User, UserSettings
    from app.models.account import Account
    from app.models.scenario import Scenario
    from app.models.category import Category
    from app.models.transaction import Transaction
    from app.models.transaction_template import TransactionTemplate
    from app.models.recurrence_rule import RecurrenceRule
    from app.models.ref import AccountType, RecurrencePattern, Status, TransactionType
    from app.services import recurrence_engine, pay_period_service
    from app.services.auth_service import hash_password

    app = create_app()

    with app.app_context():
        # --- Setup ---
        print("Setting up benchmark data...")

        user = User(
            email="benchmark@shekel.local",
            password_hash=hash_password("benchpass"),
            display_name="Benchmark User",
        )
        db.session.add(user)
        db.session.flush()

        settings = UserSettings(user_id=user.id)
        db.session.add(settings)

        checking = db.session.query(AccountType).filter_by(name="Checking").one()
        account = Account(
            user_id=user.id,
            account_type_id=checking.id,
            name="Bench Checking",
            current_anchor_balance=Decimal("5000.00"),
        )
        db.session.add(account)

        scenario = Scenario(user_id=user.id, name="Baseline", is_baseline=True)
        db.session.add(scenario)
        db.session.flush()

        category = Category(
            user_id=user.id, group_name="Home", item_name="Bench Expense"
        )
        db.session.add(category)
        db.session.flush()

        periods = pay_period_service.generate_pay_periods(
            user_id=user.id,
            start_date=date(2026, 1, 2),
            num_periods=52,
            cadence_days=14,
        )
        db.session.flush()
        account.current_anchor_period_id = periods[0].id
        db.session.commit()

        expense = db.session.query(TransactionType).filter_by(name="Expense").one()
        pattern = db.session.query(RecurrencePattern).filter_by(name="Every Period").one()

        rule = RecurrenceRule(user_id=user.id, pattern_id=pattern.id)
        db.session.add(rule)
        db.session.flush()

        template = TransactionTemplate(
            user_id=user.id,
            account_id=account.id,
            category_id=category.id,
            recurrence_rule_id=rule.id,
            transaction_type_id=expense.id,
            name="Benchmark Expense",
            default_amount=Decimal("150.00"),
        )
        db.session.add(template)
        db.session.flush()
        db.session.refresh(template)
        db.session.commit()

        # --- Benchmark ---
        results = []

        def _clean():
            db.session.execute(
                db.text("DELETE FROM budget.transactions WHERE template_id = :tid"),
                {"tid": template.id},
            )
            db.session.commit()

        def _time_gen(iters=ITERATIONS):
            times = []
            for _ in range(iters):
                _clean()
                start = time.perf_counter()
                recurrence_engine.generate_for_template(
                    template, periods, scenario.id
                )
                db.session.flush()
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)
                db.session.commit()
            times.sort()
            return times[len(times) // 2]

        # With triggers
        print("Benchmarking with triggers enabled...")
        t_with = _time_gen()

        # Without triggers
        print("Benchmarking with triggers disabled...")
        db.session.execute(
            db.text("ALTER TABLE budget.transactions DISABLE TRIGGER audit_transactions")
        )
        db.session.commit()
        t_without = _time_gen()
        db.session.execute(
            db.text("ALTER TABLE budget.transactions ENABLE TRIGGER audit_transactions")
        )
        db.session.commit()

        overhead = ((t_with - t_without) / t_without) * 100 if t_without > 0 else 0

        # --- Cleanup ---
        _clean()
        db.session.execute(
            db.text("DELETE FROM budget.transaction_templates WHERE id = :id"),
            {"id": template.id},
        )
        db.session.execute(
            db.text("DELETE FROM budget.recurrence_rules WHERE id = :id"),
            {"id": rule.id},
        )
        db.session.execute(
            db.text("DELETE FROM budget.pay_periods WHERE user_id = :uid"),
            {"uid": user.id},
        )
        db.session.execute(
            db.text("DELETE FROM budget.categories WHERE user_id = :uid"),
            {"uid": user.id},
        )
        db.session.execute(
            db.text("DELETE FROM budget.accounts WHERE user_id = :uid"),
            {"uid": user.id},
        )
        db.session.execute(
            db.text("DELETE FROM budget.scenarios WHERE user_id = :uid"),
            {"uid": user.id},
        )
        db.session.execute(
            db.text("DELETE FROM auth.user_settings WHERE user_id = :uid"),
            {"uid": user.id},
        )
        db.session.execute(
            db.text("DELETE FROM auth.users WHERE id = :uid"),
            {"uid": user.id},
        )
        db.session.commit()

        # --- Report ---
        print("\n" + "=" * 60)
        print("  AUDIT TRIGGER BENCHMARK RESULTS")
        print("=" * 60)
        print(f"  Template: every_period, 52 pay periods")
        print(f"  Iterations: {ITERATIONS} (median taken)")
        print(f"  With triggers:    {t_with:7.1f} ms")
        print(f"  Without triggers: {t_without:7.1f} ms")
        print(f"  Overhead:         {overhead:7.1f}%")
        print(f"  Threshold:        {20:7d}%")
        print(f"  Status:           {'PASS' if overhead < 20 else 'FAIL'}")
        print("=" * 60)


if __name__ == "__main__":
    run_benchmark()
