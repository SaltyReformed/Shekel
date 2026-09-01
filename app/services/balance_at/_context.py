"""
Shekel Budget App -- the balance seam's READ-PASS resolution context.

The value object that makes "one loan resolution, at one pinned as-of" a
STRUCTURAL property of a read pass rather than a discipline nobody could
enforce.

**Why this exists.**  A loan's resolved state was not a first-class value in
this architecture.  Every surface that wanted any part of a loan -- its
balance, its schedule, its payoff date, its payment feed -- re-derived the whole
thing from scratch, off a clock each producer read for itself
(``date.today()``, called in 12+ places in one ``/savings`` render).  Measured
2026-07-13 on real data: ONE ``compute_dashboard_data`` call ran the loan
resolver **eleven times for two loans** -- the balance maps, the trend window's
honest-history gate, the liability band, the loan tile, the property-equity
card, and an "ever paid off" probe each resolving independently
(``docs/audits/balance_architecture/followup_redundant_loan_resolution.md``).

That was filed as waste.  It was not only waste.  Because there was no single
resolution to compare against, nothing revealed that one of those eleven -- the
``date.max`` "ever paid off" probe -- resolved through a producer that
*structurally cannot read the genesis ledger* (the confirmed view returns
``None`` for any ``as_of`` after today), and so answered from the pre-read-switch
anchor replay, which is blind to how much cash a payment actually moved.  The
ten that agreed made the eleventh invisible.  **Redundant derivation is not a
performance smell; it is where a divergence hides.**

**The contract.**  A context is built ONCE per read pass, carries the pinned
``as_of`` and baseline scenario, and lazily memoizes each loan's
:class:`~app.services.balance_at._resolution.ResolvedLoan`.  Every consumer then reads
one resolution, so the loan tile's balance, the net-worth hero, the liability
band, and the debt card are identical BY CONSTRUCTION rather than by the luck of
four producers agreeing.

**Read pass, not request.**  This is deliberately NOT a request-scoped cache
(``flask.g``), for two reasons.  It would break the Flask-free service boundary
(``CLAUDE.md``), and -- the load-bearing one -- it would go STALE: the loan write
paths (``loan_recurrence_sync``, the transfer posting sync) resolve loans in the
middle of a mutation, and a request that writes and then re-renders must see the
post-write loan.  A context is a plain value the caller constructs, so a write
path simply builds a fresh one after its write.  There is no cache-invalidation
class of bug here because there is no cache -- only a memo whose lifetime is the
one read it was built for.

**Two dates, and they are not the same date.**  ``ctx.as_of`` is the resolver's
NOW: the date that decides what is confirmed and what the loan currently owes.
The ``as_of`` argument of :func:`app.services.balance_at.balance_at` is the
VALUATION date: the date to value the account AT, which may be in the past (the
genesis ledger answers) or the future (the schedule projects).  They were
conflated while "now" was an implicit ``date.today()`` inside each producer;
separating them is what lets a caller value an account at any date without the
loan silently resolving at a different one.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, TypeVar

from app.exceptions import BaselineMissingError, ForeignAccountError
from app.models.account import Account
from app.models.scenario import Scenario
from app.services.cash_ledger import AmountBasis, amount_basis
from app.services.loan_ledger import LoanLedgerWalk, walk_loan_ledger
from app.services.pay_calendar import PayCalendar, PeriodWindow, calendar_for
from app.services.scenario_resolver import get_baseline_scenario

if TYPE_CHECKING:
    # Type-only: all three RECORD types below are defined by seam SIBLINGS that
    # import THIS module at runtime.  They type the caches the seam FILLS (this
    # module never builds a plan, never resolves a loan and never assembles a
    # cash fold), so they carry NO runtime edge back -- the sibling cycle a
    # runtime import would close (finding N-25) stays open.
    from ._cash_fold import AssembledCashFold
    from ._plan import LoanForwardPlan
    from ._resolution import ResolvedLoan

# What a memo cache's derivation yields.  The five account-keyed caches
# (:attr:`BalanceContext.loans` / :attr:`BalanceContext.plans` /
# :attr:`BalanceContext.payoffs`, and the private ``_walks`` / ``_cash_folds``)
# differ only in this type, so :func:`_memoize_once` is generic over it and
# there is ONE store-once mechanism rather than a copy per cache.
_Derived = TypeVar("_Derived")


@dataclass(frozen=True)
class BalanceContext:  # pylint: disable=too-many-instance-attributes
    """One read pass's pinned as-of, scenario, and memoized derivations.

    Pylint: ``too-many-instance-attributes`` (10/7) -- suppressed because the
    ten ARE one read pass's state and there is no smaller cohesive object
    inside them: three PINS (``user_id`` / ``scenario`` / ``as_of``) and seven
    MEMOS, each keyed by the thing it is a derivation of.  Bundling the memos
    behind a nested record would put an access level in front of state the
    seam fills from five different modules while creating a second object with
    no behaviour of its own.  It reached 8 at plan step C2-c, when the pay
    calendar became a pass-level derivation instead of an argument every caller
    passed by hand, 9 at X-au-c2b (the amount basis) and 10 at **X-i4** (the
    cash fold); plan step **X-i1** raises it further, because that step's
    remaining inputs (the contribution feed, the standing extra, the
    contractual schedule) are memos of exactly this kind.  The count is a
    property of what a read pass IS rather than a threshold this class is
    drifting past.  *The figure read ``(8/7)`` and "five MEMOS" until X-i4:
    ``_amount_bases`` had joined without it being updated, which is the class
    of claim this file's own ``scenario_id`` docstring already warns about.*

    Frozen: the pinned inputs (``user_id`` / ``scenario`` / ``as_of``) cannot be
    reassigned mid-pass, which is the whole point -- a producer that could move
    the as-of under its consumers would reintroduce the multi-clock problem this
    object exists to kill.  The memo caches are mutable dicts held BY the frozen
    object (excluded from ``eq`` / ``repr``): derived state, not identity, so two
    contexts with the same pins are equal whether or not either has resolved a
    loan yet.

    **THREE derivations this module owns, three it stores in PUBLIC caches, and
    ONE in a PRIVATE one.**  The WALK (:meth:`loan_walk`), the CALENDAR
    (:meth:`calendar`) and the AMOUNT BASIS (:meth:`amounts`) derive from leaves
    BELOW this module, which it imports outright, so all three stay private,
    filled by this module's own methods.  *The count read "two" and named only
    the first two until plan step X-i4, having missed ``amounts`` when X-au-c2b
    added it -- the same omission the attribute-count note above records, one
    sentence over.*  The RESOLUTION, PLAN and
    PAYOFF caches (:attr:`loans` /
    :attr:`plans` / :attr:`payoffs`) are derived in the
    ``balance_at`` seam modules ABOVE it (``_resolution`` / ``_plan`` /
    ``_positions``, which import THIS
    module); the context cannot import them back to compute them without inverting
    the dependency arrow and closing a real import cycle (finding N-25), so those
    caches are PUBLIC pass-through state the seam FILLS through
    :func:`_memoize_once`.  Plan step D-ctx-b retired the earlier design that
    INJECTED the builder into a context method: no builder crosses into the context
    now -- the seam owns the derivation, the context owns the storage.  Plan step
    E1d-a moved the RESOLUTION under that same rule (it was a context METHOD, the
    one surface W9910 cannot see -- finding H1 of step D3's review), which is why
    ``loans`` is a cache here rather than a ``resolved_loan`` method.

    **Every account-keyed cache above is filled through ONE primitive, and that
    is where this pass BINDS the account it values** (plan step **X-i4**,
    finding **N-354**).  :func:`_memoize_once` takes the ``account`` rather than
    a bare id and refuses one whose ``user_id`` is not this pass's, so the
    pairing the seam used to state as two independent arguments -- an account
    here, ``ctx.amounts()`` / ``ctx.as_of`` / ``ctx.calendar()`` there, agreeing
    only because every call site happened to name one ``ctx`` -- cannot be
    stated wrongly.  It is a precondition on the one constructor of per-account
    pass state, not a guard repeated at each funnel: there is no way to memoize
    a derivation against this object without going through it, and
    :meth:`loan_walk` open-coded its own store-once lines until X-i4 routed it
    here too.  What it is NOT is a second ownership gate -- see
    :class:`~app.exceptions.ForeignAccountError` for why no upstream gate can
    answer this question at all.

    Exposing THOSE THREE caches hands out no balance the fence must guard: a plan
    is payment RECORDS, a payoff is a ``date``, and a
    :class:`~app.services.balance_at._resolution.ResolvedLoan` carries schedule
    detail and NO balance-at-T (its ``current_balance`` was deleted at the root by
    plan step D2a -- the reason step D3 un-fenced the memo in the first place).
    **That test is what decides which caches are public, and plan step X-i4's
    first build failed it**: it added the CASH FOLD as a fourth PUBLIC cache
    without re-reading this paragraph, and an
    :class:`~app.services.balance_at._cash_fold.AssembledCashFold` carries
    ``seed`` and ``steps`` -- a running total, so a prefix sum over it
    reproduces the seam's own scalar exactly.  Two adversarial reviews each
    measured that bypass independently.  It is ``_cash_folds`` now, filled
    through the one crossing in
    :func:`~app.services.balance_at._cash_fold.assembled_fold`, and the rule
    this paragraph states is the reason rather than a layering accident.

    Nor does exposing them let a consumer FORGE one.  A context is a plain value
    its caller constructs and hands to the seam; writing a fabricated bundle into
    ``loans`` is the caller lying to itself for the length of one read, not
    reaching past a boundary -- the same standing the caller already has by
    passing whatever ``as_of`` and ``scenario`` it likes.  The gate that matters
    is on the DERIVATION (every producer private to this package, W9910), not on
    the dict a pass carries its own answers in.

    Attributes:
        user_id: The owning user.  Every account a context resolves must belong
            to them, and since plan step **X-i4** that is REFUSED rather than
            trusted -- at :func:`_memoize_once` for the account, and at
            :meth:`__post_init__` for the ``scenario`` beside it.  *This entry
            read "the caller owns that check (the loaders trust it)" until
            X-i4.*
        scenario: The baseline scenario, or ``None`` for a user with no baseline
            (the degraded state: a loan then resolves from its anchor with no
            payment feed, and the seam's cash paths cannot run at all -- see
            :func:`require_scenario`).
        as_of: The resolver's NOW for this pass -- the date each loan is
            RESOLVED at.  Not the date an account is VALUED at (see the module
            docstring).
        loans: The read pass's per-loan resolution cache, keyed by ``account.id``
            and FILLED by the seam's
            :func:`~app.services.balance_at._resolution.resolved_loan` (this module
            never resolves a loan).  A ``None`` value is a MEMOIZED "not a
            configured loan", not an empty slot.
        plans: The read pass's per-loan forward-payment-plan cache, keyed by
            ``account.id`` and FILLED by the seam's
            :func:`~app.services.balance_at._plan.memoized_plan` (this module
            never builds a plan).
        payoffs: The read pass's per-loan derived-payoff cache, keyed by
            ``account.id`` and FILLED by the seam's
            :func:`~app.services.balance_at._positions.memoized_payoff`.
        _cash_folds: The pass's per-account cash-fold memo, keyed by
            ``account.id`` and filled by :meth:`cash_fold`.  **PRIVATE, and not
            for the reason ``_walks`` is** (that one is private because this
            module owns its derivation): an
            :class:`~app.services.balance_at._cash_fold.AssembledCashFold`
            carries ``seed`` and ``steps``, which ARE a balance-at-T -- five
            lines of prefix sum over them reproduce
            :func:`~app.services.balance_at.cash_balance_at`'s answer exactly.
            The three PUBLIC caches beside it are public because they carry no
            such thing, which is the argument the paragraph above makes and
            which this one would have falsified.  Two adversarial reviews found
            it public in X-i4's first build and each measured the bypass: a
            consumer importing nothing private, holding only the re-exported
            :class:`BalanceContext`, read a balance the W9910 fence exists to
            make unreachable -- and W9910 sees IMPORTS, ``protected-access``
            sees underscores, so a public dataclass FIELD passed every gate.
        _calendars: The pass's pay-calendar memo, keyed by ``user_id`` and
            filled by :meth:`calendar` -- private for the reason ``_walks`` is,
            because this module owns the derivation rather than storing a
            sibling's.
        _amount_bases: The pass's amount-model memo, keyed by ``scenario_id``
            and filled by :meth:`amounts`.  Private for the same reason.
    """

    user_id: int
    scenario: Scenario | None
    as_of: date
    _walks: dict[int, LoanLedgerWalk] = field(
        default_factory=dict, repr=False, compare=False,
    )
    loans: "dict[int, ResolvedLoan | None]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    plans: "dict[int, LoanForwardPlan]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    payoffs: "dict[int, date | None]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _cash_folds: "dict[int, AssembledCashFold]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _calendars: "dict[int, PayCalendar]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _amount_bases: "dict[int, AmountBasis]" = field(
        default_factory=dict, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        """Refuse a pass whose scenario belongs to a different owner.

        **The other half of X-i4's binding, and finding N-354's own sentence one
        field over.**  That row says ``BalanceContext`` "pins a ``user_id`` and
        never checks it against the account handed alongside"; it pinned three
        things and checked none of them against each other.  An adversarial
        review measured the gap: a pass carrying owner 1's ``user_id`` and owner
        2's :class:`~app.models.scenario.Scenario` answered
        ``cash_balance_at`` a real figure and nothing refused it -- the scenario
        is what scopes every row the fold loads, so the pass would report one
        owner's account under another's budget.

        It is checked HERE rather than in :meth:`build`, which resolves the
        baseline itself and cannot get it wrong, because the constructor is
        public, frozen and directly called: ``tests/_test_helpers`` builds one
        for the pay-calendar memo and two loan-sync suites build one by hand.
        A ``__post_init__`` covers every construction path there is, which
        :meth:`build` alone does not -- the same reason
        :func:`_memoize_once` holds the account rule rather than each funnel.

        A ``None`` scenario is legal and unchecked: it is the DEGRADED state
        :func:`require_scenario` names, not a foreign one.

        Raises:
            ForeignAccountError: When ``scenario`` belongs to another owner.
        """
        if self.scenario is not None and self.scenario.user_id != self.user_id:
            raise ForeignAccountError(
                f"read pass for user {self.user_id} was built with scenario "
                f"{self.scenario.id}, which belongs to user "
                f"{self.scenario.user_id}. The scenario scopes every row this "
                f"pass folds, so the two must name one owner: build the pass "
                f"through BalanceContext.build, which resolves the owner's own "
                f"baseline"
            )

    @classmethod
    def build(
        cls, user_id: int, as_of: date | None = None,
    ) -> "BalanceContext":
        """Build a context for *user_id*, resolving the baseline scenario once.

        The constructor a route or a top-level producer uses: it performs the
        single baseline-scenario lookup the whole read pass then shares, so the
        scenario is not re-resolved by every producer that needs it.

        Args:
            user_id: The owning user.
            as_of: The resolver's NOW.  Defaults to ``date.today()``, which is
                the basis every existing caller used and which
                :func:`app.utils.dates.display_today` documents as the
                resolver's replay boundary (storage and the replay stay UTC; the
                display timezone is a presentation concern).  Pass an explicit
                date to value a pass at another moment -- a historical read, or
                a caller whose civil window is display-timezone-bound (the
                analytics Taxes tab), which previously supplied a date that
                ``generate_debt_schedules`` silently discarded.

        Returns:
            The :class:`BalanceContext` for this read pass.
        """
        return cls(
            user_id=user_id,
            scenario=get_baseline_scenario(user_id),
            as_of=as_of if as_of is not None else date.today(),
        )

    @property
    def scenario_id(self) -> int:
        """The baseline scenario's id -- the form the loaders and resolver take.

        **It RAISES rather than returning ``None``** (plan step X-v2, ruling
        R-BX), which is what keeps the nullable from escaping this object.
        Seventeen callers read it, and the ones that make the raise
        load-bearing are those that SCOPE A QUERY with it rather than read a
        balance -- the grid's transaction load, both calendar entries, the cash
        detail's anchor resolve, the tax report's profile load, the dashboard
        pulse's unpaid-bill query, the emergency-fund history, the loan route's
        payment-context load.  A nullable id reaching any of them is a query
        silently scoped to nothing (which reads exactly like an empty account)
        or an ``AttributeError`` on ``None``, which is a failure the
        application's :class:`~app.exceptions.BaselineMissingError` handler
        cannot answer because it does not wear that name.  One accessor makes
        every dereference, seam read or query scope, fail the same named way at
        its first use.

        **The nullable itself is still :attr:`scenario`, and after plan step
        X-v2 exactly TWO callers read it** -- the two ruling R-BY carves out,
        each documenting why at its own guard:
        :func:`app.services.balance_at.liability_owed_at_dates` (a missing
        baseline is the degenerate case of its own rule) and
        :func:`app.services.loan_recurrence_sync.sync_recurring_payment_bounds`
        (a writer, where raising would roll back the user's edit).  An earlier
        draft of this paragraph said ONE, and X-v2's adversarial design review
        counted four -- the writer named 130 lines below in this same file, an
        emergency-fund reducer that fabricated ``$0.00``, and a template
        context handed the Scenario ROW.  The last two are gone; a count in a
        docstring is a claim, and this arc has paid for that one before.

        Returns:
            The baseline scenario's id.

        Raises:
            BaselineMissingError: When this pass has no baseline scenario.
        """
        require_scenario(self)
        return self.scenario.id

    @property
    def scenario_id_or_none(self) -> int | None:
        """The baseline scenario's id, or ``None`` -- for a rule that HAS an answer.

        The verbose sibling of :attr:`scenario_id`, and the naming is the point
        (plan step X-v2, ruling R-BX): the obvious name is the one that fails
        loud, and reaching for the nullable is a deliberate act that reads as
        one at the call site.

        **Exactly two callers, both inside the seam, both because a missing
        baseline is the degenerate case of their own rule rather than an
        error:**

        * :func:`._resolution.resolve_loan_bundle` -- a loan's payment feed is
          the ONE scenario-scoped input to its resolution; its params, anchors
          and rate history are contract facts.  With no baseline the feed is
          empty and the CONTRACT terms still resolve, which is plan step C8e's
          rule and what keeps escrow and rate editing working for a user whose
          baseline is missing (:func:`app.routes.loan._helpers._loan_terms_now`).
        * :func:`._confirmed_view.confirmed_view` -- the confirmed ledger view
          is scenario-scoped by construction, so with no baseline there is no
          view and the resolver falls back to its anchor replay.

        A third reader tests the nullable directly rather than its id:
        :func:`app.services.balance_at.liability_owed_at_dates`, the one seam
        entry with no :func:`require_scenario` at all.

        Anything else -- and in particular anything that SCOPES A QUERY with
        this id -- takes :attr:`scenario_id` and gets the raise, because a query
        scoped to ``NULL`` returns an empty result that reads exactly like an
        empty account.

        Returns:
            The baseline scenario's id, or ``None`` with no baseline.
        """
        return self.scenario.id if self.scenario is not None else None

    def loan_walk(self, account: Account) -> LoanLedgerWalk:
        """Return *account*'s ledger walk for this pass, walking it at most once.

        The memo that collapses a read pass's N folds of one loan to one WALK.
        The seam's total loan producer
        (:func:`app.services.balance_at.positions`) folds a loan's SOURCE events
        for the past, and the scalar, the per-period map, and the liability band
        each read it in a single ``/savings`` render.  The walk
        (:func:`~app.services.loan_ledger.walk_loan_ledger`) is the expensive part
        -- it loads the loan's params, anchors, rate periods, escrow lines and
        settled shadows -- so re-walking it per producer is exactly the redundant
        derivation the seam's resolution memo already removes.  The
        first call walks; every later call in the same pass samples that same
        :class:`~app.services.loan_ledger.LoanLedgerWalk` through
        :func:`~app.services.balance_at._fold.fold_from_walk`.

        The walk takes NO as-of and reads no clock -- it replays the loan's FACTS
        whole (:func:`~app.services.loan_ledger.walk_loan_ledger`) -- so this memo
        is a pure function of the loan and the pass's pinned ``scenario``, exactly
        like the resolver memo above.  A reader bounds the walk to a date; the memo
        does not.

        **Un-FENCED at plan step D3, the same ground as the resolution memo.**
        The walk is FACTS, not a balance-at-T (plan step D-fold), and the leaf's
        own :func:`~app.services.loan_ledger.walk_loan_ledger` is public and
        deliberately unfenced -- so this memo hands a consumer nothing it could
        not already obtain, and the fold that turns a walk into a balance is a
        seam-private module W9910 protects.  A consumer that wants a loan's
        balance takes :func:`app.services.balance_at.balance_at`.

        **It goes through :func:`_memoize_once` since plan step X-i4**, where it
        open-coded the same three store-once lines before.  That was a fourth
        copy of the primitive whose own docstring says a copy is where two memos
        drift on the property they exist to guarantee -- and it was the one
        account-keyed cache on this object that the binding could not reach.

        Args:
            account: The loan account to walk.  Must belong to ``user_id``, and
                since plan step X-i4 that is REFUSED rather than trusted (see
                :func:`_memoize_once`).  A non-loan / unconfigured
                account walks to an empty
                :class:`~app.services.loan_ledger.LoanLedgerWalk` (the leaf's own
                no-params contract), which the seam never reaches for -- it
                resolves the schedule first.

        Returns:
            The memoized :class:`~app.services.loan_ledger.LoanLedgerWalk` for
            this loan under the pass's scenario.

        Raises:
            ForeignAccountError: When *account* belongs to another owner.
            BaselineMissingError: When this pass has no baseline scenario --
                ``scenario_id`` scopes the walk.
        """
        return _memoize_once(
            self, self._walks, account,
            lambda: walk_loan_ledger(account.id, self.scenario_id),
        )

    def calendar(self) -> PayCalendar:
        """Return the owner's pay calendar for this pass, deriving it once.

        The memo that collapses a read pass's N loads of one pay calendar to
        one.  Plan step **C2-c** put it here: every per-period entry the seam
        publishes needs a period's BOUNDS, those bounds are derived from the
        owner's paydays (``docs/plans/implementation_plan_pay_calendar.md``
        section 1), and a render that asks four of those entries would
        otherwise derive the same 62-payday calendar four times.

        **It is a memo on the PASS, not a cache**, for the reason the class
        docstring gives about the whole object: a write path that records a
        payday and then re-renders builds a fresh context, so there is no
        invalidation class of bug here -- only a memo whose lifetime is the one
        read it was built for.

        Keyed by ``user_id`` rather than held in a bare slot, and the honest
        reason is SHAPE rather than safety -- a first draft of this paragraph
        claimed the key made it impossible to serve one owner's calendar to
        another, and ``frozen=True`` on a dataclass carrying ``user_id`` as a
        field already makes that unreachable (an adversarial review of C2-c
        caught the over-claim).  What the key buys is that this memo reads like
        :meth:`loan_walk`'s beside it and needs no ``None`` sentinel to tell an
        unfilled slot from a legitimately empty answer, which an owner with no
        paydays gives.

        **The derivation is imported outright**, so unlike the three
        pass-through caches beside it this one is filled here: ``pay_calendar``
        is a leaf BELOW the seam (it imports ``pay_schedule_service`` and the
        models and nothing of ``balance_at``), so the arrow stays one-way and
        no import cycle is opened -- the same standing ``loan_ledger`` has
        above.

        Returns:
            The owner's :class:`~app.services.pay_calendar.PayCalendar`.
            **Empty is a legal answer** -- an owner who has never generated a
            schedule -- and the seam's per-period entries answer an empty map
            for it rather than refusing.

        Raises:
            PayCalendarError: The owner has paydays that cannot define a
                calendar.  **The route that reached this from a page is
                closed** (plan step C4-b-2, ledger rows **P8** / **P35**): it
                needed a cadence outside 1..365, which ``resolve_cadence``'s
                fallback could infer for an owner with no
                ``budget.pay_schedule`` row, and ``fk_pay_periods_schedule``
                makes that owner unstorable.  Declared still, because
                ``derive_periods`` refuses payday sets it cannot derive from.
                Loud rather than defaulted: every projected
                horizon is a function of the cadence, so an invented one
                reports a whole schedule the owner never chose.
        """
        if self.user_id not in self._calendars:
            self._calendars[self.user_id] = calendar_for(self.user_id)
        return self._calendars[self.user_id]

    def amounts(self) -> AmountBasis:
        """Return the pass's amount-model basis, building it once.

        The memo that makes "one pricing pass per read pass" structural, and
        plan step **X-au-c2b** put it here -- the override-map half of what plan
        step X-i1 names.  What a row's amount RESOLVES to is a derivation like
        any other on this object: the paycheck engine over the owner's whole
        pay-period set, and each destination loan's P&I, payment day and escrow
        history.  A render that asks four surfaces for a figure would otherwise
        derive all of that once per surface, which is findings **N-268** and
        **N-269** -- the dashboard pulse re-pricing rows the cash fold had just
        priced, and the transfer settle door re-querying the transfer it had
        just loaded.

        **Nothing is resolved until something asks**, so a pass that reads no
        cash figure pays nothing for holding one: both derivations behind
        :class:`~app.services.cash_ledger.AmountBasis` are lazy, and each
        answers ``None`` from a row's own columns before it touches them.

        **It pins no as-of, and since plan step X-au-g-2b there is nothing
        left for one to correct.**  The basis read ``date.today()`` for the
        loan half rather than this pass's :attr:`as_of` -- finding **N-40** --
        and the remedy was expected to be plan step **X-i2**, handing every
        memoized loader this pass's clock.  Ruling **R-IJ** closed it a tier
        DOWN instead: a loan's contractual terms resolve on the installment
        they govern, so the derivation takes no date at all and
        ``cash_ledger`` makes no clock call anywhere
        (``test_amount_source.TestTheAmountModelReadsNoClock``).  X-i2 keeps
        every other loader; this derivation is no longer among its subjects.

        **The derivation is imported outright**, so like :meth:`calendar` beside
        it this memo is filled here rather than by the seam:
        ``cash_ledger`` is a leaf BELOW the seam (it imports no ``balance_at``
        module at all -- its own docstring states the arrow), so filling it here
        opens no cycle.

        Returns:
            The pass's :class:`~app.services.cash_ledger.AmountBasis`.

        Raises:
            BaselineMissingError: When this pass has no baseline scenario.  A
                row's amount rule resolves against a scenario -- which profile
                prices a paycheck, which loan a payment derives from -- so there
                is no scenario-free answer to substitute.
        """
        scenario_id = self.scenario_id
        if scenario_id not in self._amount_bases:
            self._amount_bases[scenario_id] = amount_basis(
                self.user_id, scenario_id,
            )
        return self._amount_bases[scenario_id]

    def reported_periods(self) -> PeriodWindow:
        """Return the pay periods every per-period seam entry reports over.

        **The seam's reporting domain, stated ONCE** (plan step C2-c).  Before
        it, all thirteen per-period entries TOOK the domain as an argument, and
        all eight callers in ``app/`` filled that argument with the same value
        -- the owner's complete saved period set, read out of the table as ORM
        rows whose ``end_date`` and ``period_index`` are the two derived
        columns plan step C4 drops.  An argument every caller answers
        identically is not a contract; it is the one thing a caller can get
        wrong, and ``_cash_periods``' own predecessor measured that mistake at
        ``$150,000.00`` (a fold read against a window missing its own period).

        Asking it of the pass rather than passing it in also means the answer
        cannot differ BETWEEN entries in one render: the grid's balance row,
        its subtotal rows and the cockpit's net-worth column are the same
        periods with the same bounds by construction.

        The window itself is memoized ON THE CALENDAR
        (:meth:`~app.services.pay_calendar.PayCalendar.saved`) rather than
        here, which is where the derivation lives; a second memo on this
        object would have been a memo of a memo, and an adversarial review of
        C2-c called that correctly.

        Returns:
            The :class:`~app.services.pay_calendar.PeriodWindow` over every
            saved period, ``start_date`` ascending and contiguous.  Empty for
            an owner with no pay periods.

        Raises:
            PayCalendarError: See :meth:`calendar`.  A window whose SAVED
                periods do not cover an unbroken span raises here too, which
                needs an unsaved candidate payday between two saved ones --
                a calendar :func:`~app.services.pay_calendar.calendar_for`
                cannot build (it reads saved rows only).
        """
        return self.calendar().saved()


def _memoize_once(
    ctx: BalanceContext,
    cache: "dict[int, _Derived]",
    account: Account,
    build: "Callable[[], _Derived]",
) -> "_Derived":
    """Return ``cache[account.id]``, computing it via ``build()`` at most once.

    The ONE store-once rule behind every account-keyed derivation a read pass
    holds (:func:`~app.services.balance_at._resolution.resolved_loan` fills
    :attr:`BalanceContext.loans`;
    :func:`~app.services.balance_at._plan.memoized_plan` fills
    :attr:`BalanceContext.plans`;
    :func:`~app.services.balance_at._positions.memoized_payoff` fills
    :attr:`BalanceContext.payoffs`;
    :func:`~app.services.balance_at._cash_fold.assembled_fold` fills
    the private ``_cash_folds``; and :meth:`BalanceContext.loan_walk`
    fills its own private ``_walks``).  They share this rather than each carrying
    a copy of the same three lines -- a copy is where two memos drift on the very
    property they exist to guarantee.

    **It BINDS the account to the pass, and that is plan step X-i4** (finding
    **N-354**).  It takes the ``account`` rather than a bare id precisely so it
    can refuse one this pass does not own, and it does so BEFORE the membership
    test, so a foreign account is refused on a cache hit exactly as on a miss.
    Putting the refusal here rather than at each funnel is what makes it a
    precondition rather than a fence: creating per-account state on a context is
    the thing that has to be bound, this is the only way to create it, and a
    funnel added later cannot forget a rule it never had to remember.  The
    seam's five funnels each had their own chance to get the pairing wrong until
    this took the argument away from them.  **Scoped to the ACCOUNT-keyed
    caches, and that scope is exact**: :meth:`BalanceContext.calendar` and
    :meth:`BalanceContext.amounts` beside them open-code the same three lines
    against a ``user_id`` and a ``scenario_id``, which is a residue this step
    did not remove -- taking the ``Account`` narrowed the primitive, so those
    two can no longer adopt it.  Neither is per-account, so neither is a
    pairing a caller can state at all.

    **Membership, never truthiness.**  The check is ``account.id not in cache``, not a
    truthiness test on the value, because a derivation may have a legitimately
    falsy answer: a ``None`` resolution (not a configured loan) and a ``None``
    payoff (a loan that never clears).  A truthiness check would re-derive those on
    EVERY read of every pass -- unbounded, and green under every test that happens
    to use a configured loan that clears.

    **The PLAN was a third example until plan step R16-a, and how it stopped being
    one is the better argument for the rule.**  ``loan_plan`` answered ``[]`` for a
    not-yet-configured or fully-retired loan; it now answers a
    ``LoanForwardPlan(payments=[], charges=[])``, which is unconditionally TRUTHY.
    The cache is no longer at risk there -- but a CONSUMER was, and silently:
    ``_secured_debt._debt_span_upper`` tested ``if not plan`` and took the
    wrong branch the moment the value stopped being a list, until it became
    ``if not plan.payments``.  Membership is the rule here for the same reason
    ``.payments`` is the test there: what these values MEAN is never what
    ``bool()`` says about them.  *The WALK and the CASH FOLD are dataclass
    instances and never falsy either, so neither would have caught it --
    which is why the property is pinned on the primitive rather than on
    whichever cache a test happened to use.*

    **It is not an ownership gate**; whether the requester may see the account
    was decided upstream, and this cannot know that.  What it answers is whether
    the account and the pass describe ONE read -- a question no route can ask,
    because no route knows a context exists.  See
    :class:`~app.exceptions.ForeignAccountError`.

    **A raising build is not cached.**  ``cache[account.id]`` is assigned only
    from a returned value, so a fail-loud guard inside *build* (the seam's
    ``require_scenario``) fires on every call rather than being swallowed after
    the first.

    See :class:`BalanceContext` for why four of these caches are PUBLIC
    pass-through state the seam fills, while the WALK memo beside them is a
    private method (the dependency arrow, finding N-25).

    Args:
        ctx: The read pass the derivation is being memoized on -- the owner
            *account* is bound against.
        cache: The read pass's per-account cache to fill, keyed by
            ``account.id``.
        account: The account this derivation is memoized under and bound to.
        build: The zero-argument derivation, called at most once per account.

    Returns:
        The value stored for ``account.id`` (freshly built on the first call,
        replayed after).

    Raises:
        ForeignAccountError: When *account* does not belong to ``ctx.user_id``.
    """
    if account.user_id != ctx.user_id:
        raise ForeignAccountError(
            f"read pass for user {ctx.user_id} was handed account "
            f"{account.id}, which belongs to user {account.user_id}. The "
            f"balance seam takes the account and the pass as two arguments and "
            f"they must describe one read: the pass's scenario scopes the rows, "
            f"its as-of clamps the plan and its calendar supplies the columns, "
            f"while balance assertions are per-ACCOUNT and would replay "
            f"whatever it was handed. Build the context for the account's own "
            f"owner, or resolve the account through this owner's resolver "
            f"(app.services.account_resolver)"
        )
    if account.id not in cache:
        cache[account.id] = build()
    return cache[account.id]


def require_scenario(ctx: BalanceContext) -> None:
    """Raise :class:`~app.exceptions.BaselineMissingError` when *ctx* has no baseline.

    Every balance the seam produces is scoped to a baseline scenario, so a
    context without one cannot answer anything -- the fail-loud guard at each
    seam entry's door, stated once so the contract and its message are
    single-sourced.

    **It raises a NAMED exception, and that name is the no-baseline policy**
    (plan step X-v1, ruling R-BW).  One application-level handler catches
    :class:`~app.exceptions.BaselineMissingError` and answers it in ONE way --
    the setup-recovery page for a full request, ``204 No Content`` for an HTMX
    fragment (so a live DOM is never replaced by a setup card), and an ERROR log
    event either way.  The exception subclasses ``ValueError``, so this
    function's long-documented contract is unchanged for anything that catches
    the broader type; the handler catches the narrow one, because catching
    ``ValueError`` at the application tier would swallow every unrelated
    conversion failure in the request.

    **There are no caller pre-checks left on the balance path, and that is the
    point** (plan step X-v2, rulings R-BY and R-BZ).  Every caller used to ask
    this question itself, and between them they answered it several different
    ways -- the census and the full list live at
    :func:`app.error_handlers.register_error_handlers`'s handler, which is the
    one place that now decides.  Plan step X-t2 had already tried
    single-sourcing the PREDICATE (a ``has_baseline`` property, finding
    N-107); that made the callers agree on the QUESTION while they still
    disagreed on the ANSWER, so the property is gone with them.

    **Exactly two callers keep their own handling, and each says why at the
    guard** (ruling R-BY):

    * :func:`app.services.loan_recurrence_sync.sync_recurring_payment_bounds`
      -- a WRITER, running mid-mutation.  A raise there would roll back the
      user's just-flushed loan-params edit and answer with a setup card, losing
      the write; it instead writes the contract-derived START bound and skips
      only the scenario-scoped END bound, which is plan step C8e's rule ("a
      loan's contract terms are not scenario-scoped") applied to a write.
    * :func:`app.services.balance_at.liability_owed_at_dates` -- the ONE seam
      entry that does not run this guard at all, because a missing baseline
      there is not an error but the degenerate case of its own rule (no loan is
      resolvable, so every liability holds flat); its docstring owns that
      rationale.

    Args:
        ctx: The read pass's :class:`BalanceContext`.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is ``None``.  A
            ``ValueError`` subclass.
    """
    if ctx.scenario is None:
        raise BaselineMissingError(
            "the balance_at seam requires a baseline scenario; this user has "
            "none, so no balance can be answered for them. Every owner gets one "
            "at registration (auth_service.register_user) and nothing deletes "
            "one, so reaching this means the data was changed outside the app: "
            "POST /grid/create-baseline repairs it, together with both posting "
            "ledgers",
            user_id=ctx.user_id,
        )
