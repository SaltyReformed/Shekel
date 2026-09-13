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
from app.models._transaction_table_args import transaction_table_args
from app.models.mixins import (
    OptimisticLockMixin,
    SettleDatedMixin,
    SoftDeleteOverridableMixin,
    TimestampMixin,
)


class _NotAQueryExpression:
    """What a :class:`_DerivedFlag` answers at CLASS level: a refusal.

    Every comparison, comparator lookup, truth test and SQL coercion
    raises, so ``filter_by(flag=True)``, ``Transaction.flag == True``,
    ``Transaction.flag.is_(True)``, a bare ``where(Transaction.flag)`` and
    an ORM-enabled ``insert(Transaction).values(flag=...)`` all fail to
    BUILD rather than silently matching nothing.  ``hasattr`` still answers
    ``True`` -- the declarative constructor asks it before
    ``Transaction(flag=...)`` may reach the setter -- because merely
    reaching the name is not the misuse; and a DUNDER lookup gets the
    ordinary ``AttributeError``, so ``copy``, ``pickle`` and ``pydoc`` see
    an object rather than a refusal.  What passes through is a STRING that
    names the column -- ``order_by("is_envelope")``, ``text(...)`` -- which
    is the raw-SQL boundary the column comment states.
    """

    __slots__ = ("_name",)

    def __init__(self, name):
        """Remember which flag this stands for, so the refusal can name it."""
        self._name = name

    def _refuse(self, *_args, **_kwargs):
        """Raise the one refusal; bound below to every operator a query uses."""
        raise TypeError(
            f"{self._name} is a per-row derivation, not a column, so it "
            "cannot key a query: the answer for a template-generated row is "
            "its template's and lives on another table.  Load the rows and "
            "ask each one."
        )

    __eq__ = _refuse
    __ne__ = _refuse
    __bool__ = _refuse
    __hash__ = None
    # SQLAlchemy's coercion asks for this before anything else, so a bare
    # ``where(Transaction.flag)`` reaches the refusal by name rather than
    # the generic "SQL expression element expected".
    __clause_element__ = _refuse

    def __getattr__(self, attr):
        """Refuse ``.is_()``, ``.in_()`` and every other comparator lookup.

        A dunder -- ``__deepcopy__``, ``__reduce_ex__``, ``__wrapped__`` --
        and the slot itself get the ordinary ``AttributeError``: the first
        so protocol probes behave, the second so an instance built by
        ``__new__`` alone (what ``copy`` and ``pickle`` make) cannot recurse
        through this method looking for the name it has not been given.
        """
        if attr == "_name" or attr.startswith("__"):
            raise AttributeError(attr)
        self._refuse()


class _DerivedFlag(property):
    """A per-row derivation whose class-level name is NOT a query expression.

    A plain ``property`` reached at class level is a ``property`` object,
    and that object compares ``False`` to everything: measured 2026-09-11,
    ``Transaction.is_envelope == True`` was ``False`` and
    ``query.filter_by(is_envelope=True)`` returned no rows with no error --
    a silent wrong answer, which is worse than the wrong column it replaced.
    This subclass answers a :class:`_NotAQueryExpression` at class level and
    is otherwise a ``property``: ``setter`` works, a flag declared without
    one refuses assignment, and instance reads run the derivation.

    A ``hybrid_property`` was rejected: giving the derivation a SQL body
    would be a second spelling of one rule (ruling **R-IZ**), and no query
    keys on any of these flags or is planned to.
    """

    def __init__(self, fget=None, fset=None, fdel=None, doc=None):
        # The same four positionals ``property`` takes, because
        # ``property.setter`` rebuilds the descriptor as ``type(self)(...)``
        # and this subclass has to survive that round trip.
        super().__init__(fget, fset, fdel, doc)
        self._qualname = fget.__qualname__

    def __get__(self, obj, owner=None):
        if obj is None:
            return _NotAQueryExpression(self._qualname)
        return super().__get__(obj, owner)


class Transaction(
    OptimisticLockMixin,
    SettleDatedMixin,
    SoftDeleteOverridableMixin,
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
    # **MOVED WHOLE to :mod:`app.models._transaction_table_args`** (developer
    # 2026-09-06, under ruling **balance:R-IR** -- the ceiling stays and the
    # session that breaks a module is the one that splits it).  This module
    # stood at 997 of pylint's 1000-line ceiling, so the next constraint the
    # balance arc adds had nowhere to go.  The value is UNCHANGED, and the
    # evidence is an AST comparison rather than a count: all 25 members are
    # identical in kind, in order and in every keyword argument.  The argument
    # for each index and constraint travelled with it.
    __table_args__ = transaction_table_args

    id = db.Column(db.Integer, primary_key=True)
    # WHO THIS ROW BELONGS TO (plan step ``pay_calendar:C13-a``, ruling
    # **R-PC32**).  Held equal to BOTH parents' owner by the two composite keys
    # in :mod:`app.models._transaction_table_args`; see the class docstring for
    # why that is a co-located key and not the cached copy rule 14 forbids.
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
    # WHETHER THIS ROW TAKES PURCHASE ENTRIES, on the row's OWN say-so -- and
    # that is a fact only for an AD-HOC row (``template_id IS NULL``).  A
    # template-generated row's answer is its DEFINITION's
    # (``TransactionTemplate.is_envelope``): nothing writes this cell from the
    # template and nothing may read it there, so on such a row it is DEAD.
    # It stays because for an ad-hoc row it is CONSTITUTIVE -- an input
    # nothing else can compute, ruling **R-IY**'s boundary -- and not a copy.
    # Plan step ``balance:X-bi-5`` deletes the concept.
    #
    # **SEALED since plan step balance:X-bi-1** (ruling **R-JQ**; the seal
    # over a pin, developer 2026-09-11), on the pattern ``__estimated_amount``
    # set at X-au-k: the SQL name is unchanged and the column declaration
    # identical (type, nullability, both defaults -- the autogenerate delta
    # against ``origin/dev`` was empty; its POSITION in ``CREATE TABLE``
    # moves, which is load-bearing nowhere here, see ``UserScopedMixin``),
    # and the public name :attr:`is_envelope` is a PROPERTY whose read is
    # :attr:`tracks_purchases` and whose write lands here.  So the dead cell
    # has no public name to be read by.  ``txn.is_envelope`` on a
    # template-generated row answers the TEMPLATE, in Python and in Jinja;
    # ``Transaction.is_envelope`` is no column, so a query keyed on it does
    # not build -- and neither does an ORM-enabled Core statement naming the
    # attribute, ``insert(Transaction).values(is_envelope=...)``; a Core
    # writer that must reach the cell says ``Transaction.__table__.c``.
    # Keying on this cell read 4 envelopes where there are 238
    # (``docs/design/from_scratch_architecture.md`` section 4.3, measured
    # 2026-09-01 on a production restore) and a migration draft did exactly
    # that.  What the seal cannot reach is SQL that names the column as a
    # STRING -- a migration, a ``psql`` session, ``text()``, a string
    # ``order_by`` -- which ruling **R-HJ** forbids and X-bi-5 makes
    # unwritable.
    #
    # Double-underscored for the same reason its neighbours are: a guessed
    # ``row._is_envelope`` binds a plain instance attribute that reaches no
    # column, and the next read exposes the miss.
    __is_envelope = db.Column(
        "is_envelope", db.Boolean, nullable=False, default=False,
        server_default="false",
    )
    # WHETHER A COMPANION OF THE OWNER MAY SEE THIS ROW, on the row's OWN
    # say-so -- the twin of the cell above, dead on a template-generated row
    # (its answer is ``TransactionTemplate.companion_visible``) and
    # constitutive on an ad-hoc one, for the same reasons.
    #
    # **SEALED since plan step balance:X-bi-1b** (ruling **R-BAL19**; the
    # public name :attr:`companion_visible` reads :attr:`visible_to_companion`
    # and writes here; the column declaration is unchanged and no migration
    # rides).  It could not take the seal beside ``is_envelope`` at X-bi-1
    # because ``companion_service`` keyed SQL on it -- the accessor's rule
    # spelled a second time, in another language, on the predicate that
    # decides what a companion may see (finding **BAL-482**).  That query now
    # loads the period's rows and asks each one, so the property is the ONE
    # spelling and this cell has no public name to be keyed on.  Measured
    # 2026-09-12 on a production restore, over the 951 live rows of the
    # baseline scenario: all 636 generated rows hold ``false`` here, 229 of
    # them under a definition that says ``true``, and 3 of the 315 ad-hoc
    # rows hold ``true`` -- so a reader keyed on this cell sees 3 visible
    # rows where there are 232.
    #
    # Unlike its twin, no plan step names this cell for deletion: the
    # movement unification (``X-bi``) retires the ENVELOPE concept, while "may
    # the companion see this ad-hoc item" outlives it.  So the dead half on a
    # generated row keeps a writer (the setter below, reached by a crafted
    # PATCH) with nothing bounding it, which is finding **BAL-484**.  The seal
    # keeps that half unreadable in the application -- Python, Jinja and the
    # ORM -- and that is the whole of its reach: SQL naming the column as a
    # STRING (a migration, a ``psql`` session, ``text()``) can still read it,
    # and no ruling forbids that reader.
    __companion_visible = db.Column(
        "companion_visible", db.Boolean, nullable=False, default=False,
        server_default="false",
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
    # rolls an unspent envelope forward as an ``is_override`` row, and the
    # one-time branch of ``routes/transfers/_instances.py`` materialises a
    # transfer whose template has no rule at all.  **The envelope row is DATED
    # since 2026-09-06** (``_execute._leftover_due_date``) and this column is
    # still NULL: a row priced by its definition resolves on its own due date,
    # but no occurrence names it.  On a production clone the backfill
    # considered 736 of 788 template-linked rows -- the rest sit on archived
    # templates it does not walk -- stamped 726 and left 10 NULL.
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
    # ``ck_transactions_settle_day_basis_pairing``, in
    # :mod:`app.models._transaction_table_args`, welds the two NULL-nesses
    # (plan step **X-az**, finding **N-332**).
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
    # ``fk_transactions_reconciled_by``, in
    # :mod:`app.models._transaction_table_args`, for why a single-column one
    # cannot express the rule.
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

    @_DerivedFlag
    def tracks_purchases(self):
        """True if individual purchase entries apply to this transaction.

        **The ONE accessor** for the "is this an envelope / entry-capable
        row?" question across services, routes and templates (ruling
        **R-JQ**, plan step ``balance:X-bi-1``): it DERIVES and is never
        stored.  Resolution rule: a template-generated transaction defers
        to its template's ``is_envelope`` flag (the template owns the
        setting for every instance it generates); an ad-hoc transaction
        (no template) uses its own sealed cell.  Accesses the template
        relationship only when ``template_id`` is set, so ad-hoc rows
        never trigger a lazy load.

        A :class:`_DerivedFlag` rather than a hybrid, on purpose: no query
        keys on this predicate and none is planned -- the settle path that
        needs the purchase-tracked set (``X-bi-3``) answers it at a door,
        not in a ``WHERE`` -- so a query written over the class-level name
        refuses to build.
        """
        if self.template_id is None:
            return self.__is_envelope
        return self.template.is_envelope

    @_DerivedFlag
    def is_envelope(self):
        """Return :attr:`tracks_purchases`; the public name of the sealed cell.

        This name exists so the ad-hoc doors can STATE the row's own setting
        -- ``Transaction(is_envelope=True)``, a ``setattr`` over the PATCH
        payload's field name, the form's checkbox -- without learning the
        private column.  Its READ is the one accessor, so a reader that
        reaches for the column name gets the derivation: on a
        template-generated row that is the template's answer, never the
        dead cell.  See the column comment for why (plan step
        ``balance:X-bi-1``).

        Returns:
            Whether this row takes purchase entries.
        """
        return self.tracks_purchases

    @is_envelope.setter
    def is_envelope(self, value):
        """Record the row's OWN purchase-tracking setting.

        Lands on the sealed cell for any row.  On a template-generated row
        the write is inert -- nothing reads the cell there -- and the
        popover renders no control for it, so only a crafted PATCH reaches
        this arm; a refusal was considered and left for ``X-bi-5``, which
        deletes the cell (developer, 2026-09-11).

        Args:
            value: The setting, coerced by the column type.
        """
        self.__is_envelope = value

    @_DerivedFlag
    def visible_to_companion(self):
        """True if a companion of the owner may see this transaction.

        **The ONE accessor** for the companion-visibility question, and
        :attr:`tracks_purchases`'s mirror in every respect since plan step
        ``balance:X-bi-1b`` (ruling **R-BAL19**): a template-generated
        transaction defers to its template's ``companion_visible`` flag; an
        ad-hoc transaction uses its own sealed cell.  Accesses the template
        relationship only when ``template_id`` is set, so ad-hoc rows never
        trigger a lazy load.

        It is a SECURITY predicate -- ``auth_helpers.get_accessible_transaction``
        and ``companion_service`` both decide what a companion may see by it
        -- and it has no second spelling: the service loads the period's rows
        and asks each one rather than restating this rule in ``WHERE``, which
        is why the class-level name refuses to key a query.
        """
        if self.template_id is None:
            return self.__companion_visible
        return self.template.companion_visible

    @_DerivedFlag
    def companion_visible(self):
        """Return :attr:`visible_to_companion`; the public name of the sealed cell.

        :attr:`is_envelope`'s twin, for the same reason: the ad-hoc doors
        STATE the row's own setting under the column's name --
        ``Transaction(companion_visible=True)``, a ``setattr`` over the PATCH
        payload's field name, the popover's checkbox -- and its READ is the
        one accessor, so a reader that reaches for the column name gets the
        template's answer on a generated row, never the dead cell.  See the
        column comment (plan step ``balance:X-bi-1b``).

        Returns:
            Whether a companion of the owner may see this row.
        """
        return self.visible_to_companion

    @companion_visible.setter
    def companion_visible(self, value):
        """Record the row's OWN companion-visibility setting.

        Lands on the sealed cell for any row.  On a template-generated row
        the write is inert -- nothing reads the cell there -- and the
        popover renders no control for it, so only a crafted PATCH reaches
        this arm.  :attr:`is_envelope` accepts the same inert write, and
        its refusal was left for ``X-bi-5`` because that step deletes the
        cell; no step deletes THIS cell, so here the unrefused write has no
        bound, which is what finding **BAL-484** records (see the column
        comment).

        Args:
            value: The setting, coerced by the column type.
        """
        self.__companion_visible = value

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
