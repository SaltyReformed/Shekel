"""What a match's two sides come to, and the member that closes the gap.

Plan step ``bank_import:X-f6d-4``, rulings **R-GD(a)** and **R-FN**.

**A GROUP cannot say WHICH member a difference belongs to -- and it does not
have to.**  Three rows summing to one deposit, five cents short, is not five
cents of error in one of them: it is five cents the bank moved that the app has
no row for at all.  So the door does not apportion the difference and does not
absorb it; it RECORDS the missing movement as an ordinary row with no category,
and the row becomes a member of the match.  The identity
``Sigma(lines) == Sigma(members)`` then holds BY CONSTRUCTION rather than by a
refusal, which is the whole shape of this step: the balance test stops being a
fence the door enforces and becomes an invariant it maintains.

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
before any of this arc's tiers existed.  ``paycheck_calculator``'s rounding
residue is the cause and plan step ``balance:X-aw`` owns it; this makes the
residue VISIBLE as seven named rows instead of six refusals, which is what
ruling R-GD calls the louder instrument.

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
    check: at a zero difference :func:`corrected_figure` answers ``None`` for a
    row already at the bank's figure, :func:`bank_cash_for` answers ``None``
    for a group, and :func:`~._accept.accept_match` resolves no residual period
    -- so the two writers a difference has are both provably idle and there is
    no act to consent to.  **The rule is therefore "no figure is required
    exactly where no figure would be written", and it is not a shape.**

    Requiring one anyway would also have refused every exact match built with
    scripting off, where the panel never re-renders and so can state no figure
    -- a real capability, removed to gate a write that does not happen.

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
            "correct the one you know is wrong, or tick the box to record "
            "the difference as a row with no category that you can "
            "categorise later"
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


def reject_unrecordable(
    rows: "list[CandidateRow]",
    sides: MatchSides,
    accepted: "Decimal | None",
) -> None:
    """Refuse a match whose difference this door cannot honestly record.

    **The partition is by WHAT the refusal is about**, and stating it that way
    is what made two of them reach a group (plan step ``bank_import:X-f6d-4``):

    * :func:`_reject_opposed_movements` and :func:`_reject_unstorable` are
      about the PAIR of sums;
    * :func:`_reject_uncorrectable_row` is about each named ROW, and is asked
      of every member rather than only of a lone one;
    * :func:`_reject_unaccepted_difference` is about the OWNER's consent to
      the gap, whichever way it would be written.

    **FOUR refusals live in this module**, and they are the ones about the two
    sides DISAGREEING.  :mod:`._accept` states its own and :mod:`._resolve`
    states its own; the counts are written down because this arc has shipped a
    taxonomy that did not add up before, and a fifth added here is what has to
    change this sentence.

    Args:
        rows: The submitted app rows, already priced.
        sides: What the two halves come to, derived once for the whole act.
        accepted: The difference the owner agreed to, or ``None``.

    Raises:
        ValidationError: From whichever arm fires, with the figures in it.
    """
    _reject_opposed_movements(sides, len(rows))
    _reject_unstorable(sides)
    if sides.difference:
        for row in rows:
            _reject_uncorrectable_row(row, sides)
    # **Asked of every match, in every shape.**  The one shape this used to
    # pass unasked -- a single line against a single row -- was a proxy for
    # *the screen already disclosed this figure*, and the Reconcile page's
    # tickable proposal rows manufacture it; see that function for what the
    # proxy was measured to be worth.
    _reject_unaccepted_difference(sides, len(rows), accepted)


def bank_cash_for(
    sides: MatchSides, rows: "list[CandidateRow]",
) -> "Decimal | None":
    """Return the cash the bank states for the ONE row this match names.

    **Defined only where the bank's figure names a single row**, which is the
    whole of ruling **R-GD(a)**'s determinacy: a match naming one row is an
    assertion about that row and nothing has to be apportioned.  A GROUP is a
    different question -- three rows summing to one deposit, with nothing
    saying WHICH is the six cents wrong -- so this answers ``None`` there and
    the difference becomes **R-FN**'s ordinary accepted row (:func:`mint`),
    never a figure this door invents for a member.

    **The test is on the ROWS alone, and it was ``len(lines) != 1 or
    len(rows) != 1`` until plan step ``bank_import:X-f6d-4``** (developer,
    2026-08-23).  That extra clause contradicted this docstring's own
    reasoning: two lines against one row apportion nothing either, because
    their SUM is what the bank says that row is worth.

    **What the widening needed, and two reviews independently found missing, is
    the CONSENT.**  Nothing bounds which lines an owner may tick, so without a
    figure gate a mis-tick could rewrite a `$45.00` fee to `$1,950.00` in one
    press with no undo.  :func:`_reject_unaccepted_difference` requires the
    reviewed figure for EVERY shape as of plan step ``bank_import:X-gj-1b``;
    it exempted the single-line one-row match until then, and that exemption
    is what the Reconcile page's tickable proposal rows learned to
    manufacture.  A first version of this step justified the widening by
    *"0 such pairings exist on the developer's own data"*, which is vacuous:
    no tier emits a multi-line proposal at all, so the population that reaches
    this code is whatever the owner ticks.

    **It TAKES the bank total rather than summing one**, because the refusals
    that let this run summed the same lines to test them: two derivations of
    one money figure on the two sides of a gate is what :class:`MatchSides`
    exists to remove.

    Args:
        sides: What the two halves come to, derived once for the whole act.
        rows: The app rows the match names.

    Returns:
        The lines' signed total when the match names exactly one row, else
        ``None``.
    """
    if len(rows) != 1:
        return None
    return sides.bank


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
