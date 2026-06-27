"""
Shekel Budget App -- Net-Worth Account-Data Adapter Tests.

Direct coverage for the shared :mod:`app.services.net_worth_account_data`
bridge both net-worth consumers (savings cockpit, year-end summary) feed
to ``net_worth_kernel.sum_net_worth_at_period``.  The asset-plus /
liability-minus VALUE behavior is locked end-to-end by the cross-page
balance oracle (its loan / secured cases exercise ``is_liability`` True);
these tests pin the adapter's own contract: the missing-map skip and the
degenerate-account-type guard.
"""

from collections import OrderedDict
from decimal import Decimal
from types import SimpleNamespace

from app.services import net_worth_account_data


class TestIsLiabilityAccount:
    """Tests for ``is_liability_account`` (asset-vs-liability classifier)."""

    def test_none_account_type_is_asset(self, app, db, seed_user):
        """An account with no ``account_type`` classifies as a non-liability.

        The degenerate / partially-loaded guard: a ``None`` account_type
        must not raise on ``.category_id`` and is treated as an asset, so
        net worth never crashes on a half-loaded row.
        """
        with app.app_context():
            account = SimpleNamespace(account_type=None)
            assert net_worth_account_data.is_liability_account(account) is False

    def test_seed_checking_is_asset(self, app, db, seed_user):
        """The seed Checking account (Asset category) classifies as False."""
        with app.app_context():
            assert net_worth_account_data.is_liability_account(
                seed_user["account"],
            ) is False


class TestToNetWorthAccountData:
    """Tests for ``to_net_worth_account_data`` (seam-map -> account-data)."""

    def test_pairs_balances_with_liability_flag(self, app, db, seed_user):
        """Each mapped account becomes {account_id, balances, is_liability}.

        The seed Checking account (asset) with a one-period balance map of
        $100.00 yields a single row whose ``is_liability`` is False and
        whose ``balances`` is the supplied map.
        """
        with app.app_context():
            account = seed_user["account"]
            balance_maps = {
                account.id: OrderedDict({1: Decimal("100.00")}),
            }
            data = net_worth_account_data.to_net_worth_account_data(
                [account], balance_maps,
            )
            assert data == [{
                "account_id": account.id,
                "balances": OrderedDict({1: Decimal("100.00")}),
                "is_liability": False,
            }]

    def test_skips_accounts_with_no_map(self, app, db, seed_user):
        """An account absent from ``balance_maps`` is omitted from the result.

        Mirrors the seam's no-anchor omission: build_maps drops an
        un-anchored account, so it has no key here and contributes no row
        (rather than a ``None`` balances entry that would crash the sum).
        """
        with app.app_context():
            account = seed_user["account"]
            # Empty balance_maps -> the account has no map -> omitted.
            assert net_worth_account_data.to_net_worth_account_data(
                [account], {},
            ) == []
