"""A match that lost its bank lines does not take the review screen down.

Plan step **bank_import:X-f6a-4**.  The state needs a code defect to reach --
``record_match`` refuses an empty side at the one writer,
``fk_statement_match_members_line_account`` refuses to remove a line a match
names, and migration ``e4a7c0f13b92`` deleted the acts that already held none
-- and what happens if it is reached anyway was measured by two adversarial
reviews 2026-08-20 rather than argued.

**Skipping it silently was the original defect** (invisible, and its rows stay
claimed).  **Raising takes the whole review surface down for that account**,
including the release control that would repair it, which is the rule
``_accepted_view._accepted_row`` already states in as many words for its own
degraded case.  So the reader skips and LOGS at ERROR naming the row to delete.
"""

from app.services.statement_match._accepted_view import (  # pylint: disable=protected-access
    accepted_groups,
)

from ._builders import a_bank_line, a_transaction, an_import


def _planted_lineless(db, seed_user):
    """Stage an accepted match and then take its bank-line member away.

    Through raw SQL, because the schema exists to make this unreachable and the
    ORM would rightly refuse to help.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.

    Returns:
        The transaction the act still claims.
    """
    from app.services import statement_match
    from ._builders import a_scope, a_submission

    statement = an_import(seed_user)
    line = a_bank_line(seed_user, statement, amount="-180.00")
    txn = a_transaction(seed_user, amount="180.00")
    scope = a_scope(seed_user)
    statement_match.accept_match(
        a_submission(scope, lines=[line], transactions=[txn]),
        scope,
    )
    db.session.flush()
    db.session.execute(db.text(
        "DELETE FROM budget.statement_match_members"
        " WHERE bank_statement_line_id IS NOT NULL"
    ))
    db.session.flush()
    db.session.expire_all()
    return txn


class TestALinelessActDoesNotBreakTheScreen:
    """The reader degrades; it does not raise and it does not stay silent."""

    def test_the_reader_answers_rather_than_raising(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: a bare ``max()`` on the empty side used to raise.

        ``accepted_groups`` feeds ``review_set``, which renders the review GET
        and the response to every apply-POST -- so one such act took the whole
        reconcile surface down for the account, with the release button that
        would repair it rendered by the function that raised.
        """
        _planted_lineless(db, seed_user)

        assert accepted_groups(
            seed_user["user"].id, seed_user["account"].id,
        ) == []

    def test_it_is_LOGGED_at_error_naming_the_row(
        self, app, db, seed_user, caplog,
    ):
        """Skipping silently was the original defect; this is what replaces it.

        The operator is told which row to delete, because the act still holds
        its transactions out of every later match and nothing on screen can
        show it.
        """
        import logging

        _planted_lineless(db, seed_user)

        with caplog.at_level(logging.ERROR):
            accepted_groups(seed_user["user"].id, seed_user["account"].id)

        assert any(
            record.__dict__.get("event") == "statement_match_lineless"
            for record in caplog.records
        )
