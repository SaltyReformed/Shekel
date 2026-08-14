"""
Shekel Budget App -- Transaction Service: the TYPE rule

Which settled status a row of a given TYPE takes -- income is Received, an
expense is Paid -- and the two things that follow from it: the refusal when a
door asks for the other one, and the narrowed set a dropdown may OFFER.

**It is a display convention with an enforced consequence**, which is why it is
a leaf of its own rather than a line inside the settle.  Every reader of the
settled band consumes ``settled_status_ids()`` as a SET and cannot tell Paid
from Received; the state machine is keyed on the STATUS and never sees
``transaction_type_id``, so it admits both from Projected.  This module is the
only place that holds the row's type and the status rule together.

Flask-isolated: plain data and ORM rows in, values out, no ``request`` /
``session`` imports, no flush, no commit.
"""

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services.state_machine import allowed_transitions


def settled_status_member(txn: Transaction) -> StatusEnum:
    """Return the settled STATUS a row of this TYPE takes, as the enum member.

    The type rule, stated ONCE.  :func:`settled_status_id` resolves it to a
    ``ref.statuses.id`` for the writers and
    :func:`reject_mismatched_settled_status` reads its ``value`` for the human
    message -- which used to be a third spelling, ``"Received" if txn.is_income
    else "Paid"``, sitting two lines below the resolution it was restating.  A
    message derived from a second copy of a rule tells the user something the
    code may have stopped doing.

    Args:
        txn: The transaction about to settle.  Read for ``is_income`` only.

    Returns:
        ``StatusEnum.RECEIVED`` for income, ``StatusEnum.DONE`` otherwise.  The
        member's ``value`` is the DISPLAY name ("Received" / "Paid"), which is
        what makes one expression answer both needs.
    """
    return StatusEnum.RECEIVED if txn.is_income else StatusEnum.DONE


def settled_status_id(txn: Transaction) -> int:
    """Return the settled status a row of this TYPE takes.

    Income is Received and everything else is Paid.  It is a display
    convention rather than a balance one -- every reader of the settled band
    consumes ``settled_status_ids()`` as a SET and cannot tell the members
    apart -- but it is a convention with TWO former spellings, which is one
    too many for a rule that decides a stored column.

    **Both spellings were live and they agreed by reading.**
    ``app/routes/transactions/mutations.py:mark_done`` picked the id and
    handed it down, and :func:`._settle.settle_from_entries` re-derived the same id
    from the same predicate, its own comment naming the route as the thing it
    "mirrors" -- Section 8's "two spellings that agree by reading are two
    answers until one is deleted", on a money-adjacent rule.  Ruling **R-FA**
    forced the question by giving the reconcile tick the same settle as the
    grid's Mark Paid: a third door would have made it three.

    A transfer SHADOW never reaches here.  Its settle goes through
    ``transfer_service.update_transfer``, which sets Paid on both legs
    because the income/expense split is meaningless for a pair whose whole
    point is that one leg is each.

    Args:
        txn: The transaction about to settle.  Read for ``is_income`` only.

    Returns:
        The ``ref.statuses.id`` for Received or Paid.
    """
    return ref_cache.status_id(settled_status_member(txn))


def _mismatched_settled_status_ids(txn: Transaction) -> frozenset[int]:
    """Return the TYPE-specific settled statuses *txn* does NOT take.

    One expression for the two things that need it, the refusal
    (:func:`reject_mismatched_settled_status`) and the dropdown's pre-hint
    (:func:`offerable_status_ids`).  Stated once so the enforced rule and the
    displayed rule cannot drift, which is the same reason
    :func:`._settle.settles_from_entries` is published.

    **It is the TYPE PAIR minus this row's, NOT the settled band minus this
    row's, and the difference is a capability.**  The band has THREE members and
    the third is ``Settled`` -- the archive, which is not type-specific: an
    expense reaches it from Paid and an income row from Received.  A first draft
    subtracted the whole band, so ``Settled`` counted as "mismatched" for every
    row and :func:`offerable_status_ids` removed it from every dropdown --
    silently retiring the only control that offers the archive, while the state
    machine still called ``Paid -> Settled`` legal and the seam still preserved
    the settle day across it.  Caught by adversarial review, and two assertions
    written in the same step said the opposite ("the full-edit Status dropdown
    can still reach it"), which is what made it a mistake rather than a
    decision.

    A SET rather than an equality, and deliberately: an inline equality against
    a resolved status id is the D6-09 shape the project's own census test
    refuses outside ``balance_predicates``, and the set form is both honest
    about what is being asked and the one that composes with
    :func:`app.services.state_machine.allowed_transitions`.

    Args:
        txn: The row, read for ``is_income``.

    Returns:
        The ``ref.statuses.id`` values this row's TYPE forbids it settling
        into -- exactly one member, for either type.
    """
    type_specific = frozenset({
        ref_cache.status_id(StatusEnum.DONE),
        ref_cache.status_id(StatusEnum.RECEIVED),
    })
    return type_specific - {settled_status_id(txn)}


def reject_mismatched_settled_status(
    txn: Transaction, new_status_id: int,
) -> None:
    """Refuse a settled status that contradicts the row's TYPE.

    :func:`settled_status_id` is the rule -- income settles as Received, an
    expense as Paid -- and it is the verb's to apply, so a door that asks for
    the OTHER one is asking for something the domain does not have.  The
    alternative to refusing is silent substitution: the row lands in a status
    the user did not pick, with nothing said about it.

    **The state machine cannot make this judgement**, and that is why the rule
    is here.  Its transaction map admits Projected -> Paid AND Projected ->
    Received because it is keyed on the STATUS, not on the row: it never sees
    ``transaction_type_id``.  So the two facts have to meet somewhere, and this
    is the only place that holds both.

    **It refuses nothing that exists.**  Measured on production: 17 rows carry
    an income type in the Paid status and all 17 are transfer SHADOWS, whose
    settle goes through ``transfer_service`` and never reaches here (a transfer
    sets Paid on both legs deliberately -- the income/expense split is
    meaningless for a pair whose whole point is that one leg is each).  Every
    one of the 143 non-shadow settled rows agrees with its own type.

    The full-edit dropdown pre-hints the same rule
    (:func:`offerable_status_ids`), so reaching this refusal takes a crafted
    request or a stale form -- which is exactly the layering the route's own
    purchase-tracking guard uses, and the reason this is a designed 400 rather
    than a programming error.

    Args:
        txn: The row being settled, read for ``is_income``.
        new_status_id: The settled status the door asked for.

    Raises:
        ValidationError: When *new_status_id* is a settled status other than
            the one this row's type takes.
    """
    if new_status_id not in _mismatched_settled_status_ids(txn):
        return
    kind = "Income" if txn.is_income else "An expense"
    takes = settled_status_member(txn).value
    raise ValidationError(
        f"{kind} settles as {takes}.  Transaction {txn.id} was asked to "
        f"settle as status {new_status_id} instead."
    )


def offerable_status_ids(txn: Transaction) -> frozenset[int]:
    """Return the statuses a DOOR may offer for *txn* -- the dropdown's set.

    ``state_machine.allowed_transitions`` narrowed by this module's type rule.
    The state machine grades the STATUS and cannot see the row's type, so its
    answer for a Projected row contains both Paid and Received; exactly one of
    them is reachable for any given row, and offering the other is offering a
    control that 400s at :func:`reject_mismatched_settled_status`.

    It is the pre-hint half of one rule rather than a second rule, which is the
    shape the status dropdown was already built to (grid audit D2: "options the
    state machine would reject render disabled, so an illegal transition cannot
    be picked instead of failing as a 400 after Save").  The enforcement stays
    at the verb; this only decides what the user is shown.

    **There is deliberately NO exemption for a row already sitting in the
    mismatched status.**  A first draft carried one -- ``- {txn.status_id}``, so
    such a row could still re-submit its own status -- and it was UNREACHABLE:
    the only mismatched rows in existence are the 17 income-typed Paid transfer
    SHADOWS, and ``routes/transactions/forms`` branches a shadow to the transfer
    popover before this is called, while ``TransactionUpdateSchema`` carries no
    ``transaction_type_id`` for a PATCH to flip.  A guard whose only possible
    test cannot fail is not a guard (finding **N-184**), and this module's own
    seam deleted one for that reason at plan step X-f1c.

    Args:
        txn: The row the dropdown is being rendered for.

    Returns:
        The legal successor ids, minus the settled status this row's type does
        not take.
    """
    return allowed_transitions(txn) - _mismatched_settled_status_ids(txn)
