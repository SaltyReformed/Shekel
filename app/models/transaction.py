"""
Shekel Budget App -- Transaction Model (budget schema)

Each row in the budget grid is a Transaction: an income or expense
assigned to a specific pay period and scenario, with estimated and
actual amounts plus a status workflow.
"""

from datetime import date

from sqlalchemy.ext.hybrid import hybrid_property

from app.extensions import db
from app import ref_cache
from app.enums import TxnTypeEnum
from app.models.amount_ownership import from_columns
from app.models.mixins import (
    OptimisticLockMixin,
    SettleDatedMixin,
    SoftDeleteOverridableMixin,
    TimestampMixin,
    TrackingVisibilityMixin,
)


class Transaction(
    OptimisticLockMixin,
    SettleDatedMixin,
    SoftDeleteOverridableMixin,
    TrackingVisibilityMixin,
    TimestampMixin,
    db.Model,
):
    """A single income or expense entry within a pay period.

    **A ROW HAS AN OWNER, AND IT IS A COLUMN** (plan step
    ``pay_calendar:C13-a``, ruling **R-PC32**).  ``user_id`` is who this row
    belongs to, and two COMPOSITE foreign keys hold it equal to BOTH of the
    row's parents at once -- ``fk_transactions_owner_account`` against
    ``budget.accounts (id, user_id)`` and ``fk_transactions_owner_period``
    against ``budget.pay_periods (id, user_id)``.  A row whose account and
    whose paycheck belong to different people is therefore UNCONSTRUCTIBLE
    rather than merely unwritten.

    **It is a CO-LOCATED KEY, which is the distinction CLAUDE.md rule 14 turns
    on.**  ``user_id`` IS functionally determined by ``pay_period_id`` once
    ``fk_transactions_owner_period`` exists, so this is a stored copy of a
    derivable value and the rule has to be answered rather than waved at.  What
    answers it is that the derivation and the copy CANNOT DISAGREE: the key is
    the reconciler, it runs on every write, and it lives in the database.  A
    stored copy the rule forbids is one whose source can move underneath it;
    this one's cannot, because moving it is the write the key refuses.

    **The maintenance contract does not vanish -- it MOVES.**  Nine writers in
    ``app/`` now state the owner, each reading it off a different object, so
    the honest claim is not "no writer keeps two homes in step" but "the
    readers stopped having to".  Before this column, nothing required a
    transaction's account and its paycheck to have the same owner, so every
    door that refuses a foreign row stated the relationship BY HAND: nineteen
    such comparisons in ``app/`` (finding **P75**), any one of which could
    forget.  Now a writer that gets it wrong is an ``IntegrityError`` at flush
    and no reader has to know -- which is rule 14's own stated preference, *an
    invariant that cannot be violated because there is nothing to violate is
    worth more than one a reconciler enforces.*

    Measured 2026-08-27 and re-measured 2026-09-02 before the constraint was
    written: **0 mismatched rows** of 1,028 on production and 1,057 on the dev
    clone.  *Unconstructible by every writer that reaches the table as the
    application*: ``SET session_replication_role = 'replica'`` suppresses
    referential triggers and a superuser can still force the row, which
    ``tests/test_scripts/test_integrity_check.py`` does on purpose.

    **It is the OWNER, never the AUTHOR**, and the child table next door uses
    the same column name for the other fact:
    :attr:`app.models.transaction_entry.TransactionEntry.user_id` is *the user
    who created the entry (owner or companion)*.  A companion acting on this
    row does not become its owner, so when a door asks
    ``txn.user_id == current_user.id`` it gets *is this the owner*, which is
    what ``routes/entries.py`` asks.

    **The doors read this column since plan step ``pay_calendar:C13-b``**,
    which retired the NINETEEN hand-written ownership comparisons finding
    **P75** counted: ELEVEN walked ``X.pay_period.user_id`` and became one
    equality here, and the EIGHT that refetched a SUBMITTED period id went to
    the owner's derived calendar instead -- this key answers what may be
    STORED, and a submitted id is a question about INPUT (developer 2026-09-03).

    **A transaction carries TWO clocks, and the second one is not decoration.**
    ``pay_period_id`` (with ``due_date``) is the BUDGET clock -- which column
    the user planned this in -- and :attr:`settled_on` is the CASH clock, the
    civil day the money actually moved.  They are the same period for most rows
    and different for 21 of the 156 settled rows on the 2026-08-03 production
    clone, and that difference IS the grid's timing row: a row settled outside
    its own pay period moves the balance in one column while its income /
    expense subtotal sits in another.  The same split is stated on
    ``TransactionEntry`` (``purchased_on`` beside its own ``settled_on``), on
    ``cash_ledger.CashSourceFact``, and on a loan payment (``due_date`` beside
    its pay period).

    **``settled_on`` REPLACED a ``paid_at`` instant at plan step X-f1** (ruling
    R-EC, migration ``a3f7c8e21b64``).  That column stored ``db.func.now()`` at
    the moment the user clicked, and eleven read sites across eight modules
    converted it to a display-timezone civil day to get the fact they wanted --
    while nothing read the instant itself and nothing ordered two of them.  On
    real data 65.2% of settled Checking rows shared a click-minute with another
    row, so its precision described a bookkeeping session rather than money.
    Storing the day directly leaves one clock, converted once at the write door.

    **A row carries THREE facts about money, and they have three different
    lifetimes** (plan step **X-au-c3**).  No column belongs to two of them:

    ====================  ====================  =========================
    the PLAN              WHAT MOVED            the ASSERTION
    ====================  ====================  =========================
    ``estimated_amount``  ``settled_amount``    ``settled_on``
    ``amount_source_id``  ``settled_basis_id``  ``settled_day_basis_id``
                                                ``reconciled_by_id``
    ====================  ====================  =========================

    * the **PLAN** is what the row is forecast to cost.  It exists from creation
      and no settle path writes it;
    * **WHAT MOVED** is what the bank actually took, and how that figure is
      known.  It comes into existence at a settle and is a fact about the ROW
      from then on;
    * the **ASSERTION** is "this money moved, on this day, that is what kind of
      day it is, and that statement showed it".  A revert withdraws all of it.
      ``settled_day_basis_id`` joined it at plan step **X-az**: the day and the
      KIND of day are one fact, so they share the assertion's lifetime and are
      released together (finding **N-332**).

    **A revert releases the ASSERTION and keeps WHAT MOVED**, and that asymmetry
    is the model's centre.  A first version of this step made all three one
    "settlement record" under a CHECK pairing the day with the basis, so
    withdrawing the assertion destroyed the figure -- and the full-edit popover
    TELLS the user to revert in order to edit, so the app's own instruction
    deleted a number they had read off a statement.  Splitting the lifetimes
    removes the constraint, the release and the data loss at once, and it is how
    every reconciliation system this was checked against behaves: un-clearing a
    transaction never touches its amount (developer, 2026-08-17).

    **What a CHECK cannot express is the tie to the STATUS**: that predicate is
    ``ref.statuses.is_settled`` and a constraint cannot join, while hardcoding
    the settled ids would be a magic number that breaks when a status is added
    or removed.  So ``status_seam.apply_status_change`` remains the ONE door that
    writes ``status_id``, and it writes the assertion and what moved in the same
    call.  The reading half is ``row_valuation.settled_figure``, which asks the
    STATUS -- not the columns -- whether this row is worth what it recorded or
    what it plans; a settled row that records nothing must FAIL LOUD there rather
    than fall back, because dropping such a row from a fold is silent money loss.

    **``settled_on`` has no bounds, and that is deliberate.**  A settle
    legitimately falls outside its budget period on EITHER side (measured on the
    2026-08-03 production clone: 11 of 156 settled rows before their period's
    start, 10 after its end), so neither bound exists; a "not in
    the future" rule is not expressible in a CHECK (it is not immutable) and
    lives at the write door instead, exactly as ruling R-M's purchase-date guard
    does for an entry.

    Optimistic locking: ``version_id`` is the SQLAlchemy
    ``version_id_col`` for the row.  Every ORM-emitted UPDATE or
    DELETE is automatically narrowed to ``WHERE id = ? AND
    version_id = ?`` and the stored value is incremented in the same
    statement.  Two concurrent requests that both load the row at
    version N race for the bump; the loser's WHERE matches zero
    rows, SQLAlchemy raises :class:`sqlalchemy.orm.exc.StaleDataError`,
    and the calling route returns 409 (HTMX endpoints) or
    flash + redirect (non-HTMX form posts).  See commit C-18 of the
    2026-04-15 security remediation plan.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        db.Index(
            "idx_transactions_period_scenario",
            "pay_period_id", "scenario_id",
        ),
        db.Index("idx_transactions_template", "template_id"),
        db.Index("idx_transactions_credit_payback", "credit_payback_for_id"),
        # At most one *active* CC Payback row per source transaction.
        # Backstops the SELECT-FOR-UPDATE serialisation in
        # ``credit_workflow.mark_as_credit`` and
        # ``entry_credit_workflow.sync_entry_payback`` so any future
        # caller that bypasses the service layer fails loudly with an
        # IntegrityError on this index instead of silently doubling the
        # user's projected debt.  ``is_deleted = FALSE`` keeps soft-
        # deleted paybacks out of the index so a re-mark of the same
        # source row after a soft-delete remains legal.  See commit C-19
        # of the 2026-04-15 security remediation plan.
        db.Index(
            "uq_transactions_credit_payback_unique",
            "credit_payback_for_id",
            unique=True,
            postgresql_where=db.text(
                "credit_payback_for_id IS NOT NULL "
                "AND is_deleted = FALSE"
            ),
        ),
        db.Index("idx_transactions_account", "account_id"),
        db.Index(
            "idx_transactions_transfer",
            "transfer_id",
            postgresql_where=db.text("transfer_id IS NOT NULL"),
        ),
        # At most one *active* expense shadow and one *active* income
        # shadow per transfer.  Database-level backstop for the
        # service-layer invariant (CLAUDE.md "Transfer Invariants" #1)
        # that every transfer has exactly two linked shadow
        # transactions.  Without this index a defective caller -- or
        # a hypothetical script that bypasses ``transfer_service`` --
        # could insert a third shadow row and silently double-charge
        # the user's projection.  ``is_deleted = FALSE`` keeps soft-
        # deleted shadows out of the index so the soft-delete +
        # restore round trip remains legal, mirroring the predicate
        # on ``uq_transactions_credit_payback_unique``.  Audit
        # reference: F-046 / commit C-21 of the 2026-04-15 security
        # remediation plan.
        db.Index(
            "uq_transactions_transfer_type_active",
            "transfer_id", "transaction_type_id",
            unique=True,
            postgresql_where=db.text(
                "transfer_id IS NOT NULL "
                "AND is_deleted = FALSE"
            ),
        ),
        db.Index(
            "idx_transactions_due_date",
            "due_date",
            postgresql_where=db.text("due_date IS NOT NULL"),
        ),
        # WHAT A GENERATED ROW IS, stated as storage (plan step **R17**).  A
        # row answers ONE occurrence of its template's cadence; the pay period
        # is where that occurrence's money lands, which is a DERIVED placement
        # and not the row's identity -- the owner may move it, and moving it is
        # exactly what ledger row **D57** was.  Keyed on the paycheck, this
        # index made a moved row vacate its own occurrence, so the next
        # generate pass answered it a second time: 8 rows, $1,482.93, measured
        # on a production clone 2026-08-28.
        #
        # TWO indexes rather than one, because ``occurs_on`` is NULLABLE and
        # PostgreSQL treats NULLs as distinct -- a single index over it would
        # let a template hold unlimited undated rows in one paycheck, which is
        # the "one row per template per paycheck" rule this table has always
        # had.  A row that answers NO occurrence therefore keeps the OLD key,
        # and that split is the same rule
        # ``_recurrence_common.OccurrenceClaims`` applies in Python: identity is
        # the occurrence where it is known, and the paycheck where it is not.
        # Letting an undated row claim nothing was measured at 41 phantom
        # transfers / $20,500 at the unarchive door.
        #
        # **The two predicates DIVERGED at plan step X-au-h** (ruling
        # **R-JR**): this one dropped ``is_override = FALSE`` and the undated
        # one below kept it.  The exemption guarded a PAYCHECK-key collision,
        # R17 re-keyed this index onto ``occurs_on`` which a move never
        # changes, and X-au-h then raised the flag on a RE-PRICE -- so keeping
        # it would have dropped merely-re-priced rows out of a guarantee they
        # never used to lose.  Migration ``e7c3a1f9b482`` carries the full
        # argument, the measurement and why the undated index differs.
        db.Index(
            "idx_transactions_template_scenario_occurrence",
            "template_id", "scenario_id", "occurs_on",
            unique=True,
            postgresql_where=db.text(
                "template_id IS NOT NULL "
                "AND occurs_on IS NOT NULL "
                "AND is_deleted = FALSE"
            ),
        ),
        db.Index(
            "idx_transactions_template_scenario_undated",
            "template_id", "scenario_id", "pay_period_id",
            unique=True,
            postgresql_where=db.text(
                "template_id IS NOT NULL "
                "AND occurs_on IS NULL "
                "AND is_deleted = FALSE "
                "AND is_override = FALSE"
            ),
        ),
        db.CheckConstraint(
            "estimated_amount >= 0",
            name="ck_transactions_estimated_amount",
        ),
        db.CheckConstraint(
            "settled_amount IS NULL OR settled_amount >= 0",
            name="ck_transactions_settled_amount",
        ),
        # A FIGURE CARRIES ITS PROVENANCE (plan step **X-au-c3**): a stored
        # ``settled_amount`` always says HOW it is known.  The converse is
        # deliberately NOT asserted here, and the gap is exactly one basis:
        # ``purchases`` stores no figure, because the row's own entries state it
        # and a stored copy would need a reconciler
        # (:class:`app.enums.SettlementBasisEnum`).  Saying "``purchases`` if and
        # only if ``settled_amount IS NULL``" needs the constraint to name a ref
        # id, which is the one thing the project's ref convention forbids putting
        # in a schema -- so that half is a write-door rule with its own negative
        # control (``tests/test_models/test_settlement_record.py``) rather than a
        # constraint, and saying which is which is the point: a safety that is
        # not a predicate is not a safety.
        db.CheckConstraint(
            "settled_amount IS NULL OR settled_basis_id IS NOT NULL",
            name="ck_transactions_settled_amount_needs_basis",
        ),
        # AN ASSERTION NAMES WHAT IT ASSERTS (plan step **X-au-c3**): a row
        # carrying the day its money moved always records WHAT moved.  It is the
        # half of the settlement pairing a CHECK can state, and it is stated
        # here because the two tiers that answer "what did this row settle at"
        # disagree without it -- :func:`app.services.row_valuation.settled_figure`
        # RAISES for a settled row recording nothing, while
        # ``posting_reads.settled_figure_clause`` answers ``0`` for the same row
        # through its entry sum's ``COALESCE``, and the SQL side is what writes
        # the ledger.  A disagreement between a refusal and a zero is money
        # leaving a balance in silence; a constraint is what makes the row
        # neither tier can see.
        #
        # **It was named ``ck_transactions_settle_day_needs_basis`` until plan
        # step X-az** (developer approval 2026-08-22), and the rename is a
        # correction rather than tidying: this constraint is about the FIGURE's
        # basis, and beside X-az's ``ck_transactions_settle_day_basis_pairing``
        # the old name read as though it were about the DAY's.  Two live
        # comments already read it that way.  The name it has now is what its
        # predicate says, and it is the name of the service-tier refusal that
        # mirrors it -- ``reject_settle_day_without_a_record``.
        #
        # **It is an IMPLICATION, and a first version of this step made it a
        # BICONDITIONAL** (``ck_transactions_settlement_recorded``,
        # ``(settled_on IS NULL) = (settled_basis_id IS NULL)``).  The ``<-``
        # direction is what was wrong, and the way it was wrong is worth
        # keeping: it welded two facts with different lifetimes into one record.
        # ``settled_on`` and ``reconciled_by_id`` are the ASSERTION that this
        # money moved on a named day and a named statement showed it -- a revert
        # withdraws that.  ``settled_amount`` and ``settled_basis_id`` are WHAT
        # MOVED, which is a fact about the row.  Because that direction made them
        # share a lifetime, releasing the assertion had to destroy the figure --
        # so following the full-edit popover's own instruction ("set Status to
        # Projected to edit the amounts") silently deleted a number the user had
        # read off their bank statement.  That is finding **N-241**'s shape --
        # one thing answering two questions -- rebuilt one level up, in a step
        # whose whole purpose was to remove it.  Every reconciliation system this
        # was checked against separates them: an amount belongs to the
        # transaction and cleared-ness is metadata over it, so un-clearing never
        # touches the amount (developer, 2026-08-17).
        #
        # The ``->`` direction below survives that argument untouched: a
        # RETAINED record is ``settled_on IS NULL`` with a basis, which this
        # admits.  What keeps such a figure out of a balance is still the
        # STATUS, asked by ``row_valuation.settled_figure``, and not this.
        db.CheckConstraint(
            "settled_on IS NULL OR settled_basis_id IS NOT NULL",
            name="ck_transactions_settle_day_needs_a_record",
        ),
        # A SETTLE DAY SAYS HOW IT IS KNOWN (plan step **X-az**, finding
        # **N-332**): a row carrying the day its money moved always records
        # which KIND of day it is -- a day the bank showed, a day a balance was
        # asserted for, or the owner's own entry
        # (:class:`app.enums.SettledDayBasisEnum`).
        #
        # **It is a BICONDITIONAL where the figure's pairing above is an
        # IMPLICATION, and the asymmetry is the point** (developer,
        # 2026-08-22).  ``settled_amount`` outlives the assertion that recorded
        # it -- a revert releases the day and KEEPS what moved -- so a figure
        # with no day is the legal RETAINED state and the ``<-`` direction had
        # to go.  The day and ITS basis have no such split lifetime: the basis
        # describes the day, so the two are born and released together, and
        # forbidding a basis left behind with no day costs nothing and removes
        # the only residue a revert could leave.  ``settled_day_basis_id`` is
        # written only through
        # :func:`app.services.settle_day.record_settle_day`, which assigns or
        # clears both columns in one statement; this is the storage tier that
        # makes that door's discipline a property of the table.
        #
        # Written as two NULL tests rather than against a basis VALUE, so no
        # ``ref.settled_day_bases`` id is frozen into the schema -- the same
        # reason ``ck_transactions_amount_ownership`` is written that way.
        db.CheckConstraint(
            "(settled_on IS NULL) = (settled_day_basis_id IS NULL)",
            name="ck_transactions_settle_day_basis_pairing",
        ),
        # THE AMOUNT MODEL'S ONE CONSTRAINT (ruling **R-FI**, plan step
        # X-au-c1): a row's amount is either its OWN or it is DERIVED, and a
        # derived amount is not stored at all.  ``amount_source_id`` names the
        # relation that prices a derived row and is NULL when the row owns its
        # figure, so the two states pair exactly one-to-one with the presence of
        # a figure -- which makes a stale derived amount UNREPRESENTABLE rather
        # than merely unlikely.
        #
        # **WHAT THIS CONSTRAINT NOW CATCHES, restated at plan step X-au-k
        # because the paragraph here described a world that has ended.**  It
        # used to say that every private repair mechanism R-FI names writes the
        # amount column ALONE, so a writer stamping a figure onto a derived row
        # was an ``IntegrityError`` at flush.  Two of those mechanisms were
        # converted at X-au-k and ``transfer_service``'s copy and drift
        # corrector were deleted at X-au-g-2c-2, so no writer in this
        # application writes one column any more: the pair is ONE mapped
        # attribute over a type that has no member for the illegal shape, and
        # the ORM spelling raises ``AttributeError`` before a flush is reached.
        #
        # This CHECK is therefore the backstop rather than the catcher, and it
        # is not redundant.  It refuses the EMPTY pair, which the type does not
        # represent and a half-built row passes through legitimately in memory;
        # and it refuses a writer that is not this application at all -- a
        # migration, a ``psql`` session, a trigger, a bulk ``UPDATE`` that
        # names the column rather than the attribute.  A reader that skips the
        # resolver still gets ``None`` rather than a plausible wrong figure.
        #
        # **Written as two NULL tests rather than against a source VALUE**, so no
        # ``ref.amount_sources`` id is frozen into the schema: the OWN state is
        # the ABSENCE of a source (``app.enums.AmountSourceEnum`` states why),
        # and a constraint cannot join to a ref table to learn which id means
        # what.  ``ck_transactions_estimated_amount`` (``>= 0``) is UNCHANGED and
        # still admits the NULL -- a comparison with NULL is UNKNOWN, which a
        # CHECK passes -- so this constraint is the only thing deciding when the
        # column may be empty.
        db.CheckConstraint(
            "(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)",
            name="ck_transactions_amount_ownership",
        ),
        # A row is priced through AT MOST ONE relation, so the source names an
        # unambiguous one.  The balance README states this exclusivity as a
        # CONVENTION with nothing enforcing it ("``template_id`` and
        # ``transfer_id`` are mutually exclusive across every row -- by
        # CONVENTION, with no constraint enforcing it"); ``credit_payback_for_id``
        # is the third link and carries the same convention.  Measured before it
        # was imposed: 0 of 997 rows on the 2026-08-12 production clone set two
        # of the three (606 template, 342 transfer, 21 payback, 28 with none).
        #
        # It is the amount model's own precondition rather than tidiness: a
        # derived row's source names a relation, and a row holding two links has
        # two candidate answers with only dispatch ORDER to separate them.
        db.CheckConstraint(
            "(template_id IS NOT NULL)::int "
            "+ (transfer_id IS NOT NULL)::int "
            "+ (credit_payback_for_id IS NOT NULL)::int <= 1",
            name="ck_transactions_one_pricing_link",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_transactions_version_id_positive",
        ),
        # The SUPERKEY ``transaction_entries`` names to prove its own
        # ``account_id`` is its parent's (plan step X-f3a-1).  It constrains
        # nothing -- ``id`` is already the primary key, so this key can reject no
        # row -- and exists only because PostgreSQL requires a UNIQUE over
        # exactly the referenced columns before a composite foreign key may
        # target them.
        db.UniqueConstraint("id", "account_id", name="uq_transactions_id_account"),
        # WHICH STATEMENT showed this line, as a COMPOSITE key over the account
        # (ruling **R-FL**, plan step X-f3a-1).  A single-column
        # ``REFERENCES account_anchor_history (id)`` could not say "an assertion
        # of THIS row's account", so a writer that forgot the account scope would
        # produce a link that is silently wrong about whose statement showed the
        # money -- and clearing is a per-account question: a checking statement
        # shows a transfer's outgoing leg, the savings statement shows the
        # incoming one.  ``MATCH SIMPLE`` (PostgreSQL's default) is what lets it
        # sit beside a nullable link: a row with ``reconciled_by_id IS NULL``
        # satisfies it whatever ``account_id`` says.
        db.ForeignKeyConstraint(
            ["account_id", "reconciled_by_id"],
            ["budget.account_anchor_history.account_id",
             "budget.account_anchor_history.id"],
            name="fk_transactions_reconciled_by",
            ondelete="RESTRICT",
        ),
        db.Index("idx_transactions_reconciled_by", "reconciled_by_id"),
        # **THIS ROW'S OWNER IS ITS ACCOUNT'S, guaranteed rather than
        # maintained** (plan step ``pay_calendar:C13-a``, ruling **R-PC32**).
        # The pair keys straight onto ``uq_accounts_id_user``, the superkey
        # ``fk_account_external_identities_owner`` and
        # ``fk_statement_matches_owner`` already target the same way.
        #
        # ``ON DELETE RESTRICT`` matches the single-column ``account_id`` key
        # beside it, which stays as the ``account`` relationship's declared
        # join path: that key is about the ACCOUNT'S EXISTENCE and this one is
        # about AGREEMENT, and two keys over the same column deleting
        # differently would make an account delete's outcome depend on which
        # PostgreSQL evaluated.  The reason RESTRICT is the right action is
        # unchanged and is stated on ``account_id`` itself: a transaction must
        # not silently vanish with its account.
        db.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_transactions_owner_account",
            ondelete="RESTRICT",
        ),
        # **...AND IT IS ITS PAYCHECK'S**, which is the half that makes the
        # two-parent disagreement unrepresentable: either key alone leaves the
        # OTHER parent free to belong to someone else.  Keyed onto
        # ``uq_pay_periods_id_user``, added for exactly this.
        #
        # ``ON DELETE CASCADE`` matches the single-column ``pay_period_id`` key
        # beside it, for the reason its sibling above states.  A pay period is
        # the container a row is FILED in, and deleting one has always taken
        # its rows with it.
        db.ForeignKeyConstraint(
            ["pay_period_id", "user_id"],
            ["budget.pay_periods.id", "budget.pay_periods.user_id"],
            name="fk_transactions_owner_period",
            ondelete="CASCADE",
        ),
        # No index is added over ``user_id``; that argument sits on the
        # column itself, with the rest of what the column is for.
        # A statement cannot have shown money that never moved.  The link and
        # the settle day are one fact in two columns, and every door that moves
        # or clears the day releases the link (``status_seam`` on a revert and
        # on a correction); this refuses the pair a third writer would leave
        # behind.  ``settled_on`` is itself NULL exactly when the row is not in
        # the settled band, so it also says a linked row has settled.
        db.CheckConstraint(
            "reconciled_by_id IS NULL OR settled_on IS NOT NULL",
            name="ck_transactions_cleared_needs_settle_day",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # WHO THIS ROW BELONGS TO (plan step ``pay_calendar:C13-a``, ruling
    # **R-PC32**).  Held equal to BOTH parents' owner by the two composite keys
    # above; see the class docstring for why that is a co-located key and not
    # the cached copy CLAUDE.md rule 14 forbids.
    #
    # **It does NOT use :class:`~app.models.mixins.UserScopedMixin`, and the
    # difference is the ``ondelete``** -- the mixin's is ``CASCADE`` and this
    # one is ``RESTRICT`` (developer, 2026-09-02, on the measurement below).
    # That is the same kind of documented exclusion the mixin already carries
    # for the ``ref.*`` per-user override rows, and the reason is R-PC41's, one
    # table over: deleting a user has no live source in ``app/``, so the only
    # ways to reach it are a bug, a hand-run statement, or a future door whose
    # author has not thought about it -- and each of those wants a loud
    # refusal, not a silent wipe.
    #
    # **The mixin's CASCADE was the only candidate shape that CHANGED what a
    # user delete does**, and it changed it into one statement that empties the
    # database.  Migration ``d4a92f6b13c8``'s docstring carries the driven
    # table for all four shapes; it is stated once, there, where the decision
    # was taken.
    #
    # This key is not redundant with the composites, and its second reason is
    # the stronger one.  Without it, ``user_id``'s guarantee of naming a real
    # user is transitive through ``fk_transactions_owner_account`` and leaves
    # with that key.  And it is what makes the refusal ORDER-INDEPENDENT: the
    # composites-only shape refuses only while ``accounts_user_id_fkey``'s
    # referential trigger holds a lower OID than ``pay_periods_user_id_fkey``'s,
    # so re-creating the accounts key -- which any future migration touching it
    # does -- makes the same delete SUCCEED and take everything.
    #
    # **No index over ``user_id`` alone.**  A referencing-side index is what
    # makes a parent's delete check cheap, and every key reading this column
    # leads with one already indexed: ``idx_transactions_account`` serves
    # ``fk_transactions_owner_account``, ``idx_transactions_period_scenario``
    # serves ``fk_transactions_owner_period``.  What is left is this key's own
    # check on a user delete, which is refused rather than performed.
    # ``budget.transaction_entries`` and ``budget.statement_matches`` carry the
    # same column with no index of its own.  *The QUERY half was left for
    # ``C13-b`` to re-decide with its reads in hand; it did, and the answer is
    # STILL NO INDEX* (2026-09-03) -- all nineteen reads it moved are ATTRIBUTE
    # reads on a row already loaded by primary key, and it added no
    # ``WHERE transactions.user_id = ...`` anywhere for an index to serve.  The
    # predicate for the next reader: a query in ``app/`` whose WHERE names it.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "auth.users.id",
            name="fk_transactions_user_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    template_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transaction_templates.id", ondelete="SET NULL"),
    )
    pay_period_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.pay_periods.id", ondelete="CASCADE"),
        nullable=False,
    )
    scenario_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.scenarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("ref.statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(200), nullable=False)
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id", ondelete="SET NULL"),
    )
    transaction_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.transaction_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # The row's OWN amount, and NULLABLE since plan step X-au-c1: a row whose
    # amount is DERIVED does not store one at all (ruling **R-FI**).  NULL here
    # means "ask ``cash_ledger.resolve_transaction_amount``".  No production row
    # is NULL as of this step; the per-kind cutovers (plan steps X-au-d through
    # X-au-f) are what empty it.
    #
    # **PRIVATE since plan step X-au-k**, with the SQL name unchanged: the pair
    # this column belongs to is mapped as ONE attribute
    # (:attr:`amount_ownership`), and a column that stayed publicly assignable
    # would be the half-write that attribute exists to make unsayable.  Read it
    # through :attr:`estimated_amount`, which is a read-only projection and
    # still a query expression.
    #
    # **The name is DOUBLE-underscored, and that is the seal rather than a
    # style**: Python mangles it to ``_Transaction__estimated_amount``, so the single-underscore
    # spelling a reader would guess -- ``row._estimated_amount`` -- binds a plain
    # instance attribute that reaches no column at all.  A write that misses
    # the seam is then a no-op the next read exposes, instead of the
    # half-written pair this step exists to make unrepresentable.
    __estimated_amount = db.Column("estimated_amount", db.Numeric(12, 2))
    # WHAT MOVED -- a fact about the ROW, not a second opinion about the plan
    # above and not part of the assertion beside it (plan step **X-au-c3**).
    # NULL until the row first settles, and NULL whenever the basis is
    # ``purchases`` -- there the figure is the sum of the row's own entries,
    # which are themselves the records, so storing it would be a second copy
    # beside a reconciler.  Otherwise it states what left the account.
    #
    # **It SURVIVES a revert**, which is why this is not "NULL when the row has
    # not settled": withdrawing the assertion does not un-know what the bank
    # took, and the popover instructs the user to revert in order to edit, so
    # destroying it there destroyed their own statement reading.  A row out of
    # the settled band therefore may carry a figure, and no balance reads it --
    # ``row_valuation.settled_figure`` asks the STATUS first and answers ``None``
    # for such a row.  A re-settle HONOURS a retained ``corrected`` figure
    # (``status_seam.Settlement.from_settle``), so the round trip is lossless.
    #
    # **It was ``actual_amount``, and the rename is the fix rather than tidying**
    # (finding **N-241**).  That column answered two questions at once: its VALUE
    # was the settled figure and its NULL-ness was read by three subsystems as
    # *a human entered this* (ruling **R-FH**) -- so a machine-derived figure
    # written there manufactured a correction nobody made, and a settle that had
    # no correction to record recorded nothing at all.  WHO said it is
    # ``settled_basis_id`` now, and the two facts can no longer collide.
    settled_amount = db.Column(db.Numeric(12, 2))
    # HOW the figure beside it is known -- ``derived``, ``corrected`` or
    # ``purchases`` -- and NULL only when this row has never settled (plan step
    # **X-au-c3**).  It travels WITH ``settled_amount``, not with ``settled_on``:
    # provenance is a property of a figure, so the two share a lifetime and
    # ``ck_transactions_settled_amount_needs_basis`` is the pairing.
    #
    # **It is NOT the answer to "has this row settled"** -- the STATUS is, and
    # reading this column for that question is exactly what forced a first
    # version of this step to destroy a user's figure on every revert.  RESTRICT
    # rather than SET NULL: a vanishing ref row would leave a stored figure with
    # no provenance, which is the state that pairing exists to forbid.  Resolved
    # through ``ref_cache.settlement_basis_id``.
    settled_basis_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.settlement_bases.id",
            name="fk_transactions_settled_basis_id",
            ondelete="RESTRICT",
        ),
    )
    # WHICH RELATION prices this row, or NULL when the row owns its own figure
    # (ruling **R-FI**, plan step X-au-c1).  RESTRICT rather than SET NULL: a
    # ``ref.amount_sources`` row disappearing under a derived transaction would
    # silently convert it into a row claiming to own an amount it does not have,
    # which is the state ``ck_transactions_amount_ownership`` exists to forbid --
    # so the ref DELETE is refused instead.  Resolved through
    # ``ref_cache.amount_source_id``; the OWN state is a NULL test on this column
    # and needs no cache read.
    #
    # **PRIVATE since plan step X-au-k**, for the reason its partner states,
    # and double-underscored for the same seal.
    __amount_source_id = db.Column(
        "amount_source_id",
        db.Integer,
        db.ForeignKey(
            "ref.amount_sources.id",
            name="fk_transactions_amount_source_id",
            ondelete="RESTRICT",
        ),
    )
    # THE PAIR ABOVE, AS ONE ATTRIBUTE (plan step **X-au-k**).  Assigning it is
    # the only way to move this row between R-FI's two states, so "the one
    # writer of a row's amount-ownership pair" is a property of the mapping
    # rather than a census of call sites -- which is what it was, and what had
    # to be re-run every time a cutover grew the derived population.  The two
    # sites that made the census unmaintainable are splats over a VARIABLE
    # field name (``recurrence_engine/_maintain.py``,
    # ``routes/transactions/mutations.py``): no grep and no AST pass can see
    # them, and neither can reach this attribute by accident.
    #
    # ``ck_transactions_amount_ownership`` STAYS, and the two refuse different
    # things: :class:`~app.models.amount_ownership.AmountOwnership` refuses a
    # figure BESIDE a relation, and the CHECK refuses NEITHER -- the state a
    # row still being built passes through in memory.  The CHECK is also the
    # backstop against a writer that is not this application at all: a
    # migration, a ``psql`` session, a trigger.
    # ``from_columns`` rather than the class itself: it answers ``None`` for a
    # row that has stated no ownership, which keeps that state out of the
    # value object and lets the type be TOTAL over ruling R-FI's two.
    amount_ownership = db.composite(
        from_columns, __estimated_amount, __amount_source_id,
    )
    # is_override and is_deleted are provided by SoftDeleteOverridableMixin.
    transfer_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transfers.id", ondelete="CASCADE"),
    )
    credit_payback_for_id = db.Column(
        db.Integer,
        # F-137 / C-42: explicit FK name follows the project's ``fk_*``
        # convention documented in ``docs/coding-standards.md``.
        # Earlier the self-referential FK carried the Alembic-default
        # ``transactions_credit_payback_for_id_fkey`` name; this
        # declaration keeps ``db.create_all()`` aligned with the
        # post-C-42 migrated state.  SET NULL semantics preserved.
        db.ForeignKey(
            "budget.transactions.id",
            name="fk_transactions_credit_payback_for",
            ondelete="SET NULL",
        ),
    )
    notes = db.Column(db.Text)
    due_date = db.Column(db.Date, nullable=True)
    # WHICH OCCURRENCE this row is -- the date the template's cadence named
    # when the recurrence engine wrote it (plan step **R17**, the first leaf of
    # **R5**).  It is the row's IDENTITY under its definition, which is why it
    # sits here beside ``template_id`` / ``pay_period_id`` / ``scenario_id``
    # (what the row IS) and deliberately NOT in
    # ``recurrence_engine._amounts.DerivedRowFields`` (what the definition
    # DERIVES for a period).  A maintain pass therefore never rewrites it: a
    # row's occurrence does not change because its definition did.
    #
    # **It is what makes a MOVED row durable**, which is finding **D57**.  The
    # generate pass used to ask "does this PAY PERIOD hold a row", so a row the
    # owner moved to a neighbouring paycheck emptied the period its occurrence
    # named and the next whole-schedule pass wrote a second one -- measured on
    # a production clone 2026-08-27 at **8 rows / $1,482.93 from ONE pass**,
    # seven of them already Paid.  ``pay_period_id`` is the FUNDING and the
    # owner may move it; this column is the cadence and nothing moves it.
    #
    # **NULL means the row answers no occurrence**, and that is a real state
    # rather than a gap -- so this is nullable where plan step R5's
    # specification said ``NOT NULL``.  Two live writers create a
    # template-linked row that no cadence named: ``carry_forward_service``
    # rolls an unspent envelope forward as an ``is_override`` row (and writes
    # ``due_date = None`` for the same reason), and the one-time branch of
    # ``routes/transfers/_instances.py`` materialises a transfer whose template
    # has no rule at all.  On a production clone the backfill considered 736 of
    # 788 template-linked rows -- the rest sit on archived templates it does not
    # walk -- stamped 726 and left 10 NULL.
    #
    # **A NULL row answers no occurrence, so it claims its whole PAY
    # PERIOD instead** -- the pre-R17 rule, which is the only claim that
    # can be made about a row no cadence names.  It does NOT claim
    # nothing: ``_recurrence_common.OccurrenceClaims`` carries the
    # measurement, and letting such a row block nothing writes 52 rows /
    # $26,000 where the correct answer is 11 / $5,500, at the unarchive
    # door on the developer's own data.
    occurs_on = db.Column(db.Date, nullable=True)
    # settled_on, settled_day_basis_id and reconciled_by_id are provided by
    # SettleDatedMixin -- the three columns that ARE this row's ASSERTION, and
    # its ``settled_on`` validator refuses a ``datetime`` on every write path
    # (finding **N-179**).  What is specific to THIS table is stated here:
    #
    # ``settled_on`` is the CASH clock, and its NULL is the invariant rather
    # than a gap: a transaction carries a settle day if and only if it is in a
    # settled status (Paid or Received).  Both halves are written by
    # ONE statement -- ``status_seam.apply_status_change``, the single door that
    # assigns ``status_id`` -- so they cannot diverge.  See the class docstring
    # for why that half is not a CHECK constraint and why the day has no bounds.
    #
    # ``settled_day_basis_id`` says WHICH KIND of day it is, and
    # ``ck_transactions_settle_day_basis_pairing`` above welds the two
    # NULL-nesses (plan step **X-az**, finding **N-332**).
    #
    # ``reconciled_by_id`` names WHICH statement showed this line -- the
    # ``account_anchor_history`` row whose balance the user (or, from plan step
    # X-f6a, the bank's own export) was reading when they confirmed the money
    # had moved.  Ruling **R-FL**.  Nullable, and the NULL is a FACT rather than
    # a gap: it means no statement has been RECORDED as showing this line.  It
    # is not "not cleared" -- the three-state model the developer ruled on
    # 2026-08-14 calls it UNKNOWN, and the ONE clearing rule
    # (``cash_ledger.StatementCoverage``) answers an UNKNOWN line from the date
    # rule this column exists to retire.  What turns UNKNOWN into NOT CLEARED is
    # the statement itself being recorded as walked line by line, which is plan
    # step X-f3a-2's fact and not this column's.
    #
    # **Nothing was backfilled into it and that is deliberate.**  The date rule
    # is a guess -- of 110 movements matched to the developer's bank lines only
    # 33 carry the day the bank posted them -- so backfilling this column from
    # it would launder that guess into an observation nobody made, and no later
    # reader could tell the two apart.  History is filled from the BANK at plan
    # step X-f6a.  Its foreign key is COMPOSITE over ``account_id``; see
    # ``fk_transactions_reconciled_by`` above for why a single-column one cannot
    # express the rule.
    # is_envelope and companion_visible are provided by
    # TrackingVisibilityMixin.  On an ad-hoc (template_id IS NULL) row
    # they carry the row's own setting; on a template-generated row they
    # are inert -- the resolved ``tracks_purchases`` /
    # ``visible_to_companion`` properties below defer to the template so
    # the template stays the single source of truth.
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    # ``foreign_keys`` on both, because each parent is now reached by TWO
    # declared keys -- the single-column one and the composite that also holds
    # the owner (plan step ``pay_calendar:C13-a``).  Without it SQLAlchemy
    # cannot choose a join path and raises ``AmbiguousForeignKeysError`` at
    # mapper configuration.  The SINGLE-column key is the declared path, which
    # is the same choice ``TransactionEntry.transaction`` makes over
    # ``fk_transaction_entries_parent_account``: the join loads a parent, and
    # adding ``AND parent.user_id = t.user_id`` to every load would re-check in
    # SQL what the database has already refused to store -- while making
    # ``user_id`` a column TWO relationships wanted to write on flush.
    #
    # **A JOIN between these tables now needs its onclause named**, and the
    # relationship attribute is how: ``query(Transaction).join(PayPeriod)``
    # raises ``AmbiguousForeignKeysError`` where
    # ``.join(Transaction.pay_period)`` does not, because a relationship
    # carries the ``foreign_keys`` above and a bare entity has nothing to
    # choose with.  Every join in ``app/`` already names one or goes through a
    # relationship; two in ``tests/`` did not and were corrected with this step.
    account = db.relationship(
        "Account", foreign_keys=[account_id], lazy="joined",
    )
    template = db.relationship("TransactionTemplate", back_populates="transactions")
    pay_period = db.relationship(
        "PayPeriod", foreign_keys=[pay_period_id], back_populates="transactions",
    )
    scenario = db.relationship("Scenario")
    status = db.relationship("Status", lazy="joined")
    category = db.relationship("Category", lazy="joined")
    transaction_type = db.relationship("TransactionType", lazy="joined")
    transfer = db.relationship(
        "Transfer",
        backref=db.backref("shadow_transactions", passive_deletes=True),
        lazy="select",
    )
    credit_payback_for = db.relationship(
        "Transaction", remote_side="Transaction.id", foreign_keys=[credit_payback_for_id]
    )
    entries = db.relationship(
        "TransactionEntry", back_populates="transaction",
        foreign_keys="TransactionEntry.transaction_id",
        lazy="select", cascade="all, delete-orphan",
        # Ordered by the day the purchase was MADE, not the day the bank took
        # it: this list is what the user reads back as "what I spent on this
        # envelope", which is a budget-clock question.
        order_by="TransactionEntry.purchased_on",
    )

    @hybrid_property
    def estimated_amount(self):
        """Return the figure this row states as its own, or ``None``.

        A READ-ONLY projection of :attr:`amount_ownership` (plan step
        **X-au-k**), so every reader that asked for the column still gets it
        and no reader has to learn the pair.  It reads the mapped column
        rather than the composite so that a row still being built -- one whose
        ownership has not been stated -- answers ``None`` instead of raising.

        **There is no setter, and that is the whole step.**  ``AttributeError``
        is what a direct write gets, including a ``setattr`` over a variable
        field name, which is the shape the two splat sites use.  To state this
        row's amount, assign :attr:`amount_ownership` through
        ``app.services.amount_ownership``.

        As a ``hybrid_property`` rather than a plain one because
        ``Transaction.estimated_amount`` is also a QUERY expression: at class
        level this returns the mapped column, so ``filter``, ``order_by``,
        ``in_``, ``func`` wrappers and ``aliased()`` all keep working
        unchanged.

        **The exception, stated because an enumeration that lists only what it
        verified reads as complete**: the LOADER options do not take it.
        ``load_only(Transaction.estimated_amount)`` raises ``IndexError`` on
        SQLAlchemy 2.0.49 -- a hybrid is not a mapped attribute and the option
        has no column to defer.  Nothing in ``app/`` or ``tests/`` uses
        ``load_only`` on either name; a caller that needs one wants
        ``Transaction.amount_ownership``, which IS mapped.

        Returns:
            The stored figure, or ``None`` when this row's amount is derived
            or not yet stated.
        """
        return self.__estimated_amount

    @hybrid_property
    def amount_source_id(self):
        """Return the id of the relation pricing this row, or ``None``.

        The read-only twin of :attr:`estimated_amount`; see it for why there
        is no setter and why this is a hybrid.  ``None`` means the row owns
        its figure, which is the NULL test
        ``ck_transactions_amount_ownership`` is written over.

        Returns:
            The ``ref.amount_sources`` id, or ``None`` when the row owns its
            amount or has not stated its ownership yet.
        """
        return self.__amount_source_id

    @property
    def is_income(self):
        """True if this transaction is income."""
        return self.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    @property
    def is_expense(self):
        """True if this transaction is an expense."""
        return self.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    @property
    def tracks_purchases(self):
        """True if individual purchase entries apply to this transaction.

        Single source of truth for the "is this an envelope / entry-
        capable row?" question across services, routes, and templates.
        Resolution rule: a template-generated transaction defers to its
        template's ``is_envelope`` flag (the template owns the setting for
        every instance it generates); an ad-hoc transaction (no template)
        uses its own ``is_envelope`` column.  Accesses the template
        relationship only when ``template_id`` is set, so ad-hoc rows
        never trigger a lazy load.
        """
        if self.template_id is None:
            return self.is_envelope
        return self.template.is_envelope

    @property
    def visible_to_companion(self):
        """True if a companion of the owner may see this transaction.

        Mirrors :attr:`tracks_purchases`: a template-generated
        transaction defers to its template's ``companion_visible`` flag;
        an ad-hoc transaction uses its own ``companion_visible`` column.
        Accesses the template relationship only when ``template_id`` is
        set.
        """
        if self.template_id is None:
            return self.companion_visible
        return self.template.companion_visible

    @property
    def days_until_due(self):
        """Days remaining until the due date, or None.

        Returns a positive integer for future due dates and a negative
        integer for overdue transactions.  Returns None when there is no
        due date or the transaction is already settled (no action needed).
        """
        if self.due_date is None:
            return None
        if self.status is not None and self.status.is_settled:
            return None
        return (self.due_date - date.today()).days

    @property
    def days_paid_before_due(self):
        """Days between due date and payment, or None.

        Positive means paid early, negative means paid late, zero means
        paid on the due date.  Returns None when either field is missing --
        which for :attr:`settled_on` means the row is not settled, so its
        timeliness is not yet a question.

        **Both operands are civil dates, and no timezone enters this.**  It
        subtracted ``to_display_date(paid_at)`` until plan step X-f1: an instant
        converted to a day and subtracted from a ``DATE`` column, which was one
        of the eleven statements of the same "which civil day did this settle
        on" derivation the seam now makes once at the write door.  The
        arithmetic is now exact rather than zone-dependent.

        **The behaviour is unchanged for every row that recorded an instant, and
        CHANGED for eight rows that did not** (finding **N-181**, found by a
        neutral review).  This gate used to be "was a settle instant recorded";
        it is now "is the row settled", because the migration backfilled a day
        onto every settled row -- including 8 legacy transfer shadows whose
        ``paid_at`` was NULL and which took their pay period's ``start_date``.
        Those 8 were EXCLUDED from
        ``spending_analysis.payment_timeliness_from_txns`` and are now included,
        dated by a day nothing observed: measured on production, the four
        expense legs report 8 days early, on time, on time and 1 day late.  The
        balance was always computed from that same fallback day, so no balance
        moves; what moved is a timeliness metric that used the NULL as its
        "unknown" signal.  Narrowing the backfill instead was REJECTED -- it
        would leave 8 settled rows undated, which the balance walk now refuses,
        trading a soft metric for a 500 on the grid.  The resolution is plan step
        X-f1c's edit door, which lets those 8 legacy days be corrected.
        """
        if self.due_date is None or self.settled_on is None:
            return None
        return (self.due_date - self.settled_on).days

    def __repr__(self):
        return f"<Transaction '{self.name}' ${self.estimated_amount} ({self.id})>"
