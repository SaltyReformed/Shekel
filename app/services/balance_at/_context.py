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

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from app.exceptions import ForeignAccountError
from app.models.account import Account
from app.models.scenario import Scenario
from app.services.cash_ledger import AmountBasis, amount_basis
from app.services.income_service import PaycheckPricing, paycheck_pricing
from app.services.loan_ledger import LoanLedgerWalk, walk_loan_ledger
from app.services.pay_calendar import PayCalendar, PeriodWindow, calendar_for
from app.services.recurrence import (
    OccurrencePlacement,
    RecurrenceSpec,
    ResolvedRecurrence,
    occurrence_placements,
    recurrence_spec,
    resolved_spec,
)
from app.services.scenario_resolver import get_baseline_scenario

from ._memoize import _memoize_once, require_scenario

if TYPE_CHECKING:
    # Type-only: all three RECORD types below are defined by seam SIBLINGS that
    # import THIS module at runtime.  They type the caches the seam FILLS (this
    # module never builds a plan, never resolves a loan and never assembles a
    # cash fold), so they carry NO runtime edge back -- the sibling cycle a
    # runtime import would close (finding N-25) stays open.
    from ._cash_fold import AssembledCashFold
    from ._plan import LoanForwardPlan
    from ._resolution import ResolvedLoan

@dataclass(frozen=True)
class BalanceContext:  # pylint: disable=too-many-instance-attributes
    """One read pass's pinned as-of, scenario, and memoized derivations.

    Pylint: ``too-many-instance-attributes`` (13/7) -- suppressed because the
    thirteen ARE one read pass's state and there is no smaller cohesive object
    inside them: three PINS (``user_id`` / ``scenario`` / ``as_of``) and ten
    MEMOS, each keyed by the thing it is a derivation of.  Bundling the memos
    behind a nested record would put an access level in front of state the
    seam fills from five different modules while creating a second object with
    no behaviour of its own.  It reached 8 at plan step C2-c, when the pay
    calendar became a pass-level derivation instead of an argument every caller
    passed by hand, 9 at X-au-c2b (the amount basis), 10 at **X-i4** (the
    cash fold), 11 at balance:X-au-d (the paycheck pricing), 12 at
    recurrence:**R16-b-2** (a rule's resolution) and 13 at
    recurrence:**R7d-f-2** (a resolved recurrence's occurrence walk); plan step
    **X-i1** raises it further, because that step's remaining inputs (the contribution feed, the
    standing extra, the contractual schedule) are memos of exactly this kind.
    The count is a property of what a read pass IS rather than a threshold
    this class is drifting past.  *The figure read ``(8/7)`` and "five MEMOS"
    until X-i4, and ``(10/7)`` and "seven" until R16-b-2's adversarial review:
    each time a memo had joined without it being updated, which is the class
    of claim this file's own ``scenario_id`` docstring already warns about.*

    Frozen: the pinned inputs (``user_id`` / ``scenario`` / ``as_of``) cannot be
    reassigned mid-pass, which is the whole point -- a producer that could move
    the as-of under its consumers would reintroduce the multi-clock problem this
    object exists to kill.  The memo caches are mutable dicts held BY the frozen
    object (excluded from ``eq`` / ``repr``): derived state, not identity, so two
    contexts with the same pins are equal whether or not either has resolved a
    loan yet.

    **SIX derivations this module owns, three it stores in PUBLIC caches, and
    ONE in a PRIVATE one.**  The WALK (:meth:`loan_walk`), the CALENDAR
    (:meth:`calendar`), the AMOUNT BASIS (:meth:`amounts`), the PAYCHECK
    PRICING (:meth:`paychecks`), a rule's RESOLUTION
    (:meth:`resolved_recurrence_of`) and a resolved recurrence's OCCURRENCE
    WALK (:meth:`placements_of`) derive from leaves BELOW this module,
    which it imports outright, so all six stay private, filled by this
    module's own methods.  *The count read "two" and named only the first two
    until plan step X-i4, having missed ``amounts`` when X-au-c2b added it, and
    "three" until R16-b-2's review, having missed ``paychecks`` -- the same
    omission the attribute-count note above records, one sentence over.*  The RESOLUTION, PLAN and
    PAYOFF caches (:attr:`loans` /
    :attr:`plans` / :attr:`payoffs`) are derived in the
    ``balance_at`` seam modules ABOVE it (``_resolution`` / ``_plan`` /
    ``_positions``, which import THIS
    module); the context cannot import them back to compute them without inverting
    the dependency arrow and closing a real import cycle (finding N-25), so those
    caches are PUBLIC pass-through state the seam FILLS through
    :func:`~._memoize._memoize_once`.  Plan step D-ctx-b retired the earlier design that
    INJECTED the builder into a context method: no builder crosses into the context
    now -- the seam owns the derivation, the context owns the storage.  Plan step
    E1d-a moved the RESOLUTION under that same rule (it was a context METHOD, the
    one surface W9910 cannot see -- finding H1 of step D3's review), which is why
    ``loans`` is a cache here rather than a ``resolved_loan`` method.

    **Every account-keyed cache above is filled through ONE primitive, and that
    is where this pass BINDS the account it values** (plan step **X-i4**,
    finding **N-354**).  :func:`~._memoize._memoize_once` takes the ``account`` rather than
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
            trusted -- at :func:`~._memoize._memoize_once` for the account, and at
            :meth:`__post_init__` for the ``scenario`` beside it.  *This entry
            read "the caller owns that check (the loaders trust it)" until
            X-i4.*
        scenario: The baseline scenario, or ``None`` for a user with no baseline
            (the degraded state: a loan then resolves from its anchor with no
            payment feed, and the seam's cash paths cannot run at all -- see
            :func:`~._memoize.require_scenario`).
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
        _paycheck_pricing: The pass's PAYCHECK PRICER, keyed by ``user_id``
            and filled by :meth:`paychecks` (plan step **salary:S3-d**).

            **It replaced a public ``payroll_breakdowns`` dict, and the change
            of shape is the fix.**  That field was a raw
            ``{profile_id: {payday: PaycheckBreakdown}}`` memo that
            :func:`~app.services.projection_inputs.load_payroll_feeds` FILLED
            and that :class:`~app.services.income_service.SalaryPricing` did
            not read.  It was also OPTIONAL at that loader
            (``breakdowns=None`` meant "no memo"), which is how two of its
            four callers came to bypass it inside the step that added it.  A
            :class:`~app.services.income_service.PaycheckPricing` closes the
            second hole outright -- a consumer is handed one, so there is no
            argument to omit -- and memoizes per profile AND per payday, so
            two consumers asking overlapping spans pay for the union.

            **It does not close the first, and ledger row P63 is why.**
            :class:`~app.services.income_service.SalaryPricing` still derives
            a pricer of its own; that finding's argument is stated once, at
            :class:`~app.services.income_service.PaycheckPricing`.

            **It exists because a paycheck is expensive and the seam asks for
            one per ACCOUNT.**  ``_contribution_inputs_for_account`` is the
            batch loader over a one-element set, so four seam entries calling
            it once per account re-ran the engine over the owner's WHOLE saved
            window each time: measured at 61 ``calculate_paycheck`` calls on a
            3-account, 10-period fixture against ~7 before ``salary:R14-b``,
            the multiplier being exactly the saved-period count.  The calendar
            memo two fields up exists for the same shape one tier cheaper.

            **PRIVATE, where its predecessor was public**, and the argument
            that made that one public has expired rather than been overruled:
            it was exposed because two callers had to be handed the dict, and
            nothing is handed a dict now -- :meth:`paychecks` is the accessor,
            exactly as :meth:`calendar` and :meth:`amounts` are for the two
            memos above.
        _recurrences: The pass's rule-resolution memo, keyed by the rule's
            SPEC (what it authors, not which row it is -- see
            :meth:`resolved_recurrence_of`).  Private because this module owns
            the derivation (it imports the pure resolver, a leaf below the
            seam), and a ``None`` value is a MEMOIZED "the owner has no pay
            periods", not an empty slot.
        _placements: The pass's occurrence-walk memo, keyed by the COMPOSED
            resolved recurrence the walk is a function of (see
            :meth:`placements_of`).  Private for the reason ``_recurrences``
            is; every stored value is a tuple, and an empty one is a
            legitimate answer (a definition its destination closed before it
            ever fires), so membership rather than truthiness is the test.
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
    _recurrences: "dict[RecurrenceSpec, ResolvedRecurrence | None]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _placements: "dict[ResolvedRecurrence, tuple[OccurrencePlacement, ...]]" = (
        field(default_factory=dict, repr=False, compare=False)
    )
    _paycheck_pricing: "dict[int, PaycheckPricing]" = field(
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
        :func:`~._memoize._memoize_once` holds the account rule rather than each funnel.

        A ``None`` scenario is legal and unchecked: it is the DEGRADED state
        :func:`~._memoize.require_scenario` names, not a foreign one.

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
        counted four -- the writer named in :func:`~._memoize.require_scenario`'s docstring, an
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

        **THREE callers, and the count read "exactly two, both inside the
        seam" until plan step X-au-g-2c re-took it.**  The third
        (``retirement_projection``, below) has read this since `731f6b3c`,
        2026-08-16, so the claim was false for a fortnight -- which is the
        failure this very docstring warns about two paragraphs down, committed
        in the paragraph that warns about it.  A count is a claim; re-grep it
        rather than carrying it.

        Two of the three are inside the seam, and both because a missing
        baseline is the degenerate case of their own rule rather than an error:

        * :meth:`amounts_or_none` -- the nullable form of this pass's amount
          basis, and its ONE caller is the loan bundle.  ``resolve_loan_bundle``
          read THIS accessor directly until plan step X-au-g-2c, for the same
          reason and about the same loan: a loan's payment feed is the ONE
          scenario-scoped input to its resolution; its params, anchors and rate
          history are contract facts.  With no baseline the feed is empty and
          the CONTRACT terms still resolve, which is plan step C8e's rule and
          what keeps escrow and rate editing working for a user whose baseline
          is missing (:func:`app.routes.loan._helpers._loan_terms_now`).  It
          moved one level down because ``load_loan_context`` now takes the basis
          that PRICES that feed rather than an id that only scopes it, and the
          nullability is the same nullability.
        * :func:`._confirmed_view.confirmed_view` -- the confirmed ledger view
          is scenario-scoped by construction, so with no baseline there is no
          view and the resolver falls back to its anchor replay.

        The third is OUTSIDE the seam and takes the nullable for a different
        reason, which is why it is listed apart rather than folded into the
        count: ``retirement_projection`` puts it in a MEMO KEY beside the
        owner and the as-of.  A cache key must be TOTAL over the states its
        pass can be in -- a key that raises for a no-baseline pass would turn a
        degraded read into a 500 at the memo rather than at a figure -- so the
        raise has nothing to protect there.  It scopes no query, which is the
        line the paragraph below draws.

        A third reader tests the nullable directly rather than its id:
        :func:`app.services.balance_at.liability_owed_at_dates`, the one seam
        entry with no :func:`~._memoize.require_scenario` at all.

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

        **It goes through :func:`~._memoize._memoize_once` since plan step X-i4**, where it
        open-coded the same three store-once lines before.  That was a fourth
        copy of the primitive whose own docstring says a copy is where two memos
        drift on the property they exist to guarantee -- and it was the one
        account-keyed cache on this object that the binding could not reach.

        Args:
            account: The loan account to walk.  Must belong to ``user_id``, and
                since plan step X-i4 that is REFUSED rather than trusted (see
                :func:`~._memoize._memoize_once`).  A non-loan / unconfigured
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

    def resolved_recurrence_of(self, rule) -> "ResolvedRecurrence | None":
        """Return what *rule* MEANS against this owner's calendar, resolving it once.

        The memo that collapses a read pass's N resolutions of one rule to
        one.  Plan step **R16-b-2** put it here: the composed door
        (``recurring_definition.resolved_definition``) resolves a definition's
        rule to narrow it by the loan's derived stop, and that stop is the
        forward plan's zero crossing, which since R16-b-2 walks the SAME rule
        under its authored closing to sum the definition's occurrences -- so
        one page reading one loan payment resolved its rule twice on one pass
        (plan ledger row **N-511**'s shape; rule 14's ONE WALK forbids it).
        Both readers take the pure resolution from here and each applies its
        own closing to the value.

        **A memo on the PASS, not a cache**, for the reason :meth:`calendar`
        gives, and filled here because ``app.services.recurrence`` is a leaf
        below the seam.

        **Keyed by what the rule SAYS, not by which row it is.**  The
        resolution is a pure function of the rule's authored columns and the
        pass's calendar (:func:`~app.services.recurrence.resolved_spec`), so
        the spec IS the rule's input and an entry keyed by it cannot be served
        for a different one.  A first cut keyed by ``rule.id``, and the merge
        of plan step R7d-c-2 -- which has GENERATION read a rule through this
        memo -- measured the proxy's cost: ``reauthor_rule`` rewrites columns
        IN PLACE, so a rule edited and regenerated on one pass regenerated on
        its pre-edit cadence (monthly, the 5th moved to the 19th: ``updated
        5, deleted 0, created 0``).  No live route reached it only because
        every edit route builds its pass AFTER its write: a gateless
        convention maintaining the invariant the id key carried, which a key
        that is the input carries for no rule (``CLAUDE.md`` rule 14).  A
        re-authored rule misses on any pass, two rules stating one spec share
        one resolution (the resolver cannot tell them apart either), and a
        TRANSIENT rule (``id`` ``None``) needs no special case.  The spec is
        read ONCE, as key and input: what ``resolved_spec`` exists for.  The
        CALENDAR half is :meth:`calendar`'s memo, under the route convention.

        **The same lens one memo over, left as found.**  :attr:`loans`,
        :attr:`plans` and :attr:`payoffs` are keyed by account id and derive
        from ROWS as well as from every paying definition's spec.  Under the
        rows a generate pass CREATES they are invariant by construction
        (ruling **R-R64**: an occurrence no row answers is priced as its row
        would be, so writing that row changes nothing), which is why a pass
        may read them BEFORE it writes; the maintain pass's UPDATE arm
        re-dates rows the PLANNED tier reads and its RETIRE arm deletes them,
        so after either -- as under any other write inside one pass -- they
        are stale, guarded by the convention above since their key is rows.

        **A foreign rule never enters the memo with a VALUE, and no check here
        is what makes that so.**  The pure resolver refuses a spec paired with
        another owner's calendar -- ``RecurrenceResolutionError``, naming the
        rule -- BEFORE the store on every miss, so a hit holding a value is
        always the owner's own spec (``user_id`` is a field of the key); all a
        foreign rule can leave is the ``None`` an EMPTY calendar answers ahead
        of the ownership check, which carries nothing.  The composed door
        relies on that refusal being the rule's own and reaching a caller
        first, so a second, earlier refusal here would change which error
        names the pairing; :func:`~._memoize._memoize_once` carries its own check because
        the derivations it stores do not refuse for themselves.

        Args:
            rule: The :class:`~app.models.recurrence_rule.RecurrenceRule` to
                resolve, stored or transient.

        Returns:
            The :class:`~app.services.recurrence.ResolvedRecurrence` with the
            AUTHORED closing alone, or ``None`` when the owner has no pay
            periods -- the two answers
            :func:`~app.services.recurrence.resolved_spec` gives.

        Raises:
            RecurrenceResolutionError: See
                :func:`~app.services.recurrence.resolved_spec`; a rule paired
                with another owner's pass is refused there, and an unmodelled
                stored cadence reading the key, as ``resolved_recurrence``.
        """
        spec = recurrence_spec(rule)
        if spec not in self._recurrences:
            self._recurrences[spec] = resolved_spec(spec, self.calendar())
        return self._recurrences[spec]

    def placements_of(
        self, resolved: ResolvedRecurrence,
    ) -> tuple[OccurrencePlacement, ...]:
        """Return every occurrence *resolved* names on this owner's calendar, walking once.

        The memo that collapses a read pass's N walks of one resolved
        recurrence to one, and the other half of what
        :meth:`resolved_recurrence_of` began.  Plan step **R7d-f-2** put it
        here (plan ledger row **N-513**): a ``/savings`` render reads a
        transfer from checking into a goal account through the composed door
        TWICE -- once in the emergency-fund floor's set, once in that goal's
        contribution set -- and R16-b-2's memo had already made the second
        RESOLUTION a hit while the second WALK still ran (measured on
        2026-09-12 before this step: ``resolve`` once, the walk twice).
        Rule 14's ONE WALK, read literally.

        **Keyed by the walk's INPUT, the shape :meth:`resolved_recurrence_of`
        chose** (ruling **R-R73**).  The placements are a pure function of the
        resolved value -- its cadence, its first occurrence and its COMPOSED
        closing, the destination's derived stop included -- and of this
        pass's calendar, which is :meth:`calendar`'s one memo.  So the value
        is the key: two definitions with one composed meaning share one walk
        (the walk could not tell them apart either), a re-authored rule
        resolves to a different value and misses, a definition whose loan
        moved its payoff misses with it, and an unsaved definition needs no
        special case.  A row-keyed memo would have served a pre-edit walk on
        a pass that edited and re-read, which is the defect the id key
        measured one memo over.

        **Through the saved horizon and no further**: this is
        :func:`~app.services.recurrence.occurrence_placements` with its
        default window, the walk the display readers and generation take.
        The seam's ESTIMATED loan tier walks PAST the horizon
        (``projected_occurrence_placements``, ``through=``) and is a different
        function of different inputs; it is not memoised here.

        Args:
            resolved: The recurrence's two-axis meaning, closing composed --
                what :func:`app.services.recurring_definition
                .resolved_definition` returns.  Must have been resolved
                against THIS pass's calendar, which every producer of one
                guarantees by reading :meth:`resolved_recurrence_of` or
                ``resolved_spec(spec, ctx.calendar())``.

        Returns:
            One :class:`~app.services.recurrence.OccurrencePlacement` per
            occurrence through the calendar's horizon, ascending; empty for a
            definition its composed closing admits nothing of.

        Raises:
            RecurrenceGenerationError: See
                :func:`~app.services.recurrence.occurrence_placements`; a
                raising walk is not memoised, so the refusal fires on every
                call rather than being swallowed after the first.
        """
        if resolved not in self._placements:
            self._placements[resolved] = occurrence_placements(
                resolved, self.calendar(),
            )
        return self._placements[resolved]

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
            # **The basis is NOT handed this pass's pricer, and ledger row
            # P63 is why** -- the argument is at ``income_service
            # .PaycheckPricing``; this is the line that does it.
            self._amount_bases[scenario_id] = amount_basis(
                self.user_id, scenario_id,
            )
        return self._amount_bases[scenario_id]

    def paychecks(self) -> PaycheckPricing:
        """Return the pass's paycheck pricer, building it once.

        **The pass's source of a paycheck** (plan step **salary:S3-d**): the
        payroll feeds take it
        (:func:`~app.services.projection_inputs.load_payroll_feeds`) and so do
        the two salary route renders.  The amount model does NOT -- it derives
        its own, which is ledger row **P63** and is stated at the field above
        and at :meth:`amounts`.

        **Nothing is resolved until something asks.**  The pricer holds no
        profile and issues no query until a caller names one, so a pass that
        prices no paycheck pays nothing for holding this.

        *Its per-payday memo means a caller CAN ask for a subset and pay for
        only that.*  The two salary routes ask for ``calendar.saved()``, the
        owner's whole saved schedule, because that is the domain each of them
        reports over; the payroll feeds ask for whichever period a consumer
        reads -- the balance seam's saved window, or a 40-year chart's axis
        -- one at a time, since plan step salary:S3-e-2 (ruling
        **R-SAL15**).  :meth:`~app.services.income_service.SalaryPricing
        .net_for` prices the ONE period a row names, and reads its own pricer
        rather than this one (ledger row **P63**).

        It is keyed by ``user_id`` for the reason :meth:`calendar` is, and it
        is built over that same memoized calendar so a pass cannot hold
        paydays from one derivation and paychecks priced against another.

        Returns:
            The pass's :class:`~app.services.income_service.PaycheckPricing`.
        """
        if self.user_id not in self._paycheck_pricing:
            self._paycheck_pricing[self.user_id] = paycheck_pricing(
                self.calendar(),
            )
        return self._paycheck_pricing[self.user_id]

    def amounts_or_none(self) -> "AmountBasis | None":
        """The pass's amount basis, or ``None`` -- for a rule that HAS an answer.

        The verbose sibling of :meth:`amounts`, and it is
        :attr:`scenario_id_or_none`'s companion in exactly the way ruling
        **R-BX** names: the obvious spelling is the one that FAILS LOUD, and
        reaching for the nullable is a deliberate act that reads as one at the
        call site.  It reads that accessor rather than :attr:`scenario` itself,
        so it adds no third reader of the nullable attribute ruling **R-BY**
        bounds to two.

        **One caller, and it is the one whose own rule has an answer here**
        (plan step X-au-g-2c): :func:`._resolution.resolve_loan_bundle`, which
        already spelled the nullable for the SAME loan and the SAME reason.  A
        loan's payment feed is its one scenario-scoped input; its params,
        anchors and rate history are contract facts.  With no baseline the feed
        is empty and the CONTRACT terms still resolve, which is plan step C8e's
        rule and what keeps escrow and rate editing working for an owner whose
        baseline is missing.  That caller took ``ctx.scenario_id_or_none`` and
        now takes this, because ``load_loan_context`` takes the basis that
        prices the feed rather than an id that only scopes it.

        Returns:
            The pass's :class:`~app.services.cash_ledger.AmountBasis`, or
            ``None`` when this pass has no baseline scenario.
        """
        if self.scenario_id_or_none is None:
            return None
        return self.amounts()

    def reported_periods(self) -> PeriodWindow:
        """Return the pay periods every per-period seam entry reports over.

        **The seam's reporting domain, stated ONCE** (plan step C2-c).  Before
        it, all thirteen per-period entries TOOK the domain as an argument, and
        all eight callers in ``app/`` filled that argument with the same value
        -- the owner's complete saved period set, read out of the table as ORM
        rows whose ``end_date`` and ``period_index`` are the two derived
        columns plan step C4-c dropped.  An argument every caller answers
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
