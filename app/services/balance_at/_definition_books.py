"""WHERE A RECURRING DEFINITION'S BOOKS OPEN -- the floor its occurrences start above.

Plan step ``pay_calendar:C18-a``, rulings **R-PC85** and **R-PC86**
(developer, 2026-09-22).  A recurring definition's occurrences run from the
LATER of its own first occurrence and the day after the books open on every
account it moves money in -- the opening end's mirror of how its closing
already composes the owner's end date with a loan's payoff.  The rule says
when it fires; the ACCOUNT says where the app keeps books, and money on or
before an account's opening day is already inside its opening equity (ruling
**balance:R-HG**).  This module answers the account half:
:func:`definition_books_opened_on`, read by
:meth:`~app.services.balance_at.BalanceContext.resolved_for` and attached to
the resolved value the recurrence walk then bounds by
(``recurrence._placement._lands_inside_the_books``).

**Why this is a fact about ACCOUNTS and never about the rule.**  A rule's
``starts_on`` is its first occurrence (ruling **R-R16**) -- the rhythm's
anchor and, for a rule that begins in the future, its start -- and it is
authored.  "Not before my account's books" is stored once, in
``budget.account_openings``, and it is RESTATED: on the production clone this
was measured on, the Van Loan's opening moved 2026-05-21 -> 2026-04-22.  A
copy of it on the rule would be a second home needing a reconciler
(``CLAUDE.md`` rule 14); reading it here, on every pass, is what makes the
bound follow a restatement with nothing to keep in step.

**Why it is a module of its own**: ``_context.py`` stands within a hundred
lines of pylint's 1,000-line ceiling, and the two questions below -- which
accounts a definition moves money in, and where the latest of their books
opens -- are this step's, not the pass's.  The pass keeps the MEMO (one read
per account per pass) and the one composition; this keeps the reading.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
plain values out; no Flask symbol, no writes, no clock.
"""

from __future__ import annotations

from datetime import date

from app.services.cash_ledger import governing_account_opening

#: The attributes naming an account a recurring definition moves money in,
#: read off the definition by name.  A ``TransactionTemplate`` carries
#: ``account_id``; a ``TransferTemplate`` carries ``from_account_id`` and
#: ``to_account_id`` -- money leaves one and reaches the other, so BOTH books
#: bound it; the form preview's
#: :class:`~app.services.recurring_definition.UnsavedDefinition` carries the
#: same three names for the same two shapes.  Read by name rather than by type
#: because the unsaved definition is not a template and the recurrence
#: package's own owner contract is duck-typed
#: (:data:`~app.services.recurrence.RecurrenceOwner`).
_MONEY_ACCOUNT_ATTRIBUTES = ("account_id", "from_account_id", "to_account_id")


def definition_money_accounts(definition: object | None) -> tuple[int, ...]:
    """Return the ids of every account *definition* moves money in.

    Args:
        definition: A ``TransactionTemplate``, a ``TransferTemplate``, an
            :class:`~app.services.recurring_definition.UnsavedDefinition`, or
            ``None`` -- a rule whose owner is a payroll LINE, whose cadence
            names which paychecks carry a deduction and creates no row of its
            own (the paycheck row it rides on is bounded by that row's own
            definition).

    Returns:
        The account ids *definition* names, in attribute order, ``None``
        values skipped; empty for ``None`` and for a form that has not stated
        an account yet.
    """
    if definition is None:
        return ()
    return tuple(
        account_id
        for account_id in (
            getattr(definition, name, None)
            for name in _MONEY_ACCOUNT_ATTRIBUTES
        )
        if account_id is not None
    )


def definition_books_opened_on(
    account_ids: tuple[int, ...], memo: "dict[int, date | None]",
) -> date | None:
    """Return the LATEST governing opening day among *account_ids*.

    The floor a definition's occurrences start above: a transfer moves money
    in both of its accounts, so neither's books may be written below, and the
    later opening is the binding one.  Each account's day is read once per
    pass through *memo* -- the caller's -- so a render resolving many
    definitions over one account pays one read.

    **An account with no opening record contributes no floor**, the
    disposition ``budget.assert_movement_after_books_open`` takes for the same
    state: every account gets one at creation and a migration backfilled the
    rest, so it is a broken invariant, and the READ side already refuses it
    loudly (``cash_ledger.account_opening_fact`` raises), so it is not
    re-raised here on an unrelated path.

    Args:
        account_ids: The accounts the definition moves money in
            (:func:`definition_money_accounts`).  Assumed the pass owner's --
            a stored template's by its own ownership, an unsaved definition's
            by the route's ownership gate.
        memo: The pass's ``account_id -> opened_on`` memo, filled here.

    Returns:
        The latest governing ``opened_on``, or ``None`` when no named account
        carries an opening (or none is named).
    """
    days = []
    for account_id in account_ids:
        if account_id not in memo:
            opening = governing_account_opening(account_id)
            memo[account_id] = None if opening is None else opening.opened_on
        if memo[account_id] is not None:
            days.append(memo[account_id])
    return max(days, default=None)
