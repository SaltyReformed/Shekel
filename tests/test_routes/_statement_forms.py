"""What a BROWSER would submit from a statement form: read off it, or built.

Plan step ``bank_import:X-gf-2`` moved this out of
``test_statement_matches.py``: the merchant-rule control is rendered on two
surfaces now (ruling **bank_import:R-GX**) -- the review QUEUE for a merchant
with no answer, and the REGISTER for one already answered -- and both test
modules have to read what their own page emits.

**A browser submits every control it renders, at the value it renders**, and
that is the fact a hand-written payload cannot check: it is written by the same
person as the template, so the two agree about a mistake as readily as about
the truth.  This arc has paid for that twice -- a hand-picked subset shipped a
destination arm that was DEAD in a browser at plan step X-f6a-3b, found by
three adversarial reviews.

**The payload BUILDERS are here for the same reason as the readers.**  A
register test needs an accepted act to exist and a queue test needs one not to,
and both make one through the review screen's own APPLY form -- so the field
names that form emits are stated once rather than copied into the second
module.
"""

from html.parser import HTMLParser

from app.services.statement_match import RowKind
from app.services.statement_match._sides import MatchSides
from app.services.statement_match._submission import ReviewedRow, spell_figure
from tests.test_services.test_statement_match._builders import (
    a_reviewed_token,
)

#: What :func:`match_item` uses when a caller says nothing about the consent:
#: derive the figure the proposal card renders.  **A sentinel and not
#: ``None``**, because ``None`` is a real thing a body can say -- the field
#: absent altogether -- and a case modelling a crafted or stale submission has
#: to be able to say it.  Plan step ``bank_import:X-gj-1b``.
DERIVE_THE_STATED_FIGURE = object()


class RuleFormReader(HTMLParser):
    """Collect the rule form's controls and their RENDERED values.

    **A browser submits every control it renders, at the value it renders**, and
    that is the fact a hand-written payload cannot check -- it is written by the
    same person as the template, so the two agree about a mistake as readily as
    about the truth.  This reads the page instead.

    A ``<select>`` submits the option carrying ``selected``, and its FIRST
    option when none does; an ``<input>`` submits its ``value``.  Only controls
    whose name begins with ``rule`` are collected, because the review body
    holds three forms and a browser posts one at a time.
    """

    def __init__(self, prefixes=("rule",)):
        super().__init__()
        self.prefixes = prefixes
        self.controls = {}
        self._select = None
        self._first = None

    def _mine(self, name):
        """Return whether *name* belongs to the form being read."""
        return any(name.startswith(prefix) for prefix in self.prefixes)

    def handle_starttag(self, tag, attrs):
        """Record an input's value, or open a select and read its options."""
        attributes = dict(attrs)
        name = attributes.get("name", "")
        if tag == "input" and self._mine(name):
            if attributes.get("type") == "checkbox" and (
                "checked" not in attributes
            ):
                # An unticked checkbox submits NOTHING, which is the whole
                # point of the default this screen rests on.
                return
            self.controls[name] = attributes.get("value", "")
        elif tag == "select" and self._mine(name):
            self._select, self._first = name, None
        elif tag == "option" and self._select is not None:
            value = attributes.get("value", "")
            if self._first is None:
                self._first = value
            if "selected" in attributes:
                self.controls[self._select] = value

    def handle_endtag(self, tag):
        """Close a select, defaulting it to its first option if none was set."""
        if tag == "select" and self._select is not None:
            self.controls.setdefault(self._select, self._first or "")
            self._select = None


def rule_form_controls(page):
    """Return what a browser would submit for the merchant-rule form."""
    reader = RuleFormReader()
    reader.feed(page)
    return reader.controls


def match_item(
    index=0, lines=(), transactions=(), entries=(),
    residual=DERIVE_THE_STATED_FIGURE, scope=None,
):
    """Return the form fields a TICKED match item submits.

    The FIELD NAMES ``_statement_review_body.html`` emits: the tick names the
    item's rendered position, one hidden input carries each bank line, and one
    carries each ROW as the screen showed it -- kind, id, figure and revision
    (plan step ``bank_import:X-f6d-3``).  **The row VALUES are built through
    the service, not scraped**, so this helper cannot show that the template
    renders them; :class:`TestWhatTheTEMPLATEEmittedIsWhatTheDOORAccepts` is
    what does, by posting the page's own bytes back.  That last is what makes *what commits
    is what was reviewed* (ruling **R-FP**) checkable rather than intended:
    the door refuses an item whose row moved since the render.

    **Every index it emits is a rendered POSITION**, and until plan step
    ``bank_import:X-gf-3b`` one was not: the hand-build form shared the review
    screen and submitted the reserved index ``"hand"``.  That form is a surface
    of its own now and posts through :func:`hand_match`, which carries no index
    at all.

    Args:
        index: The item's rendered position.
        lines: Bank line rows it explains.
        transactions: Transaction rows that explain them.
        entries: Purchase rows that explain them.
        residual: What this item states as the difference it was reviewed
            against (plan step ``bank_import:X-f6d-4``).  **Left alone it is
            DERIVED**, exactly as the template derives it: since plan step
            ``bank_import:X-gj-1b`` a proposal card renders
            ``match-<i>-residual`` as a HIDDEN input carrying
            the ``stated_difference`` filter, so a browser submits it on
            every ticked item and there is no state in which that field is
            absent from this surface.  A default of ``None`` modelled the form
            as it stood BEFORE that step, and would make every caller here
            post a body no page emits -- which is the defect this module
            exists to prevent.

            Pass a value to model a STALE or crafted body (a screen that
            summed wrong, or one whose rows moved after it was drawn), and
            pass ``None`` to model the field absent outright, which the door
            refuses whenever there is a difference to write.
        scope: The pass whose render this item is standing in for, or ``None``
            for one derived per row.  **A page renders ONE scope and emits
            every item off it**, so a case building several items for a single
            Apply passes the same one -- which is both faithful to what the
            screen does and the difference between one derivation and one per
            row.  :func:`~tests.test_services.test_statement_match._builders
            .a_reviewed_token` carries the freshness contract: the scope must
            be derived after every row it will be asked about is staged.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    fields = {
        "apply": [str(index)],
        f"match-{index}-line_ids": [str(line.id) for line in lines],
        f"match-{index}-rows": (
            [a_reviewed_token(txn, RowKind.TRANSACTION, scope)
             for txn in transactions]
            + [a_reviewed_token(entry, RowKind.PURCHASE, scope)
               for entry in entries]
        ),
    }
    if residual is DERIVE_THE_STATED_FIGURE:
        # **Through the service, over the very tokens this item submits.**
        # The template computes the same subtraction from the same priced
        # rows (the ``stated_difference`` filter), so the two cannot
        # disagree about what the card disclosed -- which is the reason this
        # helper builds its row values through ``as_reviewed`` rather than
        # spelling them.
        residual = spell_figure(MatchSides.of(
            lines,
            [ReviewedRow.from_token(one)
             for one in fields[f"match-{index}-rows"]],
        ).difference)
    if residual is not None:
        fields[f"match-{index}-residual"] = [str(residual)]
    return fields


def hand_match(lines=(), transactions=(), entries=(), residual=None):
    """Return the form fields the WORKBENCH's hand-build form submits.

    Plan step ``bank_import:X-gf-3b``, ruling **bank_import:R-HC**.  The FIELD
    NAMES ``_statement_workbench_body.html`` emits, which are
    :func:`match_item`'s without an index: that form's whole submission IS one
    group, so there is nothing to tick that is not already a member and nothing
    for a name to be qualified by.

    **The absent index is why this helper exists rather than a keyword on
    :func:`match_item`.**  The two are not one payload with a flag: they reach
    two doors, are graded by two payload readers
    (``batch_payload`` and ``hand_match_payload``), and the whole point of the
    step that split them is that no submission can carry both shapes at once.
    A helper that emitted either from one call would be the shared namespace
    the split deleted, rebuilt in the tests.

    **The row VALUES are built through the service, not scraped**, so this
    helper cannot show that the template renders them;
    ``test_the_HAND_BUILD_form_s_own_token_is_graded_too`` is what does, by
    posting the page's own bytes back.

    Args:
        lines: Bank line rows the group explains.
        transactions: Transaction rows that explain them.
        entries: Purchase rows that explain them.
        residual: What the consent box carries when the owner ticked it -- the
            difference the screen showed (plan step ``bank_import:X-f6d-4``).
            ``None`` leaves the field off entirely, which is what an unticked
            checkbox submits.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    fields = {
        "line_ids": [str(line.id) for line in lines],
        "rows": (
            [a_reviewed_token(txn, RowKind.TRANSACTION)
             for txn in transactions]
            + [a_reviewed_token(entry, RowKind.PURCHASE) for entry in entries]
        ),
    }
    if residual is not None:
        fields["residual"] = [str(residual)]
    return fields


def one_pass(*parts):
    """Merge several items' fields into ONE submitted form.

    **``apply`` is a REPEATED key, so merging is a union rather than an
    update.**  A plain ``dict.update`` overwrites it, which silently leaves one
    item ticked out of however many were meant -- and every assertion about
    what landed then grades a pass that was never submitted.  Found by writing
    exactly that and watching four items become two.

    Args:
        *parts: The per-item field dicts from :func:`match_item` /
            :func:`record_line`.

    Returns:
        The merged form, list values concatenated.
    """
    merged = {}
    for part in parts:
        for key, value in part.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            else:
                merged[key] = value
    return merged


def record_line(line, *, destination, name="Walmart", category_id=""):
    """Return the form fields ONE creatable line submits.

    **The name and the category are always submitted**, whichever destination
    was picked, because a browser submits every control it renders -- which is
    the fact a hand-picked payload hid at plan step X-f6a-3b.  The SELECT is
    what says which arm was chosen.

    Args:
        line: The bank line row.
        destination: ``"new"``, an envelope id, or ``""`` to leave it alone --
            which is the select's own default.
        name: What the name box carries.
        category_id: What the category select carries; ``""`` is its default,
            because the category is a decision rather than a default.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    return {
        f"destination-{line.id}": str(destination),
        f"envelope_name-{line.id}": name,
        f"category_id-{line.id}": str(category_id),
    }


def rule_item(index, merchant_id, *, answer, name="", category_id=""):
    """Return the form fields ONE merchant row of the rule section submits.

    **Every control the row renders**, whichever answer was picked, because a
    browser submits every control it renders -- the fact a hand-picked payload
    hid at plan step X-f6a-3b, applied to the section this leaf adds.

    Args:
        index: The row's rendered position, which is what keys its fields.
        merchant_id: The merchant ROW the hidden input carries (plan step
            ``bank_import:X-gd-1``); it was the bank's own string until then.
        answer: ``"unset"`` (I have not said), ``"never"``, ``"ask"``,
            ``"new"``, or ``"t:<template_id>"``.
        name: What the envelope-name box carries.
        category_id: What the category select carries; ``""`` is its default.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    return {
        f"rule-{index}": str(answer),
        f"rule_merchant-{index}": str(merchant_id),
        f"rule_name-{index}": name,
        f"rule_category-{index}": str(category_id),
    }


class ReconcileFormReader(HTMLParser):
    """Collect the RECONCILE page's controls, exactly as a browser would.

    Plan step ``bank_import:X-gj-1b``.  **A browser submits every control it
    renders, at the value it renders**, and that is the fact a hand-written
    payload cannot check -- it is written by the same person as the template,
    so the two agree about a mistake as readily as about the truth.  This arc
    has paid for that twice.

    **It keeps REPEATED names**, unlike :class:`RuleFormReader`: ``ok`` is
    rendered once per OK'd card and ``rows-<line>`` once per member row, and a
    group is exactly where a multi-value defect hides.

    **An unticked checkbox and an unchecked radio contribute NOTHING**, which
    is the whole of what makes ruling **R-HS**'s *an untouched card is not
    submitted* structural rather than a default: a page rendered with no card
    OK'd posts no ``ok`` at all, so no act can be built from it.

    **A ``disabled`` control is dropped too**, which the consent box depends
    on: the MATCH pane renders it ``value=""`` and ``disabled`` in lockstep
    until the server has a figure, so a browser cannot send an empty consent.

    **It also records what a browser COULD submit, not only what it would.**
    :attr:`offerable` holds every control the page rendered -- an unticked
    checkbox and an unchecked radio included -- as the ``(name, value)`` pair
    that control would send if the owner pressed it, or as ``(name, None)``
    for a field whose value the owner supplies.  That set is what
    :func:`~tests.test_routes.test_statement_reconcile._post` refuses a
    payload against, and it exists because of a defect a green suite hid:
    every acting case appended ``("ok", str(line.id))`` by hand, and the
    ``ok-<line>`` checkbox is rendered only for a card that suggests a working
    verb -- so 31 of the developer's 248 cards had a panel button pointing at
    an element not in the document, and no test could see it because the
    tests posted a value no browser could produce.

    **The pair and not the name**: ``ok`` is ONE name shared by every card,
    keyed by its VALUE, so a check that only asked whether the page renders
    ``ok`` anywhere would pass the very payload that hid the defect.
    """

    def __init__(self):
        super().__init__()
        self.fields = []
        self.offerable = set()
        self._select = None
        self._first = None
        self._chosen = None

    def handle_starttag(self, tag, attrs):
        """Record every control this page would submit."""
        attributes = dict(attrs)
        # **An ``<option>`` carries no name of its own**, so it is read before
        # any test on one -- a first version guarded on ``name`` up here and
        # every select therefore submitted its FIRST option, which read as a
        # template that had stopped pre-filling.  Found by running it.
        if tag == "option":
            if self._select is not None:
                value = attributes.get("value", "")
                if self._first is None:
                    self._first = value
                if "selected" in attributes:
                    self._chosen = value
            return
        name = attributes.get("name", "")
        if not name or "disabled" in attributes:
            return
        if tag == "input":
            kind = attributes.get("type", "text")
            value = attributes.get("value", "")
            # **What this control COULD send.**  A checkbox or radio sends its
            # own value or nothing, so the pair is what a browser can produce;
            # any other field carries whatever the owner types, so only the
            # name is fixed.
            self.offerable.add(
                (name, value) if kind in {"checkbox", "radio"}
                else (name, None)
            )
            if kind in {"checkbox", "radio"} and "checked" not in attributes:
                return
            self.fields.append((name, value))
        elif tag == "select":
            self.offerable.add((name, None))
            self._select, self._first, self._chosen = name, None, None

    def handle_endtag(self, tag):
        """Close a select, defaulting it to its first option if none was set."""
        if tag == "select" and self._select is not None:
            chosen = self._chosen
            self.fields.append(
                (self._select, self._first or "" if chosen is None else chosen),
            )
            self._select = self._first = self._chosen = None


class _TriggerSubtreeReader(HTMLParser):
    """Collect the control names inside the element that carries ``hx-trigger``.

    Plan step ``bank_import:X-gj-1b``.  **Containment is a DEPTH question and
    string indices cannot answer it.**  A first version of the case that uses
    this sliced from the tag carrying ``hx-trigger`` to the next ``</div>``,
    which closes whichever nested element came first -- so the slice stopped
    long before the consent control and the assertion passed whatever the
    markup said.  Measured by mutation on 2026-08-30: moving the consent box
    back inside the trigger left the case GREEN.

    This tracks the open-element depth instead, so what it reports is the
    element's real subtree.
    """

    #: Tags that never carry an end tag, so a depth counter must not count
    #: them.  **Without this the counter only ever goes up**: ``<input>`` is
    #: the most common tag in these fragments and every one of them left the
    #: subtree looking one level deeper than it was, so the trigger element
    #: never appeared to close and everything after it read as inside.
    #: Measured by mutation 2026-08-30 -- the case using this passed with the
    #: consent box on either side of the boundary.
    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })

    def __init__(self):
        super().__init__()
        self.inside = set()
        self._depth = None

    def handle_starttag(self, tag, attrs):
        """Open a tag, arming on the one that carries ``hx-trigger``."""
        attributes = dict(attrs)
        if self._depth is not None:
            name = attributes.get("name")
            if name:
                self.inside.add(name)
            if tag not in self._VOID:
                self._depth += 1
        elif "hx-trigger" in attributes and tag not in self._VOID:
            self._depth = 0

    def handle_endtag(self, tag):
        """Close a tag, disarming when the trigger's own element closes."""
        if self._depth is None:
            return
        if self._depth == 0:
            self._depth = None
        else:
            self._depth -= 1


def controls_inside_the_trigger(fragment):
    """Return the control names inside the element carrying ``hx-trigger``.

    Args:
        fragment: The rendered fragment, as text.

    Returns:
        The set of ``name`` attributes in that element's subtree.

    Raises:
        AssertionError: When the fragment carries no ``hx-trigger`` at all,
            which would make every containment claim over it vacuous.
    """
    assert "hx-trigger" in fragment, (
        "this fragment carries no hx-trigger, so nothing about what its "
        "subtree contains can be graded"
    )
    reader = _TriggerSubtreeReader()
    reader.feed(fragment)
    return reader.inside


def reconcile_form_fields(page):
    """Return what a browser would submit from the Reconcile page, verbatim.

    Args:
        page: The rendered page or body, as text.

    Returns:
        A list of ``(name, value)`` pairs, repeats kept and in document order.
    """
    reader = ReconcileFormReader()
    reader.feed(page)
    return reader.fields


class _OneFormReader(ReconcileFormReader):
    """Collect only the controls inside the FORM whose action matches.

    Plan step ``bank_import:X-gj-1b``.  The Reconcile page carries two forms --
    the cards and the standing-rule offer -- and
    :func:`reconcile_form_fields` scrapes the whole document, which a browser
    never submits.  A case posting the union to one door passes for the right
    reason only by accident: it would keep passing if the offer form dropped
    its own ``csrf_token`` or ``tab``, because the cards form carries both.
    """

    def __init__(self, action):
        super().__init__()
        self._action = action
        self._depth = None

    def handle_starttag(self, tag, attrs):
        """Collect only while inside the form named by *action*."""
        if tag == "form":
            if self._action in dict(attrs).get("action", ""):
                self._depth = 0
            return
        if self._depth is None:
            return
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        """Leave the form, and let the base class close its selects."""
        if tag == "form":
            self._depth = None
            return
        if self._depth is not None:
            super().handle_endtag(tag)


def form_fields(page, action):
    """Return what a browser would submit from ONE form on *page*.

    Args:
        page: The rendered page or body, as text.
        action: A substring of the form's ``action``, naming which form.

    Returns:
        Its ``(name, value)`` pairs, repeats kept, in document order.

    Raises:
        AssertionError: When no form on the page has that action, which would
            make every assertion over the result vacuous.
    """
    assert f'action="' in page and action in page, (
        f"no form on this page has an action containing {action!r}, so a "
        f"payload scraped from it would be empty"
    )
    reader = _OneFormReader(action)
    reader.feed(page)
    return reader.fields


def reconcile_offerable(page):
    """Return every control the Reconcile page rendered, pressed or not.

    The universe a browser's submission is drawn from, as
    :attr:`ReconcileFormReader.offerable` builds it: ``(name, value)`` for a
    checkbox or radio, which can only ever send its own value, and
    ``(name, None)`` for a field whose value the owner supplies.

    Args:
        page: The rendered page or body, as text.

    Returns:
        The set.
    """
    reader = ReconcileFormReader()
    reader.feed(page)
    return reader.offerable
