"""A loan recurrence's VALIDITY WINDOW: the resolver, and the sync it replaces.

Keeps a loan's recurring-payment :class:`~app.models.recurrence_rule.RecurrenceRule`
bounded at BOTH ends by the loan's own facts, so the recurrence engine generates
a payment only while the loan actually exists and owes:

* ``starts_on`` = the loan's FIRST CONTRACTUAL INSTALLMENT (plan step C9a), so
  nothing generates before the loan originates.  A pre-origination payment is
  not merely early -- the fold ERASES it (it splits against a zero balance and
  the origination anchor resets over it: $0.00 principal, the whole payment to
  Refund) while the cash side still debits it, so a mortgage closing one month
  out projected $3,220.92 of payments for a loan that did not exist.
* ``end_date`` = the loan's PROJECTED PAYOFF (Risk R-4), so nothing generates
  past payoff.

This used to run as a write on the loan-detail GET (documented Risk R-4); it now
runs at every chokepoint that can MOVE either bound -- a params / rate edit, a
balance true-up, and every transfer settle / revert / edit / delete / restore of
a loan payment (where an extra-principal payment shifts payoff earliest) -- so
the window tracks the loan without any read-path write.  The two bounds share
ONE entry (:func:`sync_recurring_payment_bounds`) precisely so no chokepoint can
move one and leave the other stale.

**The END bound is DERIVED from the balance, not persisted from a schedule walk**
(plan step C8d, finding B-14).  It used to read the last row of the resolver's
committed schedule -- a walk that amortizes one contractual installment per month
whether or not a payment stands behind it -- so the date this column persisted
could disagree with the payoff every screen showed.  It now reads the seam's
:func:`app.services.balance_at.loan_payoff_date`: the date the loan's BALANCE
folds to zero, the same figure the loan card's chip, the /savings cockpit, and the
property equity chart render.  One derivation, one answer, and the stored copy is
a projection of it rather than a second opinion.

Idempotent, and a genuine fixpoint: it recomputes the payoff and writes only when
``end_date`` actually changes.  Writing ``end_date = D`` stops shadow generation
after D, but the balance already reached zero AT D, so the payments the bound
removes are exactly the ones the fold ignored -- a re-run at the new state derives
D again.  The payoff is always measured in the owner's BASELINE scenario (the loan
card's trajectory), whatever scenario triggered the sync.  Flask-isolated: plain
``account_id`` in, no ``request`` / ``session`` reads; flushes into the caller's
transaction and never commits (the caller owns the transaction boundary).

The RESOLVER that replaces the closing half of all that
----------------------------------------------------------

**A persisted derivation is a cache, and this one is measurably behind on live
data** (plan ledger row **D35**): rule 48 stores ``end_date`` ``2029-01-22``
where the Van Loan's derived payoff is ``2029-02-22``, so extending the calendar
generates rows only to the stored date and the ``$531.94`` installment due
``2029-02-22`` is never created.  TEN call sites write the column between them
-- ``params.py:190`` / ``:330`` / ``:448``, ``escrow_rates.py:170``,
``payment_transfer.py:251`` / ``:277`` / ``:344`` / ``:418`` and
``_loan_posting.py:306`` / ``:387`` -- and any reader can arrive before the next
one runs.
:func:`loan_payment_window` answers the same question by ASKING the loan, and
plan step R7d decomposes into one leaf per surface that reads it: R7d-b builds
the resolver (nothing reads it), R7d-c through R7d-f move the six readers, and
R7d-g stops the write and lands
``ck_recurrence_rules_valid_window`` true by construction.

**Only the CLOSING bound stops being stored** (ruling **R-R29**).  ``starts_on``
is a contract fact rather than a fold -- it resolves for an owner with no
baseline scenario, where a payoff cannot -- and it is the cadence ANCHOR that a
MONTH-unit rule has no day to fire on without, so :func:`_sync_loan_cadence`
stays a writer.  That asymmetry is why the two halves are separate functions
here and always were.

**The resolver takes the DEFINITION and not the loan** (ruling **R-R35**), so
it never has to answer which recurring transfer into a loan is "the" payment --
a question nothing in the schema answers and every consumer of
:func:`~app.services.recurring_transfer_query.active_recurring_transfer_template`
currently tie-breaks on ``id``.  Every recurring transfer into a loan is paying
it down, and each stops when the loan does.  See that function's docstring for
the measurement and for the half of plan ledger row **D47** that remains.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.account import Account
from app.services import balance_at, loan_loaders, rate_period_engine
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    NEVER_ENDS,
    EndsOnDate,
    RecurrenceOwner,
    RecurrenceSpec,
    end_bound_from_columns,
    offerable_nominal_days,
    reauthor_rule,
    recurrence_spec,
    resolve,
    resolved_recurrence,
)
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
)
from app.services.balance_at import BalanceContext
from app.utils.log_events import (
    BUSINESS,
    EVT_LOAN_RECURRENCE_END_DATE_UPDATED,
    EVT_LOAN_RECURRENCE_START_DATE_UPDATED,
    log_event,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only; these are ORM row types
    from app.models.loan_params import LoanParams
    from app.models.recurrence_rule import RecurrenceRule

logger = logging.getLogger(__name__)


def recurrence_end_date(
    payoff_date: date | None, is_retired: bool, as_of: date,
) -> date | None:
    """Return the recurrence end_date a loan's derived payoff implies.

    The three states of the DERIVED payoff
    (:attr:`~app.services.balance_at.LoanFigures.payoff_date`), mapped onto the
    recurrence bound.  ``payoff_date`` is ``None`` for two different loans, and
    ``is_retired`` is what tells them apart -- collapsing them would either leave a
    finished loan generating payments forever or halt a loan that still owes:

    * **Pays off on a date** -- that date, so recurrence stops the month the
      balance reaches zero.
    * **Already RETIRED** (``None`` and owing nothing) -- *as_of*, the pass's own
      now: the loan plans no further payments.  (The pre-C8d writer used the last
      schedule row for a retired loan WITH history and its ``origination_date``
      for one without -- two dates for one state; *as_of* is ONE rule.)

      **This bounds the OCCURRENCE, and plan step R4a is what changed that.**
      ``end_date`` used to bound PERIODS -- a period was admitted when
      ``period.start_date <= end_date``, so the CURRENT period, which started
      before *as_of*, still matched and only the claim predicate stopped a
      further payment being generated into it for a loan that owes nothing.
      Forward generation stops at the first occurrence past the bound, so a
      retired loan's next installment is simply never emitted: finding **N-19**
      (a bound that excluded the current period outright would have to be that
      period's start minus a day) is closed by the model rather than by a
      different bound.  Note also that a retired loan whose
      payoff-affecting mutations span days rewrites this to each new day, so
      "idempotent" is idempotent WITHIN a day.
    * **Never pays off** (``None`` and NOT retired -- negative amortization, or an
      underpayment too severe to clear the plan's post-contractual extension) --
      ``None``, leaving recurrence indefinite until the user raises the payment.
      That is what C7's payment-drift warning exists to prompt.

    Args:
        payoff_date: The loan's derived payoff, or ``None``.
        is_retired: Whether the loan has originated and owes nothing
            (:attr:`~app.services.balance_at.LoanFigures.is_retired`).
        as_of: The read pass's as-of, the retired loan's bound.

    Returns:
        The recurrence ``end_date``, or ``None`` to leave generation indefinite.
    """
    if payoff_date is not None:
        return payoff_date
    return as_of if is_retired else None


class LoanPaymentWindow(ABC):
    """How long a recurring transfer INTO A LOAN goes on firing -- three shapes.

    The DERIVED closing bound of a definition whose destination is a configured
    loan, as one value with three shapes rather than the ``date``-or-``None``
    pair :func:`recurrence_end_date` answers in.  That pair spells two of the
    three alike: a real closing date and an ALREADY-EMPTY window are both a
    ``date``, and telling them apart is the whole reason plan ledger row
    **D35**'s ``ck_recurrence_rules_valid_window`` was drafted and then held
    back.  A user AUTHORING a stop before a start has made a mistake to report;
    a loan trued to zero before its first installment has an empty window that
    is CORRECT at nought occurrences, and a CHECK that cannot tell those apart
    turns the second into an unhandled ``CheckViolation`` out of a true-up.

    **Every shape answers :meth:`admits`, and this base implements none of it.**
    A default here -- "a shape that does not recognise the question keeps
    firing" -- is the partial-function-over-a-closed-set defect this arc exists
    to remove, and on a loan it would go on charging a debt the owner has
    cleared.  ``@abstractmethod`` makes a half-written fourth shape
    unconstructible rather than merely wrong; :class:`EndBound` states the same
    contract one concept over, and for the same reason.

    **It is NOT an** :class:`~app.services.recurrence.EndBound`, deliberately.
    That type is what the form OFFERS, the schema ACCEPTS and the two columns
    STORE, so each of its shapes owes a ``token``, a ``from_payload`` and a
    ``columns()``.  An empty window is none of those things: nothing offers it,
    nothing posts it, and after plan step R7d-g nothing stores it.  Adding a
    fourth shape there would put an unauthorable, unstorable member in the
    closed set the picker is derived from.

    **What a WINDOW says and what a rule's own bound says are ANDed, never
    substituted** (plan step R7d-c applies it).  A rule keeps whatever closing
    bound it authors; this narrows it further where the loan's life is shorter.
    """

    @abstractmethod
    def admits(self, occurrence: date) -> bool:
        """Return whether the loan's life still covers *occurrence*.

        **Every occurrence walk in this project is ASCENDING, so the first
        ``False`` is also the last one worth asking about**: a caller STOPS
        rather than skipping, and a shape answering ``False`` for one
        occurrence and ``True`` for a later one would be a window that reopens.
        Stated here because it is a contract over all three shapes rather than
        a property of any one of them -- the same contract
        :meth:`~app.services.recurrence.EndBound.admits` states.

        Args:
            occurrence: The date the definition's cadence names.  The
                OCCURRENCE and never the pay period it is funded from (ruling
                **R-R6**): a period whose payday precedes the payoff can
                contain an installment that follows it.

        Returns:
            ``True`` while the loan's life still covers *occurrence*.
        """


@dataclass(frozen=True)
class ClosesOn(LoanPaymentWindow):
    """The loan pays off on a date, so the definition stops there.

    Attributes:
        on: The loan's DERIVED payoff -- the date its balance folds to zero
            (:attr:`~app.services.balance_at.LoanFigures.payoff_date`), or the
            read pass's own now for a loan that is already retired.  The last
            day an occurrence may fall on, inclusive: the balance reaches zero
            AT that installment, so the installment itself is owed.
    """

    on: date

    def admits(self, occurrence: date) -> bool:
        """Admit occurrences up to and including the payoff.

        Args:
            occurrence: The date the cadence names.

        Returns:
            ``True`` when *occurrence* falls on or before :attr:`on`.
        """
        return occurrence <= self.on


@dataclass(frozen=True)
class Indefinite(LoanPaymentWindow):
    """The loan never pays off, so nothing derived stops the definition.

    Negative amortization, or an underpayment too severe to clear even the
    plan's post-contractual extension.  The payments must keep generating --
    the loan still owes -- until the owner raises them, which is what plan step
    C7's payment-drift warning exists to prompt.  Answering anything else here
    would silently stop projecting a debt the owner is still paying.
    """

    def admits(self, occurrence: date) -> bool:
        """Admit every occurrence.

        Args:
            occurrence: Unread -- an unbounded window measures nothing.

        Returns:
            Always ``True``.
        """
        return True


@dataclass(frozen=True)
class Empty(LoanPaymentWindow):
    """The loan's life closed BEFORE this definition's first occurrence.

    A loan originated 2026-06-20 with a ``payment_day`` of 15 owes its first
    installment 2026-07-15; true its balance to zero and it retires, so on a
    read pass as of 2026-07-01 the derived window is
    ``[2026-07-15, 2026-07-01]`` -- CORRECT at nought occurrences.  Plan ledger
    row **D35** carries the same shape as the state that held
    ``ck_recurrence_rules_valid_window`` back, because a CHECK cannot tell it
    from an owner's mistake.

    **The example is stated against the READ PASS's as-of, and an adversarial
    review of this step is why.**  A retired loan's closing bound IS
    ``ctx.as_of`` (:func:`recurrence_end_date`), so for the retired branch this
    shape is TRANSIENT: the same untouched loan answers ``ClosesOn(today)``
    from the day the as-of reaches the first occurrence.  An earlier wording
    here described the STORED column's behaviour -- where the date froze at
    whichever day a chokepoint last ran -- and would have taught every reader
    plan steps R7d-d through R7d-f move over the wrong model.  The shape is
    STABLE only where the loan has a real past ``payoff_date`` before the
    definition's first occurrence.

    **It admits exactly what a** :class:`ClosesOn` **before the same first
    occurrence admits -- nothing -- so it is a PRECOMPUTATION of a comparison
    its readers could make, held once where they would each make it.**
    Generation cannot tell the two apart and does not need to: it emits nothing
    either way.  What differs is what a reader may SAY -- a DISPLAY surface
    naming "until Jul 1, 2026" for a definition that fires from the 15th is
    false about a date, where "this loan is finished" is true.
    """

    def admits(self, occurrence: date) -> bool:
        """Admit nothing.

        Args:
            occurrence: Unread -- an empty window covers no date at all.

        Returns:
            Always ``False``.
        """
        return False


#: The window of a loan that never pays off.  A module-level singleton because
#: the shape carries no data, exactly as ``recurrence.NEVER_ENDS`` is one;
#: frozen dataclasses compare by value, so ``==`` answers for a fresh instance
#: too and no caller has to know which it holds.
INDEFINITE: Indefinite = Indefinite()

#: The window of a loan whose life closed before its payment's first
#: occurrence.  See :data:`INDEFINITE` for why it is a singleton.
EMPTY: Empty = Empty()


def loan_payment_window(
    template: RecurrenceOwner, ctx: BalanceContext,
) -> LoanPaymentWindow | None:
    """Return when *template* stops paying its destination loan, or ``None``.

    **The RESOLVER plan step R7d-b builds, and the value TEN call sites
    currently write into ``budget.recurrence_rules.end_date`` between them**
    (R7d-g deletes nine of them; ``params.py:190`` stays, for the OPENING bound
    R-R29 keeps stored).
    A loan's payoff is a fold over its forward plan, so a bound persisted at
    mutation time is a CACHE of a derivation -- and it is measurably behind on
    live data: rule 48 stores ``2029-01-22`` where the Van Loan's derived
    payoff is ``2029-02-22``, so extending the calendar generates rows only to
    the stored date and the ``$531.94`` installment due ``2029-02-22`` is never
    created (plan ledger row **D35**).  Asking rather than storing is what
    makes that unconstructible.

    **It takes the DEFINITION, not the loan, and that is a developer ruling of
    2026-08-25** (**R-R35**) rather than the ``(account, ctx)`` this step was
    originally specified with.  Asking "when does this loan's payment stop"
    forces a prior question -- WHICH of the recurring transfers into the loan
    is its payment -- that nothing in the schema answers, so
    :func:`~app.services.recurring_transfer_query.active_recurring_transfer_template`
    tie-breaks it on ``id``.  Measured on a production clone with the sweep's
    id FORCED below the Mortgage payment's (that tie-break is ascending, so a
    row created later loses it and the reachable ordering is the sweep
    authored FIRST): a ``$200.00``/mo transfer into the Mortgage drives the
    derived payoff from ``2048-12-01`` to ``None``, against a ``$616.99``
    monthly escrow the ``$200.00`` does not even cover.  Asking "when does THIS
    transfer into a loan stop" needs no such answer: **every** recurring
    transfer into a loan is paying it down --
    the settled fold
    (:func:`~app.services.loan_loaders.query_shadow_income`) and the PLANNED
    tier (``balance_at._plan.loan_plan``) both already sum every
    one of them with no template filter -- and each of them stops when the loan
    does, because past payoff
    :func:`~app.services.loan_ledger.split_payment_cash` routes the whole cash
    to ``excess`` (a Refund) rather than to principal.

    **What that buys is precise, and it is less than "the tie-break is gone".**
    This function's SUBJECT is no longer chosen by one -- every definition into
    a loan is asked about and every one gets the same answer -- but the
    ANSWER's value still travels through it: ``loan_figures`` ->
    ``resolved_loan`` -> ``standing_payment`` ->
    ``active_recurring_transfer_template``, whose ``.order_by(id).first()``
    prices the ESTIMATED tier.  In the `$200.00` case above the resolver
    returns ``INDEFINITE`` for BOTH definitions: they agree, and both are
    wrong.  Plan ledger row **D47** carries that half and **R16** closes it by
    making the estimate SUM, as its two sibling tiers already do.

    **EMPTY is decided against the rule's OWN first occurrence.**
    ``budget.recurrence_rules.starts_on`` IS the rule's first occurrence by
    construction -- :func:`~app.services.recurrence.resolve` normalises it and
    ``_authoring._author`` writes the normalised value (ruling **R-R16**) -- so
    "the loan closed before this definition ever fires" is exactly
    ``closes < rule.starts_on``, and it is the same comparison as "no
    occurrence of this rule falls inside the window" because every walk
    ascends from that date.  Read off the rule rather than re-derived from the
    loan's contract deliberately: the window describes what THIS rule does, and
    generation walks from the stored value.

    Additive at this step -- **nothing reads it**, so no figure moves.  Plan
    steps R7d-c through R7d-f move the six reading surfaces onto it one at a
    time, and R7d-g then stops the column being written at all.

    A pure READ: it opens no transaction, writes nothing and reads no clock of
    its own (*ctx* carries the pass's ``as_of``).

    Args:
        template: The recurring definition being asked about -- a
            ``TransferTemplate``, or a ``TransactionTemplate``, which can never
            pay into an account and always answers ``None``.  ``getattr`` on
            the FK COLUMN is what keeps this kind-agnostic across the two, the
            same way :func:`owns_validity_window` is.  **Must belong to
            ``ctx.user_id``** -- the caller owns the ownership check, as every
            seam entry this reaches states: pairing one owner's definition with
            another's read pass produces a plausible blended answer rather than
            a refusal, because the loan bundle scopes its payment feed by the
            PASS's scenario and its standing payment by the ACCOUNT's owner.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.  Its ``as_of`` is
            a retired loan's bound and its scenario scopes the fold; the pass
            is TAKEN and never built here (the 2026-08-16 ruling -- a producer
            below the route does not build one).

    Returns:
        The :class:`LoanPaymentWindow`, or ``None`` when nothing about a loan
        bounds this definition: it carries no recurrence rule, it pays into no
        account, or its destination is not a configured loan.  ``None`` is
        "this question does not apply here" and never a fourth window shape --
        the three shapes are the answers, and a definition with no loan behind
        it has no derived bound at all.

    Raises:
        BaselineMissingError: When the destination IS a configured loan and
            *ctx* has no baseline scenario, from the seam's own
            ``require_scenario``.  Ruling **R-R30** (2026-08-19): a producer
            that needs a scenario REFUSES, to the single application-level
            handler, rather than early-returning -- the early return it
            replaces left the last-written bound standing, and once nothing is
            stored there is nothing to stand.  The not-a-loan answer above is
            reached FIRST, so a savings or investment transfer still resolves
            for an owner with no baseline.
    """
    rule = getattr(template, "recurrence_rule", None)
    # **The COLUMN, then a lookup -- never ``template.to_account``**, and an
    # adversarial review of this step measured why.  That relationship is
    # ``lazy="joined"``, which loads it with the template and then does NOT
    # refresh it when the FK column is written: measured on SQLAlchemy 2.0.49,
    # a ``setattr(template, "to_account_id", other)`` leaves ``to_account``
    # pointing at the OLD account through the following ``flush()`` and only
    # re-loads at ``commit()``.  ``routes/transfers/templates.py`` writes
    # exactly that -- ``to_account_id`` is in ``_TEMPLATE_UPDATE_FIELDS`` and
    # is assigned by ``setattr`` -- and then REGENERATES before committing, so
    # a resolver reading the relationship would bound the new destination's
    # rows by the OLD loan's payoff.  A pending template is the second state:
    # its ``to_account_id`` is set and its ``to_account`` is still ``None``.
    # ``db.session.get`` costs nothing when the row is already in the identity
    # map, which is the case the joined load creates anyway.
    account_id = getattr(template, "to_account_id", None)
    if rule is None or account_id is None:
        return None
    account = db.session.get(Account, account_id)
    if account is None:
        # Unreachable for a persisted definition -- ``to_account_id`` is NOT
        # NULL under an ``ON DELETE RESTRICT`` foreign key -- and reachable
        # only for one not yet flushed, which generates nothing either way.
        # The same early return :func:`sync_recurring_payment_bounds` makes.
        return None
    # ``loan_figures`` is asked for the not-a-loan answer as well as for the
    # payoff, rather than a ``load_loan_params`` pre-check beside it: that
    # would be a second producer of "is this a configured loan", and the seam
    # runs its own test BEFORE the scenario guard precisely so a caller may
    # use it this way (see its docstring).
    figures = balance_at.loan_figures(account, ctx)
    if figures is None:
        return None
    closes = recurrence_end_date(
        figures.payoff_date, figures.is_retired, ctx.as_of,
    )
    if closes is None:
        return INDEFINITE
    return (
        EMPTY if closes < _first_occurrence(rule, ctx) else ClosesOn(on=closes)
    )


def _first_occurrence(rule: "RecurrenceRule", ctx: BalanceContext) -> date:
    """Return the first date *rule* actually fires on, for the EMPTY test.

    **RESOLVED, not read off ``starts_on``, and an adversarial review of this
    step refuted the column.**  For every unit but one the two are the same
    value -- ``_authoring._author`` writes ``resolve``'s normalised date, so
    the column IS the first occurrence (ruling **R-R16**), and it was measured
    equal for all 43 live rules on a production clone.  The exception is the
    ``PERIOD`` unit, which ``_resolution`` re-normalises on EVERY read:
    ``calendar.span_containing(max(starts_on, opening)).start_date`` is the
    START of the paycheck covering the stored date, so once the owner edits
    their pay schedule and that date stops being a payday, the walk's first
    occurrence lands BEFORE the column.  Plan ledger row **D39** is that drift.

    Reading the column would then answer EMPTY for a window that admits a real
    occurrence: an every-paycheck loan payment storing 2026-06-15, a schedule
    edited to pay on the 11th and the 25th, and a loan retired by 2026-06-12
    resolves ``[2026-06-11, 2026-06-12]`` -- one occurrence -- while
    ``2026-06-12 < 2026-06-15`` says EMPTY, and R7d-f would tell the owner a
    firing definition is finished.

    **An UNRESOLVABLE rule answers with the column rather than raising.** The
    one state :func:`~app.services.recurrence.resolved_recurrence` returns
    ``None`` for is an owner with NO pay periods, where nothing generates at
    all; the honest answer there is the conservative one, because claiming
    EMPTY would say "finished" about a definition whose schedule simply does
    not exist yet.

    Args:
        rule: The definition's stored recurrence rule.
        ctx: The read pass, for its memoized calendar -- so this costs no load
            of its own on a pass that has already resolved one.

    Returns:
        The rule's first occurrence.

    Raises:
        RecurrenceResolutionError: The rule cannot be resolved against the
            owner's schedule -- an unmodelled pattern, a non-positive interval,
            a value outside its column's domain.  The same refusal
            :func:`~app.services.recurrence.has_ended` and
            :func:`~app.services.recurrence.rule_occurrences` already make
            about the same rules, rather than a new one: a rule the app cannot
            read has no window either.
    """
    resolved = resolved_recurrence(rule, ctx.calendar())
    return rule.starts_on if resolved is None else resolved.starts_on


@dataclass(frozen=True)
class LoanCadenceStart:
    """When a loan's recurring payment first fires, as its rule states it.

    The two fields ``budget.recurrence_rules`` uses to say "the last day of
    every month starting in April", held together because they are one fact and
    the schema refuses them apart
    (``ck_recurrence_rules_nominal_day``); see :func:`loan_cadence_start`.

    Attributes:
        starts_on: The loan's first contractual installment, and therefore the
            recurrence's first occurrence.
        nominal_day: The contractual ``payment_day`` when *starts_on*'s own
            month was too short to hold it, else ``None``.
    """

    starts_on: date
    nominal_day: int | None


def loan_cadence_start(
    unit: RecurrenceUnitEnum, params: "LoanParams",
) -> LoanCadenceStart:
    """Return the first occurrence a loan's contract implies for *unit*.

    **The ONE producer of "when does this loan's payment start", and plan step
    R7c-b is what made it one** (developer ruling, 2026-08-15).  Two callers
    need the answer and each used to compute it: :func:`_sync_loan_cadence`
    below, and ``routes/loan/payment_transfer.py``, which built the rule with a
    typed cadence and then let the sync overwrite it a few lines later.  That
    second shape was worse than duplication -- on the generic transfer form the
    date the USER typed was written and then silently discarded (see
    :data:`~app.routes._recurrence_form_refusals.LOAN_PAYMENT_BOUND_IS_DERIVED`,
    which states the same rule for the EDIT path).  Deriving it BEFORE the rule
    is built is the ruling: nothing is authored that something else immediately
    replaces.

    **``starts_on`` is the loan's first contractual installment.**  A payment
    cannot precede the loan, and generation before origination is not merely
    early -- the fold ERASES it (the payment splits against a zero balance and
    the origination anchor then resets over it: $0.00 principal, the whole
    payment to Refund) while the cash side still debits it, so a mortgage
    closing one month out projected $3,220.92 of payments for a loan that did
    not exist (plan step C9a).

    **``nominal_day`` is what keeps a month-end loan on the month's end.**  A
    servicer's ``payment_day`` of 31 means the last day of every month; if the
    first installment lands in a 30-day month the DATE alone says "the 30th",
    and every later payment would be modelled a day early forever.  The pair is
    exactly the one ``ck_recurrence_rules_nominal_day`` admits -- present only
    where the installment month clamped the contractual day -- and
    :class:`~app.services.recurrence.RecurrenceSpec` refuses any other pairing
    at construction.

    A cadence with no day-of-month coordinate (an every-paycheck loan payment,
    plan ledger row **D27**'s unenforced precondition) states no nominal day:
    it bills by PAYCHECK, so ``resolve`` normalises the installment date onto
    the paycheck that hosts it and there is no contractual day to keep.

    Reads no clock: these are contract facts, not functions of when the sync
    happened to run (the A3 rule).

    Args:
        unit: The recurrence's cadence unit, which is the WHOLE of what decides
            whether the cadence has a day-of-month coordinate.  It took the
            placement beside it until plan step R7c-b; see the inline comment
            for the wrong-money defect that pairing caused, and
            :func:`~app.services.recurrence.has_day_of_month_coordinate` for why
            the unit alone is the right question.
        params: The loan's :class:`~app.models.loan_params.LoanParams`.

    Returns:
        The :class:`LoanCadenceStart`.
    """
    starts_on = rate_period_engine.first_installment_date(
        params.origination_date, params.payment_day,
    )
    # MEMBERSHIP in the reader's own set, never a second list of conditions.
    # ``offerable_nominal_days`` IS the rule -- the cadence must have a
    # day-of-month coordinate, the date must be its month's last day, and the
    # value must exceed it and stay inside 29-31 -- and it is what
    # ``RecurrenceSpec``, ``ResolvedRecurrence`` and
    # ``ck_recurrence_rules_nominal_day`` all admit against.
    #
    # **Restating the conditions here was a wrong-money defect** (plan step
    # R7c-b).  This asked ``fires_on_day_of_month(unit, placement)``, which
    # answers whether a generated ROW is dated from that day,
    # while ``ResolvedRecurrence.day_of_month`` keys on the UNIT alone -- and
    # they differ for exactly ``Monthly First``.  Measured against the real
    # functions, a servicer's day-31 payment first billing in a 30-day month::
    #
    #   MONTH / CONTAINING_DATE
    #     orig 2026-03-10 pday 31 -> starts_on 2026-04-30 nominal 31, day 31
    #   MONTH / PERIOD_STARTING_ON_OR_AFTER
    #     orig 2026-03-10 pday 31 -> starts_on 2026-04-30 nominal None, day 30
    #
    # Reachable through ``POST /transfers``, where ``Monthly First`` is
    # authorable and ``settle_first_occurrence`` derives the pair for it: under
    # that placement the funding PAYCHECK moves a period early in every month
    # whose schedule puts a payday on the 30th, for the life of the loan.
    return LoanCadenceStart(
        starts_on=starts_on,
        nominal_day=(
            params.payment_day
            if params.payment_day in offerable_nominal_days(unit, starts_on)
            else None
        ),
    )


def loan_cadence_spec(
    spec: RecurrenceSpec, params: "LoanParams",
) -> RecurrenceSpec:
    """Return *spec* with the loan's contractual first occurrence stated.

    :func:`loan_cadence_start` applied to a spec the caller already holds --
    the shape the two IN-PLACE writers need, where the rest of the recurrence
    is the rule's own and only its start is the loan's.  A create route builds
    its spec from the value instead; see that function for the derivation and
    for why one producer answers both.

    Args:
        spec: The rule's current authored state.
        params: The loan's :class:`~app.models.loan_params.LoanParams`.

    Returns:
        *spec* with ``starts_on`` and ``nominal_day`` replaced.
    """
    start = loan_cadence_start(spec.unit, params)
    return replace(
        spec, starts_on=start.starts_on, nominal_day=start.nominal_day,
    )


def _sync_loan_cadence(rule: "RecurrenceRule", params: "LoanParams") -> None:
    """Bring a loan recurrence's opening bound onto the loan's contract.

    Nothing else re-points a loan payment after a ``payment_day`` or
    ``origination_date`` edit.  Before plan step R7c-b this had to keep TWO
    columns in step -- the opening bound and the scheduling day -- and the two
    disagreeing was worse than either being stale: measured on a mortgage whose
    ``payment_day`` went 1 -> 20, the bound advanced to the 20th while the rule
    still matched the 1st, so the surviving period contained no matching day and
    the recurrence generated **nothing at all**.  One date carries both facts
    now (ruling **R-R16**), so that failure mode is unconstructible rather than
    guarded against.

    **Scenario-INDEPENDENT, which is why it is separated from the end bound**
    (the C8e lesson): the value is a function of the loan's params alone, so it
    resolves for a user with NO baseline scenario, where the payoff-derived end
    bound cannot.  Keeping this ahead of the caller's scenario guard is what
    stops a missing baseline from silently leaving a loan unbounded at the start.

    **Idempotent in TWO stages, and the second one is what a day-less rule
    needs.**  The cheap comparison is on the authored spec, which settles it for
    every rule that bills on a day of the month -- both of the developer's live
    loan payments, and the case the settle / revert path hits on every mutation,
    so the schedule is still not loaded there.  A rule that bills by PAYCHECK
    stores the payday ``resolve`` normalised the installment onto, which never
    equals the raw installment date, so the spec comparison alone would re-author
    and log on every settle forever.  Comparing the RESOLVED values is what
    answers that, and it is reached only when the cheap check fails.

    Args:
        rule: The recurring payment's :class:`RecurrenceRule`.
        params: The loan's :class:`~app.models.loan_params.LoanParams`.
    """
    current = recurrence_spec(rule)
    wanted = loan_cadence_spec(current, params)
    if wanted == current:
        return
    calendar = calendar_for(rule.user_id)
    if resolve(wanted, calendar) == resolve(current, calendar):
        return
    old_start = current.starts_on
    # RE-AUTHORED, not assigned: a rule is written whole through one door, so
    # the cycle phase and the closed set's storage encoding are re-derived from
    # the date this call is moving rather than left holding what a previous
    # contract implied.
    reauthor_rule(rule, wanted, calendar)
    log_event(
        logger, logging.INFO,
        EVT_LOAN_RECURRENCE_START_DATE_UPDATED, BUSINESS,
        "Updated recurrence rule start date to first contractual installment",
        account_id=params.account_id,
        rule_id=rule.id,
        old_start_date=str(old_start),
        new_start_date=str(wanted.starts_on),
        old_nominal_day=current.nominal_day,
        new_nominal_day=wanted.nominal_day,
    )


def bind_rule_to_loan(rule: "RecurrenceRule", account_id: int) -> None:
    """Bound a NEWLY built recurrence rule to its destination account's loan life.

    The creation-time entry point, for a route that has just built a rule and is
    about to generate against it.  A no-op unless *account_id* is a configured
    loan, so a caller may call it for ANY destination without a type check.

    Takes the rule DIRECTLY rather than looking it up from the account, which
    :func:`sync_recurring_payment_bounds` must do: that lookup returns the
    account's FIRST active recurring template, so a second recurring payment
    created into the same loan would leave the NEW rule unbounded while
    re-bounding the old one -- silently reopening the very hole this closes.

    Args:
        rule: The just-built :class:`RecurrenceRule`, before generation.
        account_id: The transfer's destination account (any kind).
    """
    params = loan_loaders.load_loan_params(account_id)
    if params is None:
        return
    _sync_loan_cadence(rule, params)


def owns_validity_window(template: "object") -> bool:
    """Return whether THIS module writes *template*'s recurrence bounds.

    **The one predicate, so the form's LOCK and this module's own guard cannot
    disagree** (plan step R7b-4).  A recurring loan payment's opening and
    closing bounds are DERIVED -- the loan's first contractual installment and
    its projected payoff -- so both forms render those controls read-only and
    both refuse a crafted submission that states one.  What decides that has
    to be the condition :func:`sync_recurring_payment_bounds` returns early on,
    or the form locks a control for a value nothing writes.

    **It is that function's OPENING-bound precondition exactly, and its
    closing-bound one only approximately** -- an adversarial review of plan
    step R7b-4 measured the gap and it is stated rather than papered over. The
    start half is scenario-independent by design (ruling C8e: a loan's contract
    terms are not scenario-scoped), so this predicate is complete for it. The
    END half additionally returns early when the owner has no baseline
    scenario, which this does not ask -- so an owner in that state sees a
    locked "Ends" control for a payoff nothing currently writes. That is the
    same defect SHAPE this predicate was built to close, one condition
    narrower, and it is far less reachable: registration creates a baseline,
    so the state is a broken invariant rather than a configuration. Splitting
    the predicate per bound is the remedy if it is ever measured live.

    **It was NOT the same condition, and an adversarial review of plan step
    R7b-3 measured the gap.**  The lock asked
    ``_recurrence_form_refusals.is_loan_payment`` -- "does this template carry a
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row" --
    which is BROADER than what the sync writes for: the destination must also
    be a CONFIGURED loan (``LoanParams``), and the template must be the one
    that lookup returns, since a second recurring payment into one loan leaves
    the newer rule unbounded.  A settings-carrying template outside that set
    rendered its "Ends" control locked, saying the value came from the loan's
    projected payoff, for a payoff nothing wrote.  None is measured on the
    developer's data -- every live loan payment satisfies both -- but plan step
    R7b-4 locks the OPENING bound on the same question, so deciding it once
    here is what stops the second lock inheriting the first's error.

    **Not the same question as "is this a loan payment".**
    :func:`~app.routes._recurrence_form_refusals.is_loan_payment` keeps the
    settings-row reading, and correctly: what it decides is whether clearing
    the recurrence would strand a standing ``extra_principal``, which is a
    property of that row rather than of this module's write set.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` a form is
            rendering.  A transaction template can never be a loan payment, and
            ``getattr`` is what keeps this kind-agnostic for the two form
            helpers that call it.

    Returns:
        ``True`` when this module writes the template's ``starts_on`` and
        ``end_date``, so its form must render both read-only.
    """
    account_id = getattr(template, "to_account_id", None)
    if account_id is None or template.recurrence_rule is None:
        return False
    if loan_loaders.load_loan_params(account_id) is None:
        return False
    active = active_recurring_transfer_template(account_id, template.user_id)
    return active is not None and active.id == template.id


def sync_recurring_payment_bounds(account_id: int) -> None:
    """Sync a loan's recurring-payment validity window to the loan's own facts.

    The ONE entry every chokepoint calls, syncing BOTH ends of the recurrence's
    window so no caller can move one and leave the other stale:

    * ``starts_on`` -- the loan's first contractual installment
      (:func:`loan_cadence_spec`); a payment cannot precede the loan.
    * ``end_date`` -- the loan's derived payoff (R-4,
      :func:`recurrence_end_date`); a payment cannot follow the payoff.

    The two are deliberately NOT symmetric in what they require: the start is a
    contract fact and resolves with no scenario, while the end is a fold over
    the forward plan and needs a baseline.  So the start is written FIRST, ahead
    of the scenario guard -- a user with no baseline still gets a correctly
    bounded start rather than an unbounded one (the C8e lesson: a loan's
    contract terms are not scenario-scoped).

    A no-op -- returning before any write -- when the account is not a
    configured loan, has no recurring payment, or is already at the right
    bounds; the end half additionally no-ops without a baseline scenario.
    Flushes into the caller's transaction (does NOT commit).

    **A FRESH context per call, deliberately.**  This runs mid-mutation, so it
    must see the loan as the just-flushed write left it; a
    :class:`~app.services.balance_at.BalanceContext` is a plain value with
    a memo scoped to one read, never a request cache, so building one here is how
    a writer reads post-write state (see that module's "read pass, not request").

    Called from every chokepoint that can move the projected payoff: loan-params
    create / update, the ARM / origination-rate change, the balance true-up, the
    recurring-transfer creation, and the transfer settle / revert / edit / delete
    / restore paths (via :mod:`app.services.transfer_service._loan_posting`).

    Args:
        account_id: The loan account whose recurring-payment validity window
            (``starts_on`` / ``end_date``) to sync.
    """
    account = db.session.get(Account, account_id)
    if account is None:
        return
    # The template lookup comes FIRST: with no recurring payment there is no
    # end_date to bound, and deriving the payoff means folding the loan's whole
    # forward plan.  Cheapest disqualifying check first.
    template = active_recurring_transfer_template(account_id, account.user_id)
    if template is None or template.recurrence_rule is None:
        return
    rule = template.recurrence_rule

    # The START bound first: it needs the loan's params and NOTHING else, so it
    # must not sit behind the scenario guard below (C8e -- a loan's contract
    # terms are not scenario-scoped).
    params = loan_loaders.load_loan_params(account_id)
    if params is None:
        # Not a configured loan (no LoanParams) -- neither bound is defined.
        return
    _sync_loan_cadence(rule, params)

    ctx = BalanceContext.build(account.user_id)
    if ctx.scenario is None:
        # No baseline scenario: the seam cannot value this loan (and would raise),
        # and there is no trajectory to bound the recurrence END by.  The start
        # bound above is already written -- it needed no scenario.
        return
    figures = balance_at.loan_figures(account, ctx)
    if figures is None:
        # Not a configured loan (no LoanParams) -- nothing to bound.
        return

    new_end_date = recurrence_end_date(
        figures.payoff_date, figures.is_retired, ctx.as_of,
    )
    new_bound = (
        NEVER_ENDS if new_end_date is None else EndsOnDate(on=new_end_date)
    )
    # The idempotence guard compares BOUNDS, not the date column (plan step
    # R7b-3).  Reading ``rule.end_date`` alone is the two-independent-fields
    # shape this step removed, and it is wrong in a way that matters: a rule
    # carrying a COUNT bound has ``end_date IS NULL``, so against a loan that
    # never pays off (``new_end_date is None``) the column test would compare
    # ``None == None`` and return -- leaving a count bound on a loan payment
    # whose stop this module owns.  Frozen dataclasses, so ``==`` is the whole
    # comparison.
    old_bound = end_bound_from_columns(rule.end_date, rule.max_occurrences)
    if old_bound == new_bound:
        return

    # The whole OLD BOUND, not its date half: when this fires because the old
    # bound was a COUNT -- the case the comparison above exists for -- reading
    # ``rule.end_date`` logs ``None`` and loses the fact a count bound was
    # discarded.  Repr'd rather than str'd so the shape is named.
    old_end_date = repr(old_bound)
    # Re-authored like the cadence above, and for the same reason: a rule is
    # written whole through one door, so there is no field-at-a-time write to
    # leave some other column holding a value this edit invalidated.
    # ``end_date`` is not an input to any derived value, so on this path the
    # re-author is ordinarily a no-op on every column but the one named --
    # which is the point of a uniform rule rather than one applied only where
    # it happens to matter.
    #
    # **The whole BOUND is replaced, not the date half of one** (plan step
    # R7b-3), and that is what keeps this line correct now that a rule can
    # also stop after a COUNT of occurrences.  While the bound was two
    # independent columns, ``replace(spec, end_date=payoff)`` wrote a date
    # beside a count the rule already carried and the pair reached the flush
    # as a ``CheckViolation`` on ``ck_recurrence_rules_single_end_bound`` --
    # an ordinary loan edit, 500ing.  An
    # :class:`~app.services.recurrence.EndBound` has three shapes and holds
    # one, so naming the new one discards whatever it replaces and there is no
    # second field for this writer to remember to clear.
    reauthor_rule(
        rule,
        replace(
            recurrence_spec(rule),
            end_bound=new_bound,
        ),
        calendar_for(account.user_id),
    )
    log_event(
        logger, logging.INFO,
        EVT_LOAN_RECURRENCE_END_DATE_UPDATED, BUSINESS,
        "Updated recurrence rule end date to projected payoff",
        account_id=account_id,
        template_id=template.id,
        old_end_date=old_end_date,
        new_end_date=str(new_end_date),
    )
