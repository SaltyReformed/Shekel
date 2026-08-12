"""
Shekel Budget App -- Transfer Model (budget schema)

Tracks transfers between accounts (checking ↔ savings) within pay periods.
Supports both template-generated recurring transfers and ad-hoc one-time transfers.
"""

from decimal import Decimal

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
        # One non-deleted, non-override transfer per template per period
        # per scenario.  Mirrors the relaxed transactions index: override
        # siblings may coexist with their rule-generated parent so
        # carry-forward can move unpaid recurring transfers into a target
        # period that already holds the next rule-generated instance.
        # transfer_recurrence.py already skips generation when an
        # is_override = TRUE transfer exists in the period.
        db.Index(
            "idx_transfers_template_period_scenario",
            "transfer_template_id", "pay_period_id", "scenario_id",
            unique=True,
            postgresql_where=db.text(
                "transfer_template_id IS NOT NULL "
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
    amount = db.Column(db.Numeric(12, 2), nullable=False)
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
    def effective_amount(self):
        """Return the amount used in balance calculations.

        Transfers with an excluded status (Cancelled) contribute 0.
        """
        if self.status and self.status.excludes_from_balance:
            return Decimal("0")
        return self.amount

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
        # Imported here rather than at module scope: ``Transaction`` imports
        # this module for its ``transfer`` relationship, so a top-level import
        # would close the cycle.
        # pylint: disable-next=import-outside-toplevel
        from app.models.transaction import Transaction

        return (
            db.session.query(Transaction.settled_on)
            .filter(
                Transaction.transfer_id == self.id,
                Transaction.account_id == self.to_account_id,
                Transaction.is_deleted.is_(False),
            )
            .limit(1)
            .scalar()
        )

    def __repr__(self):
        return f"<Transfer '{self.name}' ${self.amount} ({self.id})>"
