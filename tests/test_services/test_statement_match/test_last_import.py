"""What the account's most recent import did, beside the Reconcile hero.

Plan step ``bank_import:X-gj-1b``.  The locked direction prints one
provenance line right of the hero's four figures -- *Last import <day> - N
lines recorded - N filed by rules - receipt* -- and this grades the two
numbers in it and the choice of WHICH import they are about.

**The subject is the three narrowings**, because each one is a way the line
could report a true-looking number about the wrong thing: the newest import
rather than any import, what an import WROTE rather than what its file held,
and the lines THIS import's own rules filed rather than the account's whole
history.
"""

from datetime import datetime, timezone

# Pylint: ``shekel-private-module-import`` -- a test of a service's
# INTERNALS reaches for them by name, which is the convention this
# package's own test modules already keep (``test_bars``,
# ``test_candidates``, ``test_reconcile``).
# pylint: disable=shekel-private-module-import
from app.services.statement_match._last_import import last_import

from ._builders import (
    a_bank_line,
    an_envelope,
    an_import,
    filed_by,
)


def _a_filed_line(seed_user, db, statement, envelope, *, merchant,
                  sequence, by_rule):
    """Record one of *statement*'s lines as a purchase, by rule or by hand.

    Args:
        seed_user: The seeded user bundle.
        db: The session fixture.
        statement: The import that recorded the line.
        envelope: The budget line to file it into.
        merchant: What the bank names the merchant.
        sequence: The ordinal completing the line's identity.
        by_rule: Whether a STANDING RULE performed the act (**R-GT**) -- the
            one fact that decides whether this line is counted.

    Returns:
        The line.
    """
    line = a_bank_line(
        seed_user, statement, amount="-57.96",
        posted_on=seed_user["bootstrap_period"].start_date,
        description=f"POINT OF SALE DEBIT L340 THING ({merchant})",
        merchant=merchant, sequence_in_group=sequence,
    )
    db.session.commit()
    filed_by(seed_user, line, envelope, by_rule=by_rule)
    db.session.commit()
    return line


def _reported(seed_user):
    """Return the provenance line for the seeded owner's checking account.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The :class:`~app.services.statement_match._last_import.LastImport`, or
        ``None``.
    """
    return last_import(seed_user["user"].id, seed_user["account"].id)


class TestWhichImportItIsAbout:
    """The NEWEST one, and the two keys that decide which that is.

    An account is imported into repeatedly -- the developer's own has three
    -- so "the last import" is an ordering claim before it is a counting one,
    and an ordering that reached for the oldest would print a true figure
    about a file from three weeks ago.
    """

    def test_the_LATER_instant_is_the_one_reported(self, app, db, seed_user):
        """Ordered by when the import RAN, newest first."""
        an_import(
            seed_user, line_count=9, recorded_count=9,
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        )
        an_import(
            seed_user, line_count=4, recorded_count=4,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        assert _reported(seed_user).recorded_count == 4

    def test_the_ID_breaks_a_tie_the_CLOCK_cannot(self, app, db, seed_user):
        """Two imports in ONE transaction carry the identical instant.

        ``created_at`` defaults to ``now()``, which in PostgreSQL is the
        TRANSACTION's start time rather than the statement's -- so a second
        key is not decoration, it is the only thing that orders two imports
        written together.  Staged with the instant stated EQUAL rather than
        left to the default, so the case says what it is about.
        """
        together = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        first = an_import(
            seed_user, line_count=9, recorded_count=9, created_at=together,
        )
        second = an_import(
            seed_user, line_count=4, recorded_count=4, created_at=together,
        )
        db.session.commit()
        assert second.id > first.id

        assert _reported(seed_user).recorded_count == 4

    def test_an_account_nobody_has_imported_into_reports_NOTHING(
        self, app, db, seed_user,
    ):
        """``None`` rather than a row of zeroes.

        A hero reading *Last import -- 0 lines recorded* over an account that
        has never been imported into would state an act that did not happen.
        The template renders the whole line or none of it.
        """
        assert _reported(seed_user) is None

    def test_it_reads_no_OTHER_account_s_import(
        self, app, db, seed_user, seed_second_user,
    ):
        """Bounded by the account, which is what the route proved."""
        an_import(
            seed_user, line_count=9, recorded_count=9,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        assert last_import(
            seed_second_user["user"].id, seed_second_user["account"].id,
        ) is None


class TestWhatItSaysWasRecorded:
    """*N lines recorded* is what the import WROTE, not what the file held."""

    def test_a_re_import_reports_what_it_ADDED_and_not_the_file_s_size(
        self, app, db, seed_user,
    ):
        """The two columns differ, and the line prints the smaller one.

        Measured on a restored production clone 2026-08-30: the developer's
        newest import recorded **2** of the **42** lines its file contained,
        because the rest were already known.  Printing ``line_count`` would
        tell him 42 lines had just landed when 40 of them were months old --
        which is the fact ``recorded_count`` exists to make visible.
        """
        an_import(
            seed_user, line_count=42, recorded_count=2,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        assert _reported(seed_user).recorded_count == 2

    def test_an_import_that_added_NOTHING_says_so(self, app, db, seed_user):
        """Zero is a real answer and the honest one for a repeat upload."""
        an_import(
            seed_user, line_count=40, recorded_count=0,
            created_at=datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        reported = _reported(seed_user)
        assert (reported.recorded_count, reported.filed_by_rules) == (0, 0)


class TestWhatItSaysWasFiledByRules:
    """*N filed by rules* is about THIS import's lines and a rule's acts.

    Both narrowings are ways the number could be true of something else: the
    account's whole rule history is not what an import just did, and an act a
    person ticked is not what a rule did by itself (**R-GT**).
    """

    def test_only_the_acts_a_RULE_performed_are_counted(
        self, app, db, seed_user,
    ):
        """One import, two lines filed, one of them by hand."""
        statement = an_import(
            seed_user, line_count=2, recorded_count=2,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        envelope = an_envelope(seed_user)
        _a_filed_line(
            seed_user, db, statement, envelope,
            merchant="Amazon", sequence=0, by_rule=True,
        )
        _a_filed_line(
            seed_user, db, statement, envelope,
            merchant="Walmart", sequence=1, by_rule=False,
        )

        reported = _reported(seed_user)
        assert reported.filed_by_rules == 1
        # The sentence's two halves are comparable only if this holds.
        assert reported.filed_by_rules <= reported.recorded_count

    def test_an_EARLIER_import_s_filed_line_is_not_counted(
        self, app, db, seed_user,
    ):
        """The count is about the newest import and not the account.

        Ruling **bank_import:R-GI** reaches new swipe lines only, so a
        rule-filed act naming an older import's line was performed by THAT
        import's run.  Attributing it to the newest one would report a rule
        firing on a pass where no rule fired.
        """
        older = an_import(
            seed_user, line_count=1, recorded_count=1,
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        )
        envelope = an_envelope(seed_user)
        _a_filed_line(
            seed_user, db, older, envelope,
            merchant="Amazon", sequence=0, by_rule=True,
        )
        an_import(
            seed_user, line_count=1, recorded_count=1,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        assert _reported(seed_user).filed_by_rules == 0

    def test_it_counts_no_other_owner_s_acts(
        self, app, db, seed_user, seed_second_user,
    ):
        """Scoped by owner AND account, as every reader beside it is.

        The import itself is found -- an import belongs to one account and
        the route has proved that account is the caller's -- and the acts are
        narrowed by both columns the write door narrows by, which is the rule
        :func:`~app.services.statement_match._accepted_view.accepted_counts`
        already states.
        """
        statement = an_import(
            seed_user, line_count=1, recorded_count=1,
            created_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        envelope = an_envelope(seed_user)
        _a_filed_line(
            seed_user, db, statement, envelope,
            merchant="Amazon", sequence=0, by_rule=True,
        )

        theirs = last_import(
            seed_second_user["user"].id, seed_user["account"].id,
        )

        assert theirs is not None
        assert theirs.filed_by_rules == 0


class TestTheInstantIsCarriedWhole:
    """The service hands over the stored instant and truncates nothing.

    Truncating here would truncate in UTC, and the display timezone is four
    or five hours behind it: an import performed on a summer evening would be
    reported under the NEXT day.  The conversion is the presentation layer's
    (``local_datetime``), which is this project's standing rule for every
    ``timestamptz`` it renders; the route test asserts the rendered day.
    """

    def test_it_is_the_stored_UTC_instant(self, app, db, seed_user):
        """Not a date, and not shifted."""
        ran_at = datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc)
        an_import(
            seed_user, line_count=1, recorded_count=1, created_at=ran_at,
        )
        db.session.commit()

        assert _reported(seed_user).at == ran_at
