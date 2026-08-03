"""The cash ledger's CIVIL-DAY vocabulary: two kinds of day, and one boundary.

Every question this arc turns on is asked between two calendar days, and the two
are not the same KIND of thing:

* :class:`MovedOn` -- **the civil day an event counts from.**  A settled row's
  cash day, a purchase's observed posting day, the day a journal entry records,
  the payday a modelled payroll contribution lands on.  It is the LEFT side of
  the question.
* :class:`ObservedOn` -- **the civil day a balance was declared TRUE for.**  One
  ``AccountAnchorHistory`` row's ``observed_on``; the day an assertion is the
  CLOSING balance for (ruling R-DH (a)).  It is the RIGHT side.
* :class:`ReconciledThrough` -- the BOUNDARY an account's latest assertion
  establishes, and :meth:`ReconciledThrough.covers` is the one implementation of
  the question itself.

**They are separate TYPES because comparing them by hand is what cost production
``$4,001.42``** (``docs/audits/balance_architecture/anchor_settle_partition.md``).
Plan step 3 fenced the boundary -- ``settled_on <= reconciled_through`` has been a
``TypeError`` since -- and left the two FACT FIELDS as plain ``date``s, so
``x <= fact.observed_on``, verbatim the line that step deleted from the posting
walk, still compiled in any new module.  That residue is finding **N-135**, and
ruling **R-DJ** discharges it here: with a day of each kind, both
``moved <= observed`` and ``observed <= moved`` raise, because neither type
defines an ordering against the other or against a bare ``date``.

**One type would not have been enough, and that is N-135's own wording.**  A
single ``CivilDay`` wrapper would stop a bare ``date`` sneaking in and would
still let a settled day be compared against an observed one -- which is the
comparison, not the operand type, that the whole arc is about.

**Ordering within a kind is deliberate and safe.**  Both records are
``order=True`` so a stream of events sorts naturally
(``settled_cash_facts`` sorts on ``(settled_on, transaction_id)``).  Section 14.4
measured that this does NOT reopen the hole: the generated dunders compare the
same class only and return ``NotImplemented`` against anything else, so Python
falls through to a ``TypeError`` exactly as it does with no ordering at all.

**Raw days enter through named doors and leave through one named field.**
:meth:`MovedOn.recorded` promotes a stored nullable column; the plain constructor
takes a day already known to exist; and ``.civil_day`` is the documented escape
hatch for the places that genuinely need a bare ``date`` -- a period-bucketing
lookup, a sort key, an SQL bound, a stored ``journal_entries.entry_date``.
Reaching for it is a visible act at the call site, which a ``<=`` was not.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data in,
frozen dataclasses out.  This module is the package's FLOOR -- it imports nothing
from the app at all -- so every other cash-ledger module can state its days in
this vocabulary without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, order=True)
class MovedOn:
    """The civil day an event counts from -- the LEFT side of R-DH's question.

    One kind of day, wherever it is read from: a settled transaction's cash day
    (:attr:`~app.services.cash_ledger.CashSourceFact.settled_on`), a purchase's
    observed posting day (``TransactionEntry.settled_on``, "the day the bank TOOK
    the money"), the day a journal entry records
    (``JournalEntry.entry_date``), and the payday a modelled payroll
    contribution lands on (``balance_at._asset_contributions``, which emits
    ``(period.start_date, amount)`` as the event's own date).  Ruling R-DJ
    traced all four and found them one concept, which is why
    :meth:`ReconciledThrough.covers` can narrow to this type without losing the
    totality Section 14.2 rules load-bearing: every event has a day it counts
    from.

    Attributes:
        civil_day: The day itself, in the USER's timezone (ruling R-DH (b)).
            Read it only where a bare ``date`` is genuinely required -- a
            period-bucketing lookup, a stored column, an SQL bound.  To ask
            whether the event is inside a declared balance, call
            :meth:`ReconciledThrough.covers`.
    """

    civil_day: date

    @classmethod
    def recorded(cls, civil_day: date | None) -> "MovedOn | None":
        """Promote a STORED nullable day, or ``None`` when none was recorded.

        The door for a column that may legitimately hold no day --
        ``TransactionEntry.settled_on``, which is NULL until the user has SEEN
        the purchase on a statement (ruling R-DH (d) as restated at plan step
        S1-c: the engine never guesses a posting day).  ``None`` in means
        ``None`` out, and :meth:`ReconciledThrough.covers` answers ``False`` for
        it -- the conservative arm, where the envelope keeps holding its whole
        budget back.

        It is separate from the plain constructor so the two cases read
        differently at the call site: ``MovedOn(day)`` says the day is known to
        exist (a NOT NULL derivation, a period start), and
        ``MovedOn.recorded(day)`` says it may not have been recorded yet.  One
        combined door taking ``date | None`` would let a caller pass a nullable
        column where a concrete day is required and lose the distinction
        silently.

        Args:
            civil_day: The stored day, or ``None`` when the fact has never been
                observed.

        Returns:
            The wrapped day, or ``None``.
        """
        return None if civil_day is None else cls(civil_day)


@dataclass(frozen=True, order=True)
class ObservedOn:
    """The civil day a balance was declared TRUE for -- the RIGHT side.

    One ``AccountAnchorHistory`` row's stored ``observed_on``: the day its
    asserted balance is the CLOSING balance for (ruling R-DH (a)).  It is a
    different KIND of day from :class:`MovedOn` and carries no ordering against
    it, so the comparison that decides real money cannot be written by hand.

    It is the assertion's BUSINESS date and never its recording instant.  The
    two were the same value until plan step 2 made the column user-supplied;
    a balance asserted for an earlier day but recorded later is not the current
    one, and ordering on the keystroke would have named it.

    Attributes:
        civil_day: The day itself, in the user's timezone.  Read it where a bare
            ``date`` is required -- the day a correction's journal entry is
            stamped with, a period-bucketing lookup, a rendered caption.
    """

    civil_day: date


@dataclass(frozen=True)
class ReconciledThrough:
    """The day through which an account's movements are inside a declared balance.

    **The ONE statement of the question this whole arc turns on** -- *is this
    movement already reflected in the balance the user declared?* -- and ruling
    R-DH (a)'s answer to it: an assertion is the CLOSING balance for its civil
    day, so a movement dated at or before that day is inside it by definition.
    :meth:`covers` is that rule, and it is the only implementation of it in the
    codebase -- including the MODELLED side, where an adversarial review found
    a seventh statement of it hiding behind a loose date
    (``balance_at._asset_contributions``: a payroll contribution on a payday
    the assertion already covers is money the asserted balance contains, and
    modelling it again double counts).

    **It is a TYPE rather than a bare date because the mistake it prevents is
    the one that cost production ``$4,001.42``.**  The question had FOUR
    implementations when
    ``docs/audits/balance_architecture/anchor_settle_partition.md`` was
    written, three of them comparing different things in different units, and
    the plan's answer was a pylint checker that would flag a fifth.  The
    developer ruled the fence structural instead: with no ordering methods,
    ``settled_on <= reconciled_through`` raises ``TypeError`` rather than
    silently answering the question a fifth way.  Asking it correctly and
    asking it wrongly stopped being the same keystroke.

    **The residue that survived step 3 is CLOSED at plan step X-d** (finding
    **N-135**, ruling **R-DJ**).  This class fenced the derived boundary and
    left ``CashAnchorFact.observed_on`` / ``CashSourceFact.settled_on`` as plain
    ``date`` fields, so ``x <= fact.observed_on`` still compiled in a new
    module -- the shape a lint checker over the assertion-day vocabulary would
    have caught and this type did not.  Those fields are now
    :class:`ObservedOn` and :class:`MovedOn`, and :meth:`covers` takes the
    event kind, so the remaining hole is a bare ``date`` a caller constructs
    itself -- which no longer reaches this rule without saying which kind of day
    it is.

    A caller that genuinely needs the raw civil day -- an SQL bound, a rendered
    caption -- reads :attr:`observed_day` and says so at the call site.  That
    is the deliberate escape hatch, and naming it is the point: reaching for it
    is visible in review, where a ``<=`` was not.

    **Why the day and not the instant, measured.**  Neither instant available
    is a fact about money: ``Transaction.paid_at`` is ``db.func.now()`` at the
    click and an assertion's ``created_at`` is when it was typed.  So the
    instant partition asked "which button was pressed first" and answered a
    question about cash with it.  On production 2026-07-31 an ordinary session
    -- read the bank, enter ``$1,307.66``, tick off what cleared -- recorded
    three already-cleared payments in the NINE SECONDS after the assertion and
    subtracted ``$4,001.42`` a second time, rendering ``-$4,021.37`` against a
    true ``-$19.95``.  Across four months of that account, 65 of 139 settled
    rows (``$19,602.13`` gross) were classified by click order, and the
    correction the model was forced to plug at each assertion totalled
    ``$40,554.34`` gross / ``-$6,998.90`` net against ``$15,367.94`` /
    ``-$940.06`` under this rule.  The instant partition's own stated evidence
    was ONE 2026-07-25 pair (an anchor at 12:57 UTC, two expenses at 13:07,
    ``$108.15`` a date-keyed rule absorbs -- finding cash D1); scored over the
    whole account rather than that pair, the day rule books a SMALLER plug at
    that very assertion (``$39.27`` against ``$68.88``), because the rows it
    absorbs were overwhelmingly recorded late rather than cleared late.

    **The residual is stated rather than hidden.**  A payment that genuinely
    clears AFTER the balance was observed on the same day is absorbed anyway,
    and the projection reads high until the next assertion.  It is bounded
    (median ``$184.55`` a day against the ``$4,161.47`` the instant rule
    produced on 2026-07-31) and it self-corrects at the next assertion.

    **What removes it is an OBSERVATION, not a second derived date.**  With
    both real dates recorded, a movement made after the balance was read still
    carries the same civil day as one made before it, so no rule comparing two
    dates can tell them apart (``anchor_settle_partition.md`` Section 10.3).
    The guess ends only where the user says what their statement showed --
    which plan step S1-c built for PURCHASES
    (``entry_service.record_settled_days``) and which a bank import would do
    for settles.  Until then this is the best available guess on the settle
    side, and saying so is the honest form of it.

    Attributes:
        observed_day: The civil day the account's latest balance assertion is
            the closing balance for (``AccountAnchorHistory.observed_on``), or
            ``None`` when no balance has ever been declared for it.  It is a
            bare ``date`` deliberately -- this attribute IS the escape hatch,
            and the two callers that read it (the reconcile panel's SQL offer
            bound and the day it stamps onto a ticked purchase) both need a
            plain day.  To ask whether a movement is inside the balance, call
            :meth:`covers`.
    """

    observed_day: date | None

    def covers(self, moved_on: MovedOn | None) -> bool:
        """Return whether an event dated *moved_on* is inside this balance.

        Ruling R-DH (a): an assertion is the closing balance for its civil day,
        so a movement dated at or before that day is inside it.

        **Total in both the argument and the boundary**, so it is a rule rather
        than one with a precondition each caller must remember.  A ``None``
        *moved_on* is a purchase whose posting day has never been observed --
        still outstanding, whatever any balance says (ruling R-DH (d) as
        restated at plan step S1-c: the engine never guesses a posting day).  A
        ``None`` :attr:`observed_day` is an account that has never had a
        balance declared, so there is nothing for anything to be inside of.

        **It takes a :class:`MovedOn` and not a bare ``date``, and that is
        ruling R-DJ.**  The totality above is over the CONCEPT -- every event
        has a day it counts from -- not over the Python type: all four kinds of
        day this rule is ever asked about (a settled row's, a purchase's
        observed posting day, a journal entry's, a modelled payday) are the same
        concept, so requiring the caller to say so costs nothing and stops the
        boundary being compared against a day of the OTHER kind.

        **Totality means something different to the two caller shapes, and the
        absorb loops rely on an invariant rather than on this arm.**  For the
        entry reservation a ``None`` day means "this purchase is outstanding",
        which is the answer wanted.  Inside the walk's ``while`` loop a False
        HALTS absorption for that assertion and every later one, so a ``None``
        day there would silently short the ledger where the bare ``<=`` it
        replaced would have raised.  It cannot arise:
        :attr:`~app.services.cash_ledger.CashSourceFact.settled_on` is typed
        non-optional and derives from ``to_display_civil_date(paid_at,
        period.start_date)`` over a NOT NULL ``pay_periods.start_date``.
        Stated because a fail-open substitution in a money path is not
        something a reader should have to re-derive.

        Args:
            moved_on: The day the event counts from -- a settled row's or a
                purchase's :class:`MovedOn` -- or ``None`` when it has not been
                observed.

        Returns:
            True when the event is already inside the declared balance.
        """
        if moved_on is None or self.observed_day is None:
            return False
        return moved_on.civil_day <= self.observed_day
