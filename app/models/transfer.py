"""
Shekel Budget App -- Transfer Model (budget schema)

Tracks transfers between accounts (checking ↔ savings) within pay periods.
Supports both template-generated recurring transfers and ad-hoc one-time transfers.
"""


from app.extensions import db
from app.models.mixins import (
    OptimisticLockMixin,
    SoftDeleteOverridableMixin,
    TimestampMixin,
    UserScopedMixin,
)


class Transfer(
    UserScopedMixin, OptimisticLockMixin, SoftDeleteOverridableMixin,
    TimestampMixin, db.Model,
):
    """A transfer between two accounts within a pay period.

    Optimistic locking: ``version_id`` is the SQLAlchemy
    ``version_id_col`` for the row.  Every ORM-emitted UPDATE or
    DELETE is narrowed to ``WHERE id = ? AND version_id = ?`` and
    the stored value is atomically incremented; concurrent
    mutations race for the bump and the loser raises
    :class:`sqlalchemy.orm.exc.StaleDataError`.  The transfer
    service propagates parent-transfer mutations to both shadow
    transactions, so the parent's version pin protects the entire
    three-row write set even though the shadow rows carry their
    own ``version_id`` columns.  See commit C-18 of the 2026-04-15
    security remediation plan.
    """

    __tablename__ = "transfers"
    __table_args__ = (
        db.Index("idx_transfers_period_scenario", "pay_period_id", "scenario_id"),
        db.CheckConstraint(
            "from_account_id != to_account_id",
            name="ck_transfers_different_accounts",
        ),
        db.CheckConstraint("amount > 0", name="ck_transfers_positive_amount"),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_transfers_version_id_positive",
        ),
        # THE AMOUNT MODEL'S ONE CONSTRAINT, on the second of the two columns it
        # covers (ruling **R-FI**, plan step X-au-c1).  Identical in form and in
        # purpose to ``ck_transactions_amount_ownership`` -- see that constraint
        # for why the pairing is written as two NULL tests rather than against a
        # source value, and for the write-side teeth it buys.  On THIS table the
        # writer it refuses is named: ``transfer_service`` copies the parent's
        # figure onto both shadows and a drift corrector repairs the copies that
        # got away, and plan step X-au-f deletes both by making a shadow READ its
        # parent -- at which point Transfer Invariant 3 is structural rather than
        # maintained.
        #
        # ``ck_transfers_positive_amount`` (``amount > 0``) is UNCHANGED and
        # still admits the NULL (a comparison with NULL is UNKNOWN, which a CHECK
        # passes), so this constraint alone decides when the column may be empty.
        db.CheckConstraint(
            "(amount_source_id IS NULL) = (amount IS NOT NULL)",
            name="ck_transfers_amount_ownership",
        ),
        # An AD-HOC transfer owns its amount, because no definition states a
        # price for it -- ``cash_ledger.resolve_transfer_amount`` answers OWN for
        # a transfer with no template, so a declaration on one names a relation
        # that cannot be reached.
        #
        # **It is a constraint rather than a comment because
        # ``uq_transfers_adhoc_dedupe`` DEPENDS on it, and an adversarial review
        # is why.**  That index prevents the double-submit that silently doubles a
        # projected debit and credit (F-050 / C-22), and its key includes
        # ``amount``.  PostgreSQL indexes NULLs as DISTINCT by default, so two
        # ad-hoc transfers with a NULL amount and otherwise identical keys would
        # BOTH insert -- the guard disabled by a column the amount model made
        # nullable.  The first draft of the comment above asserted the index was
        # unaffected "because an ad-hoc transfer owns its amount", which was true
        # and enforced by nothing; this is that sentence made structural.
        db.CheckConstraint(
            "amount_source_id IS NULL OR transfer_template_id IS NOT NULL",
            name="ck_transfers_adhoc_owns_amount",
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
        # Both stay PARTIAL over ``is_deleted = FALSE AND is_override = FALSE``:
        # an override sibling may coexist with its rule-generated parent, which
        # carry-forward relies on so a moved unpaid item lives beside the
        # generated row for its target period.
        db.Index(
            "idx_transfers_template_scenario_occurrence",
            "transfer_template_id", "scenario_id", "occurs_on",
            unique=True,
            postgresql_where=db.text(
                "transfer_template_id IS NOT NULL "
                "AND occurs_on IS NOT NULL "
                "AND is_deleted = FALSE "
                "AND is_override = FALSE"
            ),
        ),
        db.Index(
            "idx_transfers_template_scenario_undated",
            "transfer_template_id", "scenario_id", "pay_period_id",
            unique=True,
            postgresql_where=db.text(
                "transfer_template_id IS NOT NULL "
                "AND occurs_on IS NULL "
                "AND is_deleted = FALSE "
                "AND is_override = FALSE"
            ),
        ),
        # Ad-hoc duplicate prevention (F-050 / C-22).  Without this index
        # a double-submit of the ad-hoc transfer form -- network retry,
        # double-click, browser back-and-resubmit -- creates two parent
        # transfers in the same period.  Each duplicate transfer also
        # produces two shadow transactions, so a single accidental
        # double-click silently doubles the user's projected debit and
        # credit by 4 rows total; balance projections drift by
        # ``2 * amount`` until the user notices and manually reconciles.
        # The composite key (user_id, from_account_id, to_account_id,
        # amount, pay_period_id, scenario_id) plus the
        # ``transfer_template_id IS NULL`` predicate scopes the
        # constraint to ad-hoc transfers only -- recurring transfers
        # are protected by the index above and may legitimately repeat
        # across periods.  ``is_deleted = FALSE`` keeps soft-deleted
        # rows out of the index so a delete-and-recreate workflow
        # remains legal, mirroring the predicate on
        # ``uq_transactions_transfer_type_active``.  scenario_id is
        # included so an ad-hoc transfer in the baseline scenario
        # does not block the same transfer in a what-if scenario.
        db.Index(
            "uq_transfers_adhoc_dedupe",
            "user_id", "from_account_id", "to_account_id",
            "amount", "pay_period_id", "scenario_id",
            unique=True,
            postgresql_where=db.text(
                "transfer_template_id IS NULL "
                "AND is_deleted = FALSE"
            ),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    from_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # F-136 / C-43: ondelete=CASCADE replaces the historical RESTRICT
    # so the FK matches the sibling tables (``budget.transactions``
    # and ``budget.account_anchor_history`` both CASCADE on
    # ``pay_period_id``).  The asymmetry was an unintentional drift:
    # PostgreSQL evaluates every referential action for a single
    # DELETE in one pass, so a user-cascade that fans out into
    # ``pay_periods`` and ``transfers`` simultaneously would
    # previously have raised a RESTRICT error even though every
    # row was destined for deletion.  CASCADE also keeps the
    # transfer invariant intact: the transfer + its two shadow
    # transactions + their pay period all disappear together
    # rather than leaving the parent transfer orphaned after the
    # shadows cascade away through ``transactions.pay_period_id``.
    # Name follows the SHEKEL_NAMING_CONVENTION (see
    # app/extensions.py).
    pay_period_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.pay_periods.id",
            name="fk_transfers_pay_period_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # Pylint: ``duplicate-code`` -- Incidental scenario_id + status_id FK
    # pair, shared by structure (not by domain) with the transaction table
    # -- both are budget events living in a scenario with a status.  They
    # are deliberately separate tables (the transfer owns the two-shadow
    # invariant), so a shared base would couple them wrongly
    # (coding-standards rule 13).  One-sided disable: the transaction block
    # stays un-disabled.
    # pylint: disable=duplicate-code
    scenario_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.scenarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("ref.statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    transfer_template_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transfer_templates.id", ondelete="SET NULL"),
    )
    name = db.Column(db.String(200))
    # The transfer's OWN amount, and NULLABLE since plan step X-au-c1: a transfer
    # whose amount is DERIVED does not store one (ruling **R-FI**).  NULL means
    # "ask ``cash_ledger.resolve_transfer_amount``", which for a generated
    # transfer is its definition's effective-dated price series as of the
    # transfer's own due date.  Structurally paired with ``amount_source_id`` by
    # ``ck_transfers_amount_ownership`` above.  No production row is NULL as of
    # this step; plan step X-au-f is what empties it for generated transfers.
    amount = db.Column(db.Numeric(12, 2))
    # WHICH RELATION prices this transfer, or NULL when it owns its own figure
    # (ruling **R-FI**, plan step X-au-c1).  Only ``template`` is meaningful here
    # -- a transfer has no parent transfer -- and RESTRICT is for the reason the
    # transaction twin states: a vanishing ref row would convert a derived
    # transfer into one claiming to own an amount it does not have.
    amount_source_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.amount_sources.id",
            name="fk_transfers_amount_source_id",
            ondelete="RESTRICT",
        ),
    )
    # is_override and is_deleted are provided by SoftDeleteOverridableMixin.
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id", ondelete="SET NULL"),
    )
    notes = db.Column(db.Text)
    # Calendar date the transfer is due.  Nullable, matching
    # ``Transaction.due_date`` (ad-hoc transfers may omit it; recurrence
    # places it from the rule's ``day_of_month``).  The parent is the
    # canonical value: the transfer service mirrors it to both shadow
    # transactions so the three stay equal (Transfer Invariant 3).
    # Consumers of a due date (calendar, dashboard, year-end, spending-trend)
    # read the shadow ``Transaction.due_date``; this column exists so the
    # parent is a complete record and so edits/display have one source of truth.
    #
    # On a LOAN PAYMENT the shadow's ``due_date`` is a POSTING INPUT, not
    # display metadata: it is the INSTALLMENT the payment satisfies (the loan
    # engine reads it via ``loan_loaders.loan_payment_due_date`` rather than
    # re-deriving it from the pay period, which is wrong for a payment settled
    # late), and the genesis write walk orders payments by it and applies its
    # strict ``anchor_date < due_date`` post-anchor boundary against it.  Moving
    # it therefore changes the POSTED balance, so ``due_date`` is in
    # ``transfer_service._POSTING_RELEVANT_FIELDS`` and any other writer of this
    # column MUST follow it with a posting reconcile.  The three rows must be
    # kept equal for the same reason: the full-edit form pre-fills its input from
    # the PARENT, so a stale parent would be written back over a corrected shadow
    # by a no-op save.
    due_date = db.Column(db.Date, nullable=True)
    # WHICH OCCURRENCE this transfer is -- the date its template's cadence
    # named when the transfer recurrence engine wrote it.  The parallel of
    # ``Transaction.occurs_on``, which carries the full statement; both engines
    # write it from the same ``PlannedOccurrence.occurrence`` and the shared
    # skip predicate reads it identically, so a divergence here would be the
    # drift ``_recurrence_common`` exists to prevent (plan step **R17**).
    #
    # **It is NOT mirrored onto the two shadow transactions.**  Transfer
    # Invariant 3 makes the shadows equal to their parent in amount, status and
    # period, and a shadow is not the rule's own row: it is created by
    # ``transfer_service`` from the parent, never by the recurrence engine from
    # an occurrence, and no generate pass ever asks a shadow whether an
    # occurrence has been written.  Mirroring it would put a second writer on a
    # column whose whole purpose is that only the engine writes it.
    occurs_on = db.Column(db.Date, nullable=True)
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    template = db.relationship("TransferTemplate", back_populates="transfers")
    from_account = db.relationship(
        "Account", foreign_keys=[from_account_id], lazy="joined"
    )
    to_account = db.relationship(
        "Account", foreign_keys=[to_account_id], lazy="joined"
    )
    status = db.relationship("Status", lazy="joined")
    pay_period = db.relationship("PayPeriod")
    scenario = db.relationship("Scenario")
    category = db.relationship("Category", lazy="joined")

    @property
    def settled_on(self):
        """Return the civil day this transfer's money moved, or ``None``.

        A transfer has no ``settled_on`` COLUMN -- the day lives on its two
        shadow ``Transaction`` rows, which carry the same value (Transfer
        Invariant 3, maintained by
        ``app.services.transfer_service._status.apply_settle_day_to_pair``).  This is
        the read of that shared fact, so the two surfaces that need it -- the
        full-edit form's pre-filled correction input, opened from either the
        transfers page or a grid shadow cell -- ask ONE question rather than
        each re-deriving "which shadow, and what if it is missing".

        Read off the INCOME (to-account) shadow, the same row
        ``posting_service._entry_date`` reads for the pair, so the day this
        renders is the day the ledger files the postings under.

        **It answers ``None`` rather than raising**, which is the difference
        between this read and ``_entry_date``'s: that one is about to WRITE
        real money to a journal entry and must refuse an undated settled row
        (fail loud -- a fabricated date files money on a day nothing recorded);
        this one is filling in a form field, and a form that 500s because a row
        is malformed helps nobody.  The template renders the correction box for
        any SETTLED transfer, dated or not, so an undated one gets a repair path
        rather than a blank.

        **The ``limit(1)`` is what makes that true, and a bare ``.scalar()``
        did not.**  ``Query.scalar()`` swallows ``NoResultFound`` but lets
        ``MultipleResultsFound`` propagate, so a transfer with DUPLICATE income
        shadows -- data corruption ``transfer_service._validation._get_shadow_transactions``
        already fails loud on -- would have 500'd both full-edit popovers, which
        is precisely the outcome the paragraph above says this read avoids.  A
        neutral review caught the contradiction between the code and its own
        docstring.  ``_entry_date`` reaches the same place with ``.first()``;
        the duplicate pair carries one day either way (Transfer Invariant 3),
        and detecting the corruption is that validator's job, not this form
        field's.

        There is no setter, deliberately: ``status_seam.apply_status_change``
        is the single writer of ``Transaction.settled_on``, and an assignable
        property here would be a second door onto it (the shape finding N-183
        closed).  ``AttributeError`` on assignment is that refusal, structural
        rather than reviewed.

        **It QUERIES the one row rather than iterating the backref, and that is
        not a style choice.**  ``shadow_transactions`` declares no ``order_by``
        (``models/transaction.py:330-334``), so iterating it makes "the income
        shadow" and "whichever row this unordered SELECT returned first"
        indistinguishable -- an implementation that reads by POSITION is right
        half the time and no test over a two-row pair can tell the two apart.  A
        neutral review proved exactly that, twice, against two different
        one-sided controls.  Naming the row in SQL removes the ambiguity from
        the code instead of asking a test to detect it.

        **SINGLE-ROW reads only.**  One scoped SELECT per transfer, which is
        right for the full-edit popover -- one transfer, one render -- and an
        N+1 the moment anything loops: the transfers list, a grid row set, a
        projection.  A caller that needs the day for MANY transfers must load
        the shadows itself.
        """
        return self._income_shadow_settle_pair()[0]

    @property
    def settle_day_columns(self):
        """Return ``(settled_on, settled_day_basis_id)`` off the income shadow.

        **ONE read of BOTH columns, and that is a correctness property rather
        than a saving** (plan step **X-az**, corrected by adversarial review
        2026-08-22).  A transfer carries neither column -- its money moves on
        its two shadow ``Transaction`` rows -- so a caller that needs the pair
        must read a shadow, and reading it as two separate PROPERTIES would
        issue a SELECT per attribute ACCESS: five for one call of
        ``settle_day.recorded_settle_day``, over a query whose ``limit(1)``
        deliberately carries no ``ORDER BY`` (:attr:`settled_on` states why it
        tolerates duplicate shadows rather than raising on them).  Those reads
        can straddle two rows -- with duplicate income shadows, or across a
        concurrent commit under READ COMMITTED -- and hand
        ``settle_day.settle_day_from_columns`` a day from one and a basis from
        the other, which it correctly refuses as a ``ValueError`` naming a
        phantom writer: a 500 on the transfer PATCH, which is exactly the
        outcome the ``limit(1)`` was written to prevent.  One read of two
        columns cannot straddle anything.

        Both causes are still live, and the concurrent-commit one is now live
        for a narrower reason: the one caller below is a PATCH, a COMMAND, and
        plan step balance:X-i3 leaves a command at READ COMMITTED precisely so
        a lock-then-reread can see a rival's commit.  The duplicate-shadow
        cause never depended on the isolation level at all.

        **Its ONE caller is the transfer PATCH's echo rule** -- what the pair
        already records, so a re-submitted day does not restate its basis.  It
        pairs with ``settle_day.settle_day_from_columns``, which takes the two
        VALUES rather than a row precisely so a transfer can answer it: a
        ``Transfer`` is not a ``SettleDatedMixin`` and carries neither column,
        so the row-shaped reader beside it cannot be handed one.

        Read off the INCOME (to-account) shadow, the same row
        ``posting_service._entry_date`` reads for the pair, so the day this
        answers is the day the ledger files the postings under.

        There is no setter, for the reason :attr:`settled_on` has none:
        ``status_seam.apply_status_change`` and
        ``settle_day.record_settle_day`` are the writers.

        **SINGLE-ROW reads only**, the boundary :attr:`settled_on` documents:
        one scoped SELECT per transfer, which is right for a form PATCH and an
        N+1 the moment anything loops.

        Returns:
            The pair, or ``(None, None)`` when the transfer has no income shadow
            at all.
        """
        return self._income_shadow_settle_pair()

    def _income_shadow_settle_pair(self):
        """Return ``(settled_on, settled_day_basis_id)`` in ONE query.

        The single read :attr:`settled_on` and :attr:`settle_day_columns` share,
        so the row they take and the reasons they take it are stated once.  The
        ``limit(1)`` is what keeps both total; see :attr:`settled_on` for the
        ``MultipleResultsFound`` measurement that put it there, and for why
        naming the row in SQL beats iterating an unordered backref.

        Returns:
            The pair, or ``(None, None)`` when the transfer has no income
            shadow.
        """
        # Imported here rather than at module scope: ``Transaction`` imports
        # this module for its ``transfer`` relationship, so a top-level import
        # would close the cycle.
        # pylint: disable-next=import-outside-toplevel
        from app.models.transaction import Transaction

        row = (
            db.session.query(
                Transaction.settled_on, Transaction.settled_day_basis_id,
            )
            .filter(
                Transaction.transfer_id == self.id,
                Transaction.account_id == self.to_account_id,
                Transaction.is_deleted.is_(False),
            )
            .limit(1)
            .first()
        )
        return (None, None) if row is None else (row[0], row[1])

    def __repr__(self):
        return f"<Transfer '{self.name}' ${self.amount} ({self.id})>"
