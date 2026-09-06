"""The grid's door into what the BANK said, and the gate that keeps it honest.

The bank importer shipped reachable only from the account detail page, three
levels from the grid, behind a button named for a filing cabinet.  The
developer went looking for the feature in 2026-08 and did not find it, which
is the defect these tests lock closed.

Two properties matter more than the markup:

* **The door always renders** for an account that can hold statements, badge
  or no badge.  A control that appeared only once lines existed could not be
  found by someone who had never imported one -- the exact failure being fixed.
* **The door opens.**  Its href is followed here and asserted 200, because the
  grid's own account gate is WIDER than the statements page's: an owner with
  no checking account gets a Property or an IRA resolved as their grid
  account, and the statements page 404s those.
"""

import re
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, StatementSourceEnum
from app.extensions import db
from app.models.merchant import Merchant
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.user import UserSettings
from app.services import account_service
from app.utils.dates import display_today

# Matched by the CLASS, not by the whole attribute string: the door carries
# responsive utilities alongside ``grid-bank-door`` and a pattern pinned to the
# exact attribute would go quietly blind the next time one is added, reporting
# "no door" for a door that is there.
_DOOR = re.compile(rb'<a class="[^"]*\bgrid-bank-door\b[^"]*"[^>]*>')
_HREF = re.compile(rb'\bgrid-bank-door\b[^>]*?\shref="([^"]+)"')
_REVIEW_URL = re.compile(rb'data-review-url="([^"]+)"')
_BADGE = re.compile(rb'<span class="badge rounded-pill grid-bank-badge">(\d+)</span>')


def _a_recorded_line(seed_user, posted_on, account=None):
    """Record one bank line for an account, with its owning import.

    Args:
        seed_user: The seeded user bundle.
        posted_on: The day the bank posted it.
        account: The account; the seeded checking one by default.

    Returns:
        The COMMITTED :class:`BankStatementLine`.  Committed rather than
        flushed (plan step balance:X-i3): every caller goes on to issue a
        request, and a request holds its own transaction in which an
        uncommitted row does not exist.
    """
    target = account or seed_user["account"]
    statement = StatementImport(
        account_id=target.id,
        user_id=seed_user["user"].id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV,
        ),
        file_name="statement.csv",
        file_digest="d" * 64,
        period_start=posted_on,
        period_end=posted_on,
        line_count=1,
        recorded_count=1,
    )
    db.session.add(statement)
    db.session.flush()
    line = BankStatementLine(
        account_id=target.id,
        import_id=statement.id,
        posted_on=posted_on,
        amount=Decimal("-64.04"),
        description="POINT OF SALE DEBIT APPLE.COM/BILL",
        sequence_in_group=0,
    )
    db.session.add(line)
    db.session.commit()
    return line


def _tab_count(body, label):
    """Return the figure the Reconcile tab bar prints beside *label*.

    Args:
        body: The whitespace-collapsed page body.
        label: The tab's own label (``Tab.label``).

    Returns:
        The count, as a string.  Raises ``AssertionError`` naming the tab when
        the tab is absent, because a regex that returned ``None`` would make
        the caller's comparison fail with nothing said about why.
    """
    found = re.search(
        rf'{label}\s*<span class="rec-tab-count font-mono">(\d+)</span>',
        body,
    )
    assert found is not None, f"the {label} tab is not on the page"
    return found.group(1)


def _a_card_payment(seed_user, posted_on):
    """Record one line a source files as paying an account the owner holds.

    Plan step ``bank_import:X-gm``.  The class the grid badge and the
    Reconcile inbox used to disagree about: the pass holds it on the Transfers
    tab as a HOLDING state (ruling **R-HQ**) and does not count it as work.
    Staged here rather than reached for from the service builders, because
    this module builds its rows directly against the models.

    Args:
        seed_user: The seeded user bundle.
        posted_on: The day the bank posted it.

    Returns:
        The COMMITTED :class:`BankStatementLine`.
    """
    account = seed_user["account"]
    merchant = Merchant(account_id=account.id, name="Capital One Credit Card")
    db.session.add(merchant)
    statement = StatementImport(
        account_id=account.id,
        user_id=seed_user["user"].id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV,
        ),
        file_name="card.csv",
        file_digest="c" * 64,
        period_start=posted_on,
        period_end=posted_on,
        line_count=1,
        recorded_count=1,
    )
    db.session.add(statement)
    db.session.flush()
    line = BankStatementLine(
        account_id=account.id,
        import_id=statement.id,
        posted_on=posted_on,
        amount=Decimal("-793.23"),
        description="ACH DEBIT CAPITAL ONE CRCARDPMT",
        sequence_in_group=0,
        merchant_id=merchant.id,
        # **The SOURCE's own filing**, which is what ruling R-GJ reads --
        # ``_vocabulary.ACCOUNT_PAYMENT_CATEGORIES`` maps this exact string
        # for this source.  A merchant name alone parks nothing.
        source_category="Financial Services/Credit Card Payment",
    )
    db.session.add(line)
    db.session.commit()
    return line


class TestTheDoorIsOnTheGrid:
    """It renders, and it renders whether or not anything is waiting."""

    def test_the_grid_renders_the_door(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The whole point of the change: findable from the screen in use."""
        with app.app_context():
            response = auth_client.get("/grid")

            assert response.status_code == 200
            assert _DOOR.search(response.data) is not None
            assert b"Import statements" in response.data

    def test_it_renders_with_NOTHING_recorded(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """FIRING CONTROL: the state the developer was actually in.

        Zero imports, zero lines.  A door gated on ``awaiting`` being
        non-zero would be invisible here, which is precisely the bug: the
        feature cannot be discovered by someone who has never used it.
        """
        with app.app_context():
            response = auth_client.get("/grid")

            assert _DOOR.search(response.data) is not None
            assert _BADGE.search(response.data) is None

    def test_the_door_actually_OPENS(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Every call to action goes somewhere useful (design principle 4).

        Follows the rendered href rather than asserting a hardcoded path, so
        a door pointed at a page that refuses this account fails here instead
        of in the developer's browser.
        """
        with app.app_context():
            grid = auth_client.get("/grid")
            href = _HREF.search(grid.data).group(1).decode()

            assert auth_client.get(href).status_code == 200

    def test_the_palettes_review_url_also_opens(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The Ctrl+K "Review bank matches" action reads this attribute.

        It is a second destination the header does not visibly link, so
        nothing else would catch it rotting.
        """
        with app.app_context():
            grid = auth_client.get("/grid")
            url = _REVIEW_URL.search(grid.data).group(1).decode()

            assert auth_client.get(url).status_code == 200


class TestTheBadgeCountsWhatIsWaiting:
    """The figure, and its agreement with the screen it links to."""

    def test_a_recorded_line_puts_a_COUNT_on_the_door(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One unmatched line inside the calendar reads 1."""
        with app.app_context():
            _a_recorded_line(seed_user, display_today())

            badge = _BADGE.search(auth_client.get("/grid").data)

            assert badge is not None
            assert badge.group(1) == b"1"

    def test_a_line_BEFORE_the_calendar_opens_does_not_count(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The predicate that keeps the badge meaningful, at the route.

        130 of 361 lines on the developer's own export sit before his first
        payday and can never be matched.  Counting them would pin the badge
        permanently non-zero, and a badge that never clears stops being read.
        """
        with app.app_context():
            opens = seed_periods_today[0].start_date
            _a_recorded_line(seed_user, opens - timedelta(days=1))

            response = auth_client.get("/grid")

            assert _DOOR.search(response.data) is not None
            assert _BADGE.search(response.data) is None


class TestTheBadgeAndThePageItOPENSAgree:
    """Plan step ``bank_import:X-gm``, end to end through a browser's path.

    **The invariant asserted where an owner would meet it**: render the grid,
    read the number off the badge, FOLLOW the badge's own href, and count the
    cards the page it lands on renders.  Everything else about this step is
    graded on the service; this is the only case that goes through the
    rendered link, and it is the one a repointing mistake shows up in.

    It is why the badge's destination moved to the Reconcile page in the same
    change: the count became that page's own inbox membership, so a badge left
    pointing at the retiring queue would have read 18 over a page listing 27
    -- the disagreement the grid template's own comment forbids.
    """

    def test_the_door_opens_the_RECONCILE_page(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The destination itself, asserted rather than only followed.

        ``test_the_door_actually_OPENS`` follows the href and asserts 200,
        which a link to the retiring queue also satisfies.  This names WHICH
        page, because the count the badge carries is that page's and no
        other's.
        """
        with app.app_context():
            _a_recorded_line(seed_user, display_today())
            grid = auth_client.get("/grid")

            href = _HREF.search(grid.data).group(1).decode()
            review_url = _REVIEW_URL.search(grid.data).group(1).decode()

            assert href.endswith(
                f"/accounts/{seed_user['account'].id}/statements/reconcile",
            )
            # The command palette reads the attribute rather than the href,
            # so it is a second destination nothing else would catch rotting.
            assert review_url == href

    def test_the_number_on_the_door_is_the_number_on_the_page(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One ordinary line and one PARKED card payment, counted both ways.

        The parked line is what makes this case bite: it is the class the two
        producers used to disagree about, so a fixture without one would have
        passed on the tree this step replaced.  The badge must read 1, and the
        page it opens must show one inbox card and hold the card payment
        somewhere that is not the inbox.
        """
        with app.app_context():
            today = display_today()
            _a_recorded_line(seed_user, today)
            _a_card_payment(seed_user, today)
            grid = auth_client.get("/grid")

            badge = _BADGE.search(grid.data)
            href = _HREF.search(grid.data).group(1).decode()
            page = auth_client.get(href)

            assert badge is not None
            body = " ".join(page.data.decode().split())
            # The tab caption the page renders for its own inbox, which is
            # the figure the badge is claiming to be.  Read off the page
            # rather than compared to a literal, so the two are asserted
            # EQUAL rather than both asserted to be 1.
            assert badge.group(1).decode() == _tab_count(body, "To explain")
            assert badge.group(1) == b"1"
            # And the parked line is on the page, off the inbox -- so the two
            # agree by holding it back, not by both losing it.
            assert _tab_count(body, "Transfers") == "1"


class TestTheGateOnWhichAccountsGetADoor:
    """The grid's account gate is wider than the statements page's."""

    def _an_ira_as_the_grid_account(self, seed_user):
        """Make a Roth IRA this owner's grid account, and return it.

        ``resolve_grid_account`` accepts it at step 2: the saved default is
        refused only for an AMORTIZING kind, and an IRA is not one.

        Args:
            seed_user: The seeded user bundle.

        Returns:
            The COMMITTED IRA :class:`~app.models.account.Account`.  Committed
            rather than flushed (plan step balance:X-i3): both callers go on to
            issue a request, and the whole point of this fixture is that the
            REQUEST resolves the IRA as the grid account -- which it cannot do
            against a preference no transaction of its own can see.
        """
        ira = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=ref_cache.acct_type_id(AcctTypeEnum.ROTH_IRA),
                name="Roth IRA",
                anchor_balance=Decimal("0.00"),
            ),
        )
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.default_grid_account_id = ira.id
        db.session.commit()
        return ira

    def test_no_door_when_the_grid_account_cannot_hold_statements(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """FIRING CONTROL: gate the door on the grid's own rule and it 404s.

        ``is_cash_flow_account`` refuses only loans, so an IRA reaches the
        grid.  A door rendered off that predicate would link into a page
        that refuses the account.
        """
        with app.app_context():
            self._an_ira_as_the_grid_account(seed_user)

            response = auth_client.get("/grid")

            assert response.status_code == 200
            assert _DOOR.search(response.data) is None

    def test_and_the_statements_page_really_DOES_refuse_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The other half: proves the gate above is load-bearing, not cosmetic.

        Without this, a later change making the statements page serve every
        account would leave the test above passing while the door stayed
        hidden for no reason.
        """
        with app.app_context():
            ira = self._an_ira_as_the_grid_account(seed_user)

            assert auth_client.get(
                f"/accounts/{ira.id}/statements",
            ).status_code == 404
