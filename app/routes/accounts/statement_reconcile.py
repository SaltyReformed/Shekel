"""
Shekel Budget App -- The RECONCILE page, and the three doors it posts to

"What is each of these bank lines?" -- one page on four verbs, replacing the
review queue, the register and the hand-build workbench.  Plan step
``bank_import:X-gj-1b``; the direction was locked at Loop A round 4 on
2026-08-29 and is ``docs/design/bank_import_audit.md``.  Its rulings are
**bank_import:R-HP** through **R-HX** in ``docs/plans/rulings.md``, plus
**bank_import:R-IA** (the accept door exempts no shape) and
**bank_import:R-IB** (a standing rule is offered once per merchant, on the
receipt).  **Both ids were minted in the ``balance`` arc the same day**, so
every citation of them here names its arc.

**IT MOVES MONEY, through doors that already exist.**  Apply posts the OK'd
cards through :func:`~app.services.statement_match.apply_reviewed` -- the same
door the review queue and the workbench use, with the same savepoint-per-item
policy (**R-FZ(a)**) and the same receipt -- and the RECEIPT's
per-merchant standing-rule offer posts through
:func:`~._statement_rules.record_submitted_rules`, which moves none.  **This
module opens no door of its own**, which is what lets a whole screen ship
without a migration and without a new money path.

**Four routes, and only ONE of them re-renders without writing.**  The page and
Apply are the pair; the standing-rule offer's own door writes
``budget.merchant_rules`` and commits (ruling **bank_import:R-IB**); and the
MATCH pane re-renders one card's candidate rows and what the ticked ones come
to, writing nothing.  *(This paragraph said "three routes, and the third is a
READ" until **bank_import:R-IB** added the rule door, at which point the third
route in file order was the one that WRITES -- a reader counting routes would
have mapped the sentence onto the wrong one.)*  It is a POST for the reason
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
from app.routes.accounts._statement_rules import record_submitted_rules
from app.schemas.validation.statement_reconcile import (
    reconcile_match_payload,
    reconcile_payload,
)
from app.schemas.validation.statements import (
    StatementBatchSchema,
    StatementMatchSchema,
)
from app.services import balance_at, bank_agreement
from app.services.category_service import list_active_categories
from app.services.statement_match import (
    MatchCandidates,
    ReviewScope,
    RuleDoorAccepts,
    Tab,
    apply_reviewed,
    preview_hand_build,
    reconcile_page,
    review_set,
    rules_worth_offering,
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
        the lines older than the pay calendar, which no surface lists and
        whose remedy is a pay schedule rather than an account.
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
    """What one press of Apply did, and what it earns the right to ask.

    **ONE act now, and that is ruling bank_import:R-IB.**  This carried two --
    the money pass and the standing rules the card's *always, for this
    merchant* box asked for -- until the developer's ruling of 2026-08-30
    moved the rule offer onto the RECEIPT.  The two were run in one
    transaction so a rule could not survive a rolled-back pass; what the
    ruling saw is that the rule was derived from what was OK'd rather than
    from what LANDED, so a per-item refusal rolling back in its own savepoint
    left the rule standing anyway.  There is no second write here to sequence.

    Attributes:
        batch: The :class:`~app.services.statement_match.BatchOutcome` the
            money door applied.
        offers: One :class:`~app.services.statement_match.RuleOffer` per
            merchant whose purchases this pass RECORDED, in the order it
            applied them -- what the receipt may ask the owner to make
            standing.  **Derived from the outcome and never from the
            submission**, which is what makes a rule for a refused creation
            unconstructible rather than merely avoided.  Empty for a pass that
            filed no spending.
    """

    batch: object
    offers: tuple


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
        rules: What the RULE door recorded
            (:class:`~app.services.statement_match.StatedRules`), or ``None``
            on every render but the one answering a press of the receipt's own
            offer.  **A separate field from ``outcome`` because it is a
            separate ACT** -- stating where a merchant goes moves no money --
            and the two never travel together: a money pass earns the offer,
            and pressing the offer is the next request.
    """

    outcome: "ReconcilePass | None" = None
    error: "str | None" = None
    unacted: "str | None" = None
    rules: object = None


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
        "offers": () if answer.outcome is None else answer.outcome.offers,
        "rules": answer.rules,
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
    def render(scope, *, outcome=None, error=None, rules=None):
        """Render this door's surface from *scope*.

        Args:
            scope: The pass to render from.
            outcome: The :class:`ReconcilePass` to report, or ``None``.
            error: A sentence explaining why nothing was applied, or ``None``.
            rules: What the RULE door recorded, or ``None``.  Set only by
                :func:`state_reconcile_merchant_rules`, which answers with
                this same surface because pressing the receipt's offer leaves
                the owner looking at the page they were already on.

        Returns:
            The rendered body at 200, or the designed-fragment
            ``(body, 400, headers)`` triple when *error* is set.
        """
        body = render_template(
            _BODY,
            **_reconcile_context(
                account, scope, tab,
                _Answer(
                    outcome=outcome, error=error, unacted=unacted,
                    rules=rules,
                ),
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

    **It carries ONE act, and it used to carry two.**  The ADD tab had an
    *always, for this merchant* checkbox whose rules were written in this same
    transaction, so that a rule could not survive a rolled-back pass.  Ruling
    **bank_import:R-IB** (2026-08-30) moved that offer onto the RECEIPT, where
    it is asked once per merchant about what the door actually APPLIED: the
    rule was derived from what was OK'd and computed BEFORE this door ran, so
    a per-item refusal rolling back inside its own savepoint left the rule
    standing for a purchase that had not happened.  What this route now does
    with the outcome is EARN the offer (:func:`~app.services.statement_match
    .rules_worth_offering`); pressing it is the next request, through
    :func:`state_reconcile_merchant_rules`.

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
    # it, and the receipt's offer reads its destinations off it.
    scope = ReviewScope.build(current_user.id, account_id)

    payload, silent = reconcile_payload(request.form)
    render = _answering(
        account, tab,
        None if not silent else _OK_WITH_NO_ACT.format(
            count=len(silent), lines=", ".join(silent),
        ),
    )
    # **The grader runs before the door**, so a malformed body has written
    # nothing at all.  A malformed body is a pass-level refusal on purpose --
    # it is a fact about the SUBMISSION rather than about an act the owner
    # reviewed, and no browser of ours produces one.
    errors = _batch_schema.validate(payload)
    if errors:
        return render(scope, error=refusal_sentence(errors))
    submitted = _batch_schema.load(payload)
    # **Only a pass that FILES SPENDING can earn a standing-rule offer**, so a
    # matches-only or income-only press does not pay this: ``review_set`` is
    # measured at 0.136 s and ``rules_worth_offering`` would read nothing from
    # it (ruling **bank_import:R-GW** -- a merchant answer says where SPENDING
    # goes, so no inflow reaches that loop).
    review = review_set(scope) if submitted["creations"] else None

    def _apply():
        """Apply the pass against *scope*, inside the caller's transaction.

        Returns:
            The :class:`ReconcilePass` -- what landed, and what that earns the
            right to ask about.
        """
        outcome = apply_reviewed(submitted_batch(submitted), scope)
        return ReconcilePass(
            batch=outcome,
            # **From what the door APPLIED, which is the whole of
            # ``bank_import:R-IB``'s first half.**  ``AppliedItem.line_ids``
            # is documented as a
            # correlation key for exactly this: saying WHICH submitted item an
            # outcome belongs to.  Reading the submission instead is what
            # offered a standing rule for a creation the door had refused.
            offers=() if review is None else rules_worth_offering(
                submitted["creations"],
                frozenset(
                    line_id
                    for item in outcome.applied for line_id in item.line_ids
                ),
                review,
                scope,
                # **What the RULE door would take, which is not what this pass
                # can file into.**  Read here because the service answers no
                # query, and read from the two producers the door itself
                # validates against, so the offer cannot render a press that
                # can never succeed.
                RuleDoorAccepts(
                    # **The pass already holds the template set**, because the
                    # review queue's own merchant control renders it -- so
                    # this is ``offerable_templates``' answer without a second
                    # call to it, which is the DRY rule this package applies
                    # to producer calls inside one request.
                    template_ids=frozenset(
                        template_id
                        for template_id, _ in review.merchants.templates
                    ),
                    category_ids=frozenset(
                        category.id
                        for category in list_active_categories(scope.owner_id)
                    ),
                ),
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
    "/accounts/<int:account_id>/statements/reconcile/merchants",
    methods=["POST"],
)
@login_required
@require_owner
def state_reconcile_merchant_rules(account_id):
    """Record the standing answers the receipt's offer asked for.

    Plan step ``bank_import:X-gj-1b``, ruling **bank_import:R-IB**.  **It MOVES
    NO MONEY and can move none**, which is why it is its own door and its own
    request: a rule is read to SUGGEST a destination, and the only thing that
    records a purchase is an explicit destination on one specific line.

    **It opens NO door of its own.**  The act is
    :func:`~._statement_rules.record_submitted_rules`, which the review queue
    and the register already post to, reading the same
    :class:`~app.schemas.validation.merchant_rules.MerchantRuleBatchSchema` off
    the same field names.  Three surfaces, one grader, one writer -- so a rule
    stated from the Reconcile receipt cannot be validated differently from the
    identical rule stated from the register, which is what a second door here
    would have made possible.  What differs is only the SURFACE each answers
    with, which is exactly the split that module exists for.

    **The offer this answers was earned by a money pass** and named only
    merchants that pass actually filed spending for
    (:func:`~app.services.statement_match.rules_worth_offering`).  Nothing
    holds the two requests together, and nothing needs to: a merchant answer is
    a preference about the FUTURE, so stating one for a purchase recorded a
    minute ago is the same act as stating it a week later from the register.

    Args:
        account_id: The account being reconciled.

    Returns:
        The re-rendered body carrying what was recorded, at 200; or the same
        body carrying one refusal sentence at 400, marked as a designed
        fragment so htmx swaps it.
    """
    account = load_cash_account_or_404(account_id)
    tab = _requested_tab()

    # ONE derivation, built BEFORE the write and still valid after it, for the
    # reason :func:`~.statement_matches.state_merchant_rules` states at its
    # own: this door writes exactly one table, ``budget.merchant_rules``,
    # through the ORM and calls no service -- so nothing it can do touches the
    # calendar, the candidates or their prices, and ``review_set`` re-reads the
    # rules themselves.  That is a closed argument over one table rather than
    # an enumeration over an open set of writers.
    scope = ReviewScope.build(current_user.id, account_id)
    render = _answering(account, tab, None)

    outcome = record_submitted_rules(
        request.form, current_user.id, account_id, _logger,
    )
    if outcome.refusal is not None:
        return render(scope, error=outcome.refusal)
    return render(scope, rules=outcome.recorded)


# **POST-only deliberately.**  Giving this route a ``GET`` would enrol
# ``line_id`` in ``test_no_baseline_policy``'s url_map sweep, which grades
# every ``GET`` rule and demands a row per converter it finds.
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
    #
    # **Asked of ``card_subject`` and never of ``unmatched``**, which is what
    # this read once asked and what made every PROPOSED card's pane a
    # permanent spinner: ``_unexplained`` takes a proposal's line out of
    # ``unmatched`` before that list exists, so 137 of the developer's 137
    # proposal cards answered 404 here.  The set a card is drawn from is the
    # pass's own fact and lives beside the two lists it unions.
    subject = review.card_subject(line_id)
    if subject is None:
        abort(404)
    line = subject.line

    payload = reconcile_match_payload(request.form, str(line_id))
    errors = _match_schema.validate(payload)
    if errors:
        return designed_error(
            render_template(
                _MATCH_PANE, account=account, line=line,
                proposal=subject.proposal, rows=(),
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
        # **The tier's own rows travel with the pane**, so unticking one
        # re-prices like any other: they were rendered by the CARD until plan
        # step ``bank_import:X-gj-1b`` -- a sibling of this fragment, outside
        # the element whose change fires it -- so an untick reached no trigger
        # and the panel went on stating a difference that was no longer true.
        proposal=subject.proposal,
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
