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

**The measured population is finding N-239, seen from the bank's side.**  On a
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

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import transaction_service
from app.services.cash_ledger import off_statement_sum
from app.services.scenario_resolver import require_baseline_scenario
from app.services.settle_day import SettleDay
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_RESIDUAL_RECORDED,
    log_event,
)
from app.utils.money import MONEY_COLUMN_MAX, round_money

from ._candidates import transaction_candidate
from ._offers import CandidateRow, MatchDays, RowKind, merchant_label
from ._sides import MatchSides
from ._scope import ReviewScope

_logger = logging.getLogger(__name__)

#: What a residual row is CALLED, before its merchant.  The label leads with
#: WHAT the row is rather than with who paid it, so the seven of them sort
#: together on any name-ordered list and none of them reads as a budget line
#: somebody planned (developer, 2026-08-23).
_NAME_PREFIX: str = "Statement difference"

#: How long ``budget.transactions.name`` is.  The merchant is the bank's own
#: string and this door writes it directly, so the composed name is cut to fit
#: rather than left to the column to refuse -- the same bound
#: ``_create.create_purchase_from_line`` applies to a purchase's description.
_NAME_LIMIT: int = 200


def _named_for(lines) -> str:
    """Return what to call the row that closes *lines*' gap.

    The merchant of the LATEST line, which is the line whose day the whole
    match posts on (:attr:`~._offers.MatchDays.posts_on`) -- so the row's name
    and the row's day name the same movement.  Ties broken by id, because a
    name that depends on which of two same-day lines a query happened to return
    first is a name that changes without the record changing.

    :func:`~._offers.merchant_label` is what supplies it, and it is total: the
    bank's own merchant where the source names one, else the whole description.
    ``budget.transactions.name`` is NOT NULL and this door writes it directly.

    Args:
        lines: The match's bank lines, at least one.

    Returns:
        The composed name, cut to :data:`_NAME_LIMIT`.
    """
    latest = max(lines, key=lambda line: (line.posted_on, line.id))
    return f"{_NAME_PREFIX}: {merchant_label(latest)}"[:_NAME_LIMIT]


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
    line the merchant policy offers to RECORD, so the cheapest act left was to
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
    sides: MatchSides, rows: int, accepted: "Decimal | None", exempt: bool,
) -> None:
    """Refuse a difference the owner did not agree to, or did not see.

    Plan step ``bank_import:X-f6d-4``, rulings **R-GD(a)** and **R-FN**.  What
    the two remedies have in common is that both WRITE the gap somewhere the
    owner did not type it -- onto the single row a match names, or into a new
    uncategorized row -- so both are gated on the same consent, and this is
    the gate that makes "accepts" true rather than decorative.

    **Three refusals, and they are one rule read three ways:**

    * the sides differ and nothing was accepted.  The shipped behaviour, with
      its own sentence naming both sums and the gap, now saying what to do
      about it rather than sending the owner away to edit a row;
    * something was accepted and it is not the figure this door derives.  The
      per-row guard (``_resolve._reject_moved_since_review``) cannot see this:
      it reconciles each ROW against the state the screen described, and the
      SUM is a second derivation over those rows.  A screen that states one
      total over a door that writes another is finding **N-336** one tier up;
    * something was accepted where the sides AGREE.  ``0.00`` is a figure like
      any other, so this arm is stated rather than left to the comparison
      above -- a first version claimed the equality caught it and was measured
      FALSE by two independent reviews on the same day, because
      ``Decimal("0.00") == Decimal("0.00")``.

    **A SINGLE-LINE match against ONE row is exempt, and only that shape.**
    The near tier offers exactly it -- one line, one row, a figure the proposal
    card states -- and its tick carries no difference field, so requiring one
    would kill the tier that plan step ``X-f6d-1`` shipped.  Every other shape
    is the owner's own assertion built on a checkbox list, including the
    MULTI-LINE match against one row that ``bank_cash_for`` now corrects: two
    reviews independently found that, unexempted, an owner could tick five
    unrelated lines against one row and rewrite its amount to their sum with no
    figure gate at all.

    Args:
        sides: What the two halves come to.
        rows: How many app rows the match names, for the sentence.
        accepted: The signed difference the owner agreed to, or ``None``.
        exempt: Whether this shape may proceed without one -- the near tier's
            single line against a single row.  **It exempts the REQUIREMENT
            and never the reconciliation**: a figure that arrives anyway is
            still checked, because a screen that offered to record a
            difference here described an act this door will not perform, and
            silently doing the other thing is finding **N-336**'s class.  A
            first version returned early on this shape and swallowed it.

    Raises:
        ValidationError: Naming both sums and the difference.
    """
    difference = sides.difference
    if accepted is None:
        if not difference or exempt:
            return
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
    if not difference:
        raise ValidationError(
            f"These add up, so there is no difference of {accepted:+,.2f} to "
            f"record.  Reload the page and review it again." + _NOTHING
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
    _reject_unaccepted_difference(
        sides, len(rows), accepted,
        # The near tier's own shape, and the only one whose tick may carry no
        # figure: one line, one row, and the proposal card states the
        # correction it would write.
        exempt=len(rows) == 1 and sides.line_count == 1,
    )


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
    press with no undo.  :func:`_reject_unaccepted_difference` now requires the
    accepted figure for every shape except the single-line one-row match the
    near tier proposes.  A first version of this step justified the widening by
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
    the bare magnitude.

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
        return round_money(abs(bank_cash))
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

    Ruling **R-FN**'s ordinary accepted row, built and settled here and
    returned as the match member that makes the group add up.

    **It is born Projected and settled through the app's own verb**, never
    assigned a settled status directly: ``status_seam.apply_status_change`` is
    the ONE door into the settled band and a row may not be born in one, which
    is the discipline :func:`~._create._create_envelope` states and plan step
    ``balance:X-aj2`` makes structural.  ``transaction_service.apply_requested_status``
    is the same verb :func:`~._accept._apply_day` moves every other member
    with, so this opens no fourth settle door -- and it is what reconciles the
    ledger, which is how the row reaches the Uncategorized account at all.

    **It carries NO category on purpose** (**R-FN**), and that is what routes
    it: ``posting_service._settled_target`` books a NULL-category row's counter
    leg to the per-(owner, class) Uncategorized fallback.  Measured 2026-08-23
    on a production clone: 0 of 1,013 transactions carry a NULL category and no
    fallback ledger account exists yet, so this door is the first writer of
    both -- which is why the end-to-end proof is a test over the LEDGER and not
    over the row.

    **It OWNS its amount** (``amount_source_id`` NULL beside a stored figure,
    which ``ck_transactions_amount_ownership`` pairs): it names no template, no
    transfer and no card spend, so there is no derivation for it to read.  The
    stored figure is the MAGNITUDE and the direction is the transaction TYPE,
    which is what ``ck_transactions_estimated_amount`` (``>= 0``) requires.

    **It is the BASELINE scenario, unconditionally**, for
    :func:`~._create._create_envelope`'s reason: a what-if scenario is a
    hypothesis about money that has not moved, and this row records money the
    bank has already moved.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        difference: The DOOR's own figure
            (:attr:`MatchSides.difference`), already reconciled against the
            one the owner accepted by
            :func:`reject_unrecordable`.  Passed rather than
            re-derived here, because the value that was tested and the value
            that is written must be one.
        pay_period_id: The paycheck this movement belongs to, resolved by the
            caller through :meth:`~._scope.ReviewScope.period_holding`.
            **Resolved THERE rather than here, and that is a correctness
            change rather than tidying**: that lookup can refuse, and by the
            time this runs every member row has already been settled -- so a
            refusal raised here would leave written work behind and lean on
            the batch's SAVEPOINT, which :mod:`._accept` explicitly declines
            to depend on.  Found by adversarial financial review 2026-08-23,
            which also measured the reachability: a line posted past the last
            SAVED pay period is not split off by the review screen's own
            bounds, so the refusal is live rather than theoretical.
        scope: The pass, which is the ONE statement of whose account and whose
            baseline scenario this row belongs to.
        lines: The match's bank lines, for the row's name.
        days: The days the match writes, derived once for the whole act.

    Returns:
        The new row as a :class:`~._offers.CandidateRow`, so the caller can
        record it as a member exactly like every other one.

    Raises:
        PostingError: From the ledger reconcile, on a broken invariant.
        RuntimeError: When the candidate constructor refuses the row this
            function has just created -- a broken contract rather than
            anything an owner did, so it fails the request loud.  **This
            function raises no DESIGNED refusal**: every one this act owes has
            fired before it is called, which is what lets it write.
    """
    row = Transaction(
        account_id=scope.account_id,
        pay_period_id=pay_period_id,
        scenario_id=require_baseline_scenario(scope.owner_id).id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=_named_for(lines),
        category_id=None,
        transaction_type_id=ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if difference > 0 else TxnTypeEnum.EXPENSE,
        ),
        estimated_amount=abs(difference),
        is_envelope=False,
    )
    db.session.add(row)
    # The settle verb reads the row's own type and id, so it must exist first.
    db.session.flush()
    transaction_service.apply_requested_status(
        row,
        transaction_service.settled_status_id(row),
        settle_day=SettleDay(
            # ``observed``: this row exists BECAUSE a bank line showed the
            # money, so its day is a day a statement showed rather than a
            # bound or a day the owner typed (plan step **X-az**).
            day=days.posts_on, basis=SettledDayBasisEnum.OBSERVED,
        ),
    )
    candidate = transaction_candidate(row, scope.calendar, difference)
    if candidate is None:  # pragma: no cover - defended, not reachable
        # ``transaction_candidate`` answers ``None`` for a row worth nothing or
        # one whose period this calendar does not carry.  Neither can happen
        # here -- the figure is non-zero by the refusal that let this run, and
        # the period was resolved from THIS calendar by this act's own caller.
        #
        # **A RuntimeError rather than a ValidationError, and the difference
        # matters**: this row is already written and settled by now, so a
        # designed refusal would render "Nothing was changed" over money that
        # had moved.  Nothing catches this -- ``_batch._run`` takes only the
        # two designed refusals -- so it fails the whole request loud and
        # rolls back, which is the right answer for a broken contract.
        raise RuntimeError(
            f"transaction_candidate refused the residual row {row.id} this "
            f"door just created and settled; the match cannot record a "
            f"member it cannot describe.",
        )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_RESIDUAL_RECORDED, BUSINESS,
        "A matched group's difference was recorded as an uncategorized row.",
        user_id=scope.owner_id,
        account_id=scope.account_id,
        transaction_id=row.id,
        pay_period_id=pay_period_id,
        amount=str(difference),
        posts_on=days.posts_on.isoformat(),
        line_count=len(lines),
    )
    return candidate
