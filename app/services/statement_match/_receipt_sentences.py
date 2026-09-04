"""The sentence one APPLIED act writes on the pass receipt.

Plan step ``bank_import:X-gj-4b``, split out of :mod:`._batch` under ruling
**balance:R-IR**, which puts a module split on the session that BREAKS the
1,000-line bound.  Lighting the SKIP verb gave the receipt a fourth act class
and took that module to 1,042.

**The seam is :mod:`._sentence`'s, one tier over.**  That module composes the
sentence a CARD carries, as spans, for a screen that has not acted yet; this
one composes the sentence an act that HAS landed writes on the receipt, as a
finished string.  Everything left in :mod:`._batch` decides what runs, in what
order and inside which savepoint, and reads what came back; the words are
here.

**They are strings and not spans, and the difference is the audience.**  A
card's sentence is laid out by a template that inks each part
(:class:`~._sentence.Ink`), so a service that formatted one would be choosing
presentation it cannot see.  A receipt item is quoted verbatim into a list by
THREE surfaces -- the Reconcile page, the hand-build workbench and the review
queue, which :func:`~app.routes.accounts._statement_doors.log_pass_applied`
calls three ways of performing one act -- so what it needs is to read as one
sentence wherever it lands.  *An earlier draft said two surfaces and claimed
the sentence also reaches ``system.audit_log``; it does not -- what that door
logs is :func:`~app.routes.accounts._statement_doors.outcome_counts`' figures,
and ``system.audit_log`` is written by row triggers.*

**Each names its act's FIGURE and its DAY**, which is ruling **R-GD(a)**'s
rule and the reason :attr:`~._batch.AppliedItem.line_ids` is documented as a
correlation key rather than a label: a ``bank_statement_lines`` id is opaque
and appears nowhere the owner can see, so the sentence is what identifies the
act on screen.

**Nothing changed on the way across** except THREE names, which lost their
leading underscore because a sibling module calls them now; ``skip_summary``
is new in this step and was never private.  The rule is
:func:`~._cards.parked_card` set and :mod:`._card_sections` followed one split
earlier in this same step.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
strings out, no Flask import, no clock read, no query -- every figure arrives
on the value the door returned.
"""

from __future__ import annotations

from ._rules import is_inflow


def match_summary(accepted) -> str:
    """Return the sentence describing what one accepted match did.

    **It names the three effects separately**, because they are different acts
    with different consequences: settling a row books money the projection was
    still holding forward, correcting a settle day moves money already booked
    from one day to another, and correcting a PURCHASE day moves no money at
    all but rewrites when the owner says they bought something.  A single
    "2 rows updated" would hide which -- and the third was folded into the
    first until adversarial review 2026-08-18 measured that the case it was
    built for (an unsettled purchase, re-dated by up to 59 days) reported only
    "marked 1 row(s) as having happened".

    Args:
        accepted: The :class:`~._accept.AcceptedMatch`.

    Returns:
        The sentence.
    """
    did = []
    if accepted.settled_count:
        did.append(
            f"marked {accepted.settled_count} row(s) as having happened"
        )
    if accepted.corrected_count:
        did.append(
            f"moved {accepted.corrected_count} row(s) onto the bank's day"
        )
    if accepted.redated_count:
        did.append(
            f"corrected the purchase date on {accepted.redated_count} row(s)"
        )
    # **The AMOUNT, and it is named LAST because it is the one thing here that
    # changes what money was SPENT** rather than when it moved.  Without it a
    # repricing on a row already carrying the bank's day fell through to
    # "confirmed what you already had" -- a sentence that was not merely silent
    # but false.
    if accepted.repriced_count:
        did.append(
            f"corrected the amount on {accepted.repriced_count} row(s)"
        )
    # **After it, because it is the only clause naming a row that did not
    # exist before this act** (plan step bank_import:X-f6d-4).  It says
    # "recorded" rather than "corrected" for that reason: nothing of the
    # owner's was changed to produce it.
    if accepted.residual is not None:
        did.append(
            f"recorded the {accepted.residual:+,.2f} difference as a row "
            f"with no category"
        )
    what = " and ".join(did) if did else "confirmed what you already had"
    return (
        f"Matched {accepted.line_count} statement line(s) worth "
        f"{accepted.amount:+,.2f} on {accepted.posts_on}: {what}."
    )


def created_summary(recorded) -> str:
    """Return the sentence describing what recording one line did.

    **It names the container and whether it was created**, because those are
    different acts: filing a purchase under an envelope the owner already
    budgeted changes what that envelope RECORDS as its cost, while creating one
    adds a budget line to a period that did not have it.

    **It names both days only when they differ.**  A purchase carries the day
    it was MADE beside the day the bank TOOK it (ruling **R-FW**), and on 179
    of the developer's 361 lines the source states no separate made-day at all
    -- so printing "made on" unconditionally would report the clearing day as a
    swipe day on half of every statement, which is the exact substitution R-FW
    rejected.

    **The FIGURE is negated once; the VERB reads the field the door stated**
    (ruling **R-II**).  ``CreatedPurchase.amount`` is the purchase's own signed
    figure, so a refund is negative and the statement states the same movement
    the other way round -- one negation, so this sentence and the
    ``AppliedItem.amount`` beside it cannot disagree about which way the money
    went.  **The verb asks** :attr:`~._creations.CreatedPurchase.records_a_refund`
    **rather than the sign of that figure** (plan step
    ``bank_import:X-gj-2b-3``): that field exists so nothing downstream
    re-derives a direction, and this function was the first consumer written
    after it and re-derived one anyway.  The two agree by construction today --
    ``_born_purchase`` sets both from one line -- which is exactly why a second
    spelling here would be invisible until it was not.

    Args:
        recorded: The :class:`~._create.CreatedPurchase`.

    Returns:
        The sentence.
    """
    where = (
        f"a new envelope, {recorded.envelope_label}"
        if recorded.envelope_created
        else recorded.envelope_label
    )
    made = (
        f", made {recorded.made_on}" if recorded.made_on != recorded.posts_on
        else ""
    )
    # **The BANK's convention** (plan step ``bank_import:X-gj-2b``, ruling
    # **R-II**).  A refund is a NEGATIVE purchase, and this sentence printed
    # ``$-42.00 your bank took`` -- the wrong sign AND the wrong direction, in
    # the one notice the owner gets for money a rule moved without a press
    # (**R-GH**).  It also contradicted ``AppliedItem.amount`` on the same
    # item, which negates onto the bank's convention; the negation is done
    # ONCE here so the two cannot disagree.
    on_the_statement = -recorded.amount
    # **The VERB is the DOOR's answer, not this figure's sign** (plan step
    # ``bank_import:X-gj-2b-3``): ``records_a_refund`` is set by
    # ``_born_purchase`` from ``_rules.is_inflow``, which is this package's one
    # statement of the bank's sign convention.
    took_or_gave = "gave back" if recorded.records_a_refund else "took"
    return (
        f"Recorded ${abs(on_the_statement):,.2f} your bank {took_or_gave} on "
        f"{recorded.posts_on}{made} as a purchase in {where}."
    )


def income_summary(recorded) -> str:
    """Return the sentence describing what recording one deposit did.

    **It names no container and no second day, because the row has neither**
    (ruling **bank_import:R-GW**).  ``created_summary`` beside it names both because a
    purchase is filed against something that reserves for it and carries a
    budget clock of its own; an income row IS the movement, so the day the bank
    credited it is the only day there is.

    **It says the row has no category**, which is the one thing about it the
    owner has to act on later: the money is recorded correctly and filed
    nowhere, and a receipt that did not say so would leave the owner to find
    out from the grid.

    Args:
        recorded: The :class:`~._creations.RecordedIncome`.

    Returns:
        The sentence.
    """
    return (
        f"Recorded ${recorded.amount:,.2f} your bank paid in on "
        f"{recorded.posts_on} as {recorded.label}, with no category yet."
    )


def skip_summary(skipped) -> str:
    """Return the sentence describing what skipping one line did.

    **It names the figure and the day, which is how the owner recognises the
    act** -- :class:`AppliedItem`'s own rule, and ruling **R-GD(a)**'s: a
    consent naming a count and no figure is a consent to an amount nobody
    stated.  A bank line's id is opaque and appears nowhere the owner can see.

    **It names no container and no row, because a skip acts on neither.**
    That is the whole content of the decision (ruling **bank_import:R-JG**),
    and it is why this composer takes one value where
    :func:`created_summary` takes a destination:
    :func:`~._sentence.for_skip` makes the same argument for the card.

    **It says what the act did NOT do**, which no other sentence on this
    receipt has to: every sibling here reports an act CLASS that moves money,
    though not every clause of one does -- :func:`match_summary` can report
    *confirmed what you already had*, and a corrected purchase day moves
    nothing either.  This one
    reports a line leaving the inbox while the difference between the books
    and the bank stays exactly where it was.  An owner who read *skipped* as
    *resolved* would be reading the hero's own figure wrong.

    **The DIRECTION is asked through** :func:`~._rules.is_inflow`, this
    package's one statement of the bank's sign convention, rather than by
    testing the sign here -- the rule :func:`created_summary` records having
    been corrected on, where a second spelling of a direction printed a refund
    as a charge.

    Args:
        skipped: The :class:`~._skipping.SkippedLine` the door returned.

    Returns:
        The sentence.  **A repeat gets its own**, because a door that absorbs
        a double-submit and reports it as work is indistinguishable from one
        that wrote: ``was_already_skipped`` is a fact about a PRESS, and the
        receipt says which press this was.
    """
    moved = skipped.line.amount
    took_or_paid = "paid in" if is_inflow(moved) else "took"
    if skipped.was_already_skipped:
        return (
            f"The ${abs(moved):,.2f} your bank {took_or_paid} on "
            f"{skipped.line.posted_on} was already recorded as explained by "
            f"nothing, so this changed nothing."
        )
    return (
        f"Skipped the ${abs(moved):,.2f} your bank {took_or_paid} on "
        f"{skipped.line.posted_on}: nothing in your budget explains it, and "
        f"this closes no difference between your books and your bank."
    )
