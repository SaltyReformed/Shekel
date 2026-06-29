"""
Shekel Budget App -- Ledger Account Model (budget schema)

The chart of accounts for the double-entry posting ledger (Build-Order
Step 2).  Every posting leg (``budget.account_postings``, Commit 3) lands
in exactly one ledger account, and every ledger account carries a
``class_id`` fixing how a reader later interprets that account's
accumulated debit-positive posting balance (Asset/Expense are
debit-normal; Liability/Income/Equity are credit-normal -- see
:class:`app.models.ref.LedgerAccountClass`).

Four kinds of row coexist in one table, distinguished by ``account_id`` /
``category_id`` / ``is_fallback`` (the distinction is storage-enforced --
see the FK / index / CHECK rationale below):

* **Linked rows** (``account_id`` set, ``category_id`` NULL,
  ``is_fallback`` False) -- one per real ``budget.accounts`` row, created
  by the account-create sync hook (``ledger_account_service``) and by the
  Step-2 backfill migration.  ``name`` is ``NULL`` and the display label
  derives from the live ``account.name`` via the relationship.  Every such
  row is Asset or Liability (derived from the account-type category:
  Liability category -> Liability class; Asset, Retirement, and Investment
  categories -> Asset class).
* **Category rows** (``account_id`` NULL, ``category_id`` set,
  ``is_fallback`` False) -- one Income or Expense ledger account per budget
  category per accounting class: the per-category chart of accounts the
  cash-posting step (Build-Order Step 3) books an ordinary transaction's
  counter-leg into.  ``name`` snapshots the category's ``display_name``
  ("Group: Item") at creation, so renaming the budgeting category never
  rewrites posted history; the ``category_id`` link still enables live
  reporting grouping while the category exists.  A ``Category`` is
  type-agnostic, so a category used for both an income and an expense
  transaction correctly yields two rows -- one per class.
* **Fallback rows** (``account_id`` NULL, ``category_id`` NULL,
  ``is_fallback`` True) -- the per-user ``Uncategorized Income`` /
  ``Uncategorized Expense`` buckets (exactly one per owner per class) that
  catch a settled transaction whose ``category_id`` is NULL.  ``name``
  carries the canonical label.  The ``is_fallback`` flag is what marks a
  row as *the* fallback.
* **Orphan rows** (``account_id`` NULL, ``category_id`` NULL,
  ``is_fallback`` False) -- a former **category row** whose budget category
  was later deleted: ``category_id`` is SET NULL but the row, its ``name``
  snapshot, and its immutable postings persist (a permanent, now-inactive
  chart entry -- the accounting analogue of a retired expense account).
  Orphans are deliberately NOT unique: any number coexist with one another
  and with the fallback of the same class.

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

The four kinds are mutually exclusive at the storage tier:
``ck_ledger_accounts_account_or_category_null`` forbids setting both
``account_id`` and ``category_id``; ``ck_ledger_accounts_fallback_shape``
forbids ``is_fallback`` on anything but the NULL/NULL shape; and each
*constrained* kind has its own partial unique index -- one linked row per
account (``uq_ledger_accounts_account``), one category row per
``(user, category, class)`` (``uq_ledger_accounts_category``), one fallback
per ``(user, class)`` (``uq_ledger_accounts_uncategorized``, keyed
``WHERE is_fallback``).  Orphans carry no uniqueness by design.

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
live account name, a category / fallback / orphan row reads its own
snapshot.  The ``ck_ledger_accounts_name_present`` CHECK guarantees at
least one of the two is present so the display rule can never resolve to
NULL -- and because a non-linked row has ``account_id`` NULL, that CHECK
forces it to carry a ``name``.

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
    accounting class, an optional ``account_id`` linking it 1:1 to a real
    ``budget.accounts`` row, an optional ``category_id`` linking a
    per-category Income/Expense row to its budget category, an
    ``is_fallback`` flag marking the per-(owner, class) Uncategorized
    bucket, and an optional display ``name`` (set on the non-linked
    category / fallback / orphan rows; a linked row derives its label from
    ``account.name``).  See the module docstring for the
    linked/category/fallback/orphan row split, why ``is_fallback`` exists,
    the display rule, and the FK-action rationale (the CASCADE
    impossibility argument for ``account_id`` and the SET NULL disposal for
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
    # ``account.name`` whenever a ledger account is rendered.
    account = db.relationship("Account", lazy="joined")
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

    def __repr__(self):
        return (
            f"<LedgerAccount id={self.id} account_id={self.account_id} "
            f"category_id={self.category_id} is_fallback={self.is_fallback} "
            f"class_id={self.class_id}>"
        )
