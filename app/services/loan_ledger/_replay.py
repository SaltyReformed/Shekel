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

**A charge is never standing when a RESET is applied, and that is a property of
the construction rather than a case handled here.**  Every charge is dated at the
EARLIEST payment due in its period (:func:`.._charges.charges_for_due_dates`), so
a payment always shares the charge's own date; the order below applies the charge
first and that payment second, both before any reset sharing the date.  So the
question "does an assertion discard a standing charge or carry it across?" has no
reachable answer to give, and this module deliberately writes no branch for it
(``CLAUDE.md`` rule 13).  A future change that dates a charge somewhere no
payment falls would make the state reachable and would owe that ruling then.

Pure: plain data in, plain values out.  No I/O, no clock, no Flask.  All money is
:class:`~decimal.Decimal`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.utils.money import (
    PaymentCashSplit,
    accrue_monthly_interest,
    apply_payment_cash,
)

from ._charges import AccrualCharge

_ZERO_MONEY = Decimal("0.00")

# The kind order applied WITHIN one date, and the whole of what this module
# decides about ordering.  A CHARGE lands before the payments that clear it,
# because interest accrues on a balance no payment has yet reduced; a RESET lands
# last, because an assertion about the balance owed is made after the day's
# money has moved (the settled walk's own rule since step E1c: a payment due
# exactly on an anchor's date is walked, then overwritten by that anchor).
_CHARGE, _PAYMENT, _RESET = 0, 1, 2


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
        source: The caller's record, carried through untouched.  A settled income
            :class:`~app.models.transaction.Transaction` for the settled walk, a
            ``PlannedPayment`` for the forward fold.
    """

    on_date: date
    cash: Decimal
    source: object


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
    """

    on_date: date
    balance: Decimal
    source: object


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
        charges: One :class:`~._charges.AccrualCharge` per accrual period the
            payments occupy (:func:`.._charges.charges_for_due_dates`).
        payments: The cash events, in the caller's own within-date order.
        resets: The asserted balances, in the caller's own within-date order.
            Empty for a forward plan, which asserts nothing: it projects from a
            seed the caller already resolved.
    """

    charges: Sequence[AccrualCharge]
    payments: Sequence[LoanCashEvent]
    resets: Sequence[LoanResetEvent] = field(default_factory=tuple)


@dataclass(frozen=True)
class PaymentOutcome:
    """What one payment's cash did: the event, the charge it faced, and the split.

    Attributes:
        event: The :class:`LoanCashEvent` this outcome answers -- carrying the
            caller's own ``source`` record back to it.
        charge: The :class:`~._charges.AccrualCharge` standing over this payment
            -- the most recent one the walk applied, which IS its accrual
            period's, since a charge is dated at the earliest installment due in
            its period and nothing can intervene.  **The replay returns it
            because it already knows it**, and a caller that re-derived the
            pairing would be stating a SECOND association rule beside this one:
            the replay associates by accumulate-and-clear, a caller re-deriving
            it would associate by slot equality, and the two agree only while the
            charges were built from these very payments.  ``None`` only for a
            stream carrying no charge at or before the payment, which the two
            production callers cannot produce.
        split: The :class:`~app.utils.money.PaymentCashSplit` the ONE allocation
            produced, including the running ``balance_after``.  A payment that
            FOLLOWS another inside one accrual period faces the same ``charge``
            and clears nothing: its ``interest`` and ``escrow`` are ``0.00``.
    """

    event: LoanCashEvent
    charge: AccrualCharge | None
    split: PaymentCashSplit


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
        payments: One :class:`PaymentOutcome` per :class:`LoanCashEvent`.
        resets: One :class:`ResetOutcome` per :class:`LoanResetEvent` (empty when
            the stream asserts nothing).
    """

    payments: list[PaymentOutcome]
    resets: list[ResetOutcome]


def _ordered(stream: LoanEventStream) -> list[tuple[int, object]]:
    """Return *stream*'s three lists merged into ONE walk order.

    Ascending by date, and within a date charge -> payment -> reset (see
    :data:`_CHARGE`).  The sort is STABLE and keys only on ``(on_date, kind)``,
    so each input list's own order survives untouched -- the contract
    :class:`LoanEventStream` states.

    Args:
        stream: The caller's three pre-ordered event lists.

    Returns:
        ``[(kind, event), ...]`` in walk order.  The kind travels as the
        discriminant rather than being recovered with an ``isinstance`` at the
        fold, so an event class that gained a sibling field could not silently
        change arms.
    """
    tagged = (
        [(charge.on_date, _CHARGE, charge) for charge in stream.charges]
        + [(payment.on_date, _PAYMENT, payment) for payment in stream.payments]
        + [(reset.on_date, _RESET, reset) for reset in stream.resets]
    )
    tagged.sort(key=lambda event: (event[0], event[1]))
    return [(kind, event) for _when, kind, event in tagged]


def replay_loan_events(
    seed: Decimal,
    stream: LoanEventStream,
    extra_per_period: Decimal = _ZERO_MONEY,
) -> LoanReplay:
    """Replay *stream* from *seed* into every payment's split and every reset's jump.

    **The ONE loan replay in the application** (see the module docstring for the
    rule and for why it is one).  It walks the stream in contract order
    (:func:`_ordered`), accumulating each accrual period's charge and allocating
    each payment's cash against whatever stands
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
        seed: The balance the replay starts from -- ``0.00`` for the settled
            walk (whose first event is always the loan's opening assertion), the
            projection seed for the forward fold.
        stream: The loan's :class:`LoanEventStream`.
        extra_per_period: A HYPOTHETICAL extra added to the cash once per ACCRUAL
            PERIOD, for the target-date search (``_plan_fold.plan_required_extra``).
            ``0.00`` -- the default, and what every real read passes -- replays
            the stream as it stands.  Per PERIOD rather than per PAYMENT since
            plan step R16-a: added per record, "an extra $100 a month" was $2,600
            a year for a definition paying every fortnight.

    Returns:
        The :class:`LoanReplay`.
    """
    balance = seed
    interest_due = escrow_due = extra_due = _ZERO_MONEY
    standing: AccrualCharge | None = None
    payments: list[PaymentOutcome] = []
    resets: list[ResetOutcome] = []
    for kind, event in _ordered(stream):
        if kind == _CHARGE:
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
            extra_due += extra_per_period
        elif kind == _PAYMENT:
            split = apply_payment_cash(
                event.cash + extra_due, balance, interest_due, escrow_due,
            )
            balance = split.balance_after
            interest_due = escrow_due = extra_due = _ZERO_MONEY
            payments.append(
                PaymentOutcome(event=event, charge=standing, split=split)
            )
        else:
            resets.append(
                ResetOutcome(event=event, balance_before=balance)
            )
            balance = event.balance
    return LoanReplay(payments=payments, resets=resets)
