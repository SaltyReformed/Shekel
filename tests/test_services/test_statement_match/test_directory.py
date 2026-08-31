"""Every merchant this account has seen, and what the owner said about each.

Plan step **bank_import:X-gk**, ruling **bank_import:R-IC**.  The DURABLE home
for a standing answer: one row per merchant, whether or not a line is waiting
and whether or not anybody has answered.

**The first class below is the defect this step closes**, and it is the one
assertion nothing else in this package can make: the queue's control, the
register's control and the receipt's offer are each PARTIAL, and there are
merchants no union of the three renders at all.  Measured 2026-08-31 on a
migrated clone of the developer's own database, account 1: **62 merchants, 30
answered and 32 not**, the queue rendering 0 rows and the register 30 -- so 32
merchants had nowhere to be answered for.  The case reproduces that shape on a
fixture rather than citing it.

Every refusal and every bound below is a FIRING CONTROL, written to fail if
the thing it grades were deleted.
"""

from dataclasses import fields
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.category import Category
from app.services.statement_match import (
    DIRECTORY_LIMIT,
    DirectoryAsk,
    MerchantWanted,
    RuleAnswer,
    answered_merchants,
    merchant_directory,
    review_set,
)
from app.services.statement_match._directory import (  # pylint: disable=protected-access
    NOT_SAID,
    MerchantActivity,
    merchant_activity,
    says_of,
)
from app.services.statement_match._bars import (  # pylint: disable=protected-access
    CreationBars,
)
from app.services.statement_match._rules import (  # pylint: disable=protected-access
    RuleView,
)
from app.services.statement_match._section import (  # pylint: disable=protected-access
    merchant_summary,
)

from ._builders import (
    a_bank_line,
    a_merchant,
    a_rule,
    a_scope,
    a_transaction,
    an_import,
    an_unexplained_outflow,
    filed_by,
    the_merchant_id,
)

# Pylint: protected-access -- the private names imported above are this
# PACKAGE's internals and have no importer outside it, so exporting them from
# ``statement_match.__init__`` would be the surface rule 13 forbids; the tests
# for a module reach into it, which is the allowance every sibling here takes.
# pylint: disable=protected-access

#: What SECU files a card payment under, which ruling **R-GJ** reads.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _no_categories() -> "dict[int, str]":
    """Return the category names a case with no new-envelope answer needs.

    Stated as a helper rather than inlined so a case that DOES need one reads
    as the exception it is: only the NEW ENVELOPE arm names a category, and
    every other answer is total without one.
    """
    return {}


def _directory(seed_user, asked=None, categories=None, account=None):
    """Return the directory for the seeded account.

    Args:
        seed_user: The seeded user bundle.
        asked: What to ask for, or ``None`` for the whole account.
        categories: The owner's active category names by id, or ``None``.
        account: The account, or ``None`` for the seeded checking one.

    Returns:
        The :class:`~app.services.statement_match._directory.MerchantDirectory`.
    """
    return merchant_directory(
        seed_user["user"].id,
        (account or seed_user["account"]).id,
        _no_categories() if categories is None else categories,
        DirectoryAsk() if asked is None else asked,
    )


def _named(directory) -> "list[str]":
    """Return the merchants the directory DREW, in the order it drew them."""
    return [entry.summary.merchant for entry in directory.entries]


def _says(directory, merchant: str) -> str:
    """Return what the directory says about *merchant*.

    Args:
        directory: The rendered directory.
        merchant: The merchant's name.

    Returns:
        Its phrase.

    Raises:
        AssertionError: When the directory drew no row for it -- an absence a
            ``next(..., None)`` would report as a phrase comparison failing.
    """
    for entry in directory.entries:
        if entry.summary.merchant == merchant:
            return entry.says
    raise AssertionError(
        f"the directory drew no row for {merchant}: {_named(directory)}"
    )


class TestTheMerchantsNoOtherSurfaceShows:
    """The defect X-gk closes: a merchant with no answer AND no waiting line.

    **This is the union of the three partial surfaces, measured.**  The queue
    asks about a merchant only while this pass has an unexplained outflow for
    it AND nobody has answered (:class:`~._section.MerchantSection`); the
    register shows only the answered (:class:`~._section.MerchantRegister`);
    the receipt offers only what a pass just filed.  A merchant that is
    unanswered and whose lines are all explained is on NONE of them.
    """

    @pytest.fixture()
    def _a_merchant_off_every_surface(self, db, seed_user):
        """Stage a merchant with no answer and no unexplained line.

        Returns:
            Its name.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-31.00", merchant="Audible",
        )
        # EXPLAINED, so the review pass has nothing to ask about: a create
        # arm exists only for a line no match claims.
        envelope = a_transaction(
            seed_user, name="Subscriptions", amount="100.00",
            is_envelope=True,
        )
        filed_by(seed_user, line, envelope, by_rule=False)
        db.session.commit()
        return "Audible"

    def test_the_QUEUE_does_not_ask_about_it(
        self, app, db, seed_user, _a_merchant_off_every_surface,
    ):
        """Surface one of three: the exception queue asks nothing.

        **PAIRED with a merchant the queue DOES ask about**, without which the
        assertion passes on an empty set -- i.e. on a queue that asks about
        nothing at all, which is a different fact and the one this class must
        not rest on.  Found by adversarial test-quality review 2026-08-31.
        """
        with app.app_context():
            an_unexplained_outflow(seed_user, merchant="Bloom Pediatric Pa")
            db.session.commit()

            asked_about = {
                waiting.summary.merchant
                for waiting in review_set(a_scope(seed_user)).merchants.merchants
            }

            assert "Bloom Pediatric Pa" in asked_about, (
                "the queue asks about nothing, so this proves nothing"
            )
            assert _a_merchant_off_every_surface not in asked_about

    def test_the_REGISTER_does_not_show_it(
        self, app, db, seed_user, _a_merchant_off_every_surface,
    ):
        """Surface two of three: the register holds answers, and it has none.

        **PAIRED with a merchant the register DOES show**, for the reason the
        case above is: `not in` over an empty register is true of every string
        ever written.
        """
        with app.app_context():
            a_rule(seed_user, "Angier - Thank Angier", always_ask=True)
            db.session.commit()

            view = RuleView.build(seed_user["user"].id, seed_user["account"].id)
            shown = {
                row.merchant for row in answered_merchants(
                    view,
                    CreationBars.build(
                        seed_user["user"].id, seed_user["account"].id,
                        view.rules,
                    ),
                ).merchants
            }

            assert "Angier - Thank Angier" in shown, (
                "the register shows nothing, so this proves nothing"
            )
            assert _a_merchant_off_every_surface not in shown

    def test_the_DIRECTORY_shows_it_and_says_nothing_was_said(
        self, app, db, seed_user, _a_merchant_off_every_surface,
    ):
        """The whole point of the page: it is here, and it is askable."""
        with app.app_context():
            directory = _directory(seed_user)

            assert _a_merchant_off_every_surface in _named(directory)
            assert _says(
                directory, _a_merchant_off_every_surface,
            ) == NOT_SAID


class TestWhatARowSays:
    """The phrase a closed row prints, over all four answers and their absence.

    **TOTAL by construction**: every member of
    :class:`~._rules.RuleAnswer` has a case here and so does the absence of a
    rule, so a fifth answer added without a phrase fails rather than rendering
    an empty cell.
    """

    def test_every_answer_and_the_absence_has_a_phrase(self):
        """The totality control, over the enum itself.

        **It reads the ENUM rather than a list written here**, which is what
        makes it fire: a case listing the members by hand would go on passing
        when another was added, which is the one failure this class exists to
        catch.

        **It fired on 2026-08-31** for ruling **R-HT(a)**'s
        ``INCOME_CATEGORY``, and what it caught was real: ``says_of`` ended on
        a bare ``return "Ask me every time"``, so the merchants page would have
        described a stored INCOME rule -- one filing money correctly -- as the
        answer the owner did not give.  That arm is named now and the
        fall-through is a raise.
        """
        assert {member.name for member in RuleAnswer} == {
            "TEMPLATE", "NEW_ENVELOPE", "INCOME_CATEGORY",
            "NEVER", "ALWAYS_ASK",
        }, (
            "RuleAnswer gained or lost a member; says_of needs an arm for it "
            "and this class needs a case"
        )

    def test_an_unanswered_merchant_says_it_has_not_been_answered(
        self, app, db, seed_user,
    ):
        """The absence is a PHRASE, not an empty cell."""
        with app.app_context():
            a_merchant(seed_user, "Dbcode. Io")
            db.session.commit()

            assert _says(_directory(seed_user), "Dbcode. Io") == NOT_SAID

    def test_a_TEMPLATE_answer_names_the_recurring_envelope(
        self, app, db, seed_user,
    ):
        """The commonest answer prints the container it names."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Walmart", template_id=envelope.template_id)
            db.session.commit()

            assert _says(_directory(seed_user), "Walmart") == "Groceries"

    def test_a_TEMPLATE_answer_the_picker_cannot_show_says_so(
        self, app, db, seed_user,
    ):
        """A stored answer whose template has stopped being offerable.

        **The firing control for the mark**: without it the row would print
        the envelope's name with nothing saying the picker no longer offers
        it, and the owner would read a live answer where the placement is in
        fact unresolvable.  Deactivating a template through the templates
        screen is what produces this, so it is live state.
        """
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Walmart", template_id=envelope.template_id)
            envelope.template.is_active = False
            db.session.commit()

            assert _says(_directory(seed_user), "Walmart") == (
                "Groceries -- no longer offered"
            )

    def test_a_NEW_ENVELOPE_answer_names_the_envelope_and_its_category(
        self, app, db, seed_user,
    ):
        """Both halves of the answer, because both are what was decided.

        The category is what every spending report groups by -- which is why
        the control's own category select carries no default -- so a phrase
        naming only the envelope would let an owner check half of what they
        said.
        """
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            a_rule(
                seed_user, "Speedway",
                envelope_name="Fuel", category_id=category.id,
            )
            db.session.commit()

            assert _says(
                _directory(
                    seed_user,
                    categories={category.id: category.display_name},
                ),
                "Speedway",
            ) == f'a new envelope called "Fuel", under {category.display_name}'

    def test_a_NEW_ENVELOPE_answer_under_an_ARCHIVED_category_says_so(
        self, app, db, seed_user,
    ):
        """Archiving leaves a stored answer the create door will refuse.

        **The firing control**: without the mark the row reads as a working
        answer, and the owner would have no way to see that the category it
        files under is gone until a placement was refused.
        """
        with app.app_context():
            name = seed_user["categories"]["Groceries"].display_name
            category_id = seed_user["categories"]["Groceries"].id
            a_rule(
                seed_user, "Speedway",
                envelope_name="Fuel", category_id=category_id,
            )
            # **RE-FETCHED inside this context before it is archived**, and
            # that is not a formality.  ``db.session`` is scoped to the APP
            # CONTEXT, so the row the ``seed_user`` fixture built belongs to
            # the session of the context that built it; assigning to that
            # object here leaves the mutation in a session this context never
            # commits, and the case would go on reading an ACTIVE category
            # while claiming to test an archived one.  Measured: the first
            # draft did exactly that and the phrase came back with no
            # category clause at all.
            db.session.get(Category, category_id).is_active = False
            db.session.commit()

            # The picker no longer offers it, so the route's own read of the
            # ACTIVE categories does not name it either -- which is exactly
            # the state this arm is for.
            assert _says(_directory(seed_user), "Speedway") == (
                f'a new envelope called "Fuel", under {name} -- archived'
            )

    def test_a_NEVER_answer_says_never_a_purchase(self, app, db, seed_user):
        """The answer that BARS, stated as an answer rather than as a blank."""
        with app.app_context():
            a_rule(seed_user, "Capital One Credit Card")
            db.session.commit()

            assert _says(
                _directory(seed_user), "Capital One Credit Card",
            ) == "Never a purchase"

    def test_an_ALWAYS_ASK_answer_is_not_the_same_as_saying_nothing(
        self, app, db, seed_user,
    ):
        """Ruling **R-GS**'s fourth answer, and the distinction it exists for.

        *I have not decided* is a question still owed; *ask me every time* is a
        question already answered.  They have the same effect on money and
        different effects on what the owner is asked, so a phrase collapsing
        them would lose the one fact that separates them.
        """
        with app.app_context():
            a_rule(seed_user, "Amazon", always_ask=True)
            db.session.commit()

            says = _says(_directory(seed_user), "Amazon")

            assert says == "Ask me every time"
            assert says != NOT_SAID

    def test_the_phrase_is_read_off_the_ANSWER_and_not_off_a_column(
        self, app, db, seed_user,
    ):
        """``says_of`` branches on :class:`RuleAnswer`, never on a truth test.

        **The firing control for the arm ORDER.**  A ``never a purchase``
        answer stores ``never_a_purchase`` true and no container; an
        ``ask me every time`` answer stores neither.  A producer inferring the
        arm from ``envelope_name`` being unset -- the shape that made the
        existing-envelope destination unreachable from a browser one leaf
        earlier -- would answer these two identically.
        """
        with app.app_context():
            never = a_merchant(seed_user, "Bank Of America Online Pmt")
            asks = a_merchant(seed_user, "Anchor Disposal")
            a_rule(seed_user, "Bank Of America Online Pmt")
            a_rule(seed_user, "Anchor Disposal", always_ask=True)
            db.session.commit()

            view = RuleView.build(seed_user["user"].id, seed_user["account"].id)
            bars = CreationBars.build(
                seed_user["user"].id, seed_user["account"].id, view.rules,
            )

            assert says_of(
                merchant_summary(
                    never.id, "Bank Of America Online Pmt", view, bars,
                ),
                view, {},
            ) != says_of(
                merchant_summary(asks.id, "Anchor Disposal", view, bars),
                view, {},
            )


class TestTheActivityBesideTheAnswer:
    """How much of the bank's record names a merchant, and when it last did.

    **What makes a bank abbreviation answerable** (developer, 2026-08-31): the
    developer's own unanswered set holds ``Dbcode. Io`` and ``Fid Bkg Svc Llc
    Moneyline``, and a count with a day is what identifies one of those.
    """

    def test_it_counts_every_line_the_merchant_names(
        self, app, db, seed_user,
    ):
        """EXPLAINED or not -- this is the bank's record, not a review pass.

        The register carries no count deliberately, because counting WAITING
        lines is the pass's work and a surface with no pass would be stating a
        figure it cannot know.  This is the other question.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            for offset in range(3):
                a_bank_line(
                    seed_user, statement, amount="-18.00",
                    posted_on=day + timedelta(days=offset),
                    sequence_in_group=offset, merchant="Duke Energy",
                )
            db.session.commit()

            entry = next(
                one for one in _directory(seed_user).entries
                if one.summary.merchant == "Duke Energy"
            )

            assert entry.activity.line_count == 3
            assert entry.activity.last_seen == day + timedelta(days=2)

    def test_a_merchant_that_has_OUTLIVED_its_lines_carries_no_count(
        self, app, db, seed_user,
    ):
        """An ANSWERED merchant row survives the lines that prompted it.

        Deleting the import that recorded them sweeps only the merchants no
        answer is about, so this is real state.  It is ABSENT from the
        activity read and paired to a zero by the directory, which is what
        keeps the zero stated in exactly one place.
        """
        with app.app_context():
            a_rule(seed_user, "Rainbow Lanes Fam", always_ask=True)
            db.session.commit()

            entry = next(
                one for one in _directory(seed_user).entries
                if one.summary.merchant == "Rainbow Lanes Fam"
            )

            assert entry.activity == MerchantActivity(0, None)

    def test_the_read_is_scoped_to_the_account(self, app, db, seed_user):
        """A second account's lines are not counted against this one's.

        ``bank_statement_lines`` carries no ``user_id``: it is account-scoped
        exactly as ``merchants`` is, so the account IS the ownership statement
        and a missing clause here would count a sibling account's record.
        """
        with app.app_context():
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="-9.00", merchant="Target",
            )
            db.session.commit()

            counted = merchant_activity(seed_user["account"].id)
            elsewhere = merchant_activity(seed_user["account"].id + 9_999)

            assert counted[the_merchant_id(seed_user, "Target")].line_count == 1
            assert elsewhere == {}


class TestTheThreeFilters:
    """Which merchants a render is about, and what the counts beside them say.

    **The counts are over the WHOLE account and never over what is drawn**,
    which is the property that lets the bar be read while a search narrows the
    list: it answers *what do I still owe*, not *what did I just type*.
    """

    @pytest.fixture()
    def _two_of_each(self, db, seed_user):
        """Stage two answered merchants and two with no answer."""
        a_rule(seed_user, "Walmart", always_ask=True)
        a_rule(seed_user, "Capital One Credit Card")
        a_merchant(seed_user, "Audible")
        a_merchant(seed_user, "Duke Energy")
        db.session.commit()

    def test_ALL_draws_every_merchant_ascending_by_name(
        self, app, db, seed_user, _two_of_each,
    ):
        """The default, and the order a directory is read in.

        Ascending by NAME and not by key: a surrogate id sorts by when the
        bank first showed each merchant, which is not an order anyone reading
        a list of merchants is looking for.
        """
        with app.app_context():
            assert _named(_directory(seed_user)) == [
                "Audible", "Capital One Credit Card", "Duke Energy", "Walmart",
            ]

    def test_UNANSWERED_draws_only_the_merchants_still_owed_an_answer(
        self, app, db, seed_user, _two_of_each,
    ):
        """The filter that answers "what is left"."""
        with app.app_context():
            directory = _directory(
                seed_user, DirectoryAsk(wanted=MerchantWanted.UNANSWERED),
            )

            assert _named(directory) == ["Audible", "Duke Energy"]

    def test_ANSWERED_draws_only_the_merchants_already_decided(
        self, app, db, seed_user, _two_of_each,
    ):
        """The other half of the partition."""
        with app.app_context():
            directory = _directory(
                seed_user, DirectoryAsk(wanted=MerchantWanted.ANSWERED),
            )

            assert _named(directory) == ["Capital One Credit Card", "Walmart"]

    def test_the_counts_are_of_the_ACCOUNT_and_not_of_what_was_drawn(
        self, app, db, seed_user, _two_of_each,
    ):
        """The firing control for the bar.

        Counting the RENDERED rows would make every filter read its own size,
        so the bar would say *You have not said 2* while showing 2 answered
        merchants -- and the one number the owner reads it for, how much is
        left, would be unavailable from the filter they are not on.
        """
        with app.app_context():
            directory = _directory(
                seed_user, DirectoryAsk(wanted=MerchantWanted.ANSWERED),
            )

            assert {
                count.wanted: count.count for count in directory.counts
            } == {
                MerchantWanted.ALL: 4,
                MerchantWanted.UNANSWERED: 2,
                MerchantWanted.ANSWERED: 2,
            }

    def test_the_two_narrow_filters_PARTITION_the_account(
        self, app, db, seed_user, _two_of_each,
    ):
        """Answered and unanswered sum to all, with nothing in both.

        A merchant is answered exactly when a rule row exists for it, so the
        two filters cannot overlap and cannot leave a gap -- and a count that
        did not sum would mean one of them was reading something else.
        """
        with app.app_context():
            counts = {
                count.wanted: count.count
                for count in _directory(seed_user).counts
            }

            assert (
                counts[MerchantWanted.UNANSWERED]
                + counts[MerchantWanted.ANSWERED]
                == counts[MerchantWanted.ALL]
            )

    def test_every_filter_has_a_label(self):
        """The totality control for the bar's own words.

        :attr:`MerchantWanted.label` reads a mapping, so a member added
        without one raises here rather than rendering an empty tab.
        """
        assert {member: member.label for member in MerchantWanted} == {
            MerchantWanted.ALL: "All",
            MerchantWanted.UNANSWERED: "You have not said",
            MerchantWanted.ANSWERED: "Answered",
        }


class TestTheSearch:
    """Finding one merchant by name, which is the only thing a person types."""

    @pytest.fixture()
    def _three_merchants(self, db, seed_user):
        """Stage three merchants with one shared substring."""
        a_merchant(seed_user, "Duke Energy")
        a_merchant(seed_user, "Dollar Tree")
        a_merchant(seed_user, "Kindle Unlimited")
        db.session.commit()

    def test_it_matches_a_substring_of_the_name(
        self, app, db, seed_user, _three_merchants,
    ):
        """A bank name is long, so the box matches part of it."""
        with app.app_context():
            assert _named(
                _directory(seed_user, DirectoryAsk(text="energy")),
            ) == ["Duke Energy"]

    def test_it_ignores_case_and_surrounding_space(
        self, app, db, seed_user, _three_merchants,
    ):
        """Bank names are shouted, typed names are not.

        The developer's own record holds ``TOWN OF CLAYTON PAYROLL`` beside
        ``Duke Energy``, so a case-sensitive box would answer nothing for half
        of what is on the page.
        """
        with app.app_context():
            assert _named(
                _directory(seed_user, DirectoryAsk(text="  DUKE  ")),
            ) == ["Duke Energy"]

    def test_it_narrows_INSIDE_a_filter_rather_than_replacing_it(
        self, app, db, seed_user,
    ):
        """The two narrowings compose; neither overrides the other.

        **The firing control**: a search that ignored the filter would return
        an answered merchant while the owner was looking at what they still
        owe, and pressing its Edit would leave the filter behind.
        """
        with app.app_context():
            a_rule(seed_user, "Duke Energy", always_ask=True)
            a_merchant(seed_user, "Duke Power")
            db.session.commit()

            assert _named(
                _directory(
                    seed_user,
                    DirectoryAsk(
                        wanted=MerchantWanted.UNANSWERED, text="duke",
                    ),
                ),
            ) == ["Duke Power"]

    def test_a_search_that_matches_nothing_draws_nothing(
        self, app, db, seed_user, _three_merchants,
    ):
        """An empty result is empty, and the counts still say what is there."""
        with app.app_context():
            directory = _directory(seed_user, DirectoryAsk(text="zzzz"))

            assert directory.entries == ()
            assert directory.matched_count == 0
            assert directory.total == 3


class TestTheOpenRow:
    """Which row's control is showing, and why it outranks the narrowing."""

    def test_the_named_merchant_is_the_only_open_row(
        self, app, db, seed_user,
    ):
        """One control on the page, which is what one-per-press means."""
        with app.app_context():
            a_merchant(seed_user, "Audible")
            target = a_merchant(seed_user, "Duke Energy")
            db.session.commit()

            directory = _directory(
                seed_user, DirectoryAsk(opened=target.id),
            )

            assert [
                entry.summary.merchant for entry in directory.entries
                if entry.is_open
            ] == ["Duke Energy"]
            assert directory.opened.summary.merchant == "Duke Energy"

    def test_the_open_row_survives_a_filter_that_excludes_it(
        self, app, db, seed_user,
    ):
        """**The firing control for the override.**

        An owner on *You have not said* opens a merchant to answer it.  If the
        filter decided membership alone, the row the answer is being given in
        would be the row the filter is about to remove -- so the control would
        vanish under the press that used it.
        """
        with app.app_context():
            answered = a_rule(seed_user, "Walmart", always_ask=True)
            db.session.commit()

            directory = _directory(
                seed_user,
                DirectoryAsk(
                    wanted=MerchantWanted.UNANSWERED,
                    opened=answered.merchant_id,
                ),
            )

            assert _named(directory) == ["Walmart"]
            assert directory.opened is not None

    def test_the_open_row_survives_a_search_that_excludes_it(
        self, app, db, seed_user,
    ):
        """The same override, against the other narrowing."""
        with app.app_context():
            target = a_merchant(seed_user, "Audible")
            a_merchant(seed_user, "Duke Energy")
            db.session.commit()

            directory = _directory(
                seed_user, DirectoryAsk(text="duke", opened=target.id),
            )

            assert set(_named(directory)) == {"Audible", "Duke Energy"}

    def test_a_merchant_this_account_has_never_seen_opens_nothing(
        self, app, db, seed_user,
    ):
        """What the route's 404 rests on.

        A well-formed id that names no merchant of this account has no row, so
        the directory reports that it opened nothing rather than silently
        rendering the page with no control -- which is what would let a stale
        or crafted URL look like a working page.
        """
        with app.app_context():
            a_merchant(seed_user, "Audible")
            db.session.commit()

            assert _directory(
                seed_user, DirectoryAsk(opened=9_999_999),
            ).opened is None

    def test_ANOTHER_accounts_merchant_opens_nothing(
        self, app, db, seed_user,
    ):
        """The ownership half of the same control.

        A merchant is per ACCOUNT, so a well-formed id belonging to a sibling
        account of the same owner is as unopenable here as a stranger's --
        which is what makes the route's 404 an answer about THIS account
        rather than about this user.
        """
        with app.app_context():
            sibling = Account(
                user_id=seed_user["user"].id,
                account_type_id=seed_user["account"].account_type_id,
                name="Second Checking",
            )
            db.session.add(sibling)
            db.session.flush()
            elsewhere = a_merchant(seed_user, "Audible", account=sibling)
            db.session.commit()

            assert _directory(
                seed_user, DirectoryAsk(opened=elsewhere.id),
            ).opened is None


class TestTheCeiling:
    """The bound on how many rows one render draws, and saying that it bound.

    **A truncated list that does not say so is a page claiming to be the whole
    record**, which is the disclosure ruling **bank_import:R-GX** already
    requires of the accepted register.
    """

    @pytest.fixture()
    def _more_merchants_than_the_ceiling(self, db, seed_user):
        """Stage one merchant past :data:`DIRECTORY_LIMIT`.

        Returns:
            How many were staged.
        """
        for ordinal in range(DIRECTORY_LIMIT + 1):
            a_merchant(seed_user, f"Merchant {ordinal:04d}")
        db.session.commit()
        return DIRECTORY_LIMIT + 1

    def test_it_draws_no_more_than_the_ceiling(
        self, app, db, seed_user, _more_merchants_than_the_ceiling,
    ):
        """The bound itself."""
        with app.app_context():
            assert len(_directory(seed_user).entries) == DIRECTORY_LIMIT

    def test_it_says_how_many_it_did_not_draw(
        self, app, db, seed_user, _more_merchants_than_the_ceiling,
    ):
        """The disclosure, which is what makes the bound honest.

        **Derived from the two numbers beside it** rather than counted again,
        so the footer cannot disagree with the list above it.
        """
        with app.app_context():
            directory = _directory(seed_user)

            assert directory.matched_count == (
                _more_merchants_than_the_ceiling
            )
            assert directory.withheld_count == (
                _more_merchants_than_the_ceiling - DIRECTORY_LIMIT
            )

    def test_the_ceiling_can_be_lifted(
        self, app, db, seed_user, _more_merchants_than_the_ceiling,
    ):
        """The escape hatch the footer links to."""
        with app.app_context():
            directory = _directory(seed_user, DirectoryAsk(limit=None))

            assert len(directory.entries) == (
                _more_merchants_than_the_ceiling
            )
            assert directory.withheld_count == 0

    def test_the_OPEN_row_survives_the_ceiling(
        self, app, db, seed_user, _more_merchants_than_the_ceiling,
    ):
        """**The axis no case varied**, and it produced a wrong 404.

        `DirectoryAsk.shows` exempts the open row from the filter and the
        search; nothing exempted it from the ceiling, and
        `MerchantDirectory.opened` scans the DRAWN rows -- so opening a
        merchant that sorts past the limit answered `None`, and the route
        refused it with the sentence it reserves for *this account has never
        seen it*.  Found on re-read and by three independent adversarial
        reviews, 2026-08-31.
        """
        with app.app_context():
            last = the_merchant_id(
                seed_user, f"Merchant {DIRECTORY_LIMIT:04d}",
            )

            directory = _directory(seed_user, DirectoryAsk(opened=last))

            assert directory.opened is not None
            assert directory.opened.summary.merchant_id == last
            # It is DRAWN, not merely resolvable: the page has to render the
            # control, and one extra row is what the ceiling gives up for it.
            assert last in {
                entry.summary.merchant_id for entry in directory.entries
            }

    def test_the_ceiling_still_binds_around_the_open_row(
        self, app, db, seed_user, _more_merchants_than_the_ceiling,
    ):
        """The pairing: keeping the open row must not lift the bound.

        Without this, "always draw the open row" could be satisfied by drawing
        everything -- which is the ceiling deleted rather than exempted.
        """
        with app.app_context():
            last = the_merchant_id(
                seed_user, f"Merchant {DIRECTORY_LIMIT:04d}",
            )

            directory = _directory(seed_user, DirectoryAsk(opened=last))

            assert len(directory.entries) == DIRECTORY_LIMIT + 1
            assert directory.withheld_count == 0

    def test_the_ceiling_counts_what_MATCHED_and_not_the_account(
        self, app, db, seed_user, _more_merchants_than_the_ceiling,
    ):
        """The bound falls on the narrowed list, which is what is drawn.

        **The firing control**: a ceiling applied before the search would draw
        a search result cut to rows the search never reached, so typing a name
        that matches one merchant past row 200 would find nothing.
        """
        with app.app_context():
            directory = _directory(
                seed_user, DirectoryAsk(text="Merchant 0200"),
            )

            assert _named(directory) == ["Merchant 0200"]
            assert directory.withheld_count == 0


class TestWhatTheRowInheritsFromTheSharedProducer:
    """The facts the control needs, which three surfaces read one producer for.

    :func:`~._section.merchant_summary` is that producer, and the directory
    composes it rather than restating it -- so a merchant the door would
    refuse a spending answer for says so HERE too, without this module knowing
    ruling **R-GJ** exists.
    """

    def test_a_merchant_that_pays_an_account_says_so_on_its_row(
        self, app, db, seed_user,
    ):
        """Ruling **R-GJ**, inherited rather than restated.

        A merchant a SOURCE files as a payment to an account the owner holds
        has two of its four answers refused by the door, and a control that
        did not say so would be the *chooser whose submission can never
        succeed* shape this package has closed five times.
        """
        with app.app_context():
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="-500.00",
                merchant="Capital One Mobile Pmt",
                source_category=_CARD_PAYMENT,
            )
            db.session.commit()

            entry = next(
                one for one in _directory(seed_user).entries
                if one.summary.merchant == "Capital One Mobile Pmt"
            )

            assert entry.summary.pays_an_account is True

    def test_the_option_list_is_the_one_the_door_checks_against(
        self, app, db, seed_user,
    ):
        """The control cannot offer what :func:`state_rules` would refuse.

        An INCOME template is not a place spending can be filed, so it is not
        an offerable answer -- and offering it here would be a chooser whose
        submission can never succeed.
        """
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_merchant(seed_user, "Walmart")
            db.session.commit()

            assert _directory(seed_user).templates == (
                (envelope.template_id, "Groceries"),
            )


class TestWhatTheDirectorySeesThatAPassCannot:
    """The merchants a review pass is structurally unable to reach.

    **RENAMED from `TestTheDerivationCosts`**, which named a COST and measured
    a VISIBILITY: its case asserted only that a pre-calendar merchant appears,
    so a `merchant_directory` that built a `ReviewScope` and threw it away
    passed.  The cost claim lives in the module's own docstring with the
    statement census behind it; what is testable here is the CONSEQUENCE --
    this page reaches merchants no pass can.
    """

    def test_a_merchant_the_pay_calendar_cannot_reach_is_still_listed(
        self, app, db, seed_user,
    ):
        """It builds on an account whose pay calendar reaches no line.

        **The firing control for the independence claim.**  ``review_set``
        narrows by the pay calendar (:class:`~._gaps.ReviewBounds`), so a
        merchant whose only line predates the calendar is invisible to a pass
        -- and this page still has to be able to ask about it.
        """
        with app.app_context():
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="-40.00",
                posted_on=date(2001, 1, 2), merchant="Speedway",
            )
            db.session.commit()

            assert "Speedway" in _named(_directory(seed_user))

    def test_a_render_after_a_WRITE_reflects_the_write(
        self, app, db, seed_user,
    ):
        """The derivation reads the state that survives, not a cached one.

        **This replaces `test_two_renders_of_one_state_agree`**, which compared
        two calls to ONE producer over unchanged state -- an equality whose two
        sides come from the same place, which this project's own lessons name
        as a gate that measures nothing.  No mutation in a fourteen-item
        program could kill it.

        What is worth asserting is the direction that can actually fail: a
        second render must SEE a write made between the two, which is what the
        POST's re-render depends on.
        """
        with app.app_context():
            a_merchant(seed_user, "Walmart")
            db.session.commit()

            before = _says(_directory(seed_user), "Walmart")
            a_rule(seed_user, "Walmart", always_ask=True)
            db.session.commit()
            after = _says(_directory(seed_user), "Walmart")

            assert before == NOT_SAID
            assert after == "Ask me every time"


class TestTheAskItself:
    """The value a route builds, and the two questions it answers."""

    def test_its_defaults_are_the_whole_account_under_the_ceiling(self):
        """A caller wanting every merchant states nothing."""
        asked = DirectoryAsk()

        assert asked.wanted is MerchantWanted.ALL
        assert asked.text == ""
        assert asked.opened is None
        assert asked.limit == DIRECTORY_LIMIT

    def test_the_needle_is_folded_once(self):
        """Folded on the ask rather than per row.

        62 comparisons cannot each fold it differently, and the value the box
        redisplays stays exactly what was typed.
        """
        asked = DirectoryAsk(text="  DUKE Energy  ")

        assert asked.needle == "duke energy"
        assert asked.text == "  DUKE Energy  "


class TestTheAmountsItDoesNotState:
    """What this page deliberately does NOT say, and why that is a control.

    **It MOVES NO MONEY and states no money.**  A figure on a page whose only
    act is a preference would invite reading it as a balance, and the sum of a
    merchant's BANK lines is not the sum of the owner's rows for it.
    """

    def test_no_row_carries_a_money_figure(self, app, db, seed_user):
        """The firing control: an amount added here fails this.

        **It reads the DATACLASS rather than a list of fields written here**,
        and the difference is the whole of it.  The first draft named three
        fields whose declared types can never be a ``Decimal``, so it could
        only ever pass -- and it was structurally blind to the one thing it
        exists to catch, which is a NEW field.  Measured by an adversarial
        mutation 2026-08-31: adding ``spent: Decimal`` to
        :class:`MerchantEntry` and populating it left the whole suite green.

        Walks the composed values too, because a figure would as readily be
        added to the activity as to the row.
        """
        with app.app_context():
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="-1234.56", merchant="Speedway",
            )
            db.session.commit()

            entry = next(
                one for one in _directory(seed_user).entries
                if one.summary.merchant == "Speedway"
            )

            carried = []
            for value in (entry, entry.activity):
                for field in fields(value):
                    carried.append(
                        (field.name, getattr(value, field.name)),
                    )

            assert [
                name for name, value in carried
                if isinstance(value, Decimal)
            ] == []
            # And the figure is not smuggled in as text, which is the other
            # way a money value reaches a row that states it holds none.
            assert "1234.56" not in entry.says
