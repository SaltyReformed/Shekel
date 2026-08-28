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
from tests.test_services.test_statement_match._builders import (
    a_reviewed_token,
)


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


def match_item(index=0, lines=(), transactions=(), entries=(), residual=None):
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
        residual: What the consent box carries when the owner ticked it -- the
            difference the screen showed (plan step ``bank_import:X-f6d-4``).
            ``None`` leaves the field off entirely, which is what an unticked
            checkbox submits.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    fields = {
        "apply": [str(index)],
        f"match-{index}-line_ids": [str(line.id) for line in lines],
        f"match-{index}-rows": (
            [a_reviewed_token(txn, RowKind.TRANSACTION)
             for txn in transactions]
            + [a_reviewed_token(entry, RowKind.PURCHASE) for entry in entries]
        ),
    }
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
