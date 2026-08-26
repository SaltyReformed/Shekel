"""The SECOND tier: a figure that nearly agrees, scored rather than gated.

Plan step ``bank_import:X-f6d-1``, ruling **R-GD(b)**, finding **N-335**.
:mod:`._propose` explains what the bank and the app agree about to the cent;
this module explains what is left, where the two disagree by a little and a
second fact says they are the same movement anyway.

**Why it exists, in one figure.**  Bank line 285 (`ACH DEBIT GEICO PREM COLL`,
`-178.29`, posted 2026-07-02) sat THREE CENTS from transaction 2461 (`178.32`,
settled 07-06).  The exact predicate offered nothing, the review screen's
next-cheapest act recorded the line as a NEW purchase, and the ledger booked
`$356.61` for one `$178.29` movement.  The gate was the defect; a score is the
remedy.

**It is a SECOND tier rather than a wider first one**, and that is a claim
about evidence rather than about code.  An exact figure is a strong
coincidence; a near one is not, so a near pairing may never displace an exact
one and the exact tier's optimality argument is left exactly as it was.  This
module runs over what that tier could not explain.

**A score is not a licence** (**R-FP**): everything here is still a PROPOSAL
the owner reviews, and ruling **R-FZ(c)** gives it its own sweep class, because
it is the only proposal on that screen that changes what money was spent.

Services-boundary discipline: plain data in, frozen dataclasses out.  No
database, no clock, no request.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from ._offers import BankLine, CandidateRow, MatchProposal, RowKind
from ._pairing import day_distance, within_window


#: How far a row's figure may sit from the bank's, AS A FRACTION OF THE LINE,
#: and still be scored as the same movement (ruling **R-GD(b)**, plan step
#: ``bank_import:X-f6d-1``, developer decision 2026-08-22).
#:
#: **RELATIVE rather than absolute, because that is where the knee is.**  An
#: absolute bound has none: three cents is 0.017% of the `$178.29` Geico line
#: this step exists for and 15% of an `$18.64` swipe, so one figure cannot mean
#: the same thing at both ends of a statement.
#:
#: **The value is the measured gap, not a preference.**  Among the pairs
#: :func:`_near_score` admits at all -- one line, one row, the row naming the
#: bank's merchant -- the furthest same-movement pair on the developer's own
#: production clone is **0.180%** of its line (`Geico`, `$178.32` against the
#: bank's `$178.00`) and the nearest coincidence is **4.76%** (a `$21.00`
#: `Walmart` swipe against an unrelated `$20.00` Walmart purchase).  0.50% sits
#: 2.8x above the first and 9.5x below the second.
#:
#: **It also bounds the money.**  A proposal is reviewed and never applied on
#: its own (**R-FP**), but this is the largest correction the app will put in
#: front of the owner unasked: `$0.09` on a `$18.64` swipe, `$12.87` on a
#: `$2,573` paycheck line.
NEAR_MISS_BOUND: Decimal = Decimal("0.005")


def _names_the_merchant(line: BankLine, row: CandidateRow) -> bool:
    """Return whether the app's own row NAMES the merchant the bank recorded.

    **The corroboration a near miss needs, and it is a REQUIREMENT here rather
    than the tie-break ruling R-GD(b) first wrote** (developer decision
    2026-08-22, on measurement).  An EXACT figure is its own evidence: two
    unrelated movements agreeing to the cent inside a fortnight is a
    coincidence the day window and the assignment already handle.  A NEAR
    figure is not -- at half a percent of `$131.74` there is 66 cents of room,
    and an account holds hundreds of rows -- so an inexact claim needs a second
    fact, and the merchant is the only independent one a statement line
    carries.

    **Measured, on a production clone carrying the developer's own 376 lines.**
    Of the nine leftover lines with a row within 1%, the five whose row names
    the merchant are all the same movement (`Geico`/``Geico``,
    `Walmart`/``Groceries: Walmart``) and all five are ACCEPTED by the accept
    door; the four whose row does not are a `Lowe's` swipe against a
    ``CC Payback: Mint Mobile``, an `Amazon` swipe against ``Father's Day`` AND
    ``CC Payback: Claude Max`` at the identical distance, and an `Amazon` swipe
    against a ``Kayla`` envelope.  Two of those four the door would have taken,
    re-pricing an unrelated envelope; and a proposal on any of them also
    REMOVES the line from the create-a-purchase arm (:func:`~._reads._unexplained`),
    taking away the act that is actually correct.

    **It is not a general matching rule and must not become one**, which is the
    same measurement from the other side: only 59 of today's 129 exact 1:1
    proposals name the merchant, because the bank says
    ``POINT OF SALE DEBIT L340 DATE 03-26 HARRIS TEETER`` where the budget says
    ``Groceries``.  Requiring it of the exact tier would throw away 70 correct
    proposals.  It is corroboration for a WEAK claim, not a predicate.

    **It reads the COLUMN, never** :attr:`~._offers.BankLine.merchant_label`.
    Ruling **R-GA(a)** drew exactly that line: the label falls back to the
    whole description so a name box is never blank, and a rule keyed on that
    fallback would fire on the identical 32 characters SECU's OFX truncates 326
    of 361 descriptions to.  ``None`` means *this source names none* and
    corroborates nothing, which is the direction a missing fact has to fail in
    -- so a source with no merchant field proposes no near misses at all, and
    the owner's own hand-build form is what R-FP reserves for asserting one.

    **The merchant must appear as a WHOLE WORD, and a bare substring was a
    real hole** rather than a tidiness point.  ``merchants.name``
    is a ``String(100)`` whose only constraint is that it is not blank, so a
    two-character merchant is storable and real -- `BP` is a filling station --
    and unanchored containment made `BP` corroborate ``Subprime Loan Payment``,
    dissolving for that line the one independent fact this tier has.  Measured
    2026-08-22: with the boundary the developer's own five proposals are
    unchanged, so the anchor costs nothing it was buying.  A MINIMUM LENGTH was
    rejected instead of this -- it would refuse `BP` itself, which is a real
    merchant naming a real row.

    Args:
        line: The recorded bank line.
        row: The candidate row.

    Returns:
        Whether the row's label names the bank's merchant, case-blind.
    """
    if line.merchant is None:
        return False
    return re.search(
        rf"(?<!\w){re.escape(line.merchant)}(?!\w)",
        row.label,
        re.IGNORECASE,
    ) is not None


def _is_a_near_miss(line: BankLine, row: CandidateRow) -> bool:
    """Return whether *row* may be offered as a NEAR miss for *line*.

    Ruling **R-GD(b)**: the predicate is a SCORE, never a tolerance -- a
    RELATIVE distance corroborated by a second, independent fact, rather than
    a blanket cent tolerance that admits a `$20.00` budget row against an
    `$18.64` swipe.

    **What this does NOT return is a RANK, and that is a measured decision
    rather than an omission** (2026-08-22, on two independent adversarial
    reviews of this step).  A first implementation ranked the admitted
    candidates by ``(amount distance, day distance)`` and proposed the best
    one.  Both terms were then measured to be incapable of choosing:

    * the AMOUNT cannot, and this module's own header is the proof.  A
      `Lowe's` swipe sits **0.106%** from a ``CC Payback: Mint Mobile`` row
      while two genuine `Geico` pairs sit **0.180%** out -- so distance does
      not order true before false, which is precisely why the merchant is
      required at all.  Two rows that BOTH name the merchant have exhausted
      the evidence, and separating them by two basis points is arithmetic
      dressed as a finding;
    * the DAY cannot either, on the rows where it would matter most.  A
      purchase the reconcile panel ticked carries a SPAN window, and
      :func:`~._pairing.days_outside` scores every day inside a span ZERO --
      which :func:`~._propose._least_cost_pairing` compensates for by ORDERING
      its rows, a compensation no scoring pass has.  Measured on the
      developer's own account: **59 of 61** reconciled purchases share
      ``settled_on = 2026-08-18`` with purchase days up to 128 days earlier,
      so their spans overlap wholesale.  Reproduced: two `Walmart` purchases
      of `-52.10` (made 04-01) and `-52.15` (made 06-20) against a `-52.12`
      line posted 06-20 both score day-distance 0, and the rank picked the
      EIGHTY-DAY-OLD one on a two-cent margin.

    **So a contest is REPORTED, not resolved** (:func:`near_misses`), which is
    the rule :func:`~._propose._groups` already states for its own ambiguity:
    *an ambiguous proposal is a question dressed as an answer*.  A separation
    threshold between best and runner-up was the other remedy and is refused
    under conventions rule 3: **0** lines on the developer's own data have two
    admissible candidates, so there is nothing to measure one against, and a
    number chosen without a measurement is the tolerance R-GD(b) rejects
    wearing a different name.  Ordering the candidates a REVIEWED line offers
    is `X-f6d-3`'s, where the screen that shows them lives.

    Every refusal below is a gate the ACCEPT DOOR would apply, asked here so
    the screen cannot render an Accept that must fail:

    * an EXACT pair is not this tier's.  :func:`~._propose._one_to_one` and
      :func:`~._propose._groups` have already had it, and a near tier that
      could re-offer one would be a second answer to a question already
      answered;
    * a row whose figure is not correctable
      (:attr:`~._offers.CandidateRow.figure_is_correctable`) -- a transfer
      shadow, an envelope worth its purchases, a payback worth the spend it
      repays.  Measured live: 2 of the 4 uncorroborated near candidates on the
      developer's own clone are CC paybacks, refused by the door by name;
    * the bound, and the merchant (:func:`_names_the_merchant`);
    * the day window (:func:`~._pairing.within_window`), which is the pair
      legality test the exact tier already uses -- INCLUDING the purchase
      floor, so a near miss cannot offer a pairing ``update_entry`` refuses.

    **A SIGN disagreement needs no test of its own and that is arithmetic, not
    an omission.**  The accept door refuses one, and a row whose cash sits
    within half a percent of the line cannot have the opposite sign: were the
    signs opposite, ``|line - row|`` would be ``|line| + |row| > |line|``,
    which fails the bound outright.  Both magnitudes are non-zero --
    ``ck_bank_statement_lines_amount_real_nonzero`` for the line, and the two
    readers that build candidates decline a row worth nothing
    (``_candidates._transaction_candidates`` through ``transaction_candidate``,
    and ``_candidates._purchase_candidates`` through its own ``if
    entry.amount``).

    Args:
        line: The recorded bank line.
        row: The candidate row.

    Returns:
        Whether this pair may be offered at all.
    """
    if row.cash_amount == line.amount:
        return False
    if not row.figure_is_correctable:
        return False
    # **Multiplied rather than divided**, so the bound is exact at every
    # magnitude: ``0.005 * 178.29`` is representable and ``0.03 / 178.29`` is
    # not.  Nothing here needs the ratio itself.
    if abs(line.amount - row.cash_amount) > NEAR_MISS_BOUND * abs(line.amount):
        return False
    if not _names_the_merchant(line, row):
        return False
    return within_window(row, line)


def near_misses(
    lines: "list[BankLine]", rows: "list[CandidateRow]",
) -> "tuple[list[MatchProposal], frozenset[int]]":
    """Return the near-miss proposals, and WHICH lines went undecided.

    The SECOND tier (plan step ``bank_import:X-f6d-1``, ruling **R-GD(b)**),
    over what the exact tier could not explain.  One line, one row: a GROUP is
    never admitted, for the reason this module's own header measures.

    **A pairing is offered only where it is the ONLY one on BOTH sides** --
    this line admits exactly one row, and that row admits exactly one line.
    Anything else is a contest, and :func:`_is_a_near_miss` states why nothing
    here may settle one: the amount cannot (the module header measures a false
    pair NEARER than two true ones) and the day cannot (a reconciled purchase's
    span scores every day inside it zero, and 59 of 61 such purchases on the
    developer's own account share one settle day).  So a contest is REPORTED,
    which is the rule :func:`~._propose._groups` already applies to its own
    ambiguity.

    **The symmetry is what keeps the offers legal as well as honest**: two
    proposals naming one row would have the second refused at the door as
    already matched, and awarding it to whichever line was iterated over first
    is the greedy shape :func:`~._propose._least_cost_pairing` replaced twice
    on the exact tier.

    Args:
        lines: The bank lines no exact proposal explains.
        rows: The candidate rows no exact proposal claims.

    Returns:
        ``(proposals, undecided line ids)`` -- one proposal per line that
        admits exactly one row which admits only it, and the ids of the lines
        this pass admitted a candidate for and then declined to choose between.

        **The second is a BOUND and rides out** because a bound that says
        nothing about what it dropped reads as a clean sweep.  It is the LINE
        IDS rather than a count since plan step ``bank_import:X-f6d-3``: a
        count can only be reported in a panel at the foot of the page, where it
        names no line and the owner cannot act on it, and the act it should
        prompt -- build this one by hand rather than record it a second time --
        is offered against one specific line in two different cards.  The count
        is still derivable (``len``) and nothing needs it.
    """
    by_line: "dict[int, list[CandidateRow]]" = defaultdict(list)
    by_row: "dict[tuple[RowKind, int], list[int]]" = defaultdict(list)
    for line in lines:
        for row in rows:
            if not _is_a_near_miss(line, row):
                continue
            by_line[line.line_id].append(row)
            by_row[(row.kind, row.row_id)].append(line.line_id)

    proposals = []
    for line in lines:
        admitted = by_line.get(line.line_id, ())
        if len(admitted) != 1:
            continue
        row = admitted[0]
        if by_row[(row.kind, row.row_id)] != [line.line_id]:
            continue
        proposals.append(MatchProposal(
            lines=(line,), rows=(row,),
            # ``None`` for a row carrying no day at all, exactly as the exact
            # tier answers it: the distance is genuinely unknown, and reading
            # it as zero captions a row nobody has settled as *confirms the day
            # you already had*.
            day_gap=day_distance(row, line.posted_on),
        ))
    decided = {
        proposal.lines[0].line_id for proposal in proposals
    }
    return proposals, frozenset(by_line) - decided
