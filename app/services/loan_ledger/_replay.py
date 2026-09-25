"""The ONE replay of a loan's event stream: charges accumulate, payments clear.

Plan step **X-au-g-2c-3b-2**.  Every walk over a loan answers the same question
in the same way -- *time charges the loan, cash covers what is charged, and the
remainder pays the debt down* -- and until this step the application stated that
rule TWICE:

* the SETTLED walk (:func:`.._walk.walk_loan_ledger`), which replays a loan's
  anchors and its real payments, and
* the FORWARD fold (``balance_at._plan_fold._split_plan``), which replays the
  projected plan.

They were not copies of each other by choice.  ``balance_at`` has an import
closure of 50 modules and reaches ``loan_ledger`` (closure 23) -- both figures
measured 2026-09-02, and worth re-measuring before either is quoted again -- so
the arrow runs one way and the seam could not hand its fold down.  *The DIRECTION
is what the argument needs and it is checkable in a line:* no module under
``loan_ledger`` imports ``balance_at``, and ``_plan_fold`` imports
``loan_ledger``.  **That is the third time this
arc has found one rule duplicated because it sat on the wrong side of the import
graph** -- the ALLOCATION at plan step X-au-g-2c-3a, the CHARGE CALENDAR at
X-au-g-2c-3b-1, and now the REPLAY that consumes them both -- which is what
argues the shape is structural rather than incidental.  The remedy is the same
one each time: put the rule at the tier every walk can reach.

## The rule, in full

```text
balance := seed
standing := (interest 0.00, escrow 0.00, extra 0.00)

CHARGE   (a period began)  balance <= 0  -> nothing accrues, nothing impounds
                           otherwise     -> standing += (accrue(balance, rate),
                                                         escrow, extra_per_period)
PAYMENT  (cash arrived)    allocate cash + standing.extra against standing,
                           advance the balance, and clear standing
RESET    (an assertion)    record the balance just before, then overwrite it
```

**Fused, the CHARGE and the PAYMENT made the payment COUNT the clock** (plan step
R16-a): while a month's interest was charged inside the per-payment step, N
payments inside one month charged N months.  Measured on a production clone, 30
payments of ``$531.94`` fourteen days apart charged the identical ``$1,096.34``
as 30 a month apart, split for split.  Separated, a second payment inside one
accrual period arrives with nothing standing and pays pure principal.

**A RESET clears whatever charges stand before it** (ruling **R-R72** part (2),
applied at plan step **recurrence:R16-c-1**): "the balance on this date is X"
supersedes every earlier month, interest owed included, so the standing
interest, escrow and hypothetical extra are zeroed with the balance overwritten.
The arm is UNREACHABLE on this tree's streams and is written anyway, because the
ruling exists and the day it fires is named: today every RECORDED charge is
dated at the EARLIEST payment due in its period
(:func:`.._charges.charges_for_due_dates`), so a payment always shares the
charge's date and clears it before any reset can land, and every PROJECTED
charge is walked behind every recorded fact, resets included; ``R16-c-2``
charges every contractual installment from origination, after which a skipped
month's charge stands when a true-up arrives.  Pinned on a hand-built stream
rather than left to that step.

**One stream, PAST and FUTURE** (plan step **recurrence:R16-c-1**, rulings
**R-R90** and **R-R72**).  The stream carries the loan's recorded FACTS -- its
assertions, its settled payments and the charges through them -- and, after
them, its PROJECTION: the payments it has not yet made
(:attr:`LoanEventStream.projections`) and the charges the plan raises against
them (:attr:`LoanEventStream.projected_charges`).  **A projected event is never
placed before a recorded fact.**  Its walk key is ``max(on_date, boundary)``
where the boundary is the day after the loan's latest recorded payment or
assertion (:func:`projection_boundary`); events pushed to the boundary keep
their order among themselves by their OWN date (an April charge, April's
catch-up, May's charge, May's catch-up), and no event's ``on_date`` is
rewritten -- a charge's date is the accrual period's identity and is rendered.
So an overdue projected installment is replayed where it will actually land --
behind everything that has happened -- and the splits of the recorded facts are
a function of the facts ALONE, whether or not a projection follows them.  That
is what lets the posted ledger (which replays the facts and nothing else) and a
screen (which replays the facts and then the plan) agree to the cent on every
recorded payment: the fact prefix of both replays is the same list in the same
order.  The hypothetical ``extra_per_period`` joins the cash at PROJECTED
charges only -- "an extra $100 a month" is money the owner has not paid yet, so
it never reprices a recorded month.

Pure: plain data in, plain values out.  No I/O, no clock, no Flask.  All money is
:class:`~decimal.Decimal`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.services.rate_period_engine import RatePeriod, period_for_date
from app.utils.money import (
    PaymentCashSplit,
    accrue_monthly_interest,
    apply_payment_cash,
)

from ._charges import AccrualCharge

_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)

# The kind order applied WITHIN one date, and the whole of what this module
# decides about ordering.  A CHARGE lands before the payments that clear it,
# because interest accrues on a balance no payment has yet reduced; a RESET lands
# last, because an assertion about the balance owed is made after the day's
# money has moved (the settled walk's own rule since step E1c: a payment due
# exactly on an anchor's date is walked, then overwritten by that anchor).  A
# PROJECTED charge or payment ranks as its recorded twin -- the two kinds are
# distinct only so the fold can flag an outcome and accrue the what-if extra at
# the plan's charges and never at a fact's.
_CHARGE, _PAYMENT, _RESET, _PROJECTED_CHARGE, _PROJECTION = 0, 1, 2, 3, 4
_KIND_ORDER = {
    _CHARGE: 0, _PROJECTED_CHARGE: 0,
    _PAYMENT: 1, _PROJECTION: 1,
    _RESET: 2,
}


@dataclass(frozen=True)
class LoanCashEvent:
    """Cash arriving against a loan, at the installment it satisfies.

    One payment as the replay sees it: WHEN it lands in contract time, HOW MUCH
    cash it moved, and an opaque *source* echoed back on its outcome so the
    caller can rejoin it to the record it came from.  The replay reads no other
    property, which is what lets a settled transfer shadow, a projected shadow at
    its live price and a synthesized contractual installment all replay alike.

    Attributes:
        on_date: The installment this cash satisfies -- CONTRACT time, never the
            day the cash settled (ruling R-A).  It orders the event and it is the
            date the charge it clears was resolved at.
        cash: The cash this payment moved.  Never negative: the caller reads a
            record's own figure, and the allocation surfaces an underpayment as
            NEGATIVE principal rather than as negative cash (plan D5).
        source: The caller's record, carried through untouched.  The settled
            transfer's :class:`~app.services.transfer_legs.TransferLeg` for a
            recorded payment, a ``PlannedPayment`` for a projected one.
        visible_on: The day this cash COUNTS from in a balance read -- the ONE
            clock (:mod:`._visible`): a recorded payment's SETTLED day, a
            projection's effective day (``max(due, as_of + 1d)``, ruling D1).
            The replay never reads it; it is computed once by the stream's
            builder and carried onto the outcome so the two consumers that
            re-key the walk by visibility (:func:`.._walk.dated_deltas`, the
            confirmed view) read one statement of it rather than each
            deriving their own from the source.
    """

    on_date: date
    cash: Decimal
    source: object
    visible_on: date


@dataclass(frozen=True)
class LoanResetEvent:
    """An asserted balance that OVERWRITES the running balance at its date.

    A loan's opening and every user true-up: an authoritative statement of what
    is owed, which the replay applies as a reset rather than as a payment (see
    :func:`.._walk.walk_loan_ledger` for why resetting at EVERY assertion, not
    only the latest, is what keeps a from-origination sum of postings equal to
    the resolver's balance).

    Attributes:
        on_date: The date the assertion is made ABOUT.
        balance: The balance owed the assertion states.
        source: The caller's record, carried through untouched (a
            :class:`~app.services.loan_loaders.LoanAnchorFact` for the settled
            walk).
        is_opening: ``True`` for the loan's ORIGINATION assertion -- the
            balance it opens at, synthesized from the immutable params -- and
            ``False`` for every later assertion (a true-up, a tracking start).
            The replay reads no kind of assertion differently; the flag is for
            the readers that must name the opening: the pass's visibility bound
            keeps it whatever its date, and the target-date search bounds its
            doubling by it for a loan not yet originated.
    """

    on_date: date
    balance: Decimal
    source: object
    is_opening: bool = False


@dataclass(frozen=True)
class LoanEventStream:
    """The three kinds of fact a loan replay folds, each PRE-ORDERED by its caller.

    Three lists rather than one merged list, because the merge is exactly what
    the two walks were each stating for themselves.  :func:`replay_loan_events`
    owns the order BETWEEN kinds (charge, then payment, then reset, ascending by
    date) and adds **no tie-break WITHIN a kind** -- it sorts stably, so each
    list's own order survives.

    That division is plan step X-an-b's, applied to a third caller: the loader is
    the one home of its own chronology, and a second statement of it here would
    be both duplication and, as X-an-b measured, an INCOMPLETE one.  So the
    settled walk hands payments in ``(pay_period.start_date, id)`` order and
    anchors in ``(anchor_date, created_at, event_id)`` order, and the forward fold
    hands payments in ``(due_date, effective_date)`` order; all three orders are
    preserved without this module knowing any of them.

    Attributes:
        charges: The RECORDED :class:`~._charges.AccrualCharge` list, ascending
            by ``on_date``: one per accrual period the recorded payments occupy
            (:func:`.._charges.charges_for_due_dates`).
        payments: The RECORDED cash events -- settled payments -- in the
            caller's own within-date order.
        resets: The asserted balances, in the caller's own within-date order.
        projections: The cash events that have NOT happened -- the forward
            plan's projected rows and estimated occurrences -- in the caller's
            own order.  Walked after every recorded fact (see the module
            docstring); empty for the posted ledger's replay, which books
            facts and nothing else.
        projected_charges: The charges the plan raises against those
            projections (``balance_at._plan._charges_for``: every contractual
            installment after the loan's latest assertion whose month the
            recorded walk did not charge), walked behind the recorded facts
            exactly as the projections are, and the only charges the what-if
            extra accrues at.  ``R16-c-2`` deletes this list with the plan's
            own calendar, when ONE contractual calendar from origination is
            the stream's ``charges``.
        periods: The loan's rate periods
            (:func:`app.services.loan_resolver.resolve_periods`), the calendar
            the charges were resolved against.  Read for a payment NO charge
            stands over -- one dated after the loan's latest assertion and
            before the first installment after it, ruling R-C's early extra
            -- so its outcome still names the period governing it.
    """

    charges: Sequence[AccrualCharge]
    payments: Sequence[LoanCashEvent]
    resets: Sequence[LoanResetEvent] = field(default_factory=tuple)
    projections: Sequence[LoanCashEvent] = field(default_factory=tuple)
    projected_charges: Sequence[AccrualCharge] = field(default_factory=tuple)
    periods: Sequence[RatePeriod] = field(default_factory=tuple)


@dataclass(frozen=True)
class PaymentOutcome:
    """What one payment's cash did: the event, the charge it faced, and the split.

    Attributes:
        event: The :class:`LoanCashEvent` this outcome answers -- carrying the
            caller's own ``source`` record back to it.
        charge: The :class:`~._charges.AccrualCharge` standing over this payment
            -- the most recent one the walk applied.  For a RECORDED payment that
            IS its accrual period's: a recorded charge is dated at the earliest
            installment due in its period, the walk is in contract order, and
            nothing can intervene.  For a PROJECTED payment it is the last charge
            walked before it, which is its period's whenever the plan's calendar
            reaches its month and is an earlier month's for a projection the
            calendar has no charge for -- an ad-hoc extra between two
            installments reads the installment it sits after (ruling R-R89's
            reading: a payment's period is the charge the replay hands it).
            **The replay returns it because it already knows it**, and a caller
            that re-derived the pairing would be stating a SECOND association
            rule beside this one: the replay associates by accumulate-and-clear,
            a caller re-deriving it would associate by slot equality, and the two
            agree only while the charges were built from these very payments.
            ``None`` for a payment walked before any charge: ruling R-C's early
            extra, a projection after the loan's latest assertion and before the
            first installment after it, which pays what stands (nothing) and
            reads its ``period`` from the stream's calendar.
        split: The :class:`~app.utils.money.PaymentCashSplit` the ONE allocation
            produced, including the running ``balance_after``.  A payment that
            FOLLOWS another inside one accrual period faces the same ``charge``
            and clears nothing: its ``interest`` and ``escrow`` are ``0.00``.
            The four parts are read HERE, off the allocation's own value: the
            per-field copy the walk used to make of them (``LoanPaymentSplit``,
            deleted at plan step recurrence:R16-c-1) was a second home for one
            value.
        period: The :class:`~app.services.rate_period_engine.RatePeriod`
            governing this payment: the standing charge's -- resolved ONCE, on
            the accrual period the charge IS, so the displayed rate is provably
            the rate its interest accrued at (plan step X-au-g-2c-3b-2) -- or,
            for a payment no charge stands over, the period the loan's calendar
            puts its installment in (:func:`~app.services.rate_period_engine
            .period_for_date` over the stream's ``periods``).
        is_projected: ``True`` for an event from
            :attr:`LoanEventStream.projections` -- a payment that has not
            happened -- ``False`` for a recorded one.  The kind travels as a
            flag set by the replay from the list the event came from, never
            recovered from the ``source``'s type.
    """

    event: LoanCashEvent
    charge: AccrualCharge | None
    split: PaymentCashSplit
    period: RatePeriod
    is_projected: bool

    # The parts every reader wants, read THROUGH the two values that hold them.
    # Delegation, not a copy: each name below has exactly one home, and the
    # flat spelling is the one idiom (the posting writer, the confirmed view,
    # the tax reader and the loan page all read ``outcome.principal``).

    @property
    def source(self) -> object:
        """The record this payment was read off (:attr:`LoanCashEvent.source`)."""
        return self.event.source

    @property
    def due_date(self) -> date:
        """The installment this payment satisfies (:attr:`LoanCashEvent.on_date`)."""
        return self.event.on_date

    @property
    def visible_on(self) -> date:
        """The day this cash counts from (:attr:`LoanCashEvent.visible_on`)."""
        return self.event.visible_on

    @property
    def cash(self) -> Decimal:
        """The cash this payment moved (:attr:`LoanCashEvent.cash`)."""
        return self.event.cash

    @property
    def interest(self) -> Decimal:
        """The interest charge this payment cleared (``>= 0``)."""
        return self.split.interest

    @property
    def escrow(self) -> Decimal:
        """The escrow charge this payment cleared (``>= 0``)."""
        return self.split.escrow

    @property
    def principal(self) -> Decimal:
        """The debt this payment paid down; NEGATIVE for an underpayment (plan D5)."""
        return self.split.principal

    @property
    def excess(self) -> Decimal:
        """Cash beyond what closes the loan, routed to a refund (plan D4)."""
        return self.split.excess

    @property
    def balance_after(self) -> Decimal:
        """The running balance owed after this payment."""
        return self.split.balance_after

    @property
    def charge_date(self) -> date | None:
        """The standing charge's date -- the accrual period's identity -- or ``None``."""
        return None if self.charge is None else self.charge.on_date


@dataclass(frozen=True)
class ResetOutcome:
    """One asserted balance and the running balance it displaced.

    Attributes:
        event: The :class:`LoanResetEvent` this outcome answers.
        balance_before: The running balance JUST BEFORE the reset -- the value a
            correction is booked against, so that the ledger's implied owed moves
            from it to the asserted balance and the two legs sum to zero.
    """

    event: LoanResetEvent
    balance_before: Decimal


@dataclass(frozen=True)
class LoanReplay:
    """A replay's full output: every payment's split and every reset's displacement.

    Both lists are in the order the replay APPLIED them, which is chronological,
    so a caller reading either one reads the loan's own sequence.

    Attributes:
        payments: One :class:`PaymentOutcome` per :class:`LoanCashEvent`, the
            recorded payments and then the projections -- ONE list, in the order
            the replay applied them; ``is_projected`` tells them apart.
        resets: One :class:`ResetOutcome` per :class:`LoanResetEvent` (empty when
            the stream asserts nothing).
    """

    payments: list[PaymentOutcome]
    resets: list[ResetOutcome]


def projection_boundary(stream: LoanEventStream) -> date | None:
    """Return the first day on which *stream*'s projections may be walked.

    The day after the loan's latest RECORDED fact -- its last settled payment
    or its last assertion, in contract time -- or ``None`` when the stream
    records nothing.  A projection whose own date is earlier is keyed here
    instead (:func:`_ordered`), so no projection is ever replayed ahead of a
    fact, and the ``extra_per_period`` what-if accrues only at charges on or
    after it (:func:`replay_loan_events`).  Charges do not move it: a charge is
    the calendar, not a record of anything having happened.

    Public so a test can state the day it expects a projection to land on;
    the seam composes a stream and never re-dates an event against it.

    Args:
        stream: The loan's :class:`LoanEventStream`.

    Returns:
        The boundary ``date``, or ``None`` for a stream with no recorded fact.
    """
    latest = max(
        (event.on_date for event in (*stream.payments, *stream.resets)),
        default=None,
    )
    return None if latest is None else latest + _ONE_DAY


def _ordered(stream: LoanEventStream) -> list[tuple[int, object]]:
    """Return *stream*'s lists merged into ONE walk order.

    Every event is keyed ``(walk_date, on_date, kind rank)``: a recorded event's
    walk date is its own date; a projected charge's or projection's is
    ``max(on_date, projection_boundary)``, so nothing projected precedes a
    recorded fact.  The second key keeps the events pushed to the boundary in
    CONTRACT order among themselves -- April's charge, April's catch-up, May's
    charge, May's catch-up -- exactly as they would have walked had nothing
    pushed them; for a recorded event it equals the first key and changes
    nothing.  The rank orders one date: charge -> payment -> reset (see
    :data:`_CHARGE`), a projected kind ranking as its recorded twin.  The sort
    is STABLE, so each input list's own within-date order survives untouched --
    the contract :class:`LoanEventStream` states -- and a projected event
    listed after the recorded ones walks after them at an equal key.

    Args:
        stream: The caller's pre-ordered event lists.

    Returns:
        ``[(kind, event), ...]`` in walk order, the kind travelling as the
        discriminant rather than being recovered with an ``isinstance`` at the
        fold, so an event class that gained a sibling field could not silently
        change arms.
    """
    boundary = projection_boundary(stream)

    def _pushed(on_date: date) -> date:
        """The walk date of a projected event dated *on_date*."""
        return on_date if boundary is None else max(on_date, boundary)

    tagged = (
        [(charge.on_date, _CHARGE, charge) for charge in stream.charges]
        + [(payment.on_date, _PAYMENT, payment) for payment in stream.payments]
        + [(reset.on_date, _RESET, reset) for reset in stream.resets]
        + [
            (_pushed(charge.on_date), _PROJECTED_CHARGE, charge)
            for charge in stream.projected_charges
        ]
        + [
            (_pushed(payment.on_date), _PROJECTION, payment)
            for payment in stream.projections
        ]
    )
    tagged.sort(
        key=lambda event: (event[0], event[2].on_date, _KIND_ORDER[event[1]]),
    )
    return [(kind, event) for _when, kind, event in tagged]


def replay_loan_events(
    seed: Decimal,
    stream: LoanEventStream,
    extra_per_period: Decimal = _ZERO_MONEY,
) -> LoanReplay:
    """Replay *stream* from *seed* into every payment's split and every reset's jump.

    **The ONE loan replay in the application** (see the module docstring for the
    rule and for why it is one).  It walks the stream in contract order
    (:func:`_ordered`) -- the recorded facts, then the projections behind them
    -- accumulating each accrual period's charge and allocating each payment's
    cash against whatever stands
    (:func:`~app.utils.money.apply_payment_cash`, the ONE allocation, over
    :func:`~app.utils.money.accrue_monthly_interest`, the ONE accrual).

    A CLOSED loan (``balance <= 0``) accrues nothing and impounds nothing, so its
    charge arm is skipped entirely; a payment that follows is a refund in full,
    which the allocator's own closed-loan arm answers.

    **That skip is a TRANSCRIPTION of the fold it replaced, and no current test
    distinguishes it** -- an adversarial review deleted the two lines and all 101
    tests in the loan files stayed green, which is worth recording rather than
    hiding.  The reason it cannot be observed today: a balance reaches ``<= 0``
    only at exactly ``0.00`` (principal caps at the balance), ``accrue_monthly_
    interest(0, r)`` is ``0.00`` anyway, and whatever escrow the arm would
    accumulate is discarded by the allocator's own closed-loan arm at the payment
    that always shares the charge's date.  It is kept because it is not
    unobservable in PRINCIPLE: without it, ``extra_per_period`` would join a
    closed loan's refund, so a what-if search would report an ``excess`` inflated
    by money the owner never pays.  No reader consumes ``excess`` on the forward
    path today, which is the whole of why no test can see it.

    Args:
        seed: The balance the replay starts from.  ``0.00`` for every
            production stream, whose first event is the loan's opening
            assertion -- the ONE seed since plan step recurrence:R16-c-1, when
            the forward fold stopped starting from a balance a separate fold
            had resolved and joined this replay behind the recorded facts.
        stream: The loan's :class:`LoanEventStream`.
        extra_per_period: A HYPOTHETICAL extra added to the cash once per ACCRUAL
            PERIOD, for the target-date search (``_plan_fold.required_extra``).
            ``0.00`` -- the default, and what every real read passes -- replays
            the stream as it stands.  Per PERIOD rather than per PAYMENT since
            plan step R16-a: added per record, "an extra $100 a month" was $2,600
            a year for a definition paying every fortnight.  Accrues at the
            PROJECTED charges only: it is money not yet paid, so it never
            reprices a recorded month.

    Returns:
        The :class:`LoanReplay`.
    """
    balance = seed
    interest_due = escrow_due = extra_due = _ZERO_MONEY
    standing: AccrualCharge | None = None
    payments: list[PaymentOutcome] = []
    resets: list[ResetOutcome] = []
    for kind, event in _ordered(stream):
        if kind in (_CHARGE, _PROJECTED_CHARGE):
            # Recorded BEFORE the closed-loan test, so a payment always carries
            # its own accrual period's charge -- what governs its rate is a fact
            # about the period, not about whether the period accrued anything.
            standing = event
            if balance <= _ZERO_MONEY:
                continue
            interest_due += accrue_monthly_interest(
                balance, event.period.annual_rate,
            )
            escrow_due += event.escrow
            if kind == _PROJECTED_CHARGE:
                extra_due += extra_per_period
        elif kind in (_PAYMENT, _PROJECTION):
            split = apply_payment_cash(
                event.cash + extra_due, balance, interest_due, escrow_due,
            )
            balance = split.balance_after
            interest_due = escrow_due = extra_due = _ZERO_MONEY
            payments.append(PaymentOutcome(
                event=event,
                charge=standing,
                split=split,
                period=(
                    period_for_date(stream.periods, event.on_date)
                    if standing is None else standing.period
                ),
                is_projected=kind == _PROJECTION,
            ))
        else:
            resets.append(
                ResetOutcome(event=event, balance_before=balance)
            )
            # The assertion supersedes every charge standing before it
            # (ruling R-R72 part (2); see the module docstring for when this
            # arm first becomes reachable).
            balance = event.balance
            interest_due = escrow_due = extra_due = _ZERO_MONEY
    return LoanReplay(payments=payments, resets=resets)
