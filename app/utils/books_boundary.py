"""
Shekel Budget App -- The Books Boundary

ONE comparison: whether an account whose books open on a day may record money
on another day (ruling **balance:R-HG**).  Pure date arithmetic -- no Flask, no
SQLAlchemy, no clock -- so it imports cleanly into the recurrence package,
which is pure by design, and into :mod:`app.services.cash_ledger` alike.

**Why a module of its own** (plan step ``pay_calendar:C18-a``, ruling
**R-PC85**).  :func:`books_hold` lived in ``cash_ledger._books`` while every
reader of it sat in or beside the cash ledger.  R-PC85 gave it a reader the
cash ledger cannot serve: the recurrence walk, which drops an occurrence whose
row would land on or before the books of an account the definition moves
money in (ruling **R-PC86**: compared on the row's CASH day, with a day ON the
opening inside it).  That walk is pure and may not import an ORM package, so
the leaf moved to the tier both can reach -- ``CLAUDE.md`` rule 14's placement
clause, the move ``recurrence._row_date`` made for ``compute_due_date`` one
step over.  ``cash_ledger`` imports it back and re-exports it under its old
name, so every existing ``cash_ledger.books_hold`` reader is unchanged.
"""

from datetime import date


def books_hold(opened_on: date, day: date) -> bool:
    """Return whether books opening on *opened_on* may record money on *day*.

    **THE comparison the books boundary is about, stated once** (ruling
    **R-HG**).  An account's opening equity is the balance at the CLOSE of
    ``opened_on``, so a day on or before it is ALREADY INSIDE the figure and
    recording money there counts it twice.  Every refusal in
    ``cash_ledger._books`` asks this and none re-spells it.

    **It is ``>`` and not ``>=``, and that is the whole of R-HG's ruled
    half.**  The ruling weighed the start-of-day reading -- refuse only a
    STRICTLY earlier movement -- and rejected it, because
    ``account_service.create_account`` stores the balance a human typed *as
    of* a day, which is that day's close, and admitting a same-day movement
    leaves the harm alive for exactly the rows finding **N-378** measured: on
    a MODELLED account the correction that heals the double count books to
    ``unrealized_change``, so a transfer becomes market performance that never
    unwinds.  Stating it in one function is what stops the two readings
    drifting apart across its five call sites in ``cash_ledger._books``, ONE
    in ``statement_match`` (``_gaps._split_at_books_open``), ONE in the
    recurrence walk (``recurrence._placement._placements``, plan step
    ``pay_calendar:C18-a``: ruling **R-PC86** chose this same strict reading
    for a GENERATED row, so a bill due on the opening day is inside the
    opening exactly as a movement settled that day is), and one SQL tier --
    and the SQL tier states it once too, as ``budget.books_hold``, which
    every predicate there asks rather than re-spelling.  *It said TWO in
    ``statement_match``, the second being ``_undisposed.awaiting_review_count``
    open-coding the same ``>`` as a COLUMN EXPRESSION because a SQL filter
    cannot call a Python predicate.*  Plan step ``bank_import:X-gm`` deleted
    that count in favour of a walk over the rows ``_split_at_books_open``
    already bounds, so the exception it stated no longer exists and this
    census is re-read rather than decremented.  It was open-coded in five
    PL/pgSQL predicates until plan step X-f3c-2b-2b's adversarial design
    review counted them, three of which that step had just added under a
    docstring claiming the comparison was stated once.

    Args:
        opened_on: The day the account's books open.
        day: The civil day money is claimed to have moved.

    Returns:
        ``True`` when *day* falls after the books opened, so the movement is
        outside the opening equity and may be recorded.
    """
    return day > opened_on
