"""
Shekel Budget App -- Ledger Account Model (budget schema)

The chart of accounts for the double-entry posting ledger (Build-Order
Step 2; extended with the per-category cash chart in Step 3 and the per-loan
interest / escrow / refund accounts in Step 4).  Every posting leg
(``budget.account_postings``) lands in exactly one ledger account, and every
ledger account carries a ``class_id`` fixing how a reader later interprets
that account's accumulated debit-positive posting balance (Asset/Expense are
debit-normal; Liability/Income/Equity are credit-normal -- see
:class:`app.models.ref.LedgerAccountClass`).

**The explicit row-kind discriminator (``kind_id``).**  Every row carries a
NOT NULL ``kind_id`` FK to :class:`app.models.ref.LedgerAccountKind` that
names its kind *positively* -- a reader branches on that integer ID, never
on which of ``account_id`` / ``category_id`` / ``is_fallback`` /
``loan_account_id`` happen to be NULL.  ``kind_id`` is the authoritative
discriminator; it is stamped by the sole writer (``ledger_account_service``)
on exactly the same trust contract ``class_id`` carries (see "Storage-tier
shape enforcement" below for what the constraints do and do not police).
Seven kinds coexist in one table:

* **linked** (``account_id`` set, ``category_id`` NULL, ``is_fallback``
  False, ``loan_account_id`` NULL) -- one per real ``budget.accounts`` row,
  created by the account-create sync hook (``ledger_account_service``) and by
  the Step-2 backfill migration.  ``name`` is ``NULL`` and the display label
  derives from the live ``account.name`` via the relationship.  Every such
  row is Asset or Liability (derived from the account-type category:
  Liability category -> Liability class; Asset, Retirement, and Investment
  categories -> Asset class).
* **category** (``account_id`` NULL, ``category_id`` set, ``is_fallback``
  False, ``loan_account_id`` NULL) -- one Income or Expense ledger account
  per budget category per accounting class: the per-category chart of
  accounts the cash-posting step (Build-Order Step 3) books an ordinary
  transaction's counter-leg into.  ``name`` snapshots the category's
  ``display_name`` ("Group: Item") at creation, so renaming the budgeting
  category never rewrites posted history; the ``category_id`` link still
  enables live reporting grouping while the category exists.  A ``Category``
  is type-agnostic, so a category used for both an income and an expense
  transaction correctly yields two rows -- one per class.
* **fallback** (``account_id`` NULL, ``category_id`` NULL, ``is_fallback``
  True, ``loan_account_id`` NULL) -- the per-user ``Uncategorized Income`` /
  ``Uncategorized Expense`` buckets (exactly one per owner per class) that
  catch a settled transaction whose ``category_id`` is NULL.  ``name``
  carries the canonical label.  The ``is_fallback`` flag is what marks a
  row as *the* fallback.
* **orphan** (``account_id`` NULL, ``category_id`` NULL, ``is_fallback``
  False, ``loan_account_id`` NULL) -- a former **category** row whose budget
  category was later deleted: ``category_id`` is SET NULL but the row, its
  ``name`` snapshot, and its immutable postings persist (a permanent,
  now-inactive chart entry -- the accounting analogue of a retired expense
  account).  Orphans are deliberately NOT unique: any number coexist with one
  another and with the fallback of the same class.
* **loan_interest** / **loan_escrow** / **loan_refund** (``loan_account_id``
  set, ``account_id`` NULL, ``category_id`` NULL, ``is_fallback`` False) --
  the three per-loan accounts the Step-4 loan-payment correction books into:
  the loan's accrued-interest Expense account, its configured-escrow Expense
  account, and its payoff-overpayment refund Asset account.  ``name``
  snapshots a per-loan label naming the loan and the component; at most one
  of each kind exists per loan (``uq_ledger_accounts_loan``).

**Why ``is_fallback`` exists.**  A fallback and an orphan are BOTH
``(account_id NULL, category_id NULL)`` -- nothing in those two columns
tells them apart.  Without a discriminator, a per-(owner, class) singleton
over the NULL/NULL space would (a) forbid a second retired category of a
class, and worse (b) make a category delete's ``category_id`` SET NULL
*fail at the database* the moment the orphan it produces lands on an
existing fallback of that class -- the SET NULL is part of the DELETE, so
the whole category delete would raise.  ``is_fallback`` confines the
singleton to the true fallback, so a deleted category becomes a
freely-coexisting orphan and the delete always succeeds.

**Storage-tier shape enforcement.**  The constraints keep each row's column
*shape* mutually exclusive and consistent with its kind; the ``kind_id``
value itself is trusted from the sole writer (the same contract ``class_id``
has -- no CHECK pins ``class_id`` to a valid class for the shape either).
``ck_ledger_accounts_account_or_category_null`` forbids setting both
``account_id`` and ``category_id``; ``ck_ledger_accounts_fallback_shape``
forbids ``is_fallback`` on anything but the NULL/NULL shape;
``ck_ledger_accounts_loan_shape`` forbids a ``loan_account_id`` row from also
carrying an ``account_id`` / ``category_id`` / ``is_fallback`` (so a per-loan
row can never also be a linked / category / fallback row); and each
*constrained* kind has its own partial unique index -- one linked row per
account (``uq_ledger_accounts_account``), one category row per
``(user, category, class)`` (``uq_ledger_accounts_category``), one fallback
per ``(user, class)`` (``uq_ledger_accounts_uncategorized``, keyed
``WHERE is_fallback``), and one per ``(user, loan, kind)``
(``uq_ledger_accounts_loan``, keyed ``WHERE loan_account_id IS NOT NULL``).
Orphans carry no uniqueness by design.

**Why ``ck_ledger_accounts_loan_shape`` does not pin ``kind_id`` to the loan
kinds.**  A CHECK constraint cannot contain a subquery, so "this row's
``kind_id`` is one of the three loan kinds" is inexpressible without
embedding the seeded ``ref.ledger_account_kinds`` row IDs as literals -- which
the project forbids (ref IDs are resolved through ``ref_cache``, never
hardcoded).  The CHECK therefore policies the column *shape* of a loan row,
and "a loan row's kind is a loan kind" is guaranteed by the sole writer
(``ledger_account_service.get_or_create_loan_ledger_account``, Step 4) and its
tests -- the identical, deliberately-accepted trust contract the (un-CHECKed)
``class_id`` already carries (see ``ledger_account_service``).

**Reconciliation of orphans** (a forward note for Build-Order Step 8's
oracle).  An orphan still holds the immutable postings made while its
category was live, but the transactions that produced them now read
``category_id IS NULL`` (``transactions.category_id`` is itself SET NULL on
category delete).  So the counter-leg reconciliation must follow the
postings' ``transaction_id`` linkage on the journal entries (which row
posted to which ledger account), NOT a ``category_id`` match (the category
is gone): that formulation reconciles an orphan against exactly the
transactions whose legs landed on it, and the fallback against the
still-uncategorized remainder.

The ``name`` column is display-only and is never used for logic
(IDs-for-logic invariant).  The display rule is
``COALESCE(account.name, ledger_account.name)``: a linked row reads the
live account name, a category / fallback / orphan / per-loan row reads its
own snapshot.  The ``ck_ledger_accounts_name_present`` CHECK guarantees at
least one of the two is present so the display rule can never resolve to
NULL -- and because a non-linked row has ``account_id`` NULL, that CHECK
forces it to carry a ``name``.

**Write-once, not append-only.**  Rows are created by the sync hook /
backfill and never edited afterwards: ``class_id``, ``kind_id``,
``account_id``, and ``loan_account_id`` are intrinsic, and a linked row's
``name`` stays NULL (the display name lives on ``account.name``).  The table
therefore uses :class:`CreatedAtMixin` (no ``updated_at``).  Unlike the
append-only ledger tables (``journal_entries`` / ``account_postings``) it
carries no ORM immutability guards: a ledger account is a directory entry
that is disposed of -- never corrected -- when its real account is deleted.

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
in that class.  ``kind_id`` is ``ON DELETE RESTRICT`` for the same reason:
the seeded ``ref.ledger_account_kinds`` rows are non-removable invariants,
and a kind delete would strand every ledger account of that kind.

``loan_account_id`` is ``ON DELETE RESTRICT``.  It links a per-loan
interest / escrow / refund row to the loan ``budget.accounts`` row whose
payments that account splits, and is NULL on every other kind.  RESTRICT
(not SET NULL or CASCADE) because such a row accumulates immutable postings:
SET NULL would strand the row's kind (a ``loan_interest`` row with no loan),
and CASCADE would delete a posting-bearing chart entry and orphan its legs
(breaking the per-entry SUM = 0 invariant).  A loan account that has per-loan
ledger rows therefore cannot be deleted -- consistent with "accounts with
history are archived, never deleted," the same disposal contract the linked
``account_id`` CASCADE relies on (that CASCADE is only ever reachable for an
empty, postings-free account; see the impossibility argument above).  Like
``account`` and ``category``, the relationship is one-directional (no backref
on ``Account``), so an account delete never triggers an ORM action on the
ledger row.

``category_id`` is ``ON DELETE SET NULL`` (the same action
``budget.transactions.category_id`` uses).  A category ledger account
accumulates immutable postings, so the ledger account itself can never be
deleted; when the budgeting category it snapshots is deleted, clearing the
back-link (and keeping the ``name`` snapshot) turns the row into an
**orphan** (``is_fallback`` stays False, so it freely coexists with the
fallback and any other orphans -- see the row-kind taxonomy and the
"Why ``is_fallback`` exists" note above; without that discriminator this
SET NULL would collide with the per-(owner, class) fallback singleton and
abort the category delete).  RESTRICT would wrongly forbid deleting a
category that has posted history.  Like ``account``, the relationship is
one-directional (no backref on ``Category``).
"""

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


class LedgerAccount(UserScopedMixin, CreatedAtMixin, db.Model):
    """A chart-of-accounts entry for the double-entry posting ledger.

    Carries the owning ``user_id`` (tenancy), a ``class_id`` fixing the
    accounting class, a ``kind_id`` naming the row kind positively (the
    authoritative discriminator readers branch on), an optional ``account_id``
    linking it 1:1 to a real ``budget.accounts`` row, an optional
    ``category_id`` linking a per-category Income/Expense row to its budget
    category, an ``is_fallback`` flag marking the per-(owner, class)
    Uncategorized bucket, an optional ``loan_account_id`` linking a per-loan
    interest / escrow / refund row to the loan whose payments it splits, and
    an optional display ``name`` (set on the non-linked category / fallback /
    orphan / per-loan rows; a linked row derives its label from
    ``account.name``).  See the module docstring for the seven-kind taxonomy,
    why the loan shape CHECK does not pin ``kind_id``, why ``is_fallback``
    exists, the display rule, and the FK-action rationale (the CASCADE
    impossibility argument for ``account_id``, the RESTRICT for ``class_id`` /
    ``kind_id`` / ``loan_account_id``, and the SET NULL disposal for
    ``category_id``).
    """

    __tablename__ = "ledger_accounts"
    __table_args__ = (
        # Exactly one *linked* ledger account per real account.  Partial
        # (``WHERE account_id IS NOT NULL``) so it constrains only linked
        # rows; the non-linked category/fallback/orphan rows all carry NULL
        # ``account_id`` and fall outside this index (they have their own
        # uniques below).  The ``postgresql_where`` text matches the
        # migration's index DDL byte-for-byte so autogenerate produces no
        # spurious diff.
        db.Index(
            "uq_ledger_accounts_account",
            "account_id",
            unique=True,
            postgresql_where=db.text("account_id IS NOT NULL"),
        ),
        # Exactly one *category* ledger account per (owner, category,
        # class).  Partial (``WHERE category_id IS NOT NULL AND account_id
        # IS NULL``) so it constrains only category rows.  Keyed on
        # ``class_id`` as well as ``category_id`` because a type-agnostic
        # category used for both income and expense correctly yields two
        # rows (Income-class + Expense-class); each is a distinct chart
        # entry.  All three indexed columns are non-NULL within the
        # predicate's scope, so ordinary (NULL-distinct) unique semantics
        # apply cleanly.
        db.Index(
            "uq_ledger_accounts_category",
            "user_id", "category_id", "class_id",
            unique=True,
            postgresql_where=db.text(
                "category_id IS NOT NULL AND account_id IS NULL"
            ),
        ),
        # Exactly one *fallback* ledger account per (owner, class) -- one
        # Uncategorized-Income and one Uncategorized-Expense per user.  Keyed
        # ``WHERE is_fallback`` (NOT ``WHERE category_id IS NULL``) so the
        # singleton confines itself to the true fallback: a deleted-category
        # ORPHAN is also ``(account_id NULL, category_id NULL)`` but carries
        # ``is_fallback`` False, so it stays outside this index and any number
        # of orphans coexist with the fallback (see the module docstring's
        # "Why is_fallback exists" -- keying on ``category_id IS NULL`` would
        # instead make a category delete's SET NULL collide here and abort).
        # ``ck_ledger_accounts_fallback_shape`` guarantees an ``is_fallback``
        # row has the NULL/NULL shape, so the ``(user_id, class_id)`` key
        # (both non-NULL) enforces the singleton cleanly.
        db.Index(
            "uq_ledger_accounts_uncategorized",
            "user_id", "class_id",
            unique=True,
            postgresql_where=db.text("is_fallback"),
        ),
        # At most one *per-loan* ledger account of each kind per loan -- one
        # ``loan_interest``, one ``loan_escrow``, one ``loan_refund`` per
        # (owner, loan).  Partial (``WHERE loan_account_id IS NOT NULL``) so it
        # constrains only the per-loan rows; every other kind carries NULL
        # ``loan_account_id`` and falls outside this index.  ``kind_id`` is in
        # the key because a single loan has up to three distinct per-loan rows
        # (interest / escrow / refund), each a separate chart entry; all three
        # key columns are non-NULL within the predicate's scope, so ordinary
        # NULL-distinct unique semantics apply cleanly.  The ``postgresql_where``
        # text matches the migration's index DDL byte-for-byte so autogenerate
        # produces no spurious diff.
        db.Index(
            "uq_ledger_accounts_loan",
            "user_id", "loan_account_id", "kind_id",
            unique=True,
            postgresql_where=db.text("loan_account_id IS NOT NULL"),
        ),
        # At least one of (name, account_id) is present so the display
        # rule COALESCE(account.name, ledger_account.name) never resolves
        # to NULL: a linked row may omit ``name`` (derives it from
        # ``account.name``); a category/fallback/orphan row (account_id NULL)
        # must carry one.
        db.CheckConstraint(
            "name IS NOT NULL OR account_id IS NOT NULL",
            name="ck_ledger_accounts_name_present",
        ),
        # A row is EITHER linked to a real account OR a category bucket,
        # never both: at most one of (account_id, category_id) is set
        # (both-NULL is the legitimate fallback/orphan shape).  This makes
        # the linked/category/fallback/orphan partition exhaustive and
        # non-overlapping at the storage tier rather than by convention, so
        # no writer bug can mint a both-set row that would slip outside the
        # category uniqueness index.
        db.CheckConstraint(
            "account_id IS NULL OR category_id IS NULL",
            name="ck_ledger_accounts_account_or_category_null",
        ),
        # ``is_fallback`` marks ONLY the Uncategorized fallback bucket, which
        # by definition has neither a real account nor a category.  Forbidding
        # ``is_fallback`` on any other shape keeps the flag a true discriminator
        # (so the fallback singleton index above cannot be subverted by a
        # linked/category row flagged ``is_fallback``) and lets that index key
        # simply ``WHERE is_fallback``.
        db.CheckConstraint(
            "NOT is_fallback OR (account_id IS NULL AND category_id IS NULL)",
            name="ck_ledger_accounts_fallback_shape",
        ),
        # A *per-loan* row (``loan_account_id`` set) is ONLY a per-loan row:
        # it carries no real-account link, no category link, and is not the
        # fallback, so it can never also be a linked / category / fallback row
        # and so falls outside every other partial unique above.  This pins
        # the column *shape* of the three loan kinds; that the row's ``kind_id``
        # is in fact one of the loan kinds is the sole writer's contract (a
        # CHECK cannot subquery ``ref.ledger_account_kinds`` and the project
        # forbids hardcoding its IDs -- see the module docstring's
        # "Why ck_ledger_accounts_loan_shape does not pin kind_id" and the
        # parallel un-CHECKed ``class_id``).  ``NOT is_fallback`` matches the
        # sibling ``ck_ledger_accounts_fallback_shape`` form so the two read
        # alike; the predicate matches the migration's CHECK DDL byte-for-byte.
        db.CheckConstraint(
            "loan_account_id IS NULL OR (account_id IS NULL AND "
            "category_id IS NULL AND NOT is_fallback)",
            name="ck_ledger_accounts_loan_shape",
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
    # The explicit row-kind discriminator (linked / category / fallback /
    # orphan / loan_interest / loan_escrow / loan_refund).  NOT NULL -- every
    # row names its kind positively; readers branch on this integer ID, never
    # on which other FKs are NULL.  Stamped by the sole writer
    # (``ledger_account_service``) on the same trust contract as ``class_id``.
    # RESTRICT: the seeded ``ref.ledger_account_kinds`` rows are non-removable
    # invariants.  Explicit convention FK name (the naming convention is not
    # installed on the metadata, so new FKs name themselves).
    kind_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.ledger_account_kinds.id",
            name="fk_ledger_accounts_kind_id",
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
    # The budget category this Income/Expense ledger account books, or NULL
    # on a linked Asset/Liability row, on the per-user Uncategorized
    # fallback, and on a deleted-category orphan.  SET NULL on category
    # delete (see the module docstring's FK-action rationale): the immutable
    # postings keep the ledger account alive, so clearing the link and
    # retaining the ``name`` snapshot is the correct disposal (producing an
    # orphan).  Mutually exclusive with ``account_id``
    # (``ck_ledger_accounts_account_or_category_null``).  Explicit
    # convention FK name for the same reason as ``class_id`` (the naming
    # convention is not installed on the metadata, so new FKs name
    # themselves).
    category_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.categories.id",
            name="fk_ledger_accounts_category_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    # The loan ``budget.accounts`` row whose payments a per-loan interest /
    # escrow / refund row splits; NULL on every other kind.  RESTRICT (NOT SET
    # NULL or CASCADE): a per-loan row accumulates immutable postings, so the
    # loan account cannot be deleted while it has these rows -- SET NULL would
    # strand the kind, CASCADE would orphan posting legs (see the module
    # docstring's FK-action rationale).  Explicit convention FK name for the
    # same reason as ``class_id`` / ``kind_id``.
    loan_account_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.accounts.id",
            name="fk_ledger_accounts_loan_account_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    # True ONLY on the per-(owner, class) Uncategorized fallback bucket;
    # False on every linked, category, and deleted-category *orphan* row.
    # The discriminator that lets ``uq_ledger_accounts_uncategorized`` apply
    # the singleton to the true fallback while leaving orphans (also NULL/
    # NULL, see the module docstring's "Why is_fallback exists") free to
    # coexist -- which is what keeps a category delete's ``category_id`` SET
    # NULL from colliding with the fallback.  ``ck_ledger_accounts_fallback_shape``
    # ties it to the NULL/NULL shape.
    is_fallback = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    # Display-only label, never used for logic.  NULL on a linked row
    # (display derives from ``account.name``); NOT NULL on a category /
    # fallback / orphan row (its canonical label, snapshotted at creation).
    # The presence of one of the two is enforced by
    # ``ck_ledger_accounts_name_present``.
    name = db.Column(db.String(100), nullable=True)
    # user_id (NOT NULL, CASCADE FK to auth.users.id) from UserScopedMixin.
    # created_at (TIMESTAMPTZ NOT NULL DEFAULT now()) from CreatedAtMixin.

    # Relationships.  ``account`` is one-directional (no backref on
    # Account) so an account delete never triggers an ORM SET-NULL on the
    # ledger row -- the DB-level CASCADE disposes of it (see module
    # docstring).  Eager-loaded because the display rule reads
    # ``account.name`` whenever a ledger account is rendered.  ``foreign_keys``
    # is explicit because the table now has TWO FKs into ``budget.accounts``
    # (``account_id`` and ``loan_account_id``); without it SQLAlchemy cannot
    # pick which one this relationship traverses.
    account = db.relationship(
        "Account", lazy="joined", foreign_keys="LedgerAccount.account_id",
    )
    # The loan a per-loan interest / escrow / refund row splits, via
    # ``loan_account_id``.  ``lazy="select"`` (load on access, no eager JOIN):
    # a per-loan row's display label is its own ``name`` snapshot, never
    # navigated through this relationship, so eager-loading would add a JOIN no
    # display path consumes.  One-directional (no backref on Account), so an
    # account delete never triggers an ORM action here -- the DB-level RESTRICT
    # governs the loan account's disposal.  Kept for ORM navigation (the Step-4
    # poster resolves a loan's per-loan accounts) and the chart-of-accounts
    # shape.
    loan_account = db.relationship(
        "Account", lazy="select", foreign_keys="LedgerAccount.loan_account_id",
    )
    # ``category`` is one-directional (no backref on Category) and
    # ``lazy="select"`` (load on access, no eager JOIN): a category row's
    # display label is its own ``name`` snapshot, never navigated through
    # this relationship, so eager-loading would add a JOIN no display path
    # consumes.  Kept for live reporting grouping (Step 5) while the
    # category exists and for ORM navigation; the SET NULL on category
    # delete leaves it resolving to None with the ``name`` snapshot intact.
    category = db.relationship("Category", lazy="select")
    # ``lazy="select"`` (load on access, no eager JOIN): a reader gets the
    # class's natural-balance side from the cached
    # ``ref_cache.ledger_class_is_debit_normal`` accessor keyed by
    # ``class_id``, never by navigating this relationship, so eager-loading
    # it would add a JOIN to every LedgerAccount query for data no path
    # consumes.  Kept for ORM navigation / debugging and the chart-of-accounts
    # shape; a future reader that needs the row can opt into joinedload.
    ledger_account_class = db.relationship("LedgerAccountClass", lazy="select")
    # ``lazy="select"`` (load on access, no eager JOIN): a reader resolves the
    # row kind from the cached ``ref_cache.ledger_account_kind_id`` accessor
    # keyed by ``kind_id``, never by navigating this relationship, so
    # eager-loading it would add a JOIN no path consumes.  Kept for ORM
    # navigation / debugging and the chart-of-accounts shape, mirroring
    # ``ledger_account_class``.
    ledger_account_kind = db.relationship("LedgerAccountKind", lazy="select")

    def __repr__(self):
        return (
            f"<LedgerAccount id={self.id} kind_id={self.kind_id} "
            f"account_id={self.account_id} category_id={self.category_id} "
            f"loan_account_id={self.loan_account_id} "
            f"is_fallback={self.is_fallback} class_id={self.class_id}>"
        )
