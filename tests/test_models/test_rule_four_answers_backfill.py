"""The backfill in migration ``e6b2c07d3f19``, run as the migration runs it.

Plan step ``bank_import:X-gd-2``, ruling **R-GS**.  **It is the money arm of
that revision and nothing else grades it.**
``scripts/build_test_template.py`` runs ``upgrade head`` against an EMPTY
database, so the statement executes over zero rows and proves nothing; the
downgrade is never executed at all.

**What it protects, stated as the failure it prevents.**  Before the revision, a
rule with all three container columns NULL MEANS *never a purchase* -- the
answer that stops a bank line ever becoming a purchase, and the answer worth
`-$7,412.94` on the developer's own statement.  After it, that same shape means
*ask me every time* unless the flag says otherwise.  A revision that added the
column and forgot the UPDATE would therefore lift every stored bar silently, on
a table whose whole subject is which money may be recorded twice.  Nothing
would raise: the row stays legal, the screen still renders it, and the only
visible difference is a select that reads *ask me every time* on the merchant
the owner told it never to touch.

**The downgrade is the same hazard pointed the other way.**  Dropping the flag
would republish every *ask me every time* row as *never a purchase* under the
older CHECK, INVENTING a bar the owner never set, so the revision deletes those
rows first -- and the older schema's own word for "no standing answer" is the
absence of a row, which is what a withdrawal wrote there.

**It executes the migration's own strings**, imported from the module, which is
the convention ``efffcf647644``'s ``BACKFILL_SQL`` established here: a test that
re-typed the predicate would agree with a mistake as readily as with the truth.

**The fixture reproduces the PRE-migration shape** -- the flag defaulted back
onto the column and the older three-shape CHECK in place -- because the test
database is already at head.  It is the same construction
``test_merchant_promotion_backfill.py`` uses beside it, and it restores the
schema on teardown.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture pattern,
# and ``unused-argument`` is unavoidable for a fixture requested for its side
# effect -- ``pre_migration_shape`` puts the schema back the way the migration
# found it and the test bodies do not reference what it yields.
# pylint: disable=unused-argument
from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import text

from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.services.statement_match import RuleAnswer
from tests.test_services.test_statement_match._builders import (
    a_transaction,
)


_MIGRATIONS = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load(filename: str):
    """Load an Alembic revision as a module, the way alembic itself does.

    ``migrations/versions`` has no ``__init__.py``, so a plain import cannot
    reach it.

    Args:
        filename: The revision file's name.

    Returns:
        The loaded module.
    """
    path = _MIGRATIONS / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REVISION = _load("e6b2c07d3f19_a_rule_has_four_answers.py")


@pytest.fixture
def pre_migration_shape(db):
    """Put the table back into the shape this revision found it in.

    Gives ``never_a_purchase`` the transient default the revision adds it with
    -- so a row staged by the ORM without stating it lands ``FALSE``, which is
    what every pre-revision row looks like the instant the column appears --
    and restores the three-shape CHECK the revision replaces.

    Args:
        db: The test database session.

    Yields:
        ``None`` -- it is requested for its side effect.
    """
    db.session.execute(text(
        "ALTER TABLE budget.merchant_rules "
        "ALTER COLUMN never_a_purchase SET DEFAULT FALSE"
    ))
    db.session.execute(text(
        "ALTER TABLE budget.merchant_rules "
        "DROP CONSTRAINT ck_merchant_rules_one_answer"
    ))
    db.session.execute(text(
        "ALTER TABLE budget.merchant_rules "
        f"ADD CONSTRAINT ck_merchant_rules_one_answer "
        f"CHECK ({_REVISION._THREE_ANSWERS})"  # pylint: disable=protected-access
    ))
    yield
    db.session.rollback()


def _a_rule(db, seed_user, merchant_name, **columns):
    """Stage one rule row through raw SQL, in the PRE-migration shape.

    Raw rather than through the ORM because the model states
    ``never_a_purchase`` on every insert -- it is NOT NULL with no default at
    head -- and the whole subject here is a row that does NOT state it.

    Args:
        db: The test database session.
        seed_user: The seeded user bundle.
        merchant_name: What the merchant is called.
        **columns: The container columns this answer names, if any.

    Returns:
        The merchant's row id.
    """
    merchant = Merchant(
        account_id=seed_user["account"].id, name=merchant_name,
    )
    db.session.add(merchant)
    db.session.flush()
    named = ", ".join(columns)
    values = ", ".join(f":{key}" for key in columns)
    db.session.execute(
        text(
            "INSERT INTO budget.merchant_rules "
            f"(account_id, user_id, merchant_id, created_at, updated_at"
            f"{', ' + named if named else ''}) "
            "VALUES (:account_id, :user_id, :merchant_id, now(), now()"
            f"{', ' + values if values else ''})"
        ),
        {
            "account_id": seed_user["account"].id,
            "user_id": seed_user["user"].id,
            "merchant_id": merchant.id,
            **columns,
        },
    )
    return merchant.id


class TestTheUpgradeKeepsEveryBar:
    """A container-less rule MEANT never a purchase, and still does."""

    def test_a_container_less_rule_is_claimed_as_NEVER(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """THE arm that loses money silently if it is skipped.

        Measured on a clone of the developer's own database, 2026-08-26: of 29
        stored rules exactly ONE is container-less, and it is
        ``Capital One Credit Card``, whose 9 unexplained in-calendar outflows
        come to `-$7,412.94`.  (Ruling **R-GA**'s "9 of the 91... of the
        `-$11,336.36`" is an OLDER measurement, taken against a 2026-08-19
        production clone; the denominators moved when this arc recorded 221
        matches, and quoting them under today's date was a claim nobody
        re-took.  Found by adversarial review 2026-08-26.)  Without this UPDATE
        that row reads back as *ask me every time*, its
        :class:`~app.services.statement_match._bars.CreationBar` lifts, and the
        screen offers to record those lines as purchases beside the payback
        rows that already hold them.
        """
        merchant_id = _a_rule(db, seed_user, "Capital One Credit Card")
        db.session.flush()

        db.session.execute(text(_REVISION.CLAIM_NEVER_SQL))
        db.session.expire_all()

        row = db.session.query(MerchantRule).filter(
            MerchantRule.merchant_id == merchant_id,
        ).one()
        assert row.never_a_purchase is True
        assert RuleAnswer.of(row) is RuleAnswer.NEVER

    def test_a_rule_naming_a_CONTAINER_keeps_the_flag_FALSE(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """The firing control: the predicate narrows, it does not sweep.

        Without it the case above would be satisfied by an UPDATE with no WHERE
        clause -- which would claim all 29 of the developer's rules are *never
        a purchase*.

        **It asserts the FLAG, and an earlier version of this case asserted the
        ANSWER, which made it a tautology.** ``RuleAnswer.of`` reads the
        container columns FIRST and never looks at the flag when one is set, so
        a template row swept by a WHERE-less UPDATE still reads back
        ``TEMPLATE`` -- and every one of the predicate's three terms could be
        deleted with the case still green. Found by adversarial review
        2026-08-26. The flag is the only thing the statement writes, so the
        flag is what has to be read.

        **Three terms, one case each**, because the predicate is a conjunction
        and any one of them dropped is a different sweep: dropping
        ``template_id IS NULL`` sweeps the template answer, dropping
        ``envelope_name IS NULL`` or ``category_id IS NULL`` sweeps the
        new-envelope one.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        by_template = _a_rule(
            db, seed_user, "Amazon", template_id=envelope.template_id,
        )
        by_name = _a_rule(
            db, seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=seed_user["categories"]["Groceries"].id,
        )
        db.session.flush()

        db.session.execute(text(_REVISION.CLAIM_NEVER_SQL))
        db.session.expire_all()

        flags = {
            row.merchant_id: row.never_a_purchase
            for row in db.session.query(MerchantRule).all()
        }
        assert flags[by_template] is False
        assert flags[by_name] is False
        # ...and the answers they read back as, which is what the flag being
        # false is FOR.
        kept = {
            row.merchant_id: RuleAnswer.of(row)
            for row in db.session.query(MerchantRule).all()
        }
        assert kept[by_template] is RuleAnswer.TEMPLATE
        assert kept[by_name] is RuleAnswer.NEW_ENVELOPE


class TestTheDowngradeInventsNoBar:
    """The answer the older schema cannot hold LEAVES rather than changing."""

    def test_an_ALWAYS_ASK_rule_is_forgotten(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """Keeping it would republish it as a bar the owner never set.

        The older schema reads a container-less row as *never a purchase* full
        stop, so a surviving *ask me every time* row would come back meaning
        the one thing this arc measured as costing real money -- in the
        direction that INVENTS a decision rather than losing one.
        """
        merchant_id = _a_rule(
            db, seed_user, "Public Library", never_a_purchase=False,
        )
        db.session.flush()

        db.session.execute(text(_REVISION.FORGET_ALWAYS_ASK_SQL))
        db.session.expire_all()

        assert db.session.query(MerchantRule).filter(
            MerchantRule.merchant_id == merchant_id,
        ).count() == 0

    def test_a_NEVER_rule_SURVIVES_the_downgrade(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """The first firing control: the delete is scoped by the FLAG.

        Without the ``never_a_purchase IS FALSE`` term the case above would be
        satisfied by a DELETE that swept every container-less rule, which would
        take the bars with it -- the same loss the upgrade's backfill exists to
        prevent, arriving on the way back.
        """
        merchant_id = _a_rule(
            db, seed_user, "Capital One Credit Card", never_a_purchase=True,
        )
        db.session.flush()

        db.session.execute(text(_REVISION.FORGET_ALWAYS_ASK_SQL))
        db.session.expire_all()

        assert db.session.query(MerchantRule).filter(
            MerchantRule.merchant_id == merchant_id,
        ).one().never_a_purchase is True

    def test_a_rule_naming_a_CONTAINER_survives_the_downgrade(
        self, app, db, seed_user, pre_migration_shape,
    ):
        """The second firing control, and the DESTRUCTIVE statement's own.

        **The upgrade had a container control and the downgrade had none**,
        which is the asymmetry an adversarial review measured on 2026-08-26:
        drop the three container terms from ``FORGET_ALWAYS_ASK_SQL`` and it
        becomes ``DELETE ... WHERE never_a_purchase IS FALSE``, which destroys
        every TEMPLATE and NEW ENVELOPE rule on the account -- 28 of the
        developer's 29 -- while both of the cases beside it stay green, because
        both stage container-LESS rows only.

        The upgrade's worst case writes a wrong flag; this one deletes the row.
        The more destructive direction had the weaker grading.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", is_envelope=True,
        )
        by_template = _a_rule(
            db, seed_user, "Amazon", template_id=envelope.template_id,
            never_a_purchase=False,
        )
        by_name = _a_rule(
            db, seed_user, "Lowe's", envelope_name="Home Improvement",
            category_id=seed_user["categories"]["Groceries"].id,
            never_a_purchase=False,
        )
        db.session.flush()

        db.session.execute(text(_REVISION.FORGET_ALWAYS_ASK_SQL))
        db.session.expire_all()

        survived = {
            row.merchant_id for row in db.session.query(MerchantRule).all()
        }
        assert by_template in survived
        assert by_name in survived
