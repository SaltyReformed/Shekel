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
*structurally cannot read the genesis ledger* (``confirmed_loan_view`` returns
``None`` for any ``as_of`` after today), and so answered from the pre-read-switch
anchor replay, which is blind to how much cash a payment actually moved.  The
ten that agreed made the eleventh invisible.  **Redundant derivation is not a
performance smell; it is where a divergence hides.**

**The contract.**  A context is built ONCE per read pass, carries the pinned
``as_of`` and baseline scenario, and lazily memoizes each loan's
:class:`~app.services.loan_resolution.ResolvedLoan`.  Every consumer then reads
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

from app.models.account import Account
from app.models.scenario import Scenario
from app.services.loan_resolution import ResolvedLoan, resolve_loan_bundle
from app.services.scenario_resolver import get_baseline_scenario


@dataclass(frozen=True)
class BalanceContext:
    """One read pass's pinned as-of, scenario, and memoized loan resolutions.

    Frozen: the pinned inputs (``user_id`` / ``scenario`` / ``as_of``) cannot be
    reassigned mid-pass, which is the whole point -- a producer that could move
    the as-of under its consumers would reintroduce the multi-clock problem this
    object exists to kill.  The memo is a mutable dict held BY the frozen object
    (excluded from ``eq`` / ``repr``): it is derived state, not identity, so two
    contexts with the same pins are equal whether or not either has resolved a
    loan yet.

    Attributes:
        user_id: The owning user.  Every account a context resolves must belong
            to them; the caller owns that check (the loaders trust it).
        scenario: The baseline scenario, or ``None`` for a user with no baseline
            (the degraded state: a loan then resolves from its anchor with no
            payment feed, and the seam's cash paths cannot run at all -- see
            :func:`require_scenario`).
        as_of: The resolver's NOW for this pass -- the date each loan is
            RESOLVED at.  Not the date an account is VALUED at (see the module
            docstring).
    """

    user_id: int
    scenario: Scenario | None
    as_of: date
    _loans: dict[int, ResolvedLoan | None] = field(
        default_factory=dict, repr=False, compare=False,
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
    def scenario_id(self) -> int | None:
        """The baseline scenario's id, or ``None`` with no baseline.

        The form the loaders and the resolver take.  Reading it off the context
        (rather than each caller writing ``scenario.id if scenario else None``)
        keeps the no-baseline degradation expressed in ONE place.
        """
        return self.scenario.id if self.scenario is not None else None

    def resolved_loan(self, account: Account) -> ResolvedLoan | None:
        """Return *account*'s resolution for this pass, resolving it at most once.

        The memo that collapses a read pass's N resolutions of a loan to one.
        The first call resolves through
        :func:`~app.services.loan_resolution.resolve_loan_bundle`; every later
        call in the same pass returns that same :class:`ResolvedLoan` instance.

        A ``None`` result (the account has no ``LoanParams`` -- it is not a
        configured loan) is memoized TOO, so a non-loan account asked repeatedly
        does not re-issue its params query each time.  That is why the membership
        test is ``in self._loans`` and not a truthiness check on the value.

        **FENCED (W9906), and named so it CAN be.**  A :class:`ResolvedLoan`
        reaches ``state.current_balance`` -- a balance-at-today -- in one
        attribute read, so handing one to a route puts a loan balance on a screen
        without passing the seam.  That is the hole this method WAS: it was called
        ``loan``, a name too generic to fence (``_called_name_in`` matches a
        call's attribute name, and ``.loan`` collides with unrelated code), so the
        checker could not see it and a route reading
        ``ctx.loan(account).state.current_balance`` rated 10.00/10 -- measured, on
        this codebase, before the rename.  The distinctive name is what makes the
        fence bind.

        Only the seam (:mod:`app.services.balance_at`) and the kernel cluster it
        composes may call it.  A consumer that wants a loan's rich figures takes
        :func:`app.services.balance_at.loan_figures` (which carries no balance,
        deliberately); one that wants its balance takes
        :func:`app.services.balance_at.balance_at`.

        Args:
            account: The account to resolve.  Must belong to ``user_id`` (the
                caller owns the ownership check).

        Returns:
            The memoized :class:`ResolvedLoan`, or ``None`` when *account* is
            not a configured loan.
        """
        if account.id not in self._loans:
            self._loans[account.id] = resolve_loan_bundle(
                account.id, self.scenario_id, self.as_of,
            )
        return self._loans[account.id]


def require_scenario(ctx: BalanceContext) -> None:
    """Raise ``ValueError`` when *ctx* has no baseline scenario -- the fail-loud guard.

    Every balance the seam produces is scoped to a baseline scenario, and
    ``get_baseline_scenario`` can return ``None`` (a fresh user with no
    baseline).  Centralising the guard keeps the contract and its message
    single-sourced.  Callers that legitimately handle the no-baseline case keep
    their own ``if ctx.scenario is None: return ...`` guard BEFORE calling the
    seam; this is the defensive backstop that turns a missed guard into a clear
    failure instead of a deep ``AttributeError`` (or a silent ``$0``).

    The ONE seam entry that does not run this guard is
    :func:`app.services.balance_at.liability_owed_at_dates`, where a missing
    baseline is not an error but the degenerate case of its own rule (no loan is
    resolvable, so every liability holds flat); its docstring owns that
    rationale.

    Args:
        ctx: The read pass's :class:`BalanceContext`.

    Raises:
        ValueError: When ``ctx.scenario`` is ``None``.
    """
    if ctx.scenario is None:
        raise ValueError(
            "the balance_at seam requires a baseline scenario; build the "
            "BalanceContext for a user who has one, and guard a None scenario "
            "before calling the seam"
        )
