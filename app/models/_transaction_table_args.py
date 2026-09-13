"""
Shekel Budget App -- ``budget.transactions``' table arguments, as one value.

**Born as a PURE MOVE** (plan step X-ca, developer 2026-09-06, under ruling
**balance:R-IR**: *pylint's 1,000-line module ceiling stays, and the session
that breaks a module is the one that splits it*).
:class:`~app.models.transaction.Transaction` stood at 997 of that ceiling, so
the next constraint the balance arc adds could not be written at all -- and
R-IR's answer is to SPLIT, never to shave a comment or raise the limit, the
module-level ``too-many-lines`` escape hatch having been withdrawn with it.  The
340 lines of ``__table_args__`` came here verbatim: nothing reworded, reordered
or dropped.  **The constraint that split was made for is the first member
added since**: ``ck_transactions_template_row_needs_due_date`` (plan step
X-bv-2, 2026-09-11), so the purity claim below is dated to the move and
describes the 25 members that came across, not the tuple as it stands.

**Why THIS seam.**  ``__table_args__`` is the one part of the model that is a
VALUE rather than a declaration.  It names no attribute the class exposes, no
caller imports it, and SQLAlchemy reads it exactly once at class construction --
so moving it changes what a reader scrolls past, and nothing else.  Every other
candidate cut (the columns, the hybrid properties, the class docstring) would
have separated things a reader needs side by side.

**What establishes "pure", and what does NOT.**  The claim is settled by an AST
comparison: ``ast.dump`` of the tuple here equals ``ast.dump`` of the expression
this replaced, so the 25 members are identical in KIND, in ORDER and in every
keyword argument -- which rules out a drop, a swap, a reworded predicate and a
changed ``ondelete`` at once.  A byte-exact reverse-dedent of the 340 lines
agrees, and so does a compiled-DDL diff of the ``CREATE TABLE`` plus all 11
``CREATE INDEX`` statements.

*Counting the constructed table is NOT sufficient, and an adversarial review
proved it by mutation rather than by argument.*  A census of
``Transaction.__table__``'s columns, constraint names, predicates and column
lists -- 29 / 27 / 11, identical here -- is BLIND to ``postgresql_using``,
``postgresql_ops``, ``deferrable``, ``initially``, ``info`` and a column
``comment``: an index silently switched to a hash method reads identical through
it.  The count is worth stating and is not the evidence.

The argument for each index and constraint stays WITH it, in the comments below.
They are why this file is long and they are worth the length: several of them
record what a missing predicate cost in dollars on real data.
"""

from app.extensions import db

#: ``budget.transactions``' indexes, CHECK constraints, composite foreign keys
#: and its schema, in the order SQLAlchemy receives them.  Assigned straight to
#: ``Transaction.__table_args__``.  The trailing ``{"schema": ...}`` mapping is a
#: MEMBER of the tuple rather than a separate argument -- SQLAlchemy's own
#: convention, and the reason this is a tuple rather than a list.
transaction_table_args = (
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
    # A ROW OF A DEFINITION IS DATED (plan step **X-bv-2**, rulings
    # **R-BAL6** and **R-BAL17**, finding **BAL-463**).  Amount rule 3
    # prices a derived row from its definition's series *as of the row's own
    # due date*, and ruling **D5** forbids substituting the pay period's
    # bounds -- so a template-linked row with no date is unpriceable the
    # moment its figure is handed back to the definition, which
    # ``resolve_conflicts``' "use the template's amount" does with one press.
    # ``AmountUnresolvable`` has no handler on the grid, and one such row was
    # the whole screen.
    #
    # **Two terms, not three, and the difference is what makes it need no
    # guard.**  A staged three-term form (``... OR amount_source_id IS NULL
    # ...``) admitted the undated row and refused only the DECLARE, turning
    # the chooser's button into an ``IntegrityError`` that a guard in
    # ``resolve_conflicts`` would then have had to fence.  This form is
    # invariant under the declare, which touches neither column here.  Every
    # constructor that sets ``template_id`` is dated -- the two engine paths
    # splat ``DerivedRowFields`` (``compute_due_date``'s answer) and the
    # carry-forward leftover is X-bv's -- and no writer in ``app/`` sets
    # ``template_id`` on an existing row, so nothing can reach the state.
    # The transaction PATCH is refused a step earlier by
    # ``routes/transactions/_gates._reject_generated_due_date_edit``, which
    # also refuses MOVING the date -- something no CHECK can say -- and
    # renders a designed 400; this is that door's backstop for a writer that
    # is not the application, and the storage-tier statement of the
    # ``due_date IS NULL`` arm ``c8f3a5d2e714``'s strand guard had to ask.
    # An AD-HOC row is untouched: nothing prices it by its date, and the
    # form still offers the field.  **The refusal arm this replaces is
    # DELETED, not kept**: ``cash_ledger._definition_cash._stated_amount``
    # refused a linked row with no date, and a refusal over a state the
    # schema cannot hold is a fence (R-BAL17).  Because that arm priced
    # BOTH row tables, the twin ``ck_transfers_template_row_needs_due_date``
    # binds in the same migration (``4d7123cd9803``); the suite's rows of a
    # definition are the engine's (plan step X-cf), which is what let this
    # bind without hand-dating them.
    db.CheckConstraint(
        "template_id IS NULL OR due_date IS NOT NULL",
        name="ck_transactions_template_row_needs_due_date",
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
    # ``ON DELETE RESTRICT`` matches the single-column ``account_id`` column key
    # in :mod:`app.models.transaction`
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
    # ``ON DELETE CASCADE`` matches the single-column ``pay_period_id`` column
    # key in :mod:`app.models.transaction`, for the reason its sibling above
    # states.  A pay period is
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
