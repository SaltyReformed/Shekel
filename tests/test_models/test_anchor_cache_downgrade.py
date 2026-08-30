"""
Shekel Budget App -- Migration ``c81f0a5b3e27``'s downgrade (plan step X-f1c3c)

The migration drops ``accounts.current_anchor_balance`` /
``current_anchor_period_id``, and its downgrade restores them by DERIVING each
account's current balance assertion.  That derivation is the part worth
grading: the columns come back from a computation, not from a saved copy, so a
downgrade whose ordering disagrees with ``cash_ledger.resolve_anchor`` restores
a DIFFERENT balance than the one the app was showing, silently and for every
account at once.

**Executed, not read.**  The repo's other migration-direction suites settle for
a source-level check because the DDL they would run needs an ACCESS EXCLUSIVE
lock that conflicts with the xdist workers.  That constraint applies to this
migration's ``ALTER TABLE`` statements, and NOT to the SELECT its backfill is
built on -- so ``_CURRENT_ASSERTION_SQL`` is a standalone statement and this
suite runs it against real rows.  A source-level check cannot see an ordering
that has drifted; this one fails on it.

The DDL half (the columns, the CHECK, the deferrable FK, the fail-loud gate)
keeps the execution-anchored source check the standard prescribes, so an edit
that deleted a statement from ``downgrade()`` while keeping its constant fails
here too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.services import account_service, cash_ledger
from app.utils.dates import display_today
from tests._test_helpers import load_migration_module
from tests.conftest import SEED_USER_BOOTSTRAP_START

_MIGRATION_FILENAME = "c81f0a5b3e27_the_anchor_balance_has_one_home.py"


def _migration():
    """Return the loaded migration module."""
    return load_migration_module(_MIGRATION_FILENAME)


def _resolved_periods():
    """Run the downgrade's own period-resolution SELECT; return it by account.

    Returns:
        ``{account_id: pay_period_id}`` as the backfill would write it.
    """
    rows = db.session.execute(text(_migration()._RESOLVED_PERIOD_SQL)).all()
    return {r.account_id: r.pay_period_id for r in rows}


def _current_assertions():
    """Run the downgrade's own current-assertion SELECT; return it by account.

    Returns:
        ``{account_id: (anchor_balance, observed_on)}`` as the downgrade's
        backfill would read it.
    """
    rows = db.session.execute(text(_migration()._CURRENT_ASSERTION_SQL)).all()
    return {r.account_id: (r.anchor_balance, r.observed_on) for r in rows}


class TestTheDowngradeResolvesTheSameAssertionTheAppDoes:
    """The restored balance equals what ``resolve_anchor`` was showing."""

    def test_it_agrees_with_the_resolver_on_every_account(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Every account's derived balance equals its resolved assertion.

        The seeded account carries its origination assertion; a second account
        is added so the ``DISTINCT ON (account_id)`` partitioning is graded
        against more than one group.
        """
        assert seed_periods_today
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            second = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Downgrade Probe",
                    anchor_balance=Decimal("3210.00"),
                ),
            )
            db.session.flush()

            # A SECOND assertion on one account, so ``DISTINCT ON`` has an
            # ordering to apply rather than one candidate per group -- without
            # it every ordering agrees and this test grades only the mapping.
            db.session.add(AccountAnchorHistory(
                account_id=second.id, anchor_balance=Decimal("4321.00"),
                observed_on=display_today(),
            ))
            db.session.flush()

            derived = _current_assertions()
            accounts = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id,
            ).all()
            assert len(accounts) >= 2
            for account in accounts:
                resolved = cash_ledger.resolve_anchor(account)
                assert account.id in derived, (
                    f"account {account.id} has no assertion for the downgrade "
                    f"to restore from"
                )
                balance, observed_on = derived[account.id]
                assert balance == resolved.balance
                assert observed_on == resolved.observed_on
            assert derived[second.id][0] == Decimal("4321.00"), (
                "the LATER assertion must win, not the origination one"
            )

    def test_the_ordering_picks_the_last_assertion_of_the_latest_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Three assertions across two days: every tie-breaker is exercised.

        The ordering is ``observed_on DESC, created_at DESC, id DESC``, and each
        of the three has to be able to decide the winner on its own or a drifted
        ordering ships undetected.

        **``created_at`` must be stamped EXPLICITLY, and a first version of this
        test did not.**  ``CreatedAtMixin`` uses
        ``server_default=db.func.now()``, which in PostgreSQL is
        ``transaction_timestamp()`` -- constant for the whole test transaction.
        Three rows inserted across three flushes therefore shared ONE
        ``created_at``, so only ``id DESC`` ever decided anything, and mutants
        that flipped ``created_at`` to ``ASC`` or deleted it outright both
        passed.  A neutral adversarial review proved that by planting them.

        The shape below is the only one that separates the two tie-breakers:
        within the latest day, the winner has the LATER ``created_at`` and the
        LOWER ``id``.  So an ordering that fell back to ``id DESC`` picks the
        loser, and one that flipped ``created_at`` to ``ASC`` picks the loser
        too.
        """
        assert seed_periods_today
        with app.app_context():
            account = seed_user["account"]
            today = display_today()
            yesterday = today - timedelta(days=1)
            noon = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)

            # Inserted in an order that makes every simpler rule wrong: the
            # WINNER is inserted FIRST (lowest id) and stamped LATEST.
            winner = AccountAnchorHistory(
                account_id=account.id, anchor_balance=Decimal("100.00"),
                observed_on=today, created_at=noon + timedelta(hours=2),
            )
            db.session.add(winner)
            db.session.flush()
            db.session.add(AccountAnchorHistory(
                account_id=account.id, anchor_balance=Decimal("900.00"),
                observed_on=today, created_at=noon,
            ))
            db.session.add(AccountAnchorHistory(
                account_id=account.id, anchor_balance=Decimal("5000.00"),
                observed_on=yesterday, created_at=noon + timedelta(hours=9),
            ))
            db.session.flush()

            balance, observed_on = _current_assertions()[account.id]
            # The later DAY beats a later recording instant; within that day the
            # later INSTANT beats both a larger balance and a larger id.
            assert observed_on == today
            assert balance == Decimal("100.00")
            assert winner.id < min(
                row.id
                for row in db.session.query(AccountAnchorHistory).filter_by(
                    account_id=account.id, observed_on=today,
                )
                if row.id != winner.id
            ), "the winner must carry the LOWEST id of its day, or id DESC alone would pick it"
            # And the app agrees -- which is the whole point: the downgrade must
            # restore what the app was showing.
            db.session.expire_all()
            resolved = cash_ledger.resolve_anchor(
                db.session.get(Account, account.id),
            )
            assert resolved.balance == balance
            assert resolved.observed_on == observed_on


class TestTheDowngradeResolvesTheOwnersOwnPeriod:
    """The period half, and the owner scoping that keeps it from crossing users."""

    def test_a_foreign_period_containing_the_day_is_not_selectable(
        self, app, db, seed_user, seed_second_user, seed_periods_today,
    ):
        """A foreign period that CONTAINS the day loses to the owner's fallback.

        ``p.user_id = a.user_id`` appears on BOTH subqueries of the period
        resolution, and the migration docstring calls it out ("a period
        belonging to someone else must never be selectable here").  Nothing
        graded it until now: the whole period derivation had no executing
        reader, so dropping the scoping would have anchored an account to
        another user's pay period on every downgrade, silently.

        **The adversarial case is CONSTRUCTED, not hoped for, and a first
        version of this test only hoped.**  It asserted that every account
        resolved to a period of its own owner and passed unchanged with both
        scoping clauses replaced by ``WHERE TRUE``, because the two seeded
        calendars gave the unscoped query no wrong answer to prefer.

        The shape here makes the wrong answer strictly better than the right one
        for an unscoped query: an account whose assertion day falls OUTSIDE its
        owner's whole calendar, so the owner can only reach the earliest-period
        FALLBACK -- against a foreign period that genuinely CONTAINS that day,
        which the first subquery prefers.  Unscoped, the decoy wins outright.
        """
        assert seed_periods_today
        with app.app_context():
            # **The stray account is the SEEDED one, and its off-calendar day
            # is a fact of the fixture rather than one edited in** (plan step
            # X-f3c-2c).  ``budget.account_anchor_history`` is append-only, so
            # this case used to build its shape by creating an account and then
            # moving its only assertion -- an act no door has.  It does not
            # need to: ``build_seed_user`` opens the Checking account on the
            # owner's bootstrap day and ``seed_periods_today`` then builds a
            # calendar around today, so the seeded account already asserts on a
            # day no period of its owner covers.  ``create_account`` could not
            # reproduce it anyway: ``resolve_observation_day`` floors an
            # assertion at the calendar's own first day.
            stray = db.session.get(Account, seed_user["account"].id)
            off_calendar = SEED_USER_BOOTSTRAP_START

            owner_periods = db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).all()
            assert not any(
                p.start_date <= off_calendar <= p.end_date
                for p in owner_periods
            ), "the owner must have NO period containing the day"
            owner_earliest = min(owner_periods, key=lambda p: p.period_index)

            # ``UNIQUE(user_id, period_index)`` -- the second user already has
            # a bootstrap period at index 0, so the decoy takes the next free
            # index.  Its INDEX does not matter here: it wins on being a
            # CONTAINING period, which the first subquery prefers over any
            # fallback whatever the ordering.
            #
            # **It is no longer the ONLY foreign containing period** (plan step
            # X-f3c-2c).  ``off_calendar`` is the seeded bootstrap day now, and
            # the second user's own bootstrap period spans it -- so an unscoped
            # query would pick one of two foreign periods rather than this one.
            # The control still fires either way: what it grades is that the
            # OWNER's earliest is chosen over any foreign period at all, and
            # the owner-has-no-containing-period precondition above is what
            # makes that the only legal answer.
            next_index = 1 + max(
                (p.period_index for p in db.session.query(PayPeriod).filter_by(
                    user_id=seed_second_user["user"].id,
                )),
                default=-1,
            )
            decoy = PayPeriod(
                user_id=seed_second_user["user"].id,
                start_date=off_calendar - timedelta(days=1),
                end_date=off_calendar + timedelta(days=1),
                period_index=next_index,
            )
            db.session.add(decoy)
            db.session.flush()

            # The preconditions, asserted: the decoy genuinely contains the day
            # (so an unscoped containing-subquery prefers it over any fallback)
            # and belongs to someone else.
            assert decoy.start_date <= off_calendar <= decoy.end_date
            assert decoy.user_id != stray.user_id

            resolved = _resolved_periods()
            assert resolved[stray.id] == owner_earliest.id, (
                f"account {stray.id} (owner {stray.user_id}) resolved period "
                f"{resolved[stray.id]}; its owner's earliest is "
                f"{owner_earliest.id} and the foreign decoy is {decoy.id}"
            )

    def test_every_account_lands_in_its_own_owners_period(
        self, app, db, seed_user, seed_second_user, seed_periods_today,
    ):
        """No account, on either calendar, resolves a period it does not own.

        The broad complement to the constructed case above: it cannot prove the
        scoping by itself (see that test's docstring) but it does catch a
        resolution that returns NULL, which would leave the downgrade unable to
        satisfy its NOT NULL column.
        """
        assert seed_periods_today
        with app.app_context():
            resolved = _resolved_periods()
            owners = {
                account.id: account.user_id
                for account in db.session.query(Account).all()
            }
            periods = {
                period.id: period.user_id
                for period in db.session.query(PayPeriod).all()
            }
            assert resolved, "no account resolved a period at all"
            for account_id, period_id in resolved.items():
                assert period_id is not None, (
                    f"account {account_id} resolved no period; the downgrade "
                    f"would leave a NOT NULL column NULL"
                )
                assert periods[period_id] == owners[account_id], (
                    f"account {account_id} (owner {owners[account_id]}) "
                    f"resolved period {period_id}, which belongs to owner "
                    f"{periods[period_id]}"
                )


class TestTheDowngradeBodyStillRunsEveryStatement:
    """Execution-anchored source check for the DDL half.

    The ``ALTER TABLE`` statements cannot run in an xdist worker, so this is
    the layer the repo's other migration suites use: it fails if a future edit
    keeps a constant but stops executing it, which the value-level checks alone
    would not catch.
    """

    def test_downgrade_restores_both_columns_and_their_constraints(self):
        """``downgrade()`` adds both columns, both constraints and the gate."""
        source = _migration_source()
        start = source.find("def downgrade")
        # ``str.find`` returns -1 on absence, and ``source[-1:]`` is a truthy
        # one-character string -- so a bare ``assert body`` never fired.
        assert start != -1, "the migration has no downgrade()"
        body = source[start:]

        # Anchored on ``sa.Column("<name>"``, which appears only inside an
        # ``add_column`` call -- a bare ``"<name>" in body`` would be satisfied
        # by the backfill SQL that merely WRITES the column and would pass
        # against a downgrade that never added it.
        for column in ("current_anchor_balance", "current_anchor_period_id"):
            assert f'sa.Column("{column}"' in body, (
                f"downgrade() never adds the {column} column back"
            )
        assert "connection.execute(_DOWNGRADE_BALANCE)" in body, (
            "downgrade() never executes the balance backfill"
        )
        assert "connection.execute(_DOWNGRADE_PERIOD)" in body, (
            "downgrade() never executes the period backfill"
        )
        assert "connection.execute(_UNRESOLVED)" in body, (
            "downgrade() never runs the fail-loud gate, so it would install "
            "NOT NULL constraints it cannot satisfy"
        )
        assert "raise RuntimeError(" in body, (
            "the unresolved gate is read but never raises"
        )
        assert "create_check_constraint(" in body, (
            "downgrade() never restores ck_accounts_anchor_balance_present"
        )
        assert "create_foreign_key(" in body, (
            "downgrade() never restores the anchor period FK"
        )
        assert "deferrable=True" in body, (
            "the restored FK must be DEFERRABLE -- migration d410f6b9caa3 "
            "made it so and the pay-period reset depended on it"
        )
        # **The ARGUMENTS, not just the call names.**  A neutral review mutated
        # downgrade() to leave both columns nullable, point the FK at
        # ``accounts`` with ``ondelete="CASCADE"``, and replace ``if
        # unresolved:`` with ``if False:`` -- all four at once -- and this class
        # still passed.  Each of those is asserted now.
        assert "nullable=False" in body, (
            "downgrade() never re-applies NOT NULL, which is the whole reason "
            "the unresolved gate exists"
        )
        assert '"pay_periods",' in body, (
            "the restored FK does not reference budget.pay_periods"
        )
        assert 'ondelete="NO ACTION"' in body or "ondelete='NO ACTION'" in body, (
            "the restored FK must be NO ACTION -- only NO ACTION can be "
            "deferred, which is why it was NO ACTION and not RESTRICT"
        )
        assert "if unresolved:" in body, (
            "the unresolved gate is computed but never branched on"
        )

    def test_both_backfills_build_on_the_one_assertion_select(self):
        """Neither UPDATE spells the current-assertion subquery for itself.

        Two copies of that ``DISTINCT ON`` could drift, and a downgrade would
        then restore the balance from one assertion and the period from
        another -- the exact two-columns-one-fact shape this whole step
        deletes.  Both must interpolate ``_CURRENT_ASSERTION_SQL``.
        """
        source = _migration_source()
        # The balance UPDATE interpolates the assertion SELECT directly; the
        # period UPDATE reaches it through ``_RESOLVED_PERIOD_SQL``, which is
        # itself a standalone SELECT so the owner scoping can be executed.
        # Follow the chain rather than expecting one shape.
        chain = {
            "_DOWNGRADE_BALANCE": "{_CURRENT_ASSERTION_SQL}",
            "_DOWNGRADE_PERIOD": "{_RESOLVED_PERIOD_SQL}",
            "_RESOLVED_PERIOD_SQL": "{_CURRENT_ASSERTION_SQL}",
        }
        for constant, expected in chain.items():
            start = source.find(f"{constant} = ")
            assert start != -1, f"{constant} is gone"
            statement = source[start:source.find('"""', source.find('"""', start) + 3)]
            assert expected in statement, (
                f"{constant} does not build on {expected}"
            )
            assert "DISTINCT ON" not in statement, (
                f"{constant} spells its own current-assertion subquery"
            )
        # And the ONE definition really is one, so no fourth copy can appear
        # unnoticed.  Anchored on ``SELECT DISTINCT ON``, which occurs only in
        # SQL: a bare ``DISTINCT ON`` also matches the COMMENT above the
        # constant, and a first version of this assertion counted that and
        # failed -- the same text-versus-code confusion a claims audit had just
        # caught in a census elsewhere in this step.
        assert source.count("SELECT DISTINCT ON") == 1, (
            f"expected exactly one current-assertion SELECT in the migration; "
            f"found {source.count('SELECT DISTINCT ON')}"
        )


def _migration_source():
    """Return the migration file's text.

    Read from the module's own ``__file__`` rather than from a path rebuilt
    here, so a rename cannot leave this suite reading a file that no longer
    exists while still passing.
    """
    with open(_migration().__file__, encoding="utf-8") as handle:
        return handle.read()
