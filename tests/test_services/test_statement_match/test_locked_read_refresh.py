"""A locked read hands back the row the lock holds -- plan step ``bank_import:X-gv``.

Finding **BI-493**, named by ``bank_import:X-gi-5``'s neutral design review.
A press carrying creations runs :func:`~app.services.statement_match.review_set`
before :func:`~app.services.statement_match.apply_reviewed`, inside the ONE
``READ COMMITTED`` transaction a command request is, and that derivation reads
every undisposed line of the account into the session's identity map,
unlocked.  The doors then read their lines LOCKED -- ``load_lines(for_write=
True)`` and ``_line_on`` -- but an instance already in the identity map is
handed back with the attributes it was hydrated with: SQLAlchemy populates
only the attributes an existing instance has NOT loaded
(``sqlalchemy.orm.loading._populate_partial``), so the locked ``SELECT``
fetched the row the lock now holds and the door decided on the one from
before it.

The one concurrent writer of a recorded line is a re-import's NULL-fill
(``statement_import._record._absorb_gained_facts``: running balance, source
category, external id, transaction day, merchant), and two of those columns
reach every door's decision: ``merchant_id`` is the rule lookup at the create
and income doors, the deposit's category placement at the income door and the
skip door's account-payment refusal (ruling **bank_import:R-JI**);
``transaction_on`` is the day the create door files the purchase on and the
day the match door re-dates one to (``MatchDays.of``, ruling **R-FW**).  The
door cases below take one door per column -- the skip and the create -- and
the read cases cover the other two, which take the same helper.

The remedy is one clause at the one spelling of the lock:
:func:`~app.services.statement_match._resolve.locked_for_write` composes
``populate_existing()`` with ``with_for_update``, so what a door reads under
the lock is what the lock protects.  Two layers of case:

1. **The READ, at each site that returns a row.**  Hydrate through the real
   pre-lock reader, fill the row from a second connection, read it locked:
   the instance handed back is the hydrated one, refreshed -- its columns and
   its joined merchant both.  FIRING CONTROL: delete ``populate_existing()``
   from ``locked_for_write`` and both cases read the hydrated ``NULL``.
2. **The DOOR, in the pass.**  The derivation, the fill, then the press: the
   skip door refuses a line whose merchant now pays an account the owner
   holds, and the create door files a purchase on the day the fill stated.
   On the old code the skip landed and the purchase took the posting day.

``lock_lines`` is the third locked read and has no case here on purpose: it
selects the id column alone and returns nothing, so there is no instance for
it to hand back stale; the refresh belongs to the reads that hand a row to a
door, and ``test_lock_order`` grades what that read is for.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from app.models.statement_line_skip import StatementLineSkip
from app.models.transaction_entry import TransactionEntry
from app.services import statement_match
from app.services.statement_match import (
    Consent,
    PurchaseCreation,
    ReviewedBatch,
    SkipRequest,
    matched_subjects,
    review_set,
)

# Pylint: protected-access -- the two locked reads are private collaborations
# inside the package with no importer outside it; a test for the read reaches
# into them, the allowance ``test_lock_order`` takes for the same reads.
from app.services.statement_match import _resolve, _skipping  # pylint: disable=protected-access
from app.services.statement_match._undisposed import undisposed_lines
from app.services.statement_match._verbs import SKIP_SHUT_PAYS_AN_ACCOUNT

from ._builders import (
    a_bank_line,
    a_merchant,
    a_scope,
    an_envelope,
    an_import,
)

_TABLE = "budget.bank_statement_lines"

#: What SECU files a card payment under, verbatim -- the one string
#: :data:`~app.services.statement_match._vocabulary.ACCOUNT_PAYMENT_CATEGORIES`
#: holds for that adapter, and what puts a merchant in the set the skip door
#: refuses (ruling **bank_import:R-JI**).
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _a_re_import_fills(db, line_id, **columns):
    """Land a re-import's NULL-fill on *line_id* from a SECOND connection.

    ``_absorb_gained_facts`` writes exactly this: a column the recorded row
    holds ``NULL`` on, set to what a later export states.  Written as the
    ``UPDATE`` the ORM flushes it to, on a connection of its own that commits
    -- a committed write the test session's open transaction did not make,
    which is the shape a re-import landing between the page's derivation and
    its press has.  The audit trigger tolerates the unbound actor (its
    ``current_setting(..., true)`` reads ``NULL``), which is the documented
    direct-write path.

    Args:
        db: The extension, for its engine.
        line_id: The recorded line.
        **columns: The columns to fill, by name, with the values a later
            export stated.
    """
    assignments = ", ".join(f"{column} = :{column}" for column in columns)
    with db.engine.connect() as connection:
        connection.execute(
            text(f"UPDATE {_TABLE} SET {assignments} WHERE id = :id"),
            {"id": line_id, **columns},
        )
        connection.commit()


def _the_row(db, line_id):
    """Return ``(merchant_id, transaction_on)`` as the DATABASE holds them.

    The session's own statement, so under ``READ COMMITTED`` it sees the
    second connection's commit -- which is what makes the ORM instance's
    stale value a fact about the identity map rather than about the write
    not having landed.
    """
    return db.session.execute(
        text(f"SELECT merchant_id, transaction_on FROM {_TABLE} WHERE id = :id"),
        {"id": line_id},
    ).one()


def _a_line_the_first_export_named_no_merchant_on(seed_user, **fields):
    """Stage one committed outflow with NO merchant and NO transaction day.

    The 10-column export's shape: the developer's first import named neither,
    and the re-export that stated them is the fill these cases land.

    Returns:
        The committed :class:`~app.models.statement_import.BankStatementLine`.
    """
    return a_bank_line(
        seed_user, an_import(seed_user), amount="-57.96",
        posted_on=seed_user["bootstrap_period"].start_date + timedelta(days=5),
        description="POINT OF SALE DEBIT L340 THING", **fields,
    )


class TestALockedReadHandsBackTheRowTheLockHolds:
    """Layer 1: the read, at each of the two sites that return a row."""

    def _hydrated_then_filled(self, db, seed_user, line):
        """Hydrate *line* unlocked, then land the fill beside the session.

        Returns:
            ``(merchant, stated_on)`` -- what the fill wrote.
        """
        merchant = a_merchant(seed_user, "Amazon")
        db.session.commit()
        stated_on = line.posted_on - timedelta(days=3)
        account_id = seed_user["account"].id

        # The real pre-lock reader, which ``review_set`` reaches through
        # ``inbox_partition``: it returns the SAME instance the fixture holds
        # (the identity map), and that instance has read the NULLs.
        hydrated = [each for each in undisposed_lines(account_id) if each.id == line.id]
        assert hydrated == [line] and hydrated[0] is line
        assert line.merchant_id is None and line.transaction_on is None
        assert line.merchant is None

        _a_re_import_fills(
            db, line.id, merchant_id=merchant.id, transaction_on=stated_on,
        )

        # The write LANDED and this transaction can see it; only the ORM
        # instance is behind.  Asserted immediately before the locked read,
        # so a pass cannot come from something else having refreshed it.
        assert _the_row(db, line.id) == (merchant.id, stated_on)
        assert line.merchant_id is None and line.transaction_on is None
        return merchant, stated_on

    def test_load_lines_returns_the_fill_not_the_hydrated_null(
        self, app, db, seed_user,
    ):
        """The three create-side doors' read: the instance, refreshed.

        FIRING CONTROL: delete ``populate_existing()`` from
        ``locked_for_write`` and ``merchant_id`` reads the hydrated ``None``.
        """
        with app.app_context():
            line = _a_line_the_first_export_named_no_merchant_on(seed_user)
            merchant, stated_on = self._hydrated_then_filled(db, seed_user, line)
            account_id = seed_user["account"].id

            [read] = _resolve.load_lines(
                account_id, frozenset({line.id}), matched_subjects(account_id),
                for_write=True,
            )

            assert read is line, "the locked read returned a second instance"
            assert read.merchant_id == merchant.id
            assert read.transaction_on == stated_on
            # The joined-eager half: ``merchant_name`` reads the relationship,
            # not the key, so a refresh of the columns alone would still name
            # the line by its description on the receipt.
            assert read.merchant is merchant
            assert read.merchant_name == "Amazon"
            db.session.rollback()

    def test_line_on_returns_the_fill_not_the_hydrated_null(
        self, app, db, seed_user,
    ):
        """The skip door's read: the instance, refreshed.

        FIRING CONTROL: as above; this site takes the lock through the same
        helper, and ``skip_line`` reads ``merchant_id`` off what it returns.
        """
        with app.app_context():
            line = _a_line_the_first_export_named_no_merchant_on(seed_user)
            merchant, stated_on = self._hydrated_then_filled(db, seed_user, line)

            read = _skipping._line_on(  # pylint: disable=protected-access
                line.id, seed_user["user"].id, seed_user["account"].id,
            )

            assert read is line, "the locked read returned a second instance"
            assert read.merchant_id == merchant.id
            assert read.transaction_on == stated_on
            assert read.merchant is merchant
            db.session.rollback()


class TestTheDoorDecidesOnTheFilledLine:
    """Layer 2: the scenario the ledger row names, at the doors.

    Each case is the press the route runs: the derivation first
    (``review_set``, which a press carrying creations pays for its rule
    offer), the fill landing between it and the press, then
    ``apply_reviewed`` against the same scope in the same transaction.
    """

    def test_the_skip_door_refuses_a_merchant_filled_after_derivation(
        self, app, db, seed_user,
    ):
        """A skip over a line that now pays an account the owner holds.

        The first export filed the line under SECU's card-payment category
        and named no merchant, so the derivation offered it as an ordinary
        line; the re-export named the card.  Ruling **R-JI** refuses the skip
        by the line's merchant, and the door must read the merchant the lock
        holds.  On the old code the skip LANDED beside the creation.
        """
        with app.app_context():
            envelope = an_envelope(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            to_file = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                description="POINT OF SALE DEBIT L340 THING (Amazon)",
                merchant="Amazon",
            )
            card_payment = a_bank_line(
                seed_user, statement, amount="-793.23",
                posted_on=day + timedelta(days=1), sequence_in_group=1,
                description="ACH DEBIT CAPITAL ONE      MOBILE PMT",
                source_category=_CARD_PAYMENT,
            )
            card = a_merchant(seed_user, "Capital One Credit Card")
            db.session.commit()

            scope = a_scope(seed_user)
            review = review_set(scope)
            # What the screen showed: the line, offered with no merchant.
            offered = {each.line_id: each.merchant_id for each in review.unmatched}
            assert offered[card_payment.id] is None
            assert card_payment.merchant_id is None

            _a_re_import_fills(db, card_payment.id, merchant_id=card.id)
            assert _the_row(db, card_payment.id)[0] == card.id

            outcome = statement_match.apply_reviewed(
                ReviewedBatch(
                    consent=Consent.TICKED, matches=(), incomes=(),
                    creations=(PurchaseCreation(
                        line_id=to_file.id, transaction_id=envelope.id,
                    ),),
                    skips=(SkipRequest(line_id=card_payment.id),),
                ),
                scope,
            )

            assert outcome.applied_count == 1, outcome.refused
            assert outcome.applied[0].line_ids == (to_file.id,)
            assert outcome.refused_count == 1, outcome.applied
            assert outcome.refused[0].line_ids == (card_payment.id,)
            assert outcome.refused[0].reason == SKIP_SHUT_PAYS_AN_ACCOUNT
            assert db.session.query(StatementLineSkip).count() == 0
            db.session.rollback()

    def test_the_create_door_files_on_the_day_filled_after_derivation(
        self, app, db, seed_user,
    ):
        """A purchase whose made-on day the re-export stated.

        Ruling **R-FW**: the purchase's budget clock is the day the bank says
        it was MADE, falling back to the posting day only for a source stating
        none.  The first export stated none; the re-export did, after the
        derivation.  On the old code the purchase was filed on the posting
        day, three days late on its own clock.
        """
        with app.app_context():
            envelope = an_envelope(seed_user)
            line = _a_line_the_first_export_named_no_merchant_on(
                seed_user, merchant="Amazon",
            )
            db.session.commit()
            posted = line.posted_on
            made_on = posted - timedelta(days=3)

            scope = a_scope(seed_user)
            review = review_set(scope)
            assert {each.line_id for each in review.unmatched} == {line.id}
            assert line.transaction_on is None

            _a_re_import_fills(db, line.id, transaction_on=made_on)
            assert _the_row(db, line.id)[1] == made_on

            outcome = statement_match.apply_reviewed(
                ReviewedBatch(
                    consent=Consent.TICKED, matches=(), incomes=(), skips=(),
                    creations=(PurchaseCreation(
                        line_id=line.id, transaction_id=envelope.id,
                    ),),
                ),
                scope,
            )

            assert outcome.applied_count == 1, outcome.refused
            entry = (
                db.session.query(TransactionEntry)
                .filter(TransactionEntry.transaction_id == envelope.id)
                .one()
            )
            assert entry.purchased_on == made_on
            assert entry.settled_on == posted
            db.session.rollback()
