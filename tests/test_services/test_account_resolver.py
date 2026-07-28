"""
Shekel Budget App -- Account Resolver Tests

Tests the resolve_grid_account() fallback chain:
1. override_account_id
2. user_settings.default_grid_account_id
3. First active checking account (by sort_order, id)
4. First active account of any type
5. None
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.user import UserSettings
from app.services.account_resolver import (
    list_grid_accounts,
    resolve_analytics_account,
    resolve_grid_account,
)
from app.services import account_service


def _create_mortgage_account(seed_user, name="Mortgage", sort_order=0):
    """Create an active AMORTIZING account for the kind-gate tests.

    A bare Mortgage-type account (no ``LoanParams``) is deliberate: the
    D4/A1 grid gate branches on the type's ``has_amortization`` column
    alone, so it must exclude a loan account the moment it exists --
    including the created-but-not-yet-configured state production
    passes through between ``create_account`` and the loan setup page.
    """
    mortgage_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
    loan = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=mortgage_type.id,
            name=name,
            anchor_balance=Decimal("0"),
        ),
        sort_order=sort_order,
    )
    db.session.add(loan)
    db.session.commit()
    return loan


class TestResolveGridAccount:
    """Tests for resolve_grid_account()."""

    def test_no_setting_returns_checking(self, app, db, seed_user):
        """Without any setting, returns the first checking account."""
        with app.app_context():
            result = resolve_grid_account(seed_user["user"].id)
            assert result is not None
            assert result.id == seed_user["account"].id
            assert result.name == "Checking"

    def test_setting_configured_returns_that_account(self, app, db, seed_user):
        """When default_grid_account_id is set, returns that account."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = savings.id
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id, settings)
            assert result.id == savings.id

    def test_inactive_configured_account_falls_back(self, app, db, seed_user):
        """When configured account is inactive, falls back to checking."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
                is_active=False,
            )
            db.session.add(savings)
            db.session.flush()

            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = savings.id
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id, settings)
            assert result.id == seed_user["account"].id

    def test_override_takes_precedence(self, app, db, seed_user):
        """override_account_id takes priority over setting."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            # Set default to checking.
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = seed_user["account"].id
            db.session.commit()

            # Override to savings.
            result = resolve_grid_account(
                seed_user["user"].id, settings,
                override_account_id=savings.id,
            )
            assert result.id == savings.id

    def test_override_validates_ownership(self, app, db, seed_user):
        """Override with wrong user's account falls back."""
        with app.app_context():
            from app.models.user import User
            from werkzeug.security import generate_password_hash

            from datetime import date as _date, timedelta as _td  # pylint: disable=import-outside-toplevel
            from app.models.pay_period import PayPeriod as _PayPeriod  # pylint: disable=import-outside-toplevel

            other_user = User(
                email="other@test.local",
                password_hash=generate_password_hash("pass"),
            )
            db.session.add(other_user)
            db.session.flush()

            # Bootstrap pay period for the second user so the factory
            # has somewhere to anchor against.
            db.session.add(_PayPeriod(
                user_id=other_user.id,
                start_date=_date(2024, 1, 5),
                end_date=_date(2024, 1, 5) + _td(days=13),
                period_index=0,
            ))
            db.session.flush()

            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
            other_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=other_user.id,
                    account_type_id=checking_type.id,
                    name="Other Checking",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(other_acct)
            db.session.commit()

            result = resolve_grid_account(
                seed_user["user"].id, None,
                override_account_id=other_acct.id,
            )
            # Should NOT return other user's account; falls back to own checking.
            assert result.id == seed_user["account"].id

    def test_no_accounts_returns_none(self, app, db, seed_user):
        """When user has no active accounts, returns None."""
        with app.app_context():
            acct = db.session.get(Account, seed_user["account"].id)
            acct.is_active = False
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id)
            assert result is None

    def test_no_checking_returns_first_active(self, app, db, seed_user):
        """Without a checking account, returns first active of any type."""
        with app.app_context():
            # Deactivate checking.
            acct = db.session.get(Account, seed_user["account"].id)
            acct.is_active = False
            db.session.flush()

            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id)
            assert result.id == savings.id

    def test_deterministic_ordering(self, app, db, seed_user):
        """With multiple checking accounts, returns the one with lowest sort_order then id."""
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            # The seed checking account has sort_order=0.  Create another with sort_order=0
            # but it will have a higher id.
            checking2 = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Checking 2",
                    anchor_balance=Decimal("0"),
                ),
                sort_order=0,
            )
            db.session.add(checking2)
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id)
            # Should return the original (lower id).
            assert result.id == seed_user["account"].id

    def test_override_inactive_falls_back(self, app, db, seed_user):
        """Override with inactive account falls back to checking."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            inactive = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Inactive Savings",
                    anchor_balance=Decimal("0"),
                ),
                is_active=False,
            )
            db.session.add(inactive)
            db.session.commit()

            result = resolve_grid_account(
                seed_user["user"].id, None,
                override_account_id=inactive.id,
            )
            assert result.id == seed_user["account"].id


class TestGridKindGate:
    """The D4 / A1 amortizing-kind gate on every grid resolution step.

    The grid is a cash-flow surface; a loan's balance is not a
    transaction sum, so the resolver refuses an AMORTIZING account at
    every step (finding B-3: the grid rendered the real Mortgage RISING
    by the full PITI each month, and the dashboard hero shares this
    resolver).
    """

    def test_override_amortizing_loan_falls_back(self, app, db, seed_user):
        """``?account_id=<loan>`` behaves like a missing account: fallback."""
        with app.app_context():
            loan = _create_mortgage_account(seed_user)

            result = resolve_grid_account(
                seed_user["user"].id, None,
                override_account_id=loan.id,
            )
            assert result.id == seed_user["account"].id

    def test_default_setting_amortizing_loan_ignored(self, app, db, seed_user):
        """A saved default naming a loan is ignored, like an archived one."""
        with app.app_context():
            loan = _create_mortgage_account(seed_user)

            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = loan.id
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id, settings)
            assert result.id == seed_user["account"].id

    def test_any_type_fallback_skips_amortizing_loan(self, app, db, seed_user):
        """Step 4 skips a loan even when its sort order would win."""
        with app.app_context():
            # No checking account, so the resolver reaches step 4.
            acct = db.session.get(Account, seed_user["account"].id)
            acct.is_active = False
            db.session.flush()

            # The loan sorts FIRST (sort_order=0 vs 5) -- proving the
            # exclusion is the kind gate, not the ordering.
            _create_mortgage_account(seed_user, sort_order=0)
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
                sort_order=5,
            )
            db.session.add(savings)
            db.session.commit()

            result = resolve_grid_account(seed_user["user"].id)
            assert result.id == savings.id

    def test_only_loans_returns_none(self, app, db, seed_user):
        """A user whose only active account is a loan gets None, not the loan."""
        with app.app_context():
            acct = db.session.get(Account, seed_user["account"].id)
            acct.is_active = False
            db.session.flush()
            _create_mortgage_account(seed_user)

            assert resolve_grid_account(seed_user["user"].id) is None

    def test_list_grid_accounts_excludes_amortizing(self, app, db, seed_user):
        """The settings picker's option list never offers a loan."""
        with app.app_context():
            loan = _create_mortgage_account(seed_user)

            accounts = list_grid_accounts(seed_user["user"].id)
            ids = [a.id for a in accounts]
            assert seed_user["account"].id in ids
            assert loan.id not in ids


class TestAnalyticsKindGate:
    """The X-a1 amortizing-kind gate on the analytics resolver (N-38).

    The calendar reads the seam's CASH-FLOW view -- the account's pure
    transaction running balance -- through ``cash_balance_at`` and
    ``cash_daily_balance_series``.  Pointed at a loan, that view sums
    the loan's payment shadows onto its anchor and answers with
    confidence: measured on a dev clone before this gate,
    ``?account_id=<Van Loan>`` rendered ``$531.94`` for a loan owing
    ``$15,663.59``, and ``?account_id=<Mortgage>`` rendered
    ``$178,103.41`` against ``$177,277.97`` owed.  That is finding B-3
    on the surface ruling D4's enumeration missed.

    Unlike the grid resolver, this one does NOT fall through to
    checking: an explicit ``account_id`` asks about THAT account, so
    answering with another account's balance would be a wrong answer
    rather than a missing one.
    """

    def test_explicit_amortizing_loan_returns_none(self, app, db, seed_user):
        """An explicit ``?account_id=<loan>`` resolves to None, not the loan."""
        with app.app_context():
            loan = _create_mortgage_account(seed_user)

            assert resolve_analytics_account(
                seed_user["user"].id, loan.id,
            ) is None

    def test_refused_loan_does_not_fall_through_like_the_grid(
        self, app, db, seed_user,
    ):
        """The two resolvers refuse the SAME loan in two different ways.

        Both gate on kind, but the analytics path returns ``None``
        where the grid path falls through to checking: an explicit
        ``?account_id=`` is a question about THAT account, so rendering
        Checking's balance under a URL that named the Mortgage would
        answer a question the caller did not ask.  Driving both
        resolvers against one loan in one test is what gives the
        distinction teeth -- asserting ``None`` alone would still pass
        if the analytics gate were changed to fall through and then
        happened to return ``None`` for another reason.
        """
        with app.app_context():
            loan = _create_mortgage_account(seed_user)
            checking_id = seed_user["account"].id

            grid = resolve_grid_account(
                seed_user["user"].id, None, override_account_id=loan.id,
            )
            analytics = resolve_analytics_account(
                seed_user["user"].id, loan.id,
            )

            assert grid is not None
            assert grid.id == checking_id
            assert analytics is None

    def test_explicit_cash_account_still_resolves(self, app, db, seed_user):
        """The gate refuses ONLY amortizing kinds, not every explicit id."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
                sort_order=5,
            )
            db.session.add(savings)
            db.session.commit()

            result = resolve_analytics_account(
                seed_user["user"].id, savings.id,
            )
            assert result is not None
            assert result.id == savings.id

    def test_fallback_branch_unaffected_by_a_loan(self, app, db, seed_user):
        """``account_id=None`` still resolves checking when a loan exists.

        The fallback is CHECKING-typed by construction, so the gate has
        nothing to add there; this pins that adding it changed nothing.
        """
        with app.app_context():
            _create_mortgage_account(seed_user)

            result = resolve_analytics_account(seed_user["user"].id, None)
            assert result is not None
            assert result.id == seed_user["account"].id
