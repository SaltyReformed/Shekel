"""What a match's two sides come to, and the member that closes the gap.

Plan steps ``bank_import:X-f6d-4`` and ``bank_import:X-gj-3a``; rulings
**R-GD**, **R-FN** and **R-HT(b)**.

**THE DOOR cannot say which member a difference belongs to, and it does not
guess.**  Three rows summing to one deposit, five cents short, is not five
cents this module can attribute: nothing it reads says which of them the bank
varied.  So where nothing says, it RECORDS the missing movement as an ordinary
row with no category and the row becomes a member of the match -- the identity
``Sigma(lines) == Sigma(members)`` holding BY CONSTRUCTION rather than by a
refusal, which is the balance test ceasing to be a fence the door enforces and
becoming an invariant it maintains.

**THE OWNER can say, and since plan step ``bank_import:X-gj-3a`` they may**
(ruling **R-HT(b)**: *where the residue lands -- onto a named row, re-pricing
it, or its own uncategorized row*).  That is not the paragraph above being
weakened: a stated attribution is a DECISION, and this module still derives
none.  What it costs is one refusal the lone-row shape never needed
(:func:`_reject_unrepresentable_landing`) and one thing this module must not
claim -- that a group's gap is *five cents the bank moved that the app has no
row for at all*.  It says that where the owner says nothing; it does not say
it as a fact about the world.  An earlier version of this paragraph did, and
:class:`DifferenceLanding`'s own measured population then asserted the
negation eight hundred lines below it.

**Ruling R-FN is what the row is**, and its mechanism already exists: an
ordinary transaction with ``category_id`` NULL books to the per-owner
``Uncategorized Income`` / ``Uncategorized Expense`` ledger account through
``ledger_account_service.get_or_create_category_ledger_account``, needs no new
ledger-account kind, and is CATEGORIZABLE later through the ordinary edit
door -- which is the mechanism that shrinks the bucket.  It is emphatically NOT
a plug to ``anchor_equity``: that is what R-FN replaced, and plan step
``balance:X-f4`` deletes the machinery it would have used.

**The measured population is finding balance:N-391, the bank-side half of the
retired N-239.**  On a
production clone carrying the developer's own 376 recorded lines, SEVEN payroll
deposits are 2-3 app rows summing ``$0.04``-``$0.06`` under what the employer
actually paid -- ``+$0.35`` across the span -- and before this step every one
was refused outright, so the deposit could not be recorded at all and its
member rows never had their day corrected either.  **Measured by APPLYING all
seven through this door and reading the ledger** (``-0.35`` in Uncategorized
Income), rather than by a query that approximates the matcher: an adversarial
review's own replay found six, missing the deposit whose rows settled five days
after the line posted -- which the day window admits and a same-day heuristic
does not.  Ruling **R-GD**'s "6 of 16" is an earlier, different count, taken
before any of this arc's tiers existed.  this makes the residue VISIBLE as seven
named rows instead of six refusals, which is what ruling R-GD calls the louder
instrument.

**The CAUSE sentence here read "``paycheck_calculator``'s rounding residue is
the cause and plan step ``balance:X-aw`` owns it" and both halves are now
false** (corrected at plan step ``bank_import:X-gj-3a``).  `N-239` called the
calculator's residue-apportionment and the bank-side gap ONE mechanism, and
`N-391` records that as MEASURED FALSE -- `X-aw` shipped and the gap did not
move, because the residue rule's whole range was `{$3,525.96, $3,525.97}`.
What `N-391` says instead is that ``salary_profiles.annual_salary`` divides to
a per-paycheck gross four cents under the stub the employer issues, and its
owner is ``balance:X-av``.  A cause sentence nobody re-checks is exactly the
shape that decays.

**Nothing PROPOSES a residual and nothing may.**  R-GD's own measurement is
why: ``n choose k`` puts 20 leftover lines within 1% of some same-day row set,
a Van Loan transfer inside a Capital One payment at **0.0064%**, tighter than
the six true payroll groups at 0.0019%.  A subset sum hits any target, so a
near miss over a group is coincidence.  The residual exists only where the
OWNER built the group by hand and agreed to the figure.

**The figure the owner agreed to TRAVELS, and the door reconciles it.**  That
is finding **N-336**'s lesson applied to the one number no other guard checks:
``_resolve._reject_moved_since_review`` compares each ROW against the state the
screen described, so a per-row figure cannot drift -- but the SUM is a second
derivation over those rows, and no per-row guard can see it being wrong.  So
the submission carries what was shown
(:attr:`~._submission.MatchSubmission.accepted_difference`) and
:func:`reject_unrecordable` refuses the act when this module's own arithmetic
disagrees.

**The screen gets that figure from HERE**, through
:func:`~._preview.preview_hand_build`, which runs these same reads and
refusals without the writes.  A first version of this step summed the two
sides in the BROWSER and posted the result back; the panel is server-rendered
now, so the figure the owner accepts and the figure this module tests are one
derivation rather than two in two languages -- which also removed a
``ROUND_HALF_EVEN`` quantizer that had been repairing a sub-cent consent into
agreement.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  :func:`mint` MUTATES and does NOT
commit -- the route owns the unit of work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services.cash_ledger import off_statement_sum
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_RESIDUAL_RECORDED,
    log_event,
)
from app.utils.money import MONEY_COLUMN_MAX, round_money

from ._offers import CandidateRow, MatchDays, RowKind, merchant_label
from ._sides import MatchSides
from ._scope import ReviewScope
from ._uncategorized import MovementToRecord, mint_uncategorized

_logger = logging.getLogger(__name__)

#: What a residual row is CALLED, before its merchant.  The label leads with
#: WHAT the row is rather than with who paid it, so the seven of them sort
#: together on any name-ordered list and none of them reads as a budget line
#: somebody planned (developer, 2026-08-23).
_NAME_PREFIX: str = "Statement difference"


def _named_for(lines) -> str:
    """Return what to call the row that closes *lines*' gap.

    The merchant of the LATEST line, which is the line whose day the whole
    match posts on (:attr:`~._offers.MatchDays.posts_on`) -- so the row's name
    and the row's day name the same movement.  Ties broken by id, because a
    name that depends on which of two same-day lines a query happened to return
    first is a name that changes without the record changing.

    :func:`~._offers.merchant_label` is what supplies it, and it is total: the
    bank's own merchant where the source names one, else the whole description.

    **It does NOT cut the name to the column, and that is the refactor rather
    than an omission** (plan step ``bank_import:X-gf-1``).
    ``budget.transactions.name`` has ONE bound and
    :func:`~._uncategorized.mint_uncategorized` is the one writer of that
    column here, so it applies it -- for its other caller as well as this one.
    Cutting here too would be the same rule in two places, which is what this
    package calls a root cause rather than a belt.

    Args:
        lines: The match's bank lines, at least one.

    Returns:
        The composed name, uncut.
    """
    latest = max(lines, key=lambda line: (line.posted_on, line.id))
    named = merchant_label(latest.merchant_name, latest.description)
    return f"{_NAME_PREFIX}: {named}"


#: The last clause of every refusal this module writes.  One spelling, because
#: an owner learns to trust the promise only if it is the same promise: a
#: refused match leaves the database exactly as it was, and the batch quotes
#: these sentences verbatim (**R-FZ(a)**).
_NOTHING = "  Nothing was changed."


def _reject_opposed_movements(sides: MatchSides, rows: int) -> None:
    """Refuse a match whose two sides are not the same DIRECTION of movement.

    **A fact about the PAIR, so it refuses every shape** -- one row or twenty.
    It was unreachable for a group until plan step ``bank_import:X-f6d-4``,
    because the group's own refusal short-circuited it, and that mattered the
    moment a group's difference became something this door WRITES: a `-100.00`
    line against a `+50.00` row would otherwise have recorded `-150.00` of
    spending the owner never claimed.  Nothing legitimate is caught -- a group
    whose rows explain a line sums NEAR it, not opposite to it -- and mixed
    signs WITHIN a group stay legal, because a net deposit really is a gross
    income row less a deduction.

    Args:
        sides: What the two halves come to.
        rows: How many app rows the match names, for the sentence's noun.

    Raises:
        ValidationError: When one side is money in and the other money out.
    """
    if not sides.oppose:
        return
    theirs = "this row is" if rows == 1 else f"the {rows} rows you picked are"
    raise ValidationError(
        f"Your bank shows {sides.bank:+,.2f} and {theirs} "
        f"{sides.app:+,.2f}.  One is money leaving the account and the other "
        f"is money coming in, so they are not the same movement." + _NOTHING
    )


def _reject_unstorable(sides: MatchSides) -> None:
    """Refuse a match whose figures are larger than this app can record.

    **A door that SUMS is not bounded by the columns it sums.**  Every figure
    here descends from a ``Numeric(12, 2)`` column, but a match may name up to
    ``_MAX_MATCH_MEMBERS`` of them on each side, so the total -- and the
    difference derived from it -- can leave the domain that any one of them
    lives in.  Both remedies this module offers then write an unstorable
    figure: :func:`corrected_figure` onto the row a match names, and
    :func:`mint` into a new one.

    **What that costs without this refusal is the whole PASS, not the item.**
    ``psycopg2.errors.NumericValueOutOfRange`` is not a ``ValidationError``, so
    :func:`~._batch._run`'s SAVEPOINT does not catch it: it propagates to the
    route's ``except SQLAlchemyError``, rolls back every item that had landed,
    and reaches the owner as "Something went wrong".  Measured by adversarial
    security review 2026-08-23, which reproduced it from three imported lines
    at the column's ceiling.  It is the shape plan step ``X-f2-c3`` already
    paid for one tier up (finding **N-256**), on a figure a schema bound could
    reach; this one no schema bound can, because the sender does not state it.

    Args:
        sides: What the two halves come to.

    Raises:
        ValidationError: When either side, or their difference, is outside
            :data:`~app.utils.money.MONEY_COLUMN_MAX`.
    """
    for figure in (sides.bank, sides.app, sides.difference):
        if abs(figure) > MONEY_COLUMN_MAX:
            raise ValidationError(
                f"These figures come to {figure:+,.2f}, which is larger than "
                f"this app can record.  Match fewer lines at a time."
                + _NOTHING
            )


def _reject_uncorrectable_row(
    row: CandidateRow, sides: MatchSides,
) -> None:
    """Refuse a match whose difference cannot be written to *row*.

    **This REPLACES a blanket refusal, on ruling R-GD(a).**  Until 2026-08-22
    any match whose two sides did not sum to the same figure was refused and
    the owner sent away to retype the number the statement already carried.
    That refusal was not neutral: a line the screen would not explain is the
    line the merchant rule offers to RECORD, so the cheapest act left was to
    enter the movement a SECOND time -- measured at `$356.61` booked for one
    `$178.29` Geico payment, finding **N-335**.  The bank's figure is the
    record, so where it names ONE row it is simply written to that row.

    Two row shapes are still refused, and each is a genuine indeterminacy
    rather than a tolerance:

    * a row whose FIGURE IS NOT ITS OWN TO STATE, which the row now SAYS
      (:attr:`~._offers.CandidateRow.states_own_figure`) rather than this door
      re-deriving.  A difference on one says a PURCHASE is missing or wrong,
      which is a different repair on a different row.  **The census that
      answers it is TWO published predicates and it moved to the candidate
      constructor at plan step X-f6d-1**, because the PROPOSER has to ask the
      same question and is pure: a near miss offered on such a row is an
      Accept button that can never succeed;
    * a transfer SHADOW.  ``CLAUDE.md`` transfer invariant 3 holds a shadow's
      amount equal to its parent's, so correcting one means correcting the
      TRANSFER, which is not this door.

    **BOTH are asked of every member of a GROUP too** (plan step X-f6d-4), and
    that is a correction rather than a widening.  A first version of this step
    dispatched on ``len(rows) == 1``, so ticking one extra row turned either
    refusal into a residual: measured by adversarial design review 2026-08-23,
    an envelope short `$180.00` of purchases booked `$180.00` to Uncategorized
    instead of naming the missing purchase, on a screen whose own sentence says
    that is what to fix.  The census is asked per ROW because that is what it
    is about; what a GROUP adds is only that nothing says WHICH member the
    remainder belongs to (:func:`_reject_unaccepted_difference`).

    Args:
        row: One app row the match names, already priced.
        sides: What the two halves come to.

    Raises:
        ValidationError: With the figures in the message, and naming which of
            the two it is, so the sentence says what to do next.
    """
    if row.kind is not RowKind.TRANSACTION:
        return
    if row.transfer_id is not None:
        raise ValidationError(
            f"Your bank shows {sides.bank:+,.2f} and these do not match it.  "
            f'"{row.label}" is one half of a transfer, and a transfer\'s two '
            f"halves must stay equal, so change the transfer itself and then "
            f"match it." + _NOTHING
        )
    if not row.states_own_figure:
        raise ValidationError(
            f"These do not add up.  Your bank shows {sides.bank:+,.2f} and "
            f'what you picked comes to {sides.app:+,.2f}.  "{row.label}" is '
            f"worth whatever its purchases are, so it has no figure of its "
            f"own to correct -- the difference is a purchase that is missing "
            f"or wrong, and that is what to fix." + _NOTHING
        )


def _reject_unaccepted_difference(
    sides: MatchSides, rows: int, accepted: "Decimal | None",
) -> None:
    """Refuse a difference the owner did not agree to, or did not see.

    Plan step ``bank_import:X-f6d-4``, rulings **R-GD(a)** and **R-FN**; the
    shape rule below is the developer's ruling of 2026-08-30 (plan step
    ``bank_import:X-gj-1b``).  What the two remedies have in common is that
    both WRITE the gap somewhere the owner did not type it -- onto the single
    row a match names, or into a new uncategorized row -- so both are gated on
    the same consent, and this is the gate that makes "accepts" true rather
    than decorative.

    **ONE rule: a difference this door would WRITE must have been reviewed,
    and the figure reviewed must be the figure derived.**  It reads three ways
    and they are the same sentence:

    * there is a difference and the submission states none.  The shipped
      behaviour, naming both sums and the gap and saying what to do about it;
    * there is a difference and the submission states a different one.  The
      per-row guard (:func:`~._resolve._reject_moved_since_review`) cannot see
      this: it reconciles each ROW against the state the screen described, and
      the difference is a subtraction OVER those rows.  A screen that states
      one total over a door that writes another is finding **N-336** one tier
      up, and so is a screen whose total was true when it was drawn;
    * there is NO difference and the submission states one anyway.  It earns
      its own sentence, because the comparison below would report a figure
      that "now comes to +0.00" when what happened is that the rows stopped
      disagreeing.

      **It is NOT reachable by a screen whose rows moved**, which an earlier
      draft of this said: :func:`~._resolve._reject_moved_since_review` runs
      inside ``resolve_rows``, which is evaluated as an ARGUMENT to
      :func:`~._accept.record_match` and so strictly before this gate -- a row
      whose figure or revision drifted is refused there, by name, with its own
      reload sentence.  What reaches here is a body that states a figure over
      rows that add up: a crafted one, or the race in which the owner ticks
      the consent and then ticks a row and presses Apply before the re-price
      returns.  The arm is kept because this function is total over its
      inputs, not because a stale page produces it.

    **The gate turns on THIS DOOR's derivation and never on the submission's
    shape**, which is the whole of the 2026-08-30 ruling.  It exempted *a
    single line against a single row* until then, on the premise that the near
    tier offers exactly that shape and its card states the figure -- so a
    SHAPE was read as a proxy for *the screen already disclosed this*.  A proxy
    for a fact the wire could carry is a guess, and the Reconcile page's
    tickable proposal rows manufacture the shape: untick one row of a proposed
    group and what arrives is one line against one row, indistinguishable from
    the tier's own offer.  Measured 2026-08-30 on a clone of the developer's
    production data, over the 137 proposals his account offers:

    * the exemption was load-bearing for **2** -- line 338 writing `-$0.15`
      and line 218 writing `-$0.04`, both onto ``Groceries: Walmart``;
    * **3** carried more than one row, and unticking down to one wrote, with
      nothing asked, `$25.51` or `$59.77` on line 253, up to `$437.76` on line
      257, and up to **`$2,572.36`** on line 358 -- the bank's `$2,611.90`
      payroll deposit written onto a `$39.54` ``Phone Allowance`` row.

    An allowlist that existed for four cents permitted twenty-five hundred
    dollars.  The two near-tier proposals state their figures on the wire now
    (:func:`app.jinja_filters.stated_difference`), and no shape is exempt
    from anything.  **Every one of the 137 proposals now emits that
    field**, not only the two that carry a figure: it is what a match
    says it was reviewed against, and a card that emitted it only when
    it was non-zero would make its absence mean two things.

    **A ZERO difference requires no figure, and the reason is the WRITE PATH
    rather than who derived the predicate.**  Both tests are the door's own
    arithmetic over a submission its sender shaped, so *sender-controlled
    versus door-derived* does not separate them -- an earlier draft of this
    paragraph said it did, and an adversarial review measured that false.  What
    separates them is what each predicate is ABOUT.  The old one predicted
    *what the screen had disclosed*, which this door cannot know and so had to
    proxy.  This one predicts *what this door is about to write*, which it can
    check.  At a zero difference the bank leaves the attributed member exactly
    what it already holds -- :class:`DifferenceLanding` subtracts the OTHER
    members from a bank total the two sides agree on -- so
    :func:`corrected_figure` answers ``None`` whether a member is named or
    not, and :func:`~._accept.record_match` resolves no residual period
    because the difference is zero.  The two writers a difference has are both
    provably idle and there is no act to consent to.

    **That clause used to read "``bank_cash_for`` answers ``None`` for a
    group", and plan step ``bank_import:X-gj-3a`` made it false** without
    changing the conclusion: an ATTRIBUTED group does name a member.  Restated
    against what the figure comes to rather than against which arm produced
    it, because this is the stated reason a money control may be skipped and a
    reason nobody re-checks is the one that decays.  **The rule is therefore "no figure is required
    exactly where no figure would be written", and it is not a shape.**

    Requiring one anyway would also have refused every exact match built with
    scripting off, where the panel never re-renders and so can state no figure
    -- a real capability, removed to gate a write that does not happen.

    **WHAT THIS CONSENT BINDS IS THE FIGURE AND NOT THE REMEDY, and the
    developer ruled that bound KNOWN AND ACCEPTED on 2026-09-01** (ruling
    **R-IV**, plan step ``bank_import:X-gj-3a``; found by adversarial code
    review).  **The ruling is a ROW and not only this paragraph**: a docstring
    is where the next author of THIS gate meets a decision, and not where a
    reviewer auditing consent handling looks.  Since that
    step a submission carries two things -- the difference agreed to, and the
    member it lands on -- and this gate compares only the first against
    :attr:`MatchSides.difference`.  So a body CAN pair the consent rendered
    under *record the difference as a row with no category* with a
    ``difference_on`` that re-prices a budget row instead: two acts the
    :class:`~._preview.HandTotals` docstring itself calls *different acts with
    different consequences*, under one agreement.

    **A browser cannot produce it, and that was verified rather than
    assumed**: the attribution select sits inside ``.rec-match-picks``, whose
    change swaps ``closest .rec-match`` with ``outerHTML``, and the consent box
    is inside that element -- so changing where the money lands always
    re-renders the box UNTICKED.  The only party who can craft such a body is
    the account's own owner, on their own rows, and every act it could reach is
    one they may perform through the screen anyway.  **Rejected: widening the
    consent value to carry the remedy beside the figure.**  That is R-IA's rule
    one field wider and it is not wrong -- it was refused because it changes
    the wire format of a field two surfaces submit and one strict reader
    grades, to close a path whose only traveller is the person consenting.
    **What this DID remove, stated because the paragraph above would otherwise
    read as though nothing was lost**: a one-line one-row NEAR MISS can no
    longer be corrected with scripting off.  That is the ruling working rather
    than a casualty of it -- with no script the panel never states the figure,
    so the owner cannot have seen what would be written to their row -- but it
    is a capability the exemption was silently providing, and the two surfaces
    that used to promise it in their JavaScript-off wording no longer do.

    Args:
        sides: What the two halves come to.
        rows: How many app rows the match names, for the sentence.
        accepted: The difference the submission states it was reviewed
            against, or ``None`` where it states none.  **A PRECONDITION and
            never a payload**: what the door writes is
            :attr:`MatchSides.difference`, derived here from the rows the ids
            name, and this is only ever compared against it.

    Raises:
        ValidationError: Naming both sums and the difference.
    """
    difference = sides.difference
    if not difference:
        if accepted is not None and accepted != difference:
            raise ValidationError(
                f"This match was reviewed against a difference of "
                f"{accepted:+,.2f} and now adds up exactly.  Reload the page "
                f"to review it against what your records hold now." + _NOTHING
            )
        return
    if accepted is None:
        remedy = (
            # **THREE remedies since plan step ``bank_import:X-gj-3a``**, and
            # this sentence enumerates them because ruling **R-FZ(a)** makes a
            # refusal the service's own words: a screen that offers a control
            # the refusal does not mention teaches the owner that the app has
            # fewer answers than it has.
            "correct the one you know is wrong, say which of them the "
            "difference belongs to, or tick the box to record it as a row "
            "with no category that you can categorise later"
            if rows != 1
            else "tick the box to write your bank's figure to it"
        )
        subject = (
            f"the {rows} rows you picked come to" if rows != 1
            else "the row you picked is"
        )
        raise ValidationError(
            f"These do not add up.  Your bank shows {sides.bank:+,.2f} and "
            f"{subject} {sides.app:+,.2f}, a difference of "
            f"{difference:+,.2f}.  Either {remedy}." + _NOTHING
        )
    if accepted != difference:
        raise ValidationError(
            f"This match was reviewed against a difference of "
            f"{accepted:+,.2f} and now comes to {difference:+,.2f}.  Reload "
            f"the page to review it against what your records hold now."
            + _NOTHING
        )


def _reject_unrepresentable_landing(
    landing: "DifferenceLanding", sides: MatchSides,
) -> None:
    """Refuse a difference the named member's own row cannot hold.

    Plan step ``bank_import:X-gj-3a`` -- **and it is a refusal the LONE-ROW
    shape never needed, which is why the widening had to bring one.**  A
    transaction stores a non-negative GROSS whose direction comes from its
    ``transaction_type_id`` and never from the figure
    (:func:`corrected_figure`'s own note), so the only cash it can be moved to
    is cash of the direction it already has.  For a match naming ONE row that
    was guaranteed: :func:`_reject_opposed_movements` compares the two sums,
    and with one row ``sides.app`` IS that row's cash, so the bank's figure
    could never oppose it.

    **A group breaks the guarantee, and this module's own docstring says
    why**: *mixed signs WITHIN a group stay legal, because a net deposit
    really is a gross income row less a deduction*.  So the select offers the
    negative member too, and the bank can leave it a figure of the other sign.

    Worked example, which is the shape it exists for.  A `+$2,060.00` deposit
    against a `+$2,050.00` salary row and a `-$50.00` deduction row comes to
    `+$2,000.00`, `$60.00` short.  Attribute that to the DEDUCTION and the bank
    says that row is worth `+$10.00` -- and an expense row cannot be worth
    `+$10.00`.  Without this refusal the door writes `$10.00` as a magnitude,
    the row settles at `-$10.00`, and
    :func:`~._accept._reject_drifted_under_the_act` catches the arithmetic and
    reports *"Applying this match moved one of its own rows"* -- which is
    false, nothing drifted, and it sends the owner to match the rows one at a
    time when what they did was point at the wrong member.  **A guard that
    catches the right state under the wrong name is not the guard**, which is
    the shape finding **N-332** records one column over.

    **A PURCHASE is asked only about ZERO**, because it stores a SIGNED amount
    since ruling **R-II** -- a refund is a negative purchase -- so either
    direction is representable and only *worth nothing* is not
    (``ck_transaction_entries_positive_amount``, now ``amount <> 0``).

    Args:
        landing: Where this match's difference goes.
        sides: What the two halves come to, for the figures in the sentence.

    Raises:
        ValidationError: When the named member cannot hold the figure the bank
            leaves it.
    """
    row = landing.on_row
    if row is None or landing.bank_cash is None:
        return
    if landing.bank_cash == 0:
        raise ValidationError(
            f"Your bank shows {sides.bank:+,.2f}, and the other rows you "
            f"ticked already come to that -- so putting the difference on "
            f'"{row.label}" would leave it worth nothing, which is not a row '
            f"this app can hold.  Put the difference somewhere else, or untick "
            f'"{row.label}".' + _NOTHING
        )
    if row.kind is RowKind.PURCHASE:
        return
    if (landing.bank_cash < 0) != (row.cash_amount < 0):
        raise ValidationError(
            f'Your bank leaves "{row.label}" at {landing.bank_cash:+,.2f}, '
            f"and this row records money moving the other way -- so it cannot "
            f"carry this difference.  Put it on a row that moves the same way, "
            f"or record it as a row with no category." + _NOTHING
        )


def reject_unrecordable(
    rows: "list[CandidateRow]",
    sides: MatchSides,
    accepted: "Decimal | None",
    landing: "DifferenceLanding | None" = None,
) -> None:
    """Refuse a match whose difference this door cannot honestly record.

    **The partition is by WHAT the refusal is about**, and stating it that way
    is what made two of them reach a group (plan step ``bank_import:X-f6d-4``):

    * :func:`_reject_opposed_movements` and :func:`_reject_unstorable` are
      about the PAIR of sums;
    * :func:`_reject_uncorrectable_row` is about each named ROW, and is asked
      of every member rather than only of a lone one;
    * :func:`_reject_unaccepted_difference` is about the OWNER's consent to
      the gap, whichever way it would be written;
    * :func:`_reject_unrepresentable_landing` is about whether the member the
      gap is written TO can hold it (plan step ``bank_import:X-gj-3a``).

    **WHETHER the attribution names a member of this match is NOT asked here**,
    and a first version of that step did ask it.  The attribution is a pointer
    into the SUBMITTED rows, so that refusal belongs where the submission's own
    shape is graded -- :func:`~._resolve.resolve_rows`, beside *this match
    names the same row more than once* -- and it is stronger there: that door
    compares the whole reviewed value, so an attribution disagreeing with its
    own row about the figure is refused too, where a check over these
    already-resolved rows could only compare the subject.  Asking in both
    places would have left this one unable to fire on any path, which is a
    control that measures nothing.

    **FIVE refusals live in this module**, and they are the ones about the two
    sides DISAGREEING.  :mod:`._accept` states its own and :mod:`._resolve`
    states its own; the counts are written down because this arc has shipped a
    taxonomy that did not add up before, and a sixth added here is what has to
    change this sentence.

    Args:
        rows: The submitted app rows, already priced.
        sides: What the two halves come to, derived once for the whole act.
        accepted: The difference the owner agreed to, or ``None``.
        landing: Where the difference would go
            (:class:`DifferenceLanding`), or ``None`` where the caller has not
            derived one.  **Defaulted, and it is the one argument here that
            is**: the doors that take a SUBMISSION derive the landing before
            they refuse, and the two that BUILD their own match name a single
            row each, where ruling **R-GD**'s determinacy answers the question
            and there is nothing about the landing to refuse.

    Raises:
        ValidationError: From whichever arm fires, with the figures in it.
    """
    _reject_opposed_movements(sides, len(rows))
    _reject_unstorable(sides)
    if landing is not None:
        _reject_unrepresentable_landing(landing, sides)
    if sides.difference:
        for row in rows:
            _reject_uncorrectable_row(row, sides)
    # **Asked of every match, in every shape.**  The one shape this used to
    # pass unasked -- a single line against a single row -- was a proxy for
    # *the screen already disclosed this figure*, and the Reconcile page's
    # tickable proposal rows manufacture it; see that function for what the
    # proxy was measured to be worth.
    _reject_unaccepted_difference(sides, len(rows), accepted)


@dataclass(frozen=True)
class DifferenceLanding:
    """Where a match's difference goes, decided ONCE for the whole act.

    **ONE rule, and it is the whole of ruling R-GD(a)'s determinacy:** the
    difference this door would write goes to the member the match ATTRIBUTES it
    to, and where it attributes it to none it becomes **R-FN**'s ordinary
    accepted row (:func:`mint`).  A match attributes it

    * to its SOLE row where it names one -- nothing has to be apportioned, so
      the bank's figure is simply an assertion about that row;
    * to the member the OWNER NAMED where it names several (plan step
      ``bank_import:X-gj-3a``);
    * to NONE where it names several and the owner named no member, which is
      every match this screen submitted before that step.

    **The middle arm is the step's whole change, and it does not weaken the
    argument above it.**  This module's opening paragraph -- *three rows
    summing to one deposit, five cents short, is not five cents of error in one
    of them* -- is about what the DOOR can DERIVE, and it stands unamended:
    nothing here apportions and nothing here guesses.  What is new is that the
    owner may SAY which member the bank varied, and a stated attribution is a
    decision rather than a derivation.  The developer ruled the control on
    2026-09-01 and ruled its DEFAULT with it (**R-IU**): the panel offers NO
    pre-selected member.

    **The ruling that settles the default is R-HX, not R-FZ(b)**, and the
    difference is the one **R-HS** carves out in its own words: R-FZ(b) banned
    the ARBITRARY default and *never meant a suggestion the app can justify*,
    so citing it for a DERIVED pick would be citing the half that was carved
    away.  R-HX is on point -- *an unmatched inflow is not pre-filled until a
    rule can justify the destination*, and *being the ONLY act is not what
    R-HS means by justified*.  The app is not without a CANDIDATE here:
    ``salary_profiles.template_id`` names the member whose figure is computed
    (:meth:`~app.services.income_service.SalaryPricing._net_by_period`
    returns the paycheck's ``net_pay`` for it and ``template.default_amount``
    for every other) so *which member rounds* is a stored fact.  What it lacks
    is a JUSTIFICATION: finding `balance:N-391` reads *NOT yet attributed*,
    and the thing that would supply one is the standing rule
    ``bank_import:X-gj-3b`` ships.

    **It is a GENERALISATION of ``bank_cash_for`` rather than a second arm
    beside it, and the algebra is why that matters.**  That function answered
    :attr:`~._sides.MatchSides.bank` for a lone row and ``None`` otherwise.
    The figure here is *what the bank moved, less what the OTHER members come
    to* -- and for a lone row there are no others, so the subtrahend is zero
    and the answer IS the bank total, identically.  A version that had kept
    the old function and added an ``elif`` for the named member would have
    been two spellings of one money rule, which is this arc's own root cause 1.

    **It is stated as a subtraction from the BANK and not as an addition to
    the ROW, and the difference is a rounding one.**  ``row.cash_amount +
    sides.difference`` is the same value in exact arithmetic and is what a
    first version of this class computed -- but ``sides.app`` is ROUNDED
    (:class:`~._sides.MatchSides`), so on a row whose cash carried sub-cent
    places that spelling would return the bank's figure plus that row's own
    rounding error, where this one returns the bank's figure exactly.  Every
    figure the app can produce today descends from ``Numeric(12, 2)``, so the
    two agree on all of them; ``MatchSides``' own docstring is what says a
    derived price with more places is expressible, and a money rule that is
    right only until then is not right.

    **The two remedies stay EXCLUSIVE by construction rather than by care.**
    :attr:`bank_cash` is a figure exactly where a member absorbs the gap and
    ``None`` exactly where none does, so correcting a row and minting a member
    for the same difference is unrepresentable -- the property
    :func:`~._accept.record_match` used to get from ``bank_cash_for`` and which
    a first version of that step lost by gating the mint on consent alone.

    **The measured population is finding balance:N-391.**  On a production
    clone carrying the developer's own 376 recorded lines, seven payroll
    deposits are 2-3 app rows summing ``$0.04``-``$0.06`` under what the
    employer paid, ``+$0.35`` across the span.  Before this step the only thing
    this door could do with that gap was mint seven uncategorized rows.

    **WHICH member it belongs to is NOT established, and this class does not
    claim it is.**  An earlier draft of this paragraph said every cent of it
    belongs to the salary row; `N-391`'s own last sentence says the opposite
    -- *NOT yet attributed: a `$0.04` error in any of the 12 hand-entered
    deductions reproduces the same net, and the gross is n=1*.  What was
    measured on 2026-09-01 is narrower and is about the GROSS: the app derives
    a per-paycheck gross by dividing ``salary_profiles.annual_salary``
    (`$91,675.00` / 26 = `$3,525.96`) where the owner's stub states
    `$3,526.00`, and re-running all seven paychecks through
    :func:`~app.services.paycheck_calculator.calculate_paycheck` at the stub's
    figure collapses the span from ``+$0.35`` to ``+$0.07``.  That is a fact
    about the derivation and not a per-member attribution: the bank shows ONE
    deposit, so no per-member bank figure exists to compare an allowance
    against, and *the allowances match exactly* is ``bank less allowances =
    salary`` restated.  The gross half is ``balance:X-av``'s, which
    `N-391` already names.

    Attributes:
        on_row: The member the difference is written to, or ``None`` where it
            has none and :func:`mint` is what closes the gap.  It is one of
            the very rows handed to :meth:`of`, so the caller cannot be given
            a row the act does not name.
        bank_cash: What the bank states :attr:`on_row` is worth, signed on
            :attr:`~._offers.CandidateRow.cash_amount`'s own convention, or
            ``None`` beside a ``None`` row.  **The CASH figure and not the
            figure to store**: :func:`corrected_figure` is what inverts it into
            what the row's own column holds, which differs on a purchase and on
            a transaction carrying entries.
    """

    on_row: "CandidateRow | None"
    bank_cash: "Decimal | None"

    @classmethod
    def of(
        cls,
        sides: MatchSides,
        rows: "list[CandidateRow]",
        attributed: "tuple[RowKind, int] | None",
    ) -> "DifferenceLanding":
        """Return where this match's difference lands.

        **Total over every submission this door accepts**, including the ones
        with no difference at all: at zero what the bank left for the named
        row IS what that row already holds, so :func:`corrected_figure`
        answers ``None`` and an agreeing match writes nothing whichever arm it
        takes -- which is what the lone-row path did before this step and is
        why no arm here tests for zero.

        Args:
            sides: What the two halves come to, derived once for the whole act.
            rows: The match's app rows, already priced.  A match with none is
                refused before this runs
                (:func:`~._accept._reject_empty_side`).
            attributed: The ``(kind, row_id)`` of the member the owner named,
                or ``None``.  Held to be one of *rows* by
                :func:`~._resolve.resolve_rows`, so a body naming a row this
                match does not carry is refused by name rather than falling
                through to the mint -- which would let the SENDER choose the
                remedy.  :func:`_named_member` raises rather than answering
                ``None`` if that guard is ever bypassed.

        Returns:
            The :class:`DifferenceLanding`.
        """
        row = _named_member(rows, attributed)
        if row is None:
            return cls(on_row=None, bank_cash=None)
        others = sum(
            (
                other.cash_amount for other in rows
                if (other.kind, other.row_id) != (row.kind, row.row_id)
            ),
            Decimal("0.00"),
        )
        return cls(on_row=row, bank_cash=round_money(sides.bank - others))

    @staticmethod
    def offers_a_choice(rows: "list[CandidateRow]") -> bool:
        """Return whether this match has a member for the owner to NAME.

        **ONE statement of ruling R-GD's determinacy, read by both sides**
        (plan step ``bank_import:X-gj-3a``, second pass).  The door's
        :func:`_named_member` asks *is this already answered* to decide whether
        to consult the attribution, and the panel asks the same question to
        decide whether to render the control -- and a first version wrote it
        out twice, once here and once as ``len(rows) > 1`` in
        :func:`~._preview.preview_hand_build`.  Nothing in the tree fails when
        two spellings of one predicate diverge: widen the panel's and it offers
        a control the door ignores, widen the door's and it honours an
        attribution no panel could have rendered.

        Args:
            rows: The match's app rows, already priced.

        Returns:
            ``False`` where the match names one row -- there is nothing to
            apportion, so the answer is the row and no control is drawn --
            and ``True`` where it names several.
        """
        return len(rows) > 1

    @property
    def mints_a_row(self) -> bool:
        """Return whether a difference here becomes an ordinary accepted row.

        Returns:
            ``True`` where no member absorbs the difference, so **R-FN**'s row
            is what makes the group add up.  It says nothing about whether
            there IS a difference: the caller pairs it with
            :attr:`~._sides.MatchSides.difference`, exactly as
            :func:`~._accept.record_match` paired the old ``bank_cash is
            None``.
        """
        return self.on_row is None

    def figure_for(self, row: CandidateRow) -> "Decimal | None":
        """Return what *row* should book, or ``None`` where it does not move.

        **Asked of every member so the answer is one rule rather than a loop
        with a condition in it.**  A member that is not the attributed one is
        not re-priced, and the attributed one is re-priced only where the
        bank's figure differs from what it already holds -- both of which
        :func:`corrected_figure` already answers, given a ``None`` cash figure
        for the first.

        Args:
            row: One member of the match.

        Returns:
            The figure to submit to that row's settle verb, or ``None``.
        """
        if self.on_row is None:
            return None
        if (row.kind, row.row_id) != (self.on_row.kind, self.on_row.row_id):
            return None
        return corrected_figure(row, self.bank_cash)


def _named_member(
    rows: "list[CandidateRow]",
    attributed: "tuple[RowKind, int] | None",
) -> "CandidateRow | None":
    """Return the member a match attributes its difference to, or ``None``.

    **The lone-row arm is a DETERMINACY argument and not a default**, and the
    ruling that states it is **R-GD**'s fourth GROUP amendment, clause (ii) --
    *one ROW is determinate however many LINES explain it, so
    ``bank_cash_for``'s test is on the row rather than the lines*.  (Not
    R-GD(a), which is the different proposition that a match RECORDS the
    variance; the deleted ``bank_cash_for`` made the same conflation and it is
    corrected here rather than inherited.)  Where a match names one row the
    bank's figure is an assertion about that row and there is nothing to
    apportion, so the screen offers no choice and none is submitted.  The test
    was ``len(lines) != 1 or len(rows) != 1`` until plan step
    ``bank_import:X-f6d-4``, and the lines half has been gone since.

    Args:
        rows: The match's app rows, already priced.
        attributed: The ``(kind, row_id)`` the owner named, or ``None``.

    Returns:
        The member, or ``None`` where the difference has none.

    Raises:
        ValueError: When *attributed* names a row this match does not carry.
            **Unconstructible from the wire rather than defensive, and stated
            rather than left to fall out of a lookup's default**:
            :func:`~._resolve.resolve_rows` refuses a submission whose
            attribution is not one of its own rows, and refuses one whose rows
            do not all resolve -- so on every path a submission takes, an
            attributed subject IS a resolved row.  What this arm guards is the
            other caller of :func:`~._accept.record_match`, which builds its
            :class:`~._accept.MatchContent` in code.  Written as a raise
            because the alternative spelling, ``next(..., None)``, would fall
            through to the MINT: the remedy would have been chosen by naming a
            row that is not there, which is the shape ruling **R-IA** measured
            at `$2,572.36` one field over.
    """
    if not DifferenceLanding.offers_a_choice(rows):
        return rows[0]
    if attributed is None:
        return None
    for row in rows:
        if (row.kind, row.row_id) == attributed:
            return row
    raise ValueError(
        "A match's difference names a row the match does not carry, which "
        "resolve_rows refuses for every submission."
    )


def corrected_figure(
    row: CandidateRow, bank_cash: "Decimal | None",
) -> "Decimal | None":
    """Return the figure *row* should book to move its cash onto the bank's.

    **The bank constrains the CASH LEG, and the stored figure is GROSS**, so
    the two are not the same number on a row carrying entries.  Inverting
    :func:`~app.services.cash_ledger.cash_leg_of` -- *gross, less what never
    reaches this account, signed by the transaction TYPE* -- gives
    ``|bank| + off_statement_sum``, which reuses that rule rather than
    restating it.  The two coincide on every row this arm reaches today (all 8
    of the developer's transaction near misses carry no entries), and the
    inversion is written anyway because a row that HAS entries is expressible
    and would otherwise book its credit purchases twice.

    **A PURCHASE stores its figure directly** -- its cash is the negated stored
    amount (:func:`~._candidates.purchase_candidate`) -- so its correction is
    that negation INVERTED, ``-bank_cash``.

    **It was ``abs(bank_cash)`` until plan step ``bank_import:X-gj-2b``, and
    the two agree only for an OUTFLOW.**  While every purchase was positive its
    cash was negative, so the magnitude and the negation were the same number
    and the simpler spelling was true.  Ruling **R-II** made a merchant refund a
    NEGATIVE purchase, whose cash is POSITIVE -- and there ``abs()`` returns
    ``+X`` where the stored figure must be ``-X``, flipping a refund into a
    charge of the same size.  The negation is the exact inverse of
    ``purchase_candidate``'s own ``cash_amount=-Decimal(str(entry.amount))``,
    and it reduces to the old expression for every outflow, so no
    already-correct case moves.

    Args:
        row: The member the bank's figure is about.
        bank_cash: What the bank states, signed, or ``None`` for a group.

    Returns:
        The figure to submit, or ``None`` when nothing should be submitted --
        a group, an unchanged figure, or a row whose amount is DERIVED from its
        own purchases and which :func:`_reject_uncorrectable_row` has already
        refused.
    """
    if bank_cash is None or bank_cash == row.cash_amount:
        return None
    if row.kind is RowKind.PURCHASE:
        return round_money(-bank_cash)
    # **The TRANSACTION arm keeps ``abs()`` and that is not an oversight.**  A
    # transaction stores a GROSS, non-negative figure (``estimated_amount >= 0``,
    # ``settled_amount IS NULL OR >= 0``) whose direction comes from the
    # transaction TYPE rather than from the figure, so the magnitude really is
    # what it should book.  Only a PURCHASE stores a signed amount.
    txn = db.session.get(Transaction, row.row_id)
    return round_money(abs(bank_cash) + off_statement_sum(txn))


def mint(
    difference: Decimal,
    pay_period_id: int,
    scope: ReviewScope,
    lines,
    days: MatchDays,
) -> CandidateRow:
    """Record the movement a group's rows do not account for.

    Ruling **R-FN**'s ordinary accepted row, returned as the match member that
    makes the group add up.

    **The ROW is minted by :func:`~._uncategorized.mint_uncategorized` and not
    here** (plan step ``bank_import:X-gf-1``, ruling **bank_import:R-GW**).  Every clause of
    what that row IS -- no category, born Projected and settled through the
    app's own verb, the bank's posting day on the ``observed`` basis, owning
    its amount, baseline scenario -- is decided by the fact that a bank
    statement is why it exists, which is equally true of the line ruling
    **bank_import:R-GW** records as INCOME.  Two spellings of one money rule is this
    package's own root cause, so there is one writer and this function supplies
    the two things that are genuinely its own: what the row is CALLED, and the
    event that says a MATCH's difference is what produced it.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        difference: The DOOR's own figure
            (:attr:`MatchSides.difference`), already reconciled against the one
            the owner accepted by :func:`reject_unrecordable`.  Passed rather
            than re-derived here, because the value that was tested and the
            value that is written must be one.
        pay_period_id: The paycheck this movement belongs to, resolved by the
            caller through :meth:`~._scope.ReviewScope.period_holding` for the
            reason :func:`~._uncategorized.mint_uncategorized` states.
        scope: The pass, which is the ONE statement of whose account and whose
            baseline scenario this row belongs to.
        lines: The match's bank lines, for the row's name.
        days: The days the match writes, derived once for the whole act.

    Returns:
        The new row as a :class:`~._offers.CandidateRow`, so the caller can
        record it as a member exactly like every other one.

    Raises:
        PostingError: From the ledger reconcile, on a broken invariant.
        RuntimeError: When the candidate constructor refuses the row just
            created, which is the shared writer's contract.
    """
    candidate = mint_uncategorized(
        MovementToRecord(
            name=_named_for(lines),
            signed_amount=difference,
            pay_period_id=pay_period_id,
            posts_on=days.posts_on,
        ),
        scope,
    )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_RESIDUAL_RECORDED, BUSINESS,
        "A matched group's difference was recorded as an uncategorized row.",
        user_id=scope.owner_id,
        account_id=scope.account_id,
        transaction_id=candidate.row_id,
        pay_period_id=pay_period_id,
        amount=str(difference),
        posts_on=days.posts_on.isoformat(),
        line_count=len(lines),
    )
    return candidate
