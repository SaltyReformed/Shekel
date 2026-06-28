"""
Shekel Budget App -- Ledger Account Model (budget schema)

The chart of accounts for the double-entry posting ledger (Build-Order
Step 2).  Every posting leg (``budget.account_postings``, Commit 3) lands
in exactly one ledger account, and every ledger account carries a
``class_id`` fixing how a reader later interprets that account's
accumulated debit-positive posting balance (Asset/Expense are
debit-normal; Liability/Income/Equity are credit-normal -- see
:class:`app.models.ref.LedgerAccountClass`).

Two kinds of row coexist in one table:

* **Linked rows** -- one per real ``budget.accounts`` row, created by the
  account-create sync hook (``ledger_account_service``) and by the Step-2
  backfill migration.  ``account_id`` is set; ``name`` is ``NULL`` and the
  display label derives from the live ``account.name`` via the
  relationship.  Every such row is Asset or Liability (derived from the
  account-type category: Liability category -> Liability class; Asset,
  Retirement, and Investment categories -> Asset class).
* **Unlinked rows** -- Income/Expense/Equity ledger accounts that later
  Build-Order steps add (category->expense-account promotion in Step 3,
  reporting equity in Step 5).  ``account_id`` is ``NULL`` and ``name``
  carries the canonical label.  None exist in Step 2.

The ``name`` column is display-only and is never used for logic
(IDs-for-logic invariant).  The display rule is
``COALESCE(account.name, ledger_account.name)``: a linked row reads the
live account name, an unlinked row reads its own.  The
``ck_ledger_accounts_name_present`` CHECK guarantees at least one of the
two is present so the display rule can never resolve to NULL.

**Write-once, not append-only.**  Rows are created by the sync hook /
backfill and never edited afterwards: ``class_id`` and ``account_id`` are
intrinsic, and a linked row's ``name`` stays NULL (the display name lives
on ``account.name``).  The table therefore uses :class:`CreatedAtMixin`
(no ``updated_at``).  Unlike the append-only ledger tables
(``journal_entries`` / ``account_postings``, Commit 3) it carries no
ORM immutability guards: a ledger account is a directory entry that is
disposed of -- never corrected -- when its real account is deleted.

**FK-action rationale (the cascade-imbalance impossibility argument).**
``account_id`` is ``ON DELETE CASCADE`` so a freshly-created *empty*
account deletes cleanly, taking its (postings-free) ledger account with
it.  A ledger account that *has* postings can never be reached by an
account delete: such an account necessarily has settled transfer **shadow
transactions**, and ``transactions.account_id`` /
``transfers.from|to_account_id`` are ``ON DELETE RESTRICT``
(``transaction.py``, ``transfer.py``) -- the account delete is refused
before any cascade fires.  So CASCADE can never orphan a posting leg.
(Accounts with history are *archived*, ``is_active=False``, never
deleted.)  The relationship to :class:`~app.models.account.Account` is
deliberately one-directional (no backref): ``Account`` has no awareness
of its ledger account, so deleting an account never triggers an ORM
SET-NULL on the ledger row -- the database-level CASCADE is what removes
the paired row, cleanly.

``class_id`` is ``ON DELETE RESTRICT`` because the seeded
``ref.ledger_account_classes`` rows are non-removable application
invariants: a successful class delete would strand every ledger account
in that class.
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


class LedgerAccount(UserScopedMixin, CreatedAtMixin, db.Model):
    """A chart-of-accounts entry for the double-entry posting ledger.

    Carries the owning ``user_id`` (tenancy), a ``class_id`` fixing the
    accounting class, an optional ``account_id`` linking it 1:1 to a real
    ``budget.accounts`` row, and an optional display ``name`` (set only on
    unlinked Income/Expense/Equity rows).  See the module docstring for
    the linked/unlinked row split, the display rule, and the FK-action
    impossibility argument that keeps CASCADE safe.
    """

    __tablename__ = "ledger_accounts"
    __table_args__ = (
        # Exactly one *linked* ledger account per real account.  Partial
        # (``WHERE account_id IS NOT NULL``) so it constrains only linked
        # rows; the unlinked Income/Expense/Equity rows later steps add all
        # carry NULL ``account_id`` and fall outside this index.  Uniqueness
        # among unlinked rows (e.g. one expense account per category) is
        # intentionally deferred to the step that first writes them -- its
        # natural key (name vs. category_id) is a Step-3 design decision, not
        # presumed here.  The ``postgresql_where`` text matches the
        # migration's index DDL byte-for-byte so autogenerate produces no
        # spurious diff.
        db.Index(
            "uq_ledger_accounts_account",
            "account_id",
            unique=True,
            postgresql_where=db.text("account_id IS NOT NULL"),
        ),
        # At least one of (name, account_id) is present so the display
        # rule COALESCE(account.name, ledger_account.name) never resolves
        # to NULL: a linked row may omit ``name`` (derives it from
        # ``account.name``); an unlinked row must carry one.
        db.CheckConstraint(
            "name IS NOT NULL OR account_id IS NOT NULL",
            name="ck_ledger_accounts_name_present",
        ),
        # Ownership-filtered queries (reconciliation, reporting) scan by
        # user_id.
        db.Index("idx_ledger_accounts_user", "user_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # The accounting class.  RESTRICT: the seeded ref rows are
    # non-removable invariants (see module docstring).  Explicit
    # convention name (``fk_<table>_<column_0_name>``) per
    # ``app.extensions.SHEKEL_NAMING_CONVENTION`` -- the convention is not
    # installed on the metadata, so new FKs name themselves.
    class_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.ledger_account_classes.id",
            name="fk_ledger_accounts_class_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    # The linked real account, or NULL for an unlinked
    # Income/Expense/Equity row (Steps 3-5).  CASCADE: see the module
    # docstring's impossibility argument -- a CASCADE here can only ever
    # fire for an empty (postings-free) account.  Explicit convention
    # name for the same reason as ``class_id``.
    account_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.accounts.id",
            name="fk_ledger_accounts_account_id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )
    # Display-only label, never used for logic.  NULL on a linked row
    # (display derives from ``account.name``); NOT NULL on an unlinked row
    # (its canonical label).  The presence of one of the two is enforced
    # by ``ck_ledger_accounts_name_present``.
    name = db.Column(db.String(100), nullable=True)
    # user_id (NOT NULL, CASCADE FK to auth.users.id) from UserScopedMixin.
    # created_at (TIMESTAMPTZ NOT NULL DEFAULT now()) from CreatedAtMixin.

    # Relationships.  ``account`` is one-directional (no backref on
    # Account) so an account delete never triggers an ORM SET-NULL on the
    # ledger row -- the DB-level CASCADE disposes of it (see module
    # docstring).  Eager-loaded because the display rule reads
    # ``account.name`` whenever a ledger account is rendered.
    account = db.relationship("Account", lazy="joined")
    # ``lazy="select"`` (load on access, no eager JOIN): a reader gets the
    # class's natural-balance side from the cached
    # ``ref_cache.ledger_class_is_debit_normal`` accessor keyed by
    # ``class_id``, never by navigating this relationship, so eager-loading
    # it would add a JOIN to every LedgerAccount query for data no path
    # consumes.  Kept for ORM navigation / debugging and the chart-of-accounts
    # shape; a future reader that needs the row can opt into joinedload.
    ledger_account_class = db.relationship("LedgerAccountClass", lazy="select")

    def __repr__(self):
        return (
            f"<LedgerAccount id={self.id} account_id={self.account_id} "
            f"class_id={self.class_id}>"
        )
