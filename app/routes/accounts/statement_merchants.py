"""
Shekel Budget App -- Where your merchants go: the durable home for an answer

Plan step ``bank_import:X-gk``, ruling **bank_import:R-IC**.  One row per
merchant this account has ever seen, carrying its standing answer or *You have
not said*, edited one merchant at a time.

**It exists because the question had three PARTIAL homes and no whole one.**
Measured 2026-08-31 on a migrated clone of the developer's own database,
account 1: **62 merchants, 30 answered and 32 not**.  The review queue asks
about a merchant only while a line is waiting AND nobody has answered -- 0 rows
that day; the register shows the 30 answered; the Reconcile receipt offers a
rule only for a merchant the pass just filed spending for.  So **32 of 62 --
every unanswered one -- were on no surface at all**, and ``X-gj-1c`` retires
the register, which would have taken the other 30 down with it.

**IT MOVES NO MONEY and can move none.**  A rule is read to SUGGEST a
destination; the only thing that records a purchase is an explicit destination
submitted for one specific line (the developer's ruling of 2026-08-19, ruling
**R-FZ**).  So this page has no confirm, no undo and no receipt of acts -- only
a sentence saying what it recorded.

**It opens NO door of its own.**  The act is
:func:`~._statement_rules.record_submitted_rules`, which the review queue, the
register and the Reconcile receipt already post to, reading the same
:class:`~app.schemas.validation.merchant_rules.MerchantRuleBatchSchema` off the
same field names.  FOUR surfaces, one grader, one writer -- so an answer stated
here cannot be validated differently from the identical answer stated from the
receipt.

**ONE MERCHANT IS ON THE WIRE PER PRESS, and that is the point of the page**
(developer, 2026-08-31).  The register submits every merchant it renders, and
this arc has paid three times for the blast radius that gives one press: a
deactivated template made a select fall onto *I have not said* and the next
Save silently WITHDREW a rule; an archived category made another fall onto the
empty option, so a Save aimed at one merchant printed a refusal for a second
the owner never touched; and the incomplete-new-envelope short-circuit read
"nothing changed" for a third.  A form carrying one merchant puts that whole
class OUT OF REACH of the rendered page.

**"Out of reach", not "unconstructible", and the difference was measured.**
This paragraph claimed the stronger thing until an adversarial review built a
body naming two merchants and watched one land while the other was refused
(2026-08-31).  The door is shared with three surfaces that legitimately submit
many merchants, so it reads every ``rule-<key>`` the body carries; what this
page changes is what a BROWSER can send from it.  That is the honest claim, and
it is still the one that matters, because every one of the three defects above
was reached from a rendered form.

**The open row is a QUERY ARGUMENT and not a script.**  ``?edit=<merchant_id>``
renders that row's control server-side, so the page works with scripting off
and the htmx swap renders the identical markup from the identical template --
there is no second spelling of the control for the two paths to drift apart in.

Services boundary: this module owns the HTTP-shaped concerns -- ownership, the
query arguments, form parsing, fragment rendering -- and delegates every read
and write to :mod:`app.services.statement_match`.
"""

import logging
from dataclasses import replace

from flask import abort, render_template, request, url_for
from flask_login import current_user, login_required

from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.models.account import Account
from app.models.category import Category
from app.routes.accounts._statement_rules import (
    RuleOutcome,
    record_submitted_rules,
)
from app.services.category_service import list_active_categories
from app.services.statement_match import (
    DIRECTORY_LIMIT,
    DirectoryAsk,
    MerchantDirectory,
    MerchantWanted,
    StatedRules,
    merchant_directory,
)
from app.utils.auth_helpers import require_owner
from app.utils.digit_strings import parse_row_id
from app.utils.error_fragments import designed_error

_logger = logging.getLogger(__name__)

#: The partial the page and the answer door both render.  ONE template, so what
#: htmx swaps in after a POST cannot drift from what a reload shows -- the
#: discipline all four sibling statement surfaces keep.
_BODY = "accounts/_statement_merchants_body.html"

#: The query argument naming which filter is showing.
_FILTER_ARG = "show"

#: The query argument carrying what the owner typed in the search box.
_SEARCH_ARG = "q"

#: The longest search this page accepts, which is the SAME number the control
#: declares (``maxlength``) because the template renders it from here.
#:
#: **The server bounds what the control bounds, and one constant states it**
#: (adversarial security review 2026-08-31).  ``maxlength`` is a browser
#: courtesy: it stops typing and stops nothing else, so without this the server
#: accepted an input the UI said it would not send -- the mirror image of the
#: *chooser whose submission can never succeed* shape this package has closed
#: five times.  Over-long is REFUSED with the same 404 the two sibling
#: arguments take, because a value this page's own control cannot produce is a
#: tampered or stale request rather than a person mid-edit.
#:
#: 200 is past any merchant name the column can hold (``merchants.name`` is
#: ``varchar(100)``), so no refusal here can fall on a search that could have
#: matched.
SEARCH_MAX_LENGTH: int = 200

#: The query argument naming the merchant whose control is open.
_EDIT_ARG = "edit"

#: The query argument that lifts the row ceiling.  **A PRESENCE test and not a
#: value one**, exactly as the register's own is: the link either carries the
#: flag or it does not, so there is no spelling of it to parse and no value to
#: refuse.  What a crafted request can ask for is the page it would get by
#: following the link the page already renders.
_ALL_ARG = "all"


def _asked() -> DirectoryAsk:
    """Return what this request asked the directory for.

    **ONE reader for all four arguments and for both methods**, over
    ``request.values``: the GET carries them as query arguments and the POST's
    action URL carries them too, so a save answers with the view the owner was
    looking at.  Two readers would be two places for the answer to differ.

    Returns:
        The :class:`~app.services.statement_match.DirectoryAsk`.

    Raises:
        werkzeug.exceptions.NotFound: When the filter names no filter, the open
            row names no well-formed id, or the search is longer than the
            control that produces it can send
            (:data:`SEARCH_MAX_LENGTH`).  **A 404 rather than a rendered
            apology**, which is the answer
            :func:`~.statement_reconcile._requested_tab` already gives for the
            same shape: nothing composes this URL by hand, so a value that does
            not resolve is a tampered or stale request rather than a person
            mid-edit.  A well-formed id this account has never seen 404s one
            tier along, where the directory can say it opened nothing
            (:attr:`~app.services.statement_match._directory.MerchantDirectory
            .opened`).
    """
    wanted = request.values.get(_FILTER_ARG)
    opened = request.values.get(_EDIT_ARG)
    text = request.values.get(_SEARCH_ARG, "")
    if len(text) > SEARCH_MAX_LENGTH:
        abort(404)
    try:
        filtered = (
            MerchantWanted.ALL if wanted is None else MerchantWanted(wanted)
        )
    except ValueError:
        abort(404)
    if opened is not None:
        opened = parse_row_id(opened)
        if opened is None:
            abort(404)
    return DirectoryAsk(
        wanted=filtered,
        text=text,
        opened=opened,
        # ``None`` is the whole record.  The default is the ceiling, so a
        # caller that says nothing gets the bounded page.
        limit=None if _ALL_ARG in request.values else DIRECTORY_LIMIT,
    )


def _derived(
    account: Account, asked: DirectoryAsk,
    categories: "list[Category]",
) -> MerchantDirectory:
    """Derive everything the directory shows, once.

    Args:
        account: The owned, attached account.
        asked: What this render was asked for.
        categories: This owner's ACTIVE categories, already read for the open
            row's picker.  **The one read serves both**, so the phrase a closed
            row prints and the options its open row offers come from one list
            rather than two that could disagree inside one render.

    Returns:
        The :class:`~app.services.statement_match._directory.MerchantDirectory`.
    """
    return merchant_directory(
        current_user.id,
        account.id,
        {category.id: category.display_name for category in categories},
        asked,
    )


def _merchants_context(
    account: Account,
    directory: MerchantDirectory,
    asked: DirectoryAsk,
    categories: "list[Category]",
    outcome: RuleOutcome | None = None,
) -> "dict[str, object]":
    """Assemble what the directory body renders, for the page and the POST.

    ONE builder, because the POST's answer IS the screen: a second assembly
    would let the surface a save swaps in disagree with the surface a reload
    shows.

    **The derivation is the CALLER's and is passed in**, which is the rule all
    three sibling statement surfaces keep and for the sharper half of the same
    reason.  A refusal must render WITHOUT reading again: on the database arm
    the connection that produced the first error very likely produces a second,
    which escapes as an unhandled 500 that htmx will not swap -- so the owner
    presses Save and sees nothing at all.

    Args:
        account: The owned, attached account.
        directory: What to render (:func:`_derived`).
        asked: What this render was asked for, so the filter bar, the search
            box and every link on the page carry the view forward.
        categories: The picker the NEW-ENVELOPE answer needs, in the ordering
            every category picker in the app shares.
        outcome: What the door did
            (:class:`~._statement_rules.RuleOutcome`), or ``None`` on a plain
            render.  **The door's own value travels whole rather than being
            unpacked by each caller**: its two fields are exclusive by
            construction, so a screen carrying both a receipt and a refusal is
            unconstructible here rather than merely avoided.

    Returns:
        The template context.
    """
    return {
        "account": account,
        "directory": directory,
        "asked": asked,
        "categories": categories,
        # **EVERY URL ON THIS PAGE IS BUILT HERE**, which is the rule
        # :func:`~.statement_reconcile._chip_href` states for the same shape:
        # a service may not build a URL, and a template composing one would be
        # a second statement of which arguments carry the view.  Each of these
        # is the SAME page with one field of the ask changed, so the filter,
        # the search and the ceiling survive every click.
        "filters": tuple(
            (
                count,
                _page_url(
                    account.id,
                    replace(asked, wanted=count.wanted, opened=None),
                ),
                count.wanted is asked.wanted,
            )
            for count in directory.counts
        ),
        "rows": tuple(
            (
                entry,
                _page_url(
                    account.id,
                    replace(asked, opened=entry.summary.merchant_id),
                ),
            )
            for entry in directory.entries
        ),
        # CLOSE keeps the view and drops only the open row: it is the Cancel
        # link, and cancelling an edit should leave the owner exactly where
        # they were.
        "close_url": _page_url(account.id, replace(asked, opened=None)),
        # CLEAR drops the SEARCH as well, because it is the control beside the
        # search box.  **A URL of its own, and it was `close_url` until an
        # adversarial review pressed it**: that one keeps `q`, so "Clear"
        # reloaded the identical search -- a control rendered only when there
        # is something to clear, that cleared nothing.
        "clear_url": _page_url(
            account.id, replace(asked, opened=None, text=""),
        ),
        # THE WHOLE LIST drops the search AND the filter, because the sentence
        # it sits in says *whole list* and prints `directory.total`, which is
        # the ALL count.  It was `close_url` too, so the one escape offered to
        # an owner whose search matched nothing landed them back on the same
        # empty page.
        "whole_list_url": _page_url(
            account.id,
            replace(
                asked, opened=None, text="", wanted=MerchantWanted.ALL,
            ),
        ),
        # The search form is a GET and serialises its own controls, so its
        # action is BARE and the view rides as hidden inputs -- otherwise the
        # browser would drop the query string the action carried.
        "search_action": url_for(
            "accounts.statement_merchants", account_id=account.id,
        ),
        # **The search form's hidden controls EXCLUDE `q`**, and that exclusion
        # is the whole of it: the form already renders a visible control named
        # `q`, so carrying it here too submitted the argument TWICE and
        # ``request.values.get`` takes the FIRST -- which is the OLD term.
        # Typing a new merchant name and pressing Find re-ran the previous
        # search, forever, and clearing the box did not clear it.  Found by
        # adversarial review 2026-08-31; introduced by this step while removing
        # a string comparison from the template, which is a remedy that traded
        # one defect for a worse one.
        "search_args": {
            name: value for name, value in _view_args(asked).items()
            if name != _SEARCH_ARG
        },
        "search_max_length": SEARCH_MAX_LENGTH,
        # **The door's own URL carries the OPEN ROW**, which is what lets a
        # refusal re-render with the control still there.  It was built from
        # ``_view_args`` alone, which deliberately omits the open row -- so
        # every real POST read ``edit`` as absent, and the 400 body rendered
        # the whole list with no form at all.  That is exactly the defect
        # ``_recorded_nothing`` was written to fix: its status-code half
        # shipped and its row-open half did not.  Found by two adversarial
        # reviews 2026-08-31.
        "save_action": _door_url(account.id, asked),
        # ``None`` where nothing was withheld, so the footer states the bound
        # only when it BINDS -- a "show all" link over a complete list is a
        # control that changes nothing.
        "unbounded_url": (
            None if not directory.withheld_count
            else _page_url(account.id, replace(asked, limit=None))
        ),
        "rules": _receipt(outcome),
        "error": None if outcome is None else outcome.refusal,
    }


def _view_args(asked: DirectoryAsk) -> "dict[str, object]":
    """Return the query arguments that carry this view onto another URL.

    **The view rides on every link and on the door's own action**, which is the
    register's own rule for its ``all`` flag and is sharper here: without it a
    Save pressed under the *You have not said* filter answers with the whole
    list, and the page the owner was working reorganises under them.
    ``url_for`` drops a ``None`` argument, so an ordinary render composes the
    plain URL.

    Args:
        asked: What this render was asked for.

    Returns:
        The keyword arguments for :func:`~flask.url_for`, WITHOUT the open row
        -- which is :func:`_page_url`'s to add, because only some of these URLs
        want it.
    """
    return {
        _FILTER_ARG: (
            None if asked.wanted is MerchantWanted.ALL else asked.wanted.value
        ),
        _SEARCH_ARG: asked.text or None,
        _ALL_ARG: 1 if asked.limit is None else None,
    }


def _receipt(outcome: RuleOutcome | None) -> StatedRules | None:
    """Return what to PRINT about a pass, or ``None`` for nothing to say.

    **A pass that named no merchant has nothing to report**, and
    :class:`~app.services.statement_match.StatedRules` cannot say so on its
    own: ``state_rules(())`` returns ``StatedRules((), (), 0)``, which is a
    frozen dataclass and therefore TRUTHY, so a template asking
    ``{% if rules %}`` printed the receipt's no-op arm -- *"Nothing changed --
    that was already your answer"* -- for a submission that stated nothing and
    was about nobody.  Reachable only by a crafted body, so the cost is a
    misleading sentence rather than a wrong write.  Found by adversarial review
    2026-08-31.

    **Asked here rather than in Jinja** because it is a question about what the
    door did, and a template deciding it would be a second reader of the
    door's own value.

    Args:
        outcome: What :func:`~._statement_rules.record_submitted_rules`
            returned, or ``None`` on a plain render.

    Returns:
        The recorded value when it has something to say -- an answer stated, a
        refusal, or a restatement that changed nothing -- else ``None``.
    """
    if outcome is None or outcome.recorded is None:
        return None
    recorded = outcome.recorded
    if recorded.stated or recorded.refused or recorded.unchanged_count:
        return recorded
    return None


def _recorded_nothing(outcome: RuleOutcome) -> bool:
    """Return whether this press wrote nothing and something refused it.

    **A per-item refusal is a refused PRESS here, and that is a property of
    what this page RENDERS rather than of the door.**  Its three sibling
    surfaces submit many merchants at once, so a refused item there sits beside
    items that landed and the pass is a partial success answered at 200; the
    form this page renders carries ONE merchant, so from it "some landed and
    some did not" cannot arise and a refusal means the answer was not recorded.

    **It is NOT unconstructible, and three docstrings said it was until an
    adversarial review reproduced it** (2026-08-31).
    :func:`~app.schemas.validation.merchant_rules.rule_payload` harvests every
    field beginning ``rule-`` up to ``_MAX_RULE_ITEMS`` (2,000), so a CRAFTED
    body states several merchants at once and a mixed pass follows.  The
    behaviour is still right -- a mixed pass has a non-empty ``stated``, so
    this returns ``False`` and both lists render at 200 -- but the reason had
    been written as a property of the DOOR when it is a property of the FORM,
    which is one writer away from false and is the shape this project's own
    lessons name.

    **What it fixes is not cosmetic.**  Reading only
    :attr:`~._statement_rules.RuleOutcome.refusal` -- the arm for a submission
    that never reached the door -- reported the ordinary refusals as SUCCESS:
    ``state_rules`` runs each statement in its own savepoint and reports the
    refusal on :class:`~app.services.statement_match.StatedRules` instead of
    raising, so a new-envelope answer missing its category answered 200, the
    route re-derived with the row CLOSED, and the owner lost the half-written
    answer they were being asked to correct.  Found by this route's own case
    2026-08-31.

    Args:
        outcome: What :func:`~._statement_rules.record_submitted_rules`
            returned.

    Returns:
        Whether the press recorded nothing that was asked of it.  **An
        UNCHANGED answer is not nothing**: restating what is already stored
        states no sentence and refuses none, and it is a successful press
        whose row closes on the answer it already held.
    """
    if outcome.refusal is not None:
        return True
    recorded = outcome.recorded
    return bool(recorded.refused) and not recorded.stated


def _door_url(account_id: int, asked: DirectoryAsk) -> str:
    """Return the answer door's URL under *asked*.

    :func:`_page_url`'s twin, on the other endpoint and for the same reason:
    the door answers with this page, so it has to be told which page.

    Args:
        account_id: The account being answered for.
        asked: The view the form was rendered under, INCLUDING its open row --
            without which a refusal cannot re-render the control the owner was
            using.

    Returns:
        The URL.
    """
    return url_for(
        "accounts.answer_for_merchant",
        account_id=account_id, **_view_args(asked),
        **{_EDIT_ARG: asked.opened},
    )


def _page_url(account_id: int, asked: DirectoryAsk) -> str:
    """Return this page's own URL under *asked*.

    Args:
        account_id: The account being answered for.
        asked: The view to compose, with whatever field the caller changed
            already replaced.

    Returns:
        The URL, carrying the open row as well as the view -- so an Edit link
        is this page plus one field, and a Close link is this page minus it.
    """
    return url_for(
        "accounts.statement_merchants",
        account_id=account_id, **_view_args(asked),
        **{_EDIT_ARG: asked.opened},
    )


@accounts_bp.route("/accounts/<int:account_id>/statements/merchants")
@login_required
@require_owner
def statement_merchants(account_id):
    """Render every merchant this account has seen, and what was said.

    Args:
        account_id: The account whose merchants to list.

    Returns:
        The rendered page, or a 404 when the account is not the caller's, is a
        kind that has no bank statement, or a query argument names no filter
        and no merchant of this account -- the security response rule's answer
        for both "not found" and "not yours".
    """
    account = load_cash_account_or_404(account_id)
    asked = _asked()
    categories = list_active_categories(current_user.id)
    directory = _derived(account, asked, categories)
    # **A merchant this account has never seen opens NO row**, and the honest
    # answer is the same 404 a bad filter gets rather than a page that silently
    # ignores half its own URL.  Asked of the DIRECTORY rather than with a
    # query of its own, because the directory has already read this account's
    # merchants and a second read could answer differently.
    if asked.opened is not None and directory.opened is None:
        abort(404)
    return render_template(
        "accounts/statement_merchants.html",
        **_merchants_context(account, directory, asked, categories),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/merchants", methods=["POST"],
)
@login_required
@require_owner
def answer_for_merchant(account_id):
    """Record where ONE merchant's spending goes.

    **It MOVES NO MONEY and can move none** -- the same ruling of 2026-08-19
    every sibling rule door is held to.

    **An answer is never WITHDRAWN, only restated** (ruling **R-GS**), so this
    door cannot empty the directory: every merchant it renders keeps its row
    whatever is submitted, and a merchant with no answer simply still has none.

    Args:
        account_id: The account being answered for.

    Returns:
        The re-rendered body carrying what was recorded, at 200; or the same
        body carrying the refusal at 400 with the row still OPEN, marked as a
        designed fragment so htmx swaps it.  Which of the two a refusal takes
        is :func:`_recorded_nothing`'s to say, and BOTH kinds reach it: the
        one that never got past the grader, and the ordinary per-item one the
        door reports rather than raises.
    """
    account = load_cash_account_or_404(account_id)
    asked = _asked()
    categories = list_active_categories(current_user.id)
    # BEFORE the write, and reused by the refusal arm.  A refused pass wrote
    # nothing, so what was derived before it still describes the state that
    # survives -- and re-deriving would run the very read whose failure that
    # arm may be handling.  It keeps the row OPEN, because a refusal is
    # something the owner fixes in the control they were using.
    before = _derived(account, asked, categories)

    outcome = record_submitted_rules(
        request.form, current_user.id, account_id, _logger,
    )
    if _recorded_nothing(outcome):
        return designed_error(
            render_template(
                _BODY,
                **_merchants_context(
                    account, before, asked, categories, outcome,
                ),
            ),
            400,
        )
    # A FRESH derivation, because this pass CHANGED what the page states: the
    # row's phrase, the three filter counts, and which rows the filter holds.
    # The open row CLOSES -- the answer has been given, and the receipt above
    # says what it was.
    settled = replace(asked, opened=None)
    return render_template(
        _BODY,
        **_merchants_context(
            account, _derived(account, settled, categories), settled,
            categories, outcome,
        ),
    )
