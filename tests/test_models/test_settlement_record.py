"""The settlement RECORD: what a row asserts, and what it merely remembers.

Plan step **X-au-c3**: *a row is a PLAN -- ``estimated_amount`` priced by
``amount_source_id`` -- until its money moves, and a RECORD of what moved once it
has.*  Three columns carry it -- ``settled_on``, ``settled_amount``,
``settled_basis_id`` -- but they are NOT one fact with one lifetime, and
believing they were is the error this module now grades the correction of.
``settled_amount`` and ``settled_basis_id`` are WHAT MOVED; ``settled_on`` (with
``reconciled_by_id``) is the ASSERTION that it moved on a named day, and a
revert withdraws the assertion while KEEPING what moved.

**Since plan step ``balance:X-bi-4b-1`` (ruling R-BAL80) WHAT MOVED is read
off the row's ENTRIES** -- its one covering movement (``status_seam._covering``)
or its purchases -- by both tiers, ``row_valuation.settled_figure`` and
``posting_reads.settled_figure_clause``; the two columns are the covering
movement's stale cache, still WRITTEN by the seam through this interval (so
the CHECKs below still have a subject) and deleted at ``X-bi-4b-2``.  The
cases here that read a figure therefore lay the movement beside the columns
(:func:`~tests._test_helpers.cover_bare_settled_row`), and the case that
graded the reader's REFUSAL of a settled row recording nothing grades its
answer now: a settled row holding no entry is the ``$0.00`` record (ruling
R-BAL82), on both tiers.

Two CHECKs state the half of that which is expressible:
``ck_transactions_settled_amount_needs_basis`` (a stored figure names its
provenance) and ``ck_transactions_settle_day_needs_a_record`` (a row asserting a
settle DAY records what moved).  Both are IMPLICATIONS.  A draft of this step
made the second a BICONDITIONAL, ``ck_transactions_settlement_recorded``, so
that releasing the assertion had to destroy the figure -- and the full-edit
popover TELLS the user to revert in order to edit, so following the app's own
instruction deleted a number they had read off a bank statement.

**Every test here is a FIRING CONTROL** (``docs/plans/verification.md`` standard
4).  A test that merely asserted the constraints EXIST would pass against
constraints admitting everything, so each one below writes the state the rule is
supposed to refuse and asserts the refusal -- by CONSTRAINT NAME at the database
tier, which is the only tier that sees a writer bypassing the ORM, and by
exception at the write door for the one rule a CHECK cannot state.

**That one rule is why this module exists at all.**  ``purchases`` is the single
basis that stores NO figure: an envelope's amount is the sum of its own entries,
and a stored copy would need a reconciler to keep it in step with its children --
the shape ruling **R-FI** deletes.  Saying *"``purchases`` if and only if
``settled_amount IS NULL``"* in SQL requires the constraint to name a
``ref.settlement_bases`` id, which is the one thing this project's ref convention
keeps out of a schema.  So it is a CONSTRUCTOR invariant on
:class:`app.services.status_seam.Settlement` instead -- a settle door cannot
BUILD a malformed record to hand over, so no door can write one -- and a rule
enforced in one constructor with no test is a rule that will stop holding without
anyone noticing.

The shapes under test, and the real writer each stands for:

* **a record's figure with no basis** -- a door that writes what moved and
  forgets how it is known, which is the state every reader would then have to
  guess about;
* **a DAY with no basis** -- a settle written in two statements, whose
  intermediate state an autoflush would try to persist.  Its mirror, a BASIS
  with no day, is deliberately ACCEPTED and has its own case: that is the
  RETAINED state a revert leaves, and refusing it is what destroyed the user's
  figure;
* **the whole record together** -- the legitimate act, which must be ACCEPTED,
  because a constraint that refuses the correct write is worse than none;
* **a ``purchases`` record carrying a figure, and a storing basis carrying
  none** -- the constructor invariant, from both sides;
* **entering the settled band with no record at all** -- the seam's own refusal,
  which is what makes "a settled row states what moved" a property of the seam
  rather than a convention its callers keep.
"""

from decimal import Decimal

import pytest
import sqlalchemy
import sqlalchemy.exc

from app import ref_cache
from app.enums import (
    AmountSourceEnum,
    MovementFigureSourceEnum,
    SettlementBasisEnum,
    StatusEnum,
)
from app.extensions import db
from app.models.ref import TransactionType
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.services.posting_reads import settled_figure_clause
from app.services.row_valuation import settled_figure
from app.services.status_seam import Settlement, apply_status_change
from tests._test_helpers import (
    bare_expense_template,
    cover_bare_settled_row,
    load_migration_module,
    settle_day_columns,
)
from app.services.amount_ownership import declare_derived

_MIGRATION = load_migration_module("e4b8a71c0f36_settlement_record.py")


def _basis_id(basis):
    """Return one ``ref.settlement_bases`` id, named by its enum member."""
    return ref_cache.settlement_basis_id(basis)


def _make_transaction(seed_user, seed_periods, **overrides):
    """Return an UNFLUSHED Projected expense row, with *overrides* applied.

    Deliberately bare: these tests write the record's columns directly, because
    the door helpers exist precisely to make the refused states unreachable and a
    control that went through them would grade the helper instead of the
    constraint.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.
        **overrides: Column values to set or replace -- the three record columns
            and the status, which are what every test here varies.

    Returns:
        The unflushed :class:`~app.models.transaction.Transaction`.

    **BARE on purpose; its pricing link is a rule-less definition of the
    owner's** (plan step ``balance:X-bi-7d-1``).  The subject here is a
    CONSTRAINT of ``budget.transactions``, and a control that reached the row
    through a door would grade the door -- so the row is still constructed by
    hand.  What it stopped being is LINK-LESS: the family's cutover
    (``X-bi-7d-2``) re-cuts ``ck_transactions_one_pricing_link`` to ``= 1``,
    so every row staged here names its own definition
    (:func:`~tests._test_helpers.bare_expense_template`) and carries the
    paycheck's start as the day it is due and the occurrence it answers --
    the shape ``one_off.place_row_of`` writes -- unless the case states
    otherwise.  The link is never the subject.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    definition = bare_expense_template(
        db.session, seed_user, name="Settlement control definition",
    )
    fields = {
        "user_id": seed_periods[0].user_id,
        "template_id": definition.id,
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Settlement control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "estimated_amount": Decimal("300.00"),
        "due_date": seed_periods[0].start_date,
        "occurs_on": seed_periods[0].start_date,
    }
    fields.update(overrides)
    # **The settle DAY carries its basis unless the caller states one** (plan
    # step **X-az**).  These builders write bare columns on purpose -- a control
    # routed through a door would grade the door -- but a row is only bare on
    # the axis its test is ABOUT: a day with no basis violates
    # ``ck_*_settle_day_basis_pairing`` before it can reach the constraint the
    # test is grading, so the pair is completed here and a test that means to
    # break it says ``settled_day_basis_id`` outright.
    if "settled_day_basis_id" not in overrides:
        fields.update(settle_day_columns(fields.get("settled_on")))
    # **The amount-ownership pair is ONE attribute** (plan step X-au-k), so the
    # figure this builder splats becomes the row's OWNERSHIP at the last
    # moment -- after every line above that reads it as a column.
    fields["amount_ownership"] = AmountOwnership.own(fields.pop("estimated_amount"))
    return Transaction(**fields)


class TestTheRecordIsOneFactInThreeColumns:
    """The two settlement CHECKs, and the state they deliberately let through.

    **A figure and its provenance share a lifetime; the settle DAY does not --
    in ONE direction.**  ``settled_amount`` and ``settled_basis_id`` say what
    the bank took and how that is known: a fact about the ROW.  ``settled_on``
    and ``reconciled_by_id`` assert that it moved on a named day and a named
    statement showed it, and a revert withdraws exactly that.  A draft of plan
    step X-au-c3 paired the day with the basis as a BICONDITIONAL
    (``ck_transactions_settlement_recorded``), which welded the two lifetimes
    together and made every revert destroy the user's figure.

    What replaced it is the surviving IMPLICATION,
    ``ck_transactions_settle_day_needs_a_record`` -- a row asserting a settle DAY
    must record what moved, while a row recording what moved need not assert a
    day.  That admits the retained state below and refuses the row on which this
    app's two tiers disagree (developer, 2026-08-17).
    """

    def test_a_figure_with_no_basis_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A recorded figure whose basis nobody stated is refused.

        The writer this stands for is a door that says WHAT moved and not how
        the figure is known -- which is the overload ``actual_amount`` carried,
        where a reader had to infer "a human typed this" from the column being
        populated at all.

        The row carries NO settle day, which is what isolates the constraint
        under test: with one, it would break
        ``ck_transactions_settle_day_needs_a_record`` as well and PostgreSQL would
        name whichever it evaluated first, so the assertion below would be
        grading the evaluation order rather than the rule.
        """
        with app.app_context():
            db.session.add(_make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_amount=Decimal("300.00"),
                settled_basis_id=None,
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "ck_transactions_settled_amount_needs_basis" in str(exc.value)
            db.session.rollback()

    def test_a_settle_day_with_no_record_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """An assertion that names no figure is refused (the surviving half).

        The row this stands for is the one on which this app's two tiers
        DISAGREE: :func:`app.services.row_valuation.settled_figure` raises for a
        settled row recording nothing, while
        ``posting_reads.settled_figure_clause`` used to answer ``0`` for the
        same row through its entry sum's ``COALESCE`` -- and the SQL side is
        what writes the ledger.  A refusal on one tier and a zero on the other
        is money leaving a balance in silence, so the state is made unstorable
        rather than handled twice.

        Its mirror is
        :meth:`test_a_figure_with_no_settle_day_is_the_REVERTED_state` below:
        this constraint is an implication, not a pairing, and the reverse
        direction is exactly what retention needs.
        """
        with app.app_context():
            db.session.add(_make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
                settled_amount=None,
                settled_basis_id=None,
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "ck_transactions_settle_day_needs_a_record" in str(exc.value)
            db.session.rollback()

    def test_a_figure_with_no_settle_day_is_the_REVERTED_state(
        self, app, db, seed_user, seed_periods,
    ):
        """What the row keeps after a revert -- legal, and worth nothing.

        **A draft of this step REFUSED this state and that was the defect.**
        ``ck_transactions_settlement_recorded`` paired the day with the basis, so
        withdrawing the assertion had to destroy the figure -- and the full-edit
        popover instructs the user to revert in order to edit, so following the
        app's own advice deleted a number they had read off a statement.

        The row is Projected here, carrying what it recorded when it last
        settled.  Two assertions, and the second is the one that makes the first
        safe: the database accepts it, and no valuation counts it, because
        ``settled_figure`` asks the STATUS rather than the columns.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                settled_on=None,
                settled_amount=Decimal("300.00"),
                settled_basis_id=_basis_id(SettlementBasisEnum.CORRECTED),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.settled_amount == Decimal("300.00")
            assert settled_figure(txn) is None

    def test_a_SETTLED_row_holding_no_entry_records_ZERO_and_never_its_plan(
        self, app, db, seed_user, seed_periods,
    ):
        """The state every settled row was in before X-au-c3, read as ``$0.00``.

        Settled with no record and no entry: every reader used to fall back to
        the row's PLAN here, which is the silent substitution X-au-c3 removed
        -- a forecast published as a fact about money that has already moved.
        Through ``X-bi-4a`` the reader REFUSED this row (``AmountUnresolvable``,
        "records no settlement"); since plan step ``balance:X-bi-4b-1`` the
        record is the row's entries and a settled row holding none IS the
        ``$0.00`` record (ruling **R-BAL82**: a movement of nothing is not
        one, so a close of nothing has no entry), which is what both tiers
        answer.  What this case still grades is the substitution: the answer is
        ``0``, not the ``$300.00`` plan, whatever the columns say.

        **The row carries no settle DAY**, which is what leaves it storable
        under ``ck_transactions_settle_day_needs_a_record``; a settled row with
        no day is ``integrity_check`` DC-11's first arm, not this reader's.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=None,
                settled_amount=None,
                settled_basis_id=None,
            )
            db.session.add(txn)
            db.session.flush()

            assert settled_figure(txn) == Decimal("0")

    def test_the_whole_record_together_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The legitimate write, without which the controls above prove nothing.

        A constraint that refused the correct act would fail every settle in the
        app, and the three refusals above would still pass -- so the accepting
        case is what tells a working pairing from one that admits nothing.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
                settled_amount=Decimal("287.31"),
                settled_basis_id=_basis_id(SettlementBasisEnum.CORRECTED),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.id is not None
            # The figure is read off the covering movement the seam mirrors
            # beside these columns (plan step balance:X-bi-4b-1), so the
            # movement is laid as the seam lays it; the columns alone read 0.
            cover_bare_settled_row(
                db.session, txn, "300.00", submitted="287.31",
            )
            assert settled_figure(txn) == Decimal("287.31")
            db.session.rollback()

    def test_a_purchases_record_stores_no_figure_and_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """The NULL branch belongs to the one basis whose entries state the figure.

        ``ck_transactions_settled_amount_needs_basis`` is satisfied by a NULL
        figure, so the database admits this row -- and it must, because it is
        every envelope close in the app.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
                settled_amount=None,
                settled_basis_id=_basis_id(SettlementBasisEnum.PURCHASES),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.id is not None
            # No entries recorded, so its purchases sum to nothing -- which is
            # what its records SAY, rather than a missing answer (ruling R-FJ).
            assert settled_figure(txn) == Decimal("0")
            db.session.rollback()

    def test_an_unsettled_row_carrying_a_figure_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A figure records a settle, so a row whose money has not moved has none.

        This is the state migration ``e4b8a71c0f36`` PROMOTED five production
        rows out of, and the one the deleted "Actual" box on the create and
        full-edit forms could reach.

        **What is refused here is the BARE FIGURE, not the state**, and the
        distinction is exact: ``ck_transactions_settled_amount_needs_basis``
        refuses a stored figure whose provenance nobody stated, whatever the
        row's status.  A row carrying a figure AND a basis while unsettled is
        legal -- it is the RETAINED state, and
        :meth:`test_a_figure_with_no_settle_day_is_the_REVERTED_state` above
        writes exactly that and asserts the database accepts it.  What keeps a
        retained figure out of every balance is the STATUS
        (``row_valuation.settled_figure``), not the schema.  (An earlier draft
        of this docstring said "a basis needs a day", which is the implication
        backwards and is the very pairing this step repealed.)
        """
        with app.app_context():
            db.session.add(_make_transaction(
                seed_user, seed_periods,
                settled_amount=Decimal("120.00"),
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "ck_transactions_settled_amount_needs_basis" in str(exc.value)
            db.session.rollback()


class TestPurchasesIffNoStoredFigure:
    """The one rule a CHECK cannot state, enforced in ``Settlement``'s constructor.

    Saying it in SQL requires naming a ``ref.settlement_bases`` id, which the
    project's ref convention keeps out of a schema -- so it is a write-door
    invariant, and these are its negative controls.  A settle door builds a
    ``Settlement`` to hand the seam, so a malformed record cannot be constructed
    and therefore cannot be written.

    **The record's stated field is the figure's SOURCE since plan step
    X-bi-3e-1** (ruling **R-BAL69**), and the basis is derived from it; so the
    rule reads *a figure names its writer, and a record with no figure names
    none*.  The two refusals below are the two halves of that biconditional,
    and the mapping test is what makes ``basis`` a stored answer's projection
    rather than a second stated fact.
    """

    def test_a_figure_stating_no_writer_is_refused(self, app):
        """A figure with nobody said to have written it is refused.

        The writer this stands for is a settle that "helpfully" caches the entry
        sum on the parent -- which is exactly what ``settle_from_entries`` used
        to do, and what needed ``entry_service`` to re-derive the column on every
        entry change afterwards: a ``purchases`` record carrying a figure.
        """
        with app.app_context():
            with pytest.raises(ValueError, match="must say who wrote it"):
                Settlement(amount=Decimal("48.98"), source=None)

    def test_a_writer_with_no_figure_is_refused(self, app):
        """A record naming a writer and storing no figure is refused.

        The mirror, and the state :func:`app.services.row_valuation.settled_figure`
        REFUSES to read: answering ``None`` there would send the caller to the
        row's PLAN, which is the fallback this whole step removes.  Every
        source member is refused, the settle's own ``resolved`` included.
        """
        with app.app_context():
            for source in MovementFigureSourceEnum:
                with pytest.raises(ValueError, match="stores no figure"):
                    Settlement(amount=None, source=source)

    def test_both_legitimate_shapes_construct(self, app):
        """The accepting cases, without which the two refusals prove nothing."""
        with app.app_context():
            stored = Settlement(
                amount=Decimal("48.98"),
                source=MovementFigureSourceEnum.RESOLVED,
            )
            assert stored.amount == Decimal("48.98")

            from_entries = Settlement(amount=None, source=None)
            assert from_entries.amount is None

    @pytest.mark.parametrize(
        ("source", "basis"),
        [
            (None, SettlementBasisEnum.PURCHASES),
            (MovementFigureSourceEnum.RESOLVED, SettlementBasisEnum.DERIVED),
            (MovementFigureSourceEnum.TYPED, SettlementBasisEnum.CORRECTED),
            (MovementFigureSourceEnum.OBSERVED, SettlementBasisEnum.CORRECTED),
        ],
    )
    def test_the_basis_is_derived_from_the_source(self, app, source, basis):
        """``basis`` is a function of ``source``, stated once (ruling R-BAL69).

        The settle's own pricing is ``derived``; a figure a person or the
        bank stated is ``corrected``; no figure is ``purchases``.  The row's
        ``settled_basis_id`` is this answer's projection through the interval
        ``X-bi-4`` closes, and it cannot disagree with the movement's
        ``figure_source_id`` because one value carries both.
        """
        with app.app_context():
            record = Settlement(
                amount=None if source is None else Decimal("48.98"),
                source=source,
            )
            assert record.basis is basis

    @pytest.mark.parametrize(
        ("source", "stated"),
        [
            (None, False),
            (MovementFigureSourceEnum.RESOLVED, False),
            (MovementFigureSourceEnum.TYPED, True),
            (MovementFigureSourceEnum.OBSERVED, True),
        ],
    )
    def test_stated_is_a_person_or_the_bank_and_never_the_settle_itself(
        self, app, source, stated,
    ):
        """``stated`` is what a re-settle HONOURS, stated once (X-bi-4b-1).

        ``Settlement.from_settle`` keeps a retained record and
        ``status_seam.honoured_correction`` publishes it exactly when somebody
        stated the figure -- a person (``typed``) or the bank's line
        (``observed``); the settle's own ``resolved`` pricing is re-derived,
        and a ``purchases`` record states nothing.  It read ``basis is
        CORRECTED`` off the row's column through ``X-bi-4a``.
        """
        with app.app_context():
            record = Settlement(
                amount=None if source is None else Decimal("48.98"),
                source=source,
            )
            assert record.stated is stated

    # ``test_the_reader_refuses_a_record_written_around_the_rule`` -- a
    # ``derived`` record with no stored figure, which ``settled_figure`` refused
    # rather than reading the plan -- is DELETED with its subject at plan step
    # ``balance:X-bi-4b-1``: the reader no longer reads the row's columns, so
    # a record "written around the rule" in them is invisible to it, and what
    # it reads (the entries) has no malformed shape to refuse.  The
    # constructor's own two refusals above are the whole of the guard now.


class TestTheSeamRefusesAnUnrecordedSettle:
    """Entering the settled band with no record is a programming error."""

    def test_entering_the_band_with_no_settlement_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The seam demands a record, so no door can settle a row silently.

        A ``ValueError`` rather than a ``ValidationError``: no form can express
        it, so it is a mistake at the call site and not a user's.  Without this
        the door would write the status alone and the row would land dated with
        no figure -- which the pairing refuses at flush, and which before this
        step was worse than a refusal, because the reader fell back to the plan
        and published a forecast as a fact.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.add(txn)
            db.session.flush()
            status_before = txn.status_id

            with pytest.raises(ValueError, match="no settlement record"):
                apply_status_change(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                )

            # A refused call leaves the row untouched, which is the ordering the
            # seam's own refusals are placed for.
            assert txn.status_id == status_before
            assert txn.settled_on is None
            db.session.rollback()

    def test_a_record_offered_for_an_unsettled_status_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The other direction: a row records what moved only while it is settled.

        A ``ValidationError`` here rather than a ``ValueError``, because it is
        the twin of the settle-day refusal and reaches the route as a 400.
        """
        with app.app_context():
            from app.exceptions import ValidationError  # noqa: PLC0415

            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
                settled_amount=Decimal("300.00"),
                settled_basis_id=_basis_id(SettlementBasisEnum.DERIVED),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(ValidationError, match="not a settled status"):
                apply_status_change(
                    txn, ref_cache.status_id(StatusEnum.PROJECTED),
                    settlement=Settlement(
                        amount=Decimal("300.00"),
                        source=MovementFigureSourceEnum.RESOLVED,
                    ),
                )
            db.session.rollback()


def _as_pre_upgrade_schema():
    """Rename ``settled_amount`` back, so the guard sees the shape it runs on.

    :func:`refuse_settled_rows_without_a_plan` is a PRE-flight: :func:`upgrade`
    calls it before any DDL, so its SQL names ``actual_amount`` -- a column that
    does not exist once the revision has run, which is the state every test
    database is in.  Driving the real guard therefore means restoring the real
    column name first.

    ``ALTER TABLE ... RENAME COLUMN`` is transactional in PostgreSQL and
    PostgreSQL rewrites the dependent CHECK expressions with it, so the test's
    own rollback undoes this completely.  Called LAST in each test, after every
    ORM flush, because the mapper still expects the post-upgrade name.

    Naming the alternative rather than leaving it implicit: a copy of the
    guard's SQL rewritten against ``settled_amount`` would test the copy, and a
    guard nobody has seen work is exactly what the migration's docstring says
    this class exists to prevent.
    """
    db.session.execute(sqlalchemy.text(
        "ALTER TABLE budget.transactions "
        "RENAME COLUMN settled_amount TO actual_amount"
    ))


class TestTheUpgradeRefusesASettledRowWithNoFigure:
    """Migration ``e4b8a71c0f36``'s only non-DDL logic, driven directly.

    ``refuse_settled_rows_without_a_plan`` is module-level for exactly this
    reason, and the migration's own docstring says so -- naming the two previous
    amount-model revisions as the precedent (``a9d3c15e7f42``, ``b3f7c2a9d514``)
    and the rule as *"a guard nothing exercises is a guard nobody has seen
    work"*.  Nothing exercised it until this class, which is the citation shape
    finding **N-30** is about: a justification that names a control nobody
    wrote.

    Definition of Done item 7 asks for both directions.  The DDL halves are
    exercised on every test-template rebuild -- ``scripts/build_test_template.py``
    replays the whole Alembic chain rather than calling ``create_all`` -- and the
    upgrade / downgrade round trip was run against a clone of production
    (1,012 transactions, 166 settled) before this leaf shipped: every settled row
    landed in exactly one backfill arm, and every row's effective figure was
    unchanged in both directions.  What no rebuild can reach is the refusal,
    because the chain never leaves a settled row with no figure at all -- that
    state only arrives once a per-kind cutover (plan steps X-au-d..X-au-i) has
    emptied ``estimated_amount``.
    """

    def test_it_passes_when_every_settled_row_has_a_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The state the chain leaves, so a real upgrade is not refused.

        Returns ``None`` rather than raising: the assertion is the ABSENCE of a
        refusal, which is why the negative control below is what gives it
        meaning.  A guard that refused everything would pass a test that only
        checked the raising case.
        """
        with app.app_context():
            db.session.add(_make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
                settled_amount=Decimal("120.00"),
                settled_basis_id=_basis_id(SettlementBasisEnum.DERIVED),
            ))
            db.session.flush()
            _as_pre_upgrade_schema()

            assert _MIGRATION.refuse_settled_rows_without_a_plan(
                db.session.connection(),
            ) is None

    def test_it_refuses_a_settled_row_carrying_neither_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The arm that keeps "zero such rows exist" true rather than assumed.

        A settled row whose ``estimated_amount`` is already NULL has a DERIVED
        plan, which means a per-kind cutover ran before this revision.  There is
        then no figure here to record, and the producer that would compute one
        lives in ``app/`` -- which a migration must not import.  Refusing names
        the rows; inventing a number nobody computed is the defect the whole
        step removes.

        The message must NAME the offending row, because an operator hitting
        this mid-deploy has only the message to work from.

        **The row carries no settle DAY, and that is a property of the harness
        rather than of the state being tested.**  The guard's SELECT reads three
        columns -- ``status_id``, ``actual_amount``, ``estimated_amount`` -- and
        never the day, so a day would add nothing it grades.  What it WOULD do
        is break ``ck_transactions_settle_day_needs_a_record``, a constraint this
        very revision creates and whose absence is the pre-upgrade shape this
        test is standing in for: the ORM flush below runs against the migrated
        test database, where it already exists, so the fixture would be refused
        before the guard ever saw it.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=None,
                settled_amount=None,
                settled_basis_id=None,
            )
            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.add(txn)
            db.session.flush()
            txn_id = txn.id
            _as_pre_upgrade_schema()

            with pytest.raises(RuntimeError, match=str(txn_id)):
                _MIGRATION.refuse_settled_rows_without_a_plan(
                    db.session.connection(),
                )


class TestTheSQLTierReadsTheSameHomeAsPython:
    """``posting_reads.settled_figure_clause`` and ``settled_figure`` agree.

    **Measured 2026-08-17 by an adversarial mutation pass**: reverting the SQL
    expression of the time to the ``COALESCE(settled_amount, Sigma(entries))``
    it replaced left the entire suite green -- so its stated reason to exist
    had no firing control at all, and this class became one.  Through
    ``X-bi-4a`` the two tiers disagreed on exactly ONE row, a settled row
    recording NOTHING (Python refused, SQL had to be made to answer ``NULL``
    rather than ``0``), and that difference was money:
    ``posting_service._settle_effective`` is a LOOKUP, not a fold -- it
    refuses a ``None`` and posts nothing, where a ``0`` is a figure it would
    post.

    **Since plan step ``balance:X-bi-4b-1`` both tiers read the row's ENTRIES**
    (ruling **R-BAL80**), and a settled row holding none is the ``$0.00``
    record on both (ruling **R-BAL82**); the row the two disagreed about has
    one answer.  The cases grade that agreement from both sides: the empty
    row, and a row whose covering movement carries its figure while its
    columns say something the readers must NOT be reading.
    """

    def test_a_settled_row_holding_no_entry_answers_ZERO_on_both_tiers(
        self, app, db, seed_user, seed_periods,
    ):
        """The one row the two expressions used to disagree about.

        It carries no settle DAY, which is what makes it storable at all:
        ``ck_transactions_settle_day_needs_a_record`` refuses the dated half of
        this state.  Both tiers answer the ``$0.00`` record.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=None,
                settled_amount=None,
                settled_basis_id=None,
            )
            db.session.add(txn)
            db.session.flush()

            answered = (
                db.session.query(settled_figure_clause())
                .filter(Transaction.id == txn.id)
                .scalar()
            )
            assert answered == Decimal("0")
            assert settled_figure(txn) == Decimal("0")

    def test_both_tiers_read_the_movement_and_neither_reads_the_columns(
        self, app, db, seed_user, seed_periods,
    ):
        """A row whose columns and movement DISAGREE is read off the movement.

        The seam writes both from one value, so the state below is written
        around it -- and that is the point: a reader still dispatching on
        ``settled_basis_id`` or reading ``settled_amount`` would answer
        ``$300.00`` here, where the covering movement (the record's one home
        after ``X-bi-4b-2``) carries ``$287.31``.  Both tiers must answer the
        movement.  An expression that answered ``0`` for everything would pass
        the case above and fail this one.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
                settled_amount=Decimal("300.00"),
                settled_basis_id=_basis_id(SettlementBasisEnum.DERIVED),
            )
            db.session.add(txn)
            db.session.flush()
            cover_bare_settled_row(
                db.session, txn, "300.00", submitted="287.31",
            )

            answered = (
                db.session.query(settled_figure_clause())
                .filter(Transaction.id == txn.id)
                .scalar()
            )
            assert answered == Decimal("287.31")
            assert settled_figure(txn) == Decimal("287.31")
