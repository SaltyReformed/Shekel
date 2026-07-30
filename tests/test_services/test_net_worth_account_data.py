"""
Shekel Budget App -- asset-vs-liability classifier tests.

Direct coverage for the shared :mod:`app.services.net_worth_account_data`
classifier every net-worth surface reaches through
:attr:`~app.services.savings_dashboard_service._types.AccountProjection.is_liability`
(it was shared with the year-end summary until plan step F2 deleted that
package).  The asset-plus / liability-minus VALUE behavior is locked end-to-end
by the cross-page balance oracle (its loan / secured cases exercise
``is_liability`` True); these tests pin the classifier's own contract: the
LIABILITY category id and the degenerate-account-type guard.

The module's ``to_net_worth_account_data`` adapter -- and the
``{account_id, balances, is_liability}`` container it built beside the
projection that derives the same flag -- went at plan step X-w (ruling R-CG,
finding N-114), so its two tests went with it.
"""

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
