"""
Shekel Budget App -- The RECONCILE page, and the two doors it posts to

"What is each of these bank lines?" -- one page on four verbs, replacing the
review queue, the register and the hand-build workbench.  Plan step
``bank_import:X-gj-1b``; the direction was locked at Loop A round 4 on
2026-08-29 and is ``docs/design/bank_import_audit.md``.  Its rulings are
**bank_import:R-HP** through **R-HX** in ``docs/plans/rulings.md``.

**IT MOVES MONEY, through doors that already exist.**  Apply posts the OK'd
cards through :func:`~app.services.statement_match.apply_reviewed` -- the same
door the review queue and the workbench use, with the same savepoint-per-item
policy (**R-FZ(a)**) and the same receipt -- and the ADD tab's
*always, for this merchant* control posts through
:func:`~app.services.statement_match.state_rules`, which moves none.  **This
module opens no door of its own**, which is what lets a whole screen ship
without a migration and without a new money path.

**Three routes, and the third is a READ.**  The page and Apply are the pair;
the third re-renders one card's MATCH tab -- its candidate rows and what the
ticked ones come to -- and writes nothing.  It is a POST for the reason
:func:`~.statement_workbench.statement_match_totals` is: it carries a list of
ids and a CSRF token, not because it changes anything.  **The alternative was
measured and refused**: rendering every card's candidate rows with the page is
67 rows in 18 cards at the workbench's own 991 bytes a row, ~1.2 MB, which is
finding **N-374** rebuilt one surface later.

**Which TABS this build serves is stated here** (:data:`_TABS_SERVED`).  The
two tabs whose cards are ACTS already applied -- Explained and Filed by rules
-- are plan step ``X-gj-1c``'s, and a tab bar offering a tab this build cannot
render would be the affordance-that-cannot-succeed shape ruling **R-HW**
bounds.  The service builds all five (:func:`~app.services.statement_match
.reconcile_page`); this route serves the three it has cards for.

**The old routes stay alive beside this page** until ``bank_import:X-gi``'s
census deletes them, which is ruling **R-HU**'s own sequencing: every door
this screen posts to is one that is already tested, and nothing is removed on
the way in.

Services boundary: this module owns the HTTP-shaped concerns -- ownership,
form parsing, fragment rendering, URLs -- and delegates every read and write
to :mod:`app.services.statement_match`.
"""

import logging
from dataclasses import dataclass

from flask import abort, render_template, request, url_for
from flask_login import current_user, login_required
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.routes.accounts._statement_doors import (
    fragment_door,
    log_pass_applied,
    refusal_sentence,
    run_statement_fragment_door,
    submitted_batch,
    submitted_item_count,
    submitted_match,
)
from app.schemas.validation.statement_reconcile import (
    ReconcileRuleBatchSchema,
    reconcile_match_payload,
    reconcile_payload,
    reconcile_rules_payload,
)
from app.schemas.validation.statements import (
    StatementBatchSchema,
    StatementMatchSchema,
)
from app.services import balance_at, bank_agreement
from app.services.category_service import list_active_categories
from app.services.statement_match import (
    MatchCandidates,
    NewEnvelope,
    NEW_ENVELOPE,
    ReviewScope,
    Tab,
    apply_reviewed,
    preview_hand_build,
    reconcile_page,
    review_set,
    rule_creating,
    rule_naming,
    state_rules,
)
from app.utils.auth_helpers import require_owner
from app.utils.error_fragments import designed_error

_logger = logging.getLogger(__name__)

#: One schema instance each, constructed at import like every sibling's.  The
#: batch schema grades a whole pass; the match schema grades the ONE card the
#: live-difference fragment is about, and it is the same schema the batch
#: nests -- so a card priced by the fragment and the same card applied by the
#: pass are graded by one set of rules.
_batch_schema = StatementBatchSchema()
_match_schema = StatementMatchSchema()
_rules_schema = ReconcileRuleBatchSchema()

#: The partial the page, the Apply door and every refusal arm render.  ONE
#: template, so what htmx swaps in after a POST cannot drift from what a
#: reload shows -- the discipline all three sibling statement surfaces keep.
_BODY = "accounts/_statement_reconcile_body.html"

#: The MATCH tab's own body: the candidate rows, and what the ticked ones come
#: to.
_MATCH_PANE = "accounts/_statement_reconcile_match.html"

#: The tabs THIS build renders cards for, in the order the bar draws them.
#:
#: **Explained and Filed by rules are plan step ``X-gj-1c``'s**, whose cards
#: are ACTS already applied rather than bank lines -- a different card, a
#: different partial and an Undo door.  Offering their tabs now would be a
#: control that cannot succeed, which is the shape ruling **R-HW** bounds and
#: ``balance:R-ET``'s corollary deletes outright.  The service already builds
#: all five, so ``X-gj-1c`` adds two members here and a partial, and changes
#: nothing else.
_TABS_SERVED: "tuple[Tab, ...]" = (Tab.TO_EXPLAIN, Tab.TRANSFERS, Tab.SKIPPED)

#: What a database failure tells the owner.  It names no table -- the
#: traceback goes to the log -- and it ends the way every refusal in this
#: package does, which is true here because the route owns the unit of work: a
#: pass that could not commit has written nothing.
_DB_ERROR_MESSAGE = (
    "Something went wrong applying that, and nothing was changed.  Here is "
    "where you were."
)

#: What the screen says about a card the owner OK'd that named no act.  It is
#: reachable from a browser -- pressing the panel's ADD button with nothing
#: chosen -- so it may not be a pass-level refusal, which would cost every
#: other OK on the page (**R-FZ(a)**), and it may not be a silent drop, which
#: would leave a press unanswered.
_OK_WITH_NO_ACT = (
    "You pressed OK on {count} card(s) without choosing what to do with "
    "them, so nothing was recorded for those: bank line(s) {lines}. Open the "
    "card and pick a destination, or a row to match it against."
)


def _requested_tab() -> Tab:
    """Return which tab the request is about.

    **ONE reader for both methods**, over ``request.values``: the GET carries
    the tab as a query argument and the POST as a hidden field, and two
    readers would be two places for the answer to differ -- which is a page
    that applies a pass and answers with another tab.

    Returns:
        The :class:`~app.services.statement_match.Tab`, defaulting to the
        inbox.

    Raises:
        werkzeug.exceptions.NotFound: When the value names no tab this build
            serves.  **A 404 rather than a rendered apology**, which is the
            answer :func:`~.bank_agreement._requested_day` already gives for
            the same shape: nothing composes this URL by hand, so a value that
            does not resolve is a tampered or stale request rather than a
            person mid-edit.
    """
    asked = request.values.get("tab")
    if asked is None:
        return Tab.TO_EXPLAIN
    try:
        tab = Tab(asked)
    except ValueError:
        abort(404)
    if tab not in _TABS_SERVED:
        abort(404)
    return tab


def _chip_href(account_id: int, chip) -> "str | None":
    """Return where a holding chip's count leads, or ``None``.

    **The route owns this and the service may not**: a chip states WHICH tab
    owns its lines (:attr:`~app.services.statement_match.Tab`), and turning
    that into a URL is the one fact a service is not allowed to build.

    **A chip naming a tab this build does not serve leads to the surface that
    still holds those acts.**  The *already explained* chip counts the acts
    ``X-gj-1c`` will put on the Explained tab; until then they are the
    register's, which is a live page with its own Undo -- so the count leads
    somewhere useful rather than nowhere, and ``X-gj-1c`` re-points it at the
    tab.

    Args:
        account_id: The account this page is about.
        chip: The :class:`~app.services.statement_match._reconcile.HoldingChip`.

    Returns:
        The URL, or ``None`` for a chip that states a fact with no way in --
        the lines older than the pay calendar, which no surface lists.
    """
    if chip.tab is None:
        return None
    if chip.tab in _TABS_SERVED:
        return url_for(
            "accounts.statement_reconcile",
            account_id=account_id, tab=chip.tab.value,
        )
    if chip.tab is Tab.EXPLAINED:
        return url_for("accounts.statement_register", account_id=account_id)
    return None


@dataclass(frozen=True)
class ReconcilePass:
    """What one press of Apply did, on both of the acts it can carry.

    **Two results because it is two ACTS**, and the developer's ruling of
    2026-08-19 is why they stay distinct: applying a card MOVES MONEY, and
    stating *always, for this merchant* moves none.  Folding them into one
    receipt would put "3 recorded" beside "2 merchants answered for" under one
    heading that could only be true of one of them.

    **One REQUEST and one transaction, though.**
    :func:`~app.services.statement_match.state_rules` does not commit, so both
    land or neither does -- which is what makes a rule stated about a
    destination the pass then refused impossible.

    Attributes:
        batch: The :class:`~app.services.statement_match.BatchOutcome` the
            money door applied.
        rules: What the rule door recorded
            (:class:`~app.services.statement_match.StatedRules`), or ``None``
            where the pass asked for none.
    """

    batch: object
    rules: object


@dataclass(frozen=True)
class _Answer:
    """What a render has to SAY, beyond the screen itself.

    A parameter object rather than four more arguments, because these four
    ARE one cohesive entity -- *what happened, and what could not* -- which is
    the distinction :func:`~._statement_doors.fragment_door`'s own docstring
    draws against its six independent facts.

    Attributes:
        outcome: The :class:`ReconcilePass` to report, or ``None`` on a plain
            render.
        error: A sentence explaining why nothing was applied at all, or
            ``None``.  Distinct from a refused ITEM: this one means the
            submission never reached the door.
        unacted: The sentence naming the cards the owner OK'd that named no
            act, or ``None``.
    """

    outcome: "ReconcilePass | None" = None
    error: "str | None" = None
    unacted: "str | None" = None


def _reconcile_context(account, scope, tab, answer: _Answer) -> dict:
    """Assemble what the Reconcile body renders, for the page and the POST.

    ONE builder, because the POST's answer IS the screen: a second assembly
    would let the surface a pass swaps in disagree with the surface a reload
    shows.

    Args:
        account: The owned, attached account.
        scope: The pass to render FROM
            (:class:`~app.services.statement_match.ReviewScope`).  **The
            caller supplies it, and a caller that has just WRITTEN must supply
            a fresh one**: a scope holds the rows and prices as they stood
            before the pass, which is exactly what must not be shown after it.
        tab: Which tab is open (:class:`~app.services.statement_match.Tab`).
        answer: What this render has to say (:class:`_Answer`).

    Returns:
        The template context.
    """
    page = reconcile_page(
        scope,
        # **The route builds the balance pass**, which is the rule every read
        # pass in this project is held to: only a route builds a
        # ``BalanceContext``, and ``bank_agreement`` needs one.
        bank_agreement.bank_agreement(
            account, balance_at.BalanceContext.build(scope.owner_id),
        ),
        tab,
    )
    return {
        "account": account,
        "page": page,
        # The tab bar, narrowed to what this build serves.  **Narrowed HERE
        # and not in Jinja**: a template restating a partition is a second
        # place for it to be wrong, which is the rule this whole package
        # keeps.
        "tabs": tuple(
            count for count in page.counts if count.tab in _TABS_SERVED
        ),
        "chips": tuple(
            (chip, _chip_href(account.id, chip)) for chip in page.chips
        ),
        # The picker the NEW-ENVELOPE arm needs.  Loaded here rather than
        # inside the page model because it is not a fact about the statement:
        # it is what any create form on this account offers, and
        # ``list_active_categories`` is the ordering every category picker in
        # the app already shares.
        "categories": list_active_categories(scope.owner_id),
        "outcome": None if answer.outcome is None else answer.outcome.batch,
        "rules": None if answer.outcome is None else answer.outcome.rules,
        "error": answer.error,
        "unacted": answer.unacted,
    }


def _answering(account, tab, unacted):
    """Return this door's own surface, as the callable every arm renders with.

    **A closure rather than a six-argument function**, which is the remedy a
    private helper over the limit takes here: what a door binds once -- the
    account, the tab and what it could not act on -- is bound once, and what
    varies per arm stays the two keywords
    :func:`~._statement_doors.run_statement_fragment_door` calls with.

    **ONE answer for all three outcomes**: a plain render, a receipt after a
    pass, and a refusal are the same page with a different thing to say.

    **A refusal is a designed 400** and carries the marker htmx needs, because
    htmx leaves a 4xx non-swapping: a refusal that renders NOTHING reads as a
    broken button, which is worse than the error it reports.  Which scope each
    arm renders from belongs to
    :func:`~._statement_doors.run_statement_fragment_door`, so "a pass is
    never reported against the state it replaced" is structural rather than
    remembered.

    Args:
        account: The owned, attached account.
        tab: Which tab is open.
        unacted: The sentence naming cards OK'd with no act named, or
            ``None``.

    Returns:
        ``(scope, *, outcome=None, error=None) -> response``.
    """
    def render(scope, *, outcome=None, error=None):
        """Render this door's surface from *scope*.

        Args:
            scope: The pass to render from.
            outcome: The :class:`ReconcilePass` to report, or ``None``.
            error: A sentence explaining why nothing was applied, or ``None``.

        Returns:
            The rendered body at 200, or the designed-fragment
            ``(body, 400, headers)`` triple when *error* is set.
        """
        body = render_template(
            _BODY,
            **_reconcile_context(
                account, scope, tab,
                _Answer(outcome=outcome, error=error, unacted=unacted),
            ),
        )
        return body if error is None else designed_error(body, 400)
    return render


@accounts_bp.route("/accounts/<int:account_id>/statements/reconcile")
@login_required
@require_owner
def statement_reconcile(account_id):
    """Render one tab of the Reconcile page.

    Args:
        account_id: The account to reconcile.

    Returns:
        The rendered page, or a 404 when the account is not the caller's, is a
        kind that has no bank statement, or the ``tab`` argument names no tab
        this build serves -- the security response rule's answer for both "not
        found" and "not yours".
    """
    account = load_cash_account_or_404(account_id)
    tab = _requested_tab()
    return render_template(
        "accounts/statement_reconcile.html",
        **_reconcile_context(
            account, ReviewScope.build(current_user.id, account_id), tab,
            _Answer(),
        ),
    )


def _rules_asked_for(submitted, asked, scope):
    """Return the standing rules this pass states, for the cards it applies.

    Ruling **bank_import:R-GI**, plan step ``bank_import:X-gj-1b``.  The ADD
    tab's *always, for this merchant* box says *and make this standing*, so
    the rule is read back off the DESTINATION the same card submitted
    (:func:`~app.services.statement_match.rule_naming`) rather than from a
    second wire value -- which is what makes it impossible for the rule and
    the purchase to name different budget lines.

    **Only a card that is being APPLIED states one.**  A tick on a card the
    owner did not OK is a rule about a destination they did not confirm, and
    the two halves are held together here because this is the only place that
    holds both.

    **An INCOME card states none** (ruling **bank_import:R-GW**): a merchant
    answer says where SPENDING goes, and a deposit is not spending, so no
    inflow reaches this loop at all -- ``submitted["incomes"]`` is not read.

    Args:
        submitted: What
            :class:`~app.schemas.validation.statements.StatementBatchSchema`
            loaded for this pass.
        asked: ``{line_id: merchant_id}``, what
            :class:`~app.schemas.validation.statement_reconcile
            .ReconcileRuleBatchSchema` loaded.
        scope: The pass, whose ``destinations`` are the offer set a chosen
            id is resolved against.  **Resolved against the SCOPE's own set
            rather than queried for**: it is already derived, and a second
            read could answer differently from the one the screen was drawn
            from.

    Returns:
        One :class:`~app.services.statement_match.RuleSubmission` per applied
        card the owner ticked, in the order the pass carries them.  A chosen
        destination the scope does not offer states nothing here and is
        refused by the money door on the same submission, which is the one
        place that refusal belongs.
    """
    offered = {
        destination.transaction_id: destination
        for destination in scope.destinations
    }
    statements = []
    for item in submitted["creations"]:
        merchant_id = asked.get(item["line_id"])
        if merchant_id is None:
            continue
        if item["destination"] == NEW_ENVELOPE:
            statements.append(rule_creating(
                merchant_id,
                NewEnvelope(
                    name=item["envelope_name"],
                    category_id=item["category_id"],
                ),
            ))
            continue
        destination = offered.get(item["destination"])
        if destination is not None:
            statements.append(rule_naming(merchant_id, destination))
    return tuple(statements)


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/reconcile", methods=["POST"],
)
@login_required
@require_owner
def apply_statement_reconcile(account_id):
    """Apply every card the owner OK'd, and answer with the screen.

    The unit of work is the REQUEST: each item runs inside its own SAVEPOINT
    so a refused one leaves nothing behind while the rest still land, and this
    route commits once -- so a failure outside a designed refusal writes
    nothing at all, which is what makes "nothing was changed" true rather than
    reassuring.

    **It carries TWO acts and one transaction.**  The money pass and the
    standing rules the ADD tab's *always* box asked for both run inside the
    one unit of work, because
    :func:`~app.services.statement_match.state_rules` does not commit either
    -- so a rule can never survive a pass that was rolled back, and a
    destination the door refused cannot be left standing as a rule.

    Args:
        account_id: The account being reconciled.

    Returns:
        The re-rendered body carrying the outcome, at 200; or the same body
        carrying one refusal sentence at 400, marked as a designed fragment so
        htmx swaps it (:mod:`app.utils.error_fragments`).
    """
    account = load_cash_account_or_404(account_id)
    tab = _requested_tab()

    # ONE derivation, built HERE.  Only a route builds a read pass -- the same
    # rule ``BalanceContext`` is held to -- and this one serves three
    # purposes: the door applies against it, either failure arm renders from
    # it, and the rules read their destinations off it.
    scope = ReviewScope.build(current_user.id, account_id)

    payload, silent = reconcile_payload(request.form)
    render = _answering(
        account, tab,
        None if not silent else _OK_WITH_NO_ACT.format(
            count=len(silent), lines=", ".join(silent),
        ),
    )
    errors = _batch_schema.validate(payload)
    rule_payload = reconcile_rules_payload(request.form)
    # **Both graders before either door**, so a malformed rule tick cannot
    # leave a money pass half-applied: a refusal here has written nothing at
    # all.  A malformed body is a pass-level refusal on purpose -- it is a
    # fact about the SUBMISSION rather than about an act the owner reviewed,
    # and no browser of ours produces one.
    errors = errors or _rules_schema.validate(rule_payload)
    if errors:
        return render(scope, error=refusal_sentence(errors))
    submitted = _batch_schema.load(payload)
    asked = {
        item["line_id"]: item["merchant_id"]
        for item in _rules_schema.load(rule_payload)["rules"]
    }

    def _apply():
        """Run both acts against *scope*, inside the caller's transaction.

        Returns:
            The :class:`ReconcilePass`.
        """
        statements = _rules_asked_for(submitted, asked, scope)
        return ReconcilePass(
            batch=apply_reviewed(submitted_batch(submitted), scope),
            rules=(
                None if not statements
                else state_rules(statements, current_user.id, account_id)
            ),
        )

    def _record(applied):
        """Log what this pass did, AFTER the commit that made it true.

        Args:
            applied: The :class:`ReconcilePass` the doors produced.
        """
        log_pass_applied(
            _logger, "A Reconcile pass was applied.",
            account_id=account_id,
            item_count=submitted_item_count(submitted),
            outcome=applied.batch,
        )

    return run_statement_fragment_door(
        fragment_door(
            _logger, render=render, scope=scope, account_id=account_id,
            act="apply a statement reconcile pass",
            db_error_message=_DB_ERROR_MESSAGE,
        ),
        _apply,
        _record,
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/reconcile/line/<int:line_id>/match",
    methods=["POST"],
)
@login_required
@require_owner
def statement_reconcile_match(account_id, line_id):
    """Answer what one card's MATCH tab offers, and what its ticks come to.

    Plan step ``bank_import:X-gj-1b``.  **It MOVES NO MONEY and writes
    nothing**, which is why it may fire on every tick and on every keystroke
    of the search box.  It runs the accept door's own reads and refusals
    through :func:`~app.services.statement_match.preview_hand_build` and
    renders the answer beside the rows; the only door that writes is
    :func:`apply_statement_reconcile`.

    **It takes the body Apply would send for THIS card**, read through the
    same :func:`~app.schemas.validation.statements.reconcile_match_payload`,
    so the difference on screen and the difference the door compares against
    are one derivation rather than two that agree by reading.

    **The rows it offers are the line's own pay period, or every unexplained
    row on the account when the owner searches** (:class:`~app.services
    .statement_match.MatchCandidates`).  The developer ruled that shape on
    2026-08-30, on the measurement in that class's own module: the period
    holds the payroll group a card is usually about, and the search is what
    keeps a card payment groupable against paybacks its period does not hold.

    Args:
        account_id: The account being reconciled.
        line_id: The bank line whose card is open.

    Returns:
        The re-rendered MATCH pane at 200; or the same pane carrying one
        refusal sentence at 400, marked as a designed fragment so htmx swaps
        it.
    """
    account = load_cash_account_or_404(account_id)
    scope = ReviewScope.build(current_user.id, account_id)
    review = review_set(scope)

    # **The line is found in the PASS rather than queried for**, so a
    # ``line_id`` naming someone else's line, one another match already
    # claims, or one this pass never offered has no card here and gets a 404.
    # It is a membership test rather than a second ownership check, for the
    # reason :func:`~.statement_workbench._preselected` gives: a check written
    # here would be a second statement of the one ``review_set`` applies.
    line = next(
        (one for one in review.unmatched if one.line_id == line_id), None,
    )
    if line is None:
        abort(404)

    payload = reconcile_match_payload(request.form, str(line_id))
    errors = _match_schema.validate(payload)
    if errors:
        return designed_error(
            render_template(
                _MATCH_PANE, account=account, line=line, rows=(),
                ticked=frozenset(), query="",
                totals=None, refusal=refusal_sentence(errors),
            ),
            400,
        )
    submitted = _match_schema.load(payload)
    query = request.form.get(f"q-{line_id}", "")
    candidates = MatchCandidates.of(scope, review)
    # NO event and NO commit.  Nothing was written, and a read pass that
    # logged would put a line in the audit trail for every checkbox on the
    # page.
    return render_template(
        _MATCH_PANE,
        account=account,
        line=line,
        rows=(
            candidates.matching(query) if query.strip()
            else candidates.for_line(line)
        ),
        # **Ticked by (kind, id) and re-rendered with the FRESH token.**  The
        # token carries the figure and revision the row was reviewed at, and
        # this fragment IS a fresh review of them: re-emitting the submitted
        # token would show the owner a row that has moved while telling the
        # door it had not.
        ticked=frozenset(
            (row.kind, row.row_id) for row in submitted["rows"]
        ),
        query=query,
        totals=preview_hand_build(submitted_match(submitted), scope),
        refusal=None,
    )
