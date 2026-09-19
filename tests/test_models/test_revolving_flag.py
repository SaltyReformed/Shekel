"""
Shekel Budget App -- ``ref.account_types.has_revolving_credit`` (plan step credit_card:CC-1)

The card is a FLAG on its type, not a projection kind (design
``docs/design/credit_card_from_scratch.md`` 3.1, ruling ``credit_card:R-CC14``).
These tests pin the schema half of that leaf:

* the seed carries the flag on exactly ONE built-in, ``Credit Card``;
* ``ck_account_types_revolving_is_plain`` refuses a revolving type that also
  carries any engine flag, so the "both flags" type the 2026-07-19 plan
  resolved by precedence is unrepresentable -- graded per flag, with the
  control that a revolving type carrying NONE of them is admitted (a CHECK
  that refused everything would pass the four refusal cases too);
* the CHECK's text on the model equals the migration's, by hand, because
  Alembic autogenerate does not diff a CHECK on an existing table and the
  migration's own docstring names this module as what keeps them equal;
* the flag is seed-only: a type born through the custom-type door carries
  ``false`` however the form is filled, exactly as ``has_appreciation`` does.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.ref import AccountType
from app.ref_seeds import seed_reference_data
from tests._test_helpers import load_migration_module

MIGRATION = "a16516c8ec05_the_card_is_a_flag_on_its_type.py"
CHECK_NAME = "ck_account_types_revolving_is_plain"
# The four engine flags the CHECK forbids beside ``has_revolving_credit`` --
# every flag ``classify_account`` dispatches on.
ENGINE_FLAGS = (
    "has_amortization", "has_interest", "has_appreciation", "has_parameters",
)


def _seeded_types():
    """Return every built-in (``user_id IS NULL``) account type by name."""
    rows = db.session.query(AccountType).filter(
        AccountType.user_id.is_(None),
    ).all()
    return {row.name: row for row in rows}


class TestTheSeedFlagsOneType:
    """Exactly one built-in is revolving, and it carries no engine flag."""

    def test_credit_card_is_the_one_revolving_seed(self, app, db):
        """``Credit Card`` carries the flag; the other 18 built-ins do not.

        Both halves matter: the predicate ``is_revolving`` gates every card
        feature, so a second flagged type would admit an account the design
        never named, and an unflagged Credit Card would make the whole arc
        unreachable.
        """
        with app.app_context():
            seeded = _seeded_types()
            assert len(seeded) == 19
            revolving = sorted(
                name for name, row in seeded.items() if row.has_revolving_credit
            )
            assert revolving == ["Credit Card"]

    def test_the_revolving_seed_is_plain(self, app, db):
        """The Credit Card row carries none of the four engine flags.

        The CHECK below guarantees this on INSERT; this pins that the seed
        list satisfies it -- a seed edit that flagged the card ``has_interest``
        would fail the migration's ``create_check_constraint`` on a populated
        database, and this is the cheaper place to learn that.
        """
        with app.app_context():
            card = _seeded_types()["Credit Card"]
            assert card.has_revolving_credit is True
            assert all(getattr(card, flag) is False for flag in ENGINE_FLAGS)


class TestTheCheckMakesTheBothFlagsTypeUnrepresentable:
    """``ck_account_types_revolving_is_plain`` refuses a revolving engine type."""

    @pytest.mark.parametrize("engine_flag", ENGINE_FLAGS)
    def test_a_revolving_type_with_an_engine_flag_is_refused(
        self, app, db, seed_user, engine_flag,
    ):
        """A revolving type carrying *engine_flag* fails the CHECK on INSERT.

        One case per flag, because the CHECK is a disjunction and a clause
        dropped from it would leave the other three cases green.  The refusal
        must NAME the constraint: a bare integrity error could be any of the
        table's other constraints.
        """
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            row = AccountType(
                # ``name`` is String(30); the flag names run to 16 chars.
                name=f"Revolving {engine_flag}",
                category_id=liability_id,
                user_id=seed_user["user"].id,
                has_revolving_credit=True,
                **{engine_flag: True},
            )
            db.session.add(row)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert CHECK_NAME in str(excinfo.value)

    def test_a_revolving_type_with_no_engine_flag_is_admitted(
        self, app, db, seed_user,
    ):
        """The control: a revolving type carrying none of the four flags is stored.

        Without this the four refusal cases above could be satisfied by a
        CHECK that refused every revolving row, which would also refuse the
        seed and no card could ever exist.
        """
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            row = AccountType(
                name="Plain revolving",
                category_id=liability_id,
                user_id=seed_user["user"].id,
                has_revolving_credit=True,
            )
            db.session.add(row)
            db.session.flush()
            stored = db.session.get(AccountType, row.id)
            assert stored.has_revolving_credit is True
            assert all(getattr(stored, flag) is False for flag in ENGINE_FLAGS)
            db.session.rollback()

    def test_the_model_and_the_migration_state_one_check(self, app):
        """The CHECK's SQL on the model equals the migration's, character for character.

        Autogenerate does not diff a CHECK constraint on an existing table, so
        nothing but this comparison keeps the two spellings equal; the
        migration's docstring names this test as that keeper.  Compared on the
        model's own rendering of the constraint the test-database was created
        WITH, not on prose.
        """
        with app.app_context():
            model_checks = {
                c.name: str(c.sqltext)
                for c in AccountType.__table__.constraints
                if c.name == CHECK_NAME
            }
            assert CHECK_NAME in model_checks, (
                f"the model carries no CHECK named {CHECK_NAME}; its table "
                f"constraints are {[c.name for c in AccountType.__table__.constraints]}"
            )
            migration = load_migration_module(MIGRATION)
            assert model_checks[CHECK_NAME] == migration._REVOLVING_IS_PLAIN_SQL  # pylint: disable=protected-access


class TestTheFlagIsSeedOnly:
    """No user door sets ``has_revolving_credit``."""

    def test_a_custom_type_born_through_the_door_is_not_revolving(
        self, app, auth_client, seed_user,
    ):
        """POST /accounts/types stores ``false`` even when the form claims otherwise.

        The schema exposes ``has_parameters`` / ``has_amortization`` /
        ``has_interest`` / ``is_pretax`` / ``is_liquid`` and nothing else, so a
        posted ``has_revolving_credit`` is unknown to it and never reaches the
        row -- the same property ``has_appreciation`` has.  Posted here so the
        test fails the day someone adds the field to the form without ruling
        it.
        """
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            response = auth_client.post(
                "/accounts/types",
                data={
                    "name": "Store card",
                    "category_id": liability_id,
                    "has_revolving_credit": "true",
                },
                follow_redirects=True,
            )
            assert response.status_code == 200
            assert b"created" in response.data
            row = db.session.query(AccountType).filter_by(
                name="Store card", user_id=seed_user["user"].id,
            ).one()
            assert row.has_revolving_credit is False

    def test_a_reseed_flags_the_built_in_and_leaves_a_users_namesake_alone(
        self, app, db, seed_user,
    ):
        """``seed_reference_data`` writes the flag onto the BUILT-IN row only.

        An owner may name a custom type after a seed (C-28: "HYSA" is the
        model docstring's own example), so two rows can be called "Credit
        Card".  Until plan step credit_card:CC-1 the seed found "the" row by
        name alone with no ORDER BY, so a reseed could land the seed's flags
        -- ``has_revolving_credit`` among them -- on the OWNER's row (the CC-1
        adversarial review's finding 2).  Pinned from both sides: the built-in
        is repaired (its icon is knocked off the seed value first, so the
        refresh arm is shown to reach it) and the owner's namesake keeps its
        own icon and stays non-revolving.

        Which row the old lookup returned depended on heap order, so the old
        defect cannot be forced deterministically here; what this pins is the
        outcome the scoped lookup guarantees on every ordering.
        """
        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            built_in = _seeded_types()["Credit Card"]
            built_in.icon_class = "bi-question"
            namesake = AccountType(
                name="Credit Card",
                category_id=liability_id,
                user_id=seed_user["user"].id,
                icon_class="bi-person",
            )
            db.session.add(namesake)
            db.session.flush()

            seed_reference_data(db.session)
            db.session.flush()

            db.session.refresh(built_in)
            db.session.refresh(namesake)
            assert built_in.has_revolving_credit is True
            assert built_in.icon_class == "bi-credit-card"
            assert namesake.has_revolving_credit is False
            assert namesake.icon_class == "bi-person"
            db.session.rollback()
