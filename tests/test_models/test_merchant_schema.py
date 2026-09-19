"""``budget.merchants``: the row a bank line names and a stated rule is about.

Plan step ``bank_import:X-gd-1``, ruling **R-GR**.  **The subject is a set of
guards that replaced a Python check**, so most of what follows is a firing
control: delete the constraint and a row the service used to refuse becomes
writable.

**What the promotion moved, and therefore what has to be graded here.**  A
merchant was a 100-character STRING kept twice -- on
``bank_statement_lines.merchant`` and on ``merchant_rules.merchant`` --
joined by equality, with ``statement_match._stating._refuse_unknown_merchants``
as the only thing between a crafted body and a stored answer keyed on a
merchant this account had never seen.  Three things carry that now, and each is
a case below: the identity key, the composite foreign keys, and the fact that a
merchant row OUTLIVES the lines that named it.
"""

from datetime import datetime, timezone

import pytest

from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.statement_import import BankStatementLine
from app.services.statement_import._merchants import resolve_merchants
from tests._test_helpers import capture_sql_statements
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_merchant,
    a_sighting,
    an_import,
)

# ``_merchants`` is the import package's own internal and has no importer
# outside it, so exporting it from ``statement_import.__init__`` would be the
# public surface ``CLAUDE.md`` rule 13 forbids.  Reaching into it from the
# module's own tests is the allowance every sibling here already takes.


class TestTheIdentity:
    """One row per name per account, and the account is half of it."""

    def test_the_SAME_name_twice_on_ONE_account_is_unwritable(
        self, app, db, seed_user,
    ):
        """``uq_merchants_account_name``, which is what makes resolving an upsert.

        Without it a second import would mint a second ``Food Lion`` beside the
        first, every rule keyed on one of them would stop reaching half that
        merchant's lines, and nothing would raise.
        """
        db.session.add(Merchant(account_id=seed_user["account"].id, name="X"))
        db.session.flush()
        db.session.add(Merchant(account_id=seed_user["account"].id, name="X"))

        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "uq_merchants_account_name" in str(caught.value)

    def test_the_same_name_on_ANOTHER_account_is_a_DIFFERENT_merchant(
        self, app, db, seed_user, seed_second_user,
    ):
        """The other side of the key, so the refusal above is not a blanket one.

        A statement is one bank's record of ONE account, and a rule is stated
        per account (**R-GA**) -- so ``Food Lion`` on Checking and ``Food Lion``
        on a card are two subjects with two answers.  Whether two SOURCES ever
        name one merchant is ``bank_import:X-f6b``'s question and opens when a
        second source exists.
        """
        mine = Merchant(account_id=seed_user["account"].id, name="Food Lion")
        theirs = Merchant(
            account_id=seed_second_user["account"].id, name="Food Lion",
        )
        db.session.add_all([mine, theirs])
        db.session.flush()

        assert mine.id != theirs.id

    def test_a_BLANK_name_is_unwritable(self, app, db, seed_user):
        """``ck_merchants_name_not_blank``, the ONE place the rule now lives.

        It was stated twice -- once on each table holding a copy of the string
        -- so the two could have drifted.  ``_secu_csv._stated_merchant``
        answers ``None`` for the same input, so the adapter and the table state
        one rule.
        """
        db.session.add(
            Merchant(account_id=seed_user["account"].id, name="   "),
        )

        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "ck_merchants_name_not_blank" in str(caught.value)


class TestASightingNamesAMerchantOfItsOwnAccount:
    """``fk_statement_line_sightings_merchant_account``, and the NULL beside it.

    The key a rule fires on is stored once, on the SIGHTING (plan step
    ``bank_import:X-f6b-1b``, ruling **R-BI16**); the composite that held the
    line's key to the line's account from ``bank_import:X-gd-1`` holds the
    sighting's to the sighting's now.
    """

    def test_ANOTHER_ACCOUNTS_merchant_is_unwritable_on_a_sighting(
        self, app, db, seed_user, seed_second_user,
    ):
        """Composite, for the reason the line key beside it is composite.

        A bare ``merchant_id`` foreign key is satisfied perfectly well by
        another account's merchant, and *is this merchant on this account* would
        then be a reader's check that can be forgotten -- on the fact a rule
        matches against.
        """
        theirs = Merchant(
            account_id=seed_second_user["account"].id, name="Theirs",
        )
        db.session.add(theirs)
        db.session.flush()
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-9.99")
        [sighting] = line.sightings
        sighting.merchant_id = theirs.id

        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "fk_statement_line_sightings_merchant_account" in str(
            caught.value,
        )

    def test_a_sighting_naming_NO_merchant_satisfies_the_key(
        self, app, db, seed_user,
    ):
        """``MATCH SIMPLE`` is what lets the composite sit on a nullable column.

        A NULL means *this source names none*, which is the direction a missing
        fact has to fail in, and it has to remain writable beside a key that
        also names ``account_id`` -- PostgreSQL's default match rule is what
        makes that so rather than a partial constraint somebody maintains.
        The line's read answers ``None`` for both halves.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-9.99", merchant=None)
        db.session.flush()

        assert line.merchant_id is None
        assert line.merchant_name is None


class TestALinesMerchantIsAReadOverItsSightings:
    """``BankStatementLine.merchant_id`` and ``merchant_name``: one SQL producer, sealed.

    Ruling **R-BI16** (plan step ``bank_import:X-f6b-1b``).  The line's
    merchant is what the EARLIEST surviving sighting that names one says, in
    SQL and on a loaded row alike, and nothing can put a key on a line.
    """

    def test_the_EARLIEST_naming_sighting_answers_not_the_latest_or_the_lowest_id(
        self, app, db, seed_user,
    ):
        """The three orders that could be confused, told apart in one case.

        The EARLIEST import by act order names ``Food Lion``; the LATEST
        names ``Walmart``; a sighting in between names none.  The later
        import's sighting is written FIRST, so "earliest act", "lowest
        sighting id" and "latest act" are three different rows.  FIRING
        CONTROL: flip ``_stated_by_the_naming_sighting``'s order to
        newest-first and the line reads ``Walmart``.
        """
        earliest = an_import(
            seed_user, created_at=datetime(2026, 3, 1, 12, tzinfo=timezone.utc),
        )
        between = an_import(
            seed_user, created_at=datetime(2026, 3, 2, 12, tzinfo=timezone.utc),
        )
        latest = an_import(
            seed_user, created_at=datetime(2026, 3, 3, 12, tzinfo=timezone.utc),
        )
        line = a_bank_line(seed_user, latest, amount="-9.99", merchant="Walmart")
        a_sighting(seed_user, between, line, merchant=None)
        a_sighting(seed_user, earliest, line, merchant="Food Lion")
        db.session.flush()
        db.session.expire_all()

        read = db.session.get(BankStatementLine, line.id)

        assert read.merchant_name == "Food Lion"
        assert read.merchant_id == a_merchant(seed_user, "Food Lion").id
        assert db.session.query(BankStatementLine.merchant_name).filter(
            BankStatementLine.id == line.id,
        ).scalar() == "Food Lion"

    def test_a_key_cannot_be_put_on_a_line(self, app, db, seed_user):
        """THE SEAL: the constructor kwarg and the assignment both refuse.

        A ``column_property`` accepts both silently (measured 2026-09-18 on
        SQLAlchemy 2.0: the value sits on the instance until the next load),
        which is the stale-cache shape the step deletes; the hybrid over it
        has no setter.  Delete the hybrid and expose the projection under the
        public name, and both statements below pass without error.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-9.99")
        merchant = a_merchant(seed_user, "Food Lion")

        with pytest.raises(AttributeError):
            line.merchant_id = merchant.id
        with pytest.raises(AttributeError):
            line.merchant_name = "Food Lion"
        with pytest.raises(AttributeError):
            BankStatementLine(
                account_id=line.account_id, posted_on=line.posted_on,
                amount=line.amount, sequence_in_group=1,
                merchant_id=merchant.id,
            )
        assert line.merchant_id is None

    def test_deleting_the_naming_import_moves_the_answer_to_the_survivor(
        self, app, db, seed_user,
    ):
        """Finding **BI-504**, closed at the root: nothing to repair.

        A line two imports showed under two words; the FIRST import goes.
        The line's merchant is the survivor's word, read, not a key the dead
        import minted -- the stored copy kept the dead import's key until
        this step.  The sightings go by cascade here, as the delete door's
        own statement takes them.
        """
        first = an_import(
            seed_user, created_at=datetime(2026, 3, 1, 12, tzinfo=timezone.utc),
        )
        second = an_import(
            seed_user, created_at=datetime(2026, 3, 2, 12, tzinfo=timezone.utc),
        )
        line = a_bank_line(seed_user, first, amount="-9.99", merchant="COFFEE")
        a_sighting(seed_user, second, line, merchant="Coffee Shop")
        db.session.flush()
        db.session.expire_all()
        assert db.session.get(BankStatementLine, line.id).merchant_name == "COFFEE"

        db.session.delete(db.session.get(type(first), first.id))
        db.session.flush()
        db.session.expire_all()

        read = db.session.get(BankStatementLine, line.id)
        assert read.merchant_name == "Coffee Shop"
        assert [sighting.import_id for sighting in read.sightings] == [second.id]


class TestWhatDeletingOneCosts:
    """The pair of consequences the DEFAULT ``NO ACTION`` gives, measured.

    Neither is obvious and the two pull against each other: the reference has
    to hold, and an account's deletion still has to succeed even though its
    merchants and its lines are removed by the same statement.
    """

    def test_a_merchant_a_SIGHTING_names_may_not_be_deleted(
        self, app, db, seed_user,
    ):
        """A line's merchant is not a thing that can vanish under it.

        ``CASCADE`` here would have declared the opposite -- that deleting a
        merchant deletes what a source said about a bank line -- which is
        false of what any door in ``app/`` does and dangerous if it ever
        became reachable.  The referrer is the sighting since plan step
        ``bank_import:X-f6b-1b`` (ruling **R-BI16**).
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-9.99", merchant="Food Lion",
        )
        db.session.flush()

        db.session.delete(db.session.get(Merchant, line.merchant_id))

        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "fk_statement_line_sightings_merchant_account" in str(
            caught.value,
        )

    def test_deleting_the_ACCOUNT_still_succeeds(
        self, app, db, seed_user, seed_second_user,
    ):
        """THE control for the choice of ``NO ACTION`` over ``RESTRICT``.

        ``RESTRICT`` forbids the deferral that makes this work: an account's
        deletion cascades to its merchants AND to its imports and their lines,
        and the referential check has to run after every cascade of that one
        statement rather than between them.  ``RESTRICT`` gives the case above
        just as well and might not give this one, which is why the two are
        graded together.

        The SECOND user's account is used because the seeded first one carries
        recurring definitions that refuse an ordinary delete for reasons of
        their own (``transaction_templates_account_id_fkey``), and a case that
        died on those would grade nothing here.
        """
        account = seed_second_user["account"]
        statement = an_import(seed_second_user, account=account)
        line = a_bank_line(
            seed_second_user, statement, amount="-9.99", merchant="Food Lion",
        )
        db.session.flush()
        # BOTH referrers, because the deferral argument is about a statement
        # whose cascade reaches more than one of them -- a class staging only
        # the line half would grade half the claim.
        db.session.add(MerchantRule(
            user_id=seed_second_user["user"].id,
            account_id=account.id,
            merchant_id=line.merchant_id,
            never_a_purchase=True,
        ))
        db.session.flush()
        assert db.session.query(Merchant).filter(
            Merchant.account_id == account.id,
        ).count() == 1

        db.session.delete(account)
        db.session.flush()

        assert db.session.query(Merchant).filter(
            Merchant.account_id == account.id,
        ).count() == 0

    def test_a_merchant_OUTLIVES_the_lines_that_named_it(
        self, app, db, seed_user,
    ):
        """The property that retired the scope check's second half.

        *Which merchants may be asked about* was the UNION of a DISTINCT over
        recorded lines and the set already answered for, and the second half
        existed only because deleting an import took a merchant's lines and
        would otherwise have made its answer unwithdrawable.  Nothing deletes a
        merchant, so the union is the table -- and this is what says so at the
        schema tier.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-9.99", merchant="Food Lion",
        )
        db.session.flush()
        merchant_id = line.merchant_id

        db.session.delete(line)
        db.session.flush()

        assert db.session.get(Merchant, merchant_id) is not None


class TestARuleIsAboutAMerchantOfItsOwnAccount:
    """``fk_merchant_rules_merchant_account``: the retired fence."""

    def test_a_rule_naming_ANOTHER_ACCOUNTS_merchant_is_unwritable(
        self, app, db, seed_user, seed_second_user,
    ):
        """THE guard ``_refuse_unknown_merchants`` used to be, alone.

        That function compared a submitted STRING against a DISTINCT over this
        account's recorded lines.  Delete it now and a crafted body reaches
        this constraint instead of a stored row -- which is the difference
        between a refusal somebody has to remember and one the schema holds.
        """
        theirs = Merchant(
            account_id=seed_second_user["account"].id, name="Theirs",
        )
        db.session.add(theirs)
        db.session.flush()
        db.session.add(MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=theirs.id,
            never_a_purchase=True,
        ))

        with pytest.raises(Exception) as caught:
            db.session.flush()

        assert "fk_merchant_rules_merchant_account" in str(caught.value)


class TestReadingALinesMerchantCostsNoSecondStatement:
    """``BankStatementLine.merchant_name`` rides in the line's own statement.

    An adversarial review of 2026-08-25 measured the claim ungraded on the
    relationship this read was then: switching ``lazy="joined"`` to
    ``lazy="select"`` left the targeted suites green while tripling their
    wall-clock, because every reader that holds a line reads what its
    merchant is called.  The review screen renders 91 unexplained lines at
    once, so a per-line load is finding **N-309**'s N+1 on the path this step
    created.  Since plan step ``bank_import:X-f6b-1b`` the name is a
    ``column_property`` -- a subquery in the SELECT list -- and the case
    grades the same claim: make it ``deferred`` and each read loads it.
    """

    def test_reading_every_lines_merchant_is_ONE_statement(
        self, app, db, seed_user,
    ):
        """The firing control the eagerness never had.

        **The lines are read INSIDE the capture and the merchants are not
        staged inside it**, because the helper charges the subject every
        statement the block emits -- including a lazy load the fixture would
        have triggered, and including the seeded account's own row if it were
        touched here.
        """
        statement = an_import(seed_user)
        for index, named in enumerate(("Food Lion", "Walmart", "Apple")):
            a_bank_line(
                seed_user, statement, amount="-9.99", merchant=named,
                sequence_in_group=index,
            )
        db.session.flush()
        db.session.expire_all()

        def _read():
            return sorted(
                line.merchant_name
                for line in db.session.query(BankStatementLine).all()
            )

        names, statements = capture_sql_statements(_read)

        assert names == ["Apple", "Food Lion", "Walmart"]
        assert len(statements) == 1


class TestResolvingWordsToRows:
    """:func:`~app.services.statement_import._merchants.resolve_merchants`."""

    def test_it_creates_what_is_new_and_finds_what_is_not(
        self, app, db, seed_user,
    ):
        """TOTAL over the words asked about, which both writers index on.

        ``_sighting_of`` looks every word up directly rather than carrying a
        fallback, so a mapping missing one of them would be a ``KeyError``
        mid-import rather than a wrong row -- but only after the import row
        had been written.
        """
        account_id = seed_user["account"].id
        first = resolve_merchants(account_id, {"Food Lion"})
        db.session.flush()

        both = resolve_merchants(account_id, {"Food Lion", "Walmart"})

        assert set(both) == {"Food Lion", "Walmart"}
        assert both["Food Lion"] == first["Food Lion"]

    def test_asking_TWICE_creates_nothing_the_second_time(
        self, app, db, seed_user,
    ):
        """Idempotent at the DATABASE, not by looking first.

        ``ON CONFLICT DO NOTHING`` against ``uq_merchants_account_name`` is
        what makes two imports racing on one account cost a no-op rather than
        an ``IntegrityError`` that fails a whole statement over a merchant it
        did not need to create.  Look-then-insert has no atomic reading at all.
        """
        account_id = seed_user["account"].id
        resolve_merchants(account_id, {"Food Lion"})
        db.session.flush()

        resolve_merchants(account_id, {"Food Lion"})
        db.session.flush()

        assert db.session.query(Merchant).filter(
            Merchant.account_id == account_id,
        ).count() == 1

    def test_it_answers_for_THIS_account_alone(
        self, app, db, seed_user, seed_second_user,
    ):
        """The account is half the identity, so it is half the lookup.

        Drop ``Merchant.account_id == account_id`` from the read and this
        account's import would file its lines against another owner's merchant
        row -- which the line's own composite key would then refuse, loudly,
        for a reason naming nothing.

        **It asks BOTH accounts once both rows exist, and that is what makes
        it deterministic.**  An adversarial review of 2026-08-25 measured a
        first version passing with the filter deleted, and a second one too:
        both rested on which row ``dict(rows)`` saw LAST, which is insertion
        order, which PostgreSQL does not promise and which happened to favour
        the assertion.  Unfiltered, the two calls below read the same set and
        return the SAME id whichever row wins -- so the inequality fails on
        every ordering rather than on a lucky one.
        """
        mine_account = seed_user["account"].id
        theirs_account = seed_second_user["account"].id
        resolve_merchants(theirs_account, {"Food Lion"})
        resolve_merchants(mine_account, {"Food Lion"})
        db.session.flush()

        mine = resolve_merchants(mine_account, {"Food Lion"})
        theirs = resolve_merchants(theirs_account, {"Food Lion"})

        assert mine["Food Lion"] != theirs["Food Lion"]
        assert db.session.get(
            Merchant, mine["Food Lion"],
        ).account_id == mine_account
        assert db.session.get(
            Merchant, theirs["Food Lion"],
        ).account_id == theirs_account

    def test_asking_about_NOTHING_issues_no_statement(
        self, app, db, seed_user,
    ):
        """An import of lines that name no merchant asks the database nothing.

        An INSERT of no rows is a syntax error and an ``IN ()`` is a statement
        with nothing to find, so the empty case is a guard rather than an
        optimisation.
        """
        # The id is read OUTSIDE the capture: touching the seeded account
        # inside it would lazy-load the row and count that SELECT as this
        # function's, which is the reader-versus-subject mistake the whole
        # capture exists to avoid.
        account_id = seed_user["account"].id

        _, statements = capture_sql_statements(
            lambda: resolve_merchants(account_id, set()),
        )

        assert statements == []

    def test_a_whole_pass_costs_ONE_insert_and_ONE_select(
        self, app, db, seed_user,
    ):
        """Set-shaped, because the alternative is an N+1 this arc has paid for.

        The developer's own dev database holds 378 recorded lines naming 62
        merchants (measured 2026-08-25), so a find-or-create per line would be
        378 round trips to learn 62 facts -- finding **N-309**'s shape on the
        import path.
        """
        account_id = seed_user["account"].id
        words = {f"Merchant {index}" for index in range(62)}

        _, statements = capture_sql_statements(
            lambda: resolve_merchants(account_id, words),
        )

        emitted = [text for text, _ in statements]
        assert len(emitted) == 2
        assert emitted[0].lstrip().upper().startswith("INSERT")
        assert emitted[1].lstrip().upper().startswith("SELECT")

    def test_a_name_at_the_COLUMN_WIDTH_is_stored_whole(
        self, app, db, seed_user,
    ):
        """100 characters, stated once now rather than on two columns.

        The two copies were one edit from disagreeing, and a key stored
        narrower than its source fails to match the longest merchants silently.
        The adapter's own pattern reads at most 100 characters
        (``_secu_csv._MERCHANT``), so this is the widest word that can arrive.
        """
        widest = "M" * 100

        resolved = resolve_merchants(seed_user["account"].id, {widest})
        db.session.flush()

        assert db.session.get(Merchant, resolved[widest]).name == widest
