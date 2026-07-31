"""
Shekel Budget App -- account-category classifier tests.

Direct coverage for the shared :mod:`app.services.account_category` classifier
every net-worth surface reaches through
:attr:`~app.services.savings_dashboard_service._types.AccountProjection.is_liability`
and every cockpit band reaches through
:func:`~app.services.savings_dashboard_service._display.category_key`
(the module was ``net_worth_account_data`` until plan step X-z, ruling R-CQ,
and was shared with the year-end summary until plan step F2 deleted that
package).  The asset-plus / liability-minus VALUE behavior is locked end-to-end
by the cross-page balance oracle (its loan / secured cases exercise
``is_liability`` True); these tests pin the classifier's own contract: the
per-category answer, the two states with no modelled category, and the
CONSTRUCTION property finding N-118 exists for -- that
``category_key(account_category(a)) == LIABILITY_KEY`` and
``is_liability_account(a)`` are one answer rather than two rules that agree.

The module's ``to_net_worth_account_data`` adapter -- and the
``{account_id, balances, is_liability}`` container it built beside the
projection that derives the same flag -- went at plan step X-w (ruling R-CG,
finding N-114), so its two tests went with it.
"""

from types import SimpleNamespace

import pytest

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.services import account_category
from app.services.savings_dashboard_service._display import (
    LIABILITY_KEY,
    _CATEGORY_KEYS,
    _OTHER_KEY,
    category_key,
)


def _account_in(category: AcctCategoryEnum):
    """Build a stand-in account whose type sits in *category*."""
    return SimpleNamespace(
        account_type=SimpleNamespace(
            category_id=ref_cache.acct_category_id(category),
        ),
    )


class TestAccountCategory:
    """The one classifier: ``category_id`` -> ``AcctCategoryEnum | None``."""

    @pytest.mark.parametrize("category", list(AcctCategoryEnum))
    def test_each_category_id_resolves_to_its_member(
        self, app, db, seed_user, category,
    ):
        """Every modelled category's cached id classifies back to its member.

        Parameterised over the enum itself rather than over a hand-written
        list, so a category added to :class:`~app.enums.AcctCategoryEnum`
        cannot ship with no coverage.
        """
        with app.app_context():
            assert account_category.account_category(
                _account_in(category),
            ) is category

    def test_no_account_type_has_no_category(self, app, db, seed_user):
        """A ``None`` account_type classifies as no modelled category.

        The transient / partially-built object: ``accounts.account_type_id``
        is ``NOT NULL`` and the relationship is ``lazy="joined"``, so a
        PERSISTED account never reaches this -- but the classifier must not
        raise on ``None.category_id``.
        """
        with app.app_context():
            assert account_category.account_category(
                SimpleNamespace(account_type=None),
            ) is None

    def test_an_unmodelled_category_id_has_no_category(
        self, app, db, seed_user,
    ):
        """A ``category_id`` outside the four members classifies as ``None``.

        Reachable only with a FIFTH ``ref.account_type_categories`` row
        inserted outside the application -- ``ref_cache.init`` requires the
        four named rows and does not forbid others.  The answer must be
        ``None`` (bucketed ``"other"``, not a liability), never a crash and
        never a silent match on the first member.
        """
        with app.app_context():
            unmodelled = max(
                ref_cache.acct_category_id(member)
                for member in AcctCategoryEnum
            ) + 1
            account = SimpleNamespace(
                account_type=SimpleNamespace(category_id=unmodelled),
            )
            assert account_category.account_category(account) is None
            assert account_category.is_liability_account(account) is False
            assert category_key(account_category.account_category(account)) == _OTHER_KEY


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
            assert account_category.is_liability_account(account) is False

    def test_seed_checking_is_asset(self, app, db, seed_user):
        """The seed Checking account (Asset category) classifies as False."""
        with app.app_context():
            assert account_category.is_liability_account(
                seed_user["account"],
            ) is False

    @pytest.mark.parametrize("category", list(AcctCategoryEnum))
    def test_exactly_the_liability_category_answers_true(
        self, app, db, seed_user, category,
    ):
        """Only the LIABILITY category is a liability, and it always is.

        The value assertion the derivation could get wrong in either
        direction: an inverted test would make every asset a liability, and
        an ``is not`` slip would make the mortgage an asset.
        """
        with app.app_context():
            assert account_category.is_liability_account(
                _account_in(category),
            ) is (category is AcctCategoryEnum.LIABILITY)


class TestTheTwoSpellingsAreOneAnswer:
    """Finding N-118's construction property, asserted rather than trusted.

    The liability rule had TWO independent id comparisons -- this module's and
    ``_display.account_category_key(...) == "liability"`` (both since
    DELETED) -- which agreed on
    every account on both databases and were held together by nothing.  The
    Horizon is where that cost the most: its three band producers partition the
    account set with BOTH spellings, so a divergence counts an account twice
    with opposite signs or not at all, silently.

    Plan step X-z made them one answer (ruling R-CP).  These pin the two facts
    the equivalence rests on, so a future edit that re-introduces a second rule
    fails here rather than on a chart.
    """

    def test_the_display_mapping_is_injective_and_misses_the_other_key(self):
        """No two categories share a key, and none of them is ``"other"``.

        The whole equivalence is: the key resolver is a LOOKUP of the
        classifier's answer, so it agrees with any other derivation of that
        answer exactly when the lookup table is one-to-one.  Two categories
        sharing a key would put two groups' money in one band; a category
        mapped to :data:`_OTHER_KEY` would make the fall-through
        indistinguishable from a real category.
        """
        keys = list(_CATEGORY_KEYS.values())
        assert len(set(keys)) == len(keys)
        assert _OTHER_KEY not in keys
        assert set(_CATEGORY_KEYS) == set(AcctCategoryEnum)
        assert _CATEGORY_KEYS[AcctCategoryEnum.LIABILITY] == LIABILITY_KEY

    @pytest.mark.parametrize("category", list(AcctCategoryEnum))
    def test_the_key_and_the_predicate_agree_for_every_category(
        self, app, db, seed_user, category,
    ):
        """``key == LIABILITY_KEY`` iff ``is_liability_account`` -- all four.

        The property the Horizon's partition depends on, exercised over every
        category rather than over whichever ones a fixture happens to hold
        (finding N-69: a test whose fixture has no data cannot distinguish two
        producers).
        """
        with app.app_context():
            account = _account_in(category)
            assert (
                (category_key(account_category.account_category(account))
                 == LIABILITY_KEY)
                is account_category.is_liability_account(account)
            )

    def test_they_agree_for_an_account_with_no_category(
        self, app, db, seed_user,
    ):
        """The no-category state agrees too: ``"other"``, and not a liability.

        The arm a partition would drop an account through: ``"other"`` is a
        real band with a card and a chart colour, so the balance is rendered
        as an asset rather than vanishing.
        """
        with app.app_context():
            account = SimpleNamespace(account_type=None)
            assert category_key(account_category.account_category(account)) == _OTHER_KEY
            assert category_key(
                account_category.account_category(account),
            ) != LIABILITY_KEY
            assert account_category.is_liability_account(account) is False
