"""
Shekel Budget App -- What the Reconcile page's QUERY STRING says

Three readers, one per argument the screen carries: which TAB is open, whether
the bound on the settled tabs is lifted, and which card's MATCH pane renders
in the document.  Plan step ``bank_import:X-gi-1``.

**They are here because the page module is FULL**, and that is the honest
reason rather than a discovered cohesion: ``statement_reconcile`` stood at 993
of pylint's 1000-line ``max-module-lines`` before this step, so the correctness
fix below could not be written where it belonged.  The cohesion is real all the
same -- these three are the only readers of ``request`` in that module that
answer *what did the URL ask for*, they share one argument's worth of state
between them, and every one of them is called by a ROUTE.

**THAT LAST WORD IS THE CORRECTNESS FIX** (adversarial review 2026-09-05).
:func:`asked_to_open` was read inside the page's context builder, which
:func:`~._statement_doors.run_statement_fragment_door` calls AFTER
``db.session.commit()`` -- so a POST carrying a malformed ``open`` applied a
money pass, committed it, and then answered a bare 404.  A reader of the
request runs before its door; putting all three in one module is what makes
that visible rather than remembered.

Services boundary: these are HTTP-shaped concerns and import no service beyond
the :class:`~app.services.statement_match.Tab` they resolve to.
"""

from flask import abort, request

from app.services.statement_match import Tab


def requested_tab() -> Tab:
    """Return which tab the request is about.

    **ONE reader for both methods**, over ``request.values``: the GET carries
    the tab as a query argument and the POST as a hidden field, and two
    readers would be two places for the answer to differ -- which is a page
    that applies a pass and answers with another tab.

    **Every tab the service builds is served, as of plan step
    ``bank_import:X-gj-1c``.**  This route carried a ``_TABS_SERVED`` tuple and
    404'd a tab outside it, because ``X-gj-1b`` shipped the three whose cards
    are bank lines and the two whose cards are ACTS were not built yet --
    offering one would have been a control that cannot succeed (**R-HW**).
    Both are built now, so the tuple guarded nothing and is DELETED rather than
    widened to hold every member of the enum: a subset constant equal to the
    whole set is a fence a reader has to check against the enum to trust.

    Returns:
        The :class:`~app.services.statement_match.Tab`, defaulting to the
        inbox.

    Raises:
        werkzeug.exceptions.NotFound: When the value names no tab at all.
            **A 404 rather than a rendered apology**, which is the answer
            :func:`~.bank_agreement._requested_day` already gives for the same
            shape: nothing composes this URL by hand, so a value that does not
            resolve is a tampered or stale request rather than a person
            mid-edit.
    """
    asked = request.values.get("tab")
    if asked is None:
        return Tab.TO_EXPLAIN
    try:
        return Tab(asked)
    except ValueError:
        return abort(404)


def asked_for_everything() -> bool:
    """Return whether the request asked for the whole settled record.

    Plan steps ``bank_import:X-gj-1c`` and ``X-gj-4c-2``.  **The bound the
    register offered to lift, carried onto the tabs that replace it**
    (**R-HU**, **R-GX**): three tabs now render
    :data:`~app.services.statement_match.REGISTER_LIMIT` rows and say how many
    they withheld -- the two settled ones and the Skipped tab -- and this is
    what each of their *show the other N* links asks.  On the developer's own
    account it reaches 171 of 221 acts, so retiring the register without it
    would put them out of reach.

    A PRESENCE test and not a value one, exactly as the register's own reader
    is: the link either carries the flag or it does not, so there is no
    spelling of it to parse and no value to refuse.  What a crafted request
    can ask for is the page it would get by following the link the page
    already renders.

    **Over ``request.args`` and not ``request.values``**, which is the register's
    own reader and is the narrower of the two.  Nothing submits this in a form
    BODY: the *show the other N* link carries it in a query string, and the
    Undo form carries it in its own ACTION's query string -- which is
    ``request.args`` on a POST as much as on a GET, and is why that form needs
    no hidden field at all.  Reading ``values`` would let a body flip the bound
    on a door, which is a widening nothing here asks for.  (:func:`requested_tab`
    does read ``values``, and must: the Apply form carries ``tab`` as a real
    hidden field.)

    Returns:
        Whether the bound is lifted for this render.
    """
    return "all" in request.args


def asked_to_open() -> "int | None":
    """Return which card's MATCH pane renders IN the document, or ``None``.

    Plan step ``bank_import:X-gi-1``, ruling **bank_import:R-KA**.  The pane is
    a fragment htmx fetches when the tab is first shown, so with scripting off
    it never arrives at all and the card's ``<noscript>`` is what sends the
    owner here: the same page, the same tab, and ``?open=<line_id>``.

    **Over ``request.args``**, for the reason :func:`asked_for_everything`
    argues two functions above.  The Apply form carries it in its ACTION's
    query string, so a REFUSED press answers with the card's rows rather than
    the spinner -- though NOT with the owner's ticks, since the page prices
    its pane from what the pass OFFERS and never from the submitted body.
    **That is finding bank_import:BI-478**, and it is now the only hand-build
    door there is: it was parity with the workbench, which lost a refused
    press's ticks the same way, until plan step ``bank_import:X-gi-2`` deleted
    that page.  ``bank_import:X-gi-2a`` owns the remedy -- pricing the pane
    from the submitted body when one is present, which is what the live
    fragment already does.

    **CALLED BY EACH ROUTE, BEFORE ITS DOOR**, exactly as :func:`requested_tab`
    is.  It was read inside :func:`~.statement_reconcile._reconcile_context`
    until adversarial review 2026-09-05 -- and
    :func:`~._statement_doors.run_statement_fragment_door`
    calls that builder AFTER ``db.session.commit()``, so a POST whose ``open``
    was not an integer APPLIED THE PASS, COMMITTED IT, then answered a bare 404
    with no receipt and no way to know whether the money had moved.  *An
    earlier draft of this docstring cited ``_requested_tab`` as precedent for
    the opposite placement; that function is read per route.*

    Returns:
        The bank line id, or ``None`` where the request asked for no card.
        A line this pass renders no card for is NOT refused here: that is what
        a stale ``?open=`` is, and :func:`~app.services.statement_match
        .reconcile_page` answers it with no pane.

    Raises:
        werkzeug.exceptions.NotFound: When the value is not an integer at all,
            which is the answer :func:`requested_tab` already gives for the
            same shape -- nothing composes this URL by hand, so a value that
            cannot even be a line id is tampered rather than stale.
    """
    asked = request.args.get("open")
    if asked is None:
        return None
    try:
        return int(asked)
    except ValueError:
        return abort(404)
