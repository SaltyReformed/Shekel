"""Architecture test: nothing ships invisible with no rule that can reveal it.

Plan step **bank_import:X-gj-1b**.

What the property is
--------------------

The Reconcile page renders two families of control that a browser cannot see
until something else acts:

* **scripted-only controls** -- the page footer's keyboard hints and the
  panel's Close.  Both are emitted with ``hidden``, because the affordance
  that works with nothing running is the card's own summary, and a Close that
  could not close would be the control-that-cannot-succeed shape ruling
  **bank_import:R-HW** bounds.  ``static/js/statement_reconcile.js`` reveals
  them by one selector;
* **the panel's primary button** -- one per OPEN verb, all `display: none`
  until the verb radio its tab belongs to is checked.
  ``static/css/accounts.css`` reveals the right one;
* **the merchant-rule control's new-envelope fields** (plan step
  ``bank_import:X-gj-1c``, finding **N-403**) -- the name and category boxes
  that belong to ONE option of the answer select, `display: none` until that
  option is chosen.  ``static/css/accounts.css`` hides and reveals them, and
  both halves sit inside one ``@supports`` guard.

Why this test exists, and what it caught
----------------------------------------

**The reveal rule is measured by nothing else.**  Adversarial review of this
step deleted the panel footer's marker from the JavaScript selector and ran
both changed test modules: **49 passed, 0 failed.**  Nothing in ``tests/``
reads that asset.  A rename or a typo in the selector alone therefore ships
every scripted-only control on the page permanently invisible, with the whole
suite green -- the same defect the ``hidden`` attribute exists to prevent,
arrived at from the other side.

**The third family is here because its predecessor was the defect.**  Those
fields were hidden by a CLASS the server emitted and revealed by a script --
so with nothing running the answer could not be completed and the door refused
it every time, on three surfaces, for as long as the control had existed.  The
remedy moves the dependency into the stylesheet, which is the same shape as the
second family above and fails the same way: if the REVEAL selector stops
matching while the HIDE selector still does, the fields are invisible in every
modern browser and no route test can tell -- the markup is correct either way.
Two adversarial reviews on 2026-08-31 measured that the whole ``@supports``
block could be DELETED with the suite green.

**It grades the RENDERED page against the shipped assets**, which is the shape
:mod:`.test_the_confirm_guard_binds_first` already keeps for the same reason:
the property is a relationship between a document and a file, and an assertion
against either alone grades the wrong thing.  A control added to a template
with no reveal rule fails here without its author knowing this file exists.
"""

import pathlib
import re

from tests.test_services.test_statement_match._builders import (
    a_merchant,
    a_rule,
    an_envelope,
    an_unexplained_outflow,
)

#: The assets whose rules this grades.
_JS = pathlib.Path("app/static/js/statement_reconcile.js")
_CSS = pathlib.Path("app/static/css/accounts.css")

#: Every opening tag in a rendered page, with its attributes.
_TAG = re.compile(r"<[a-z][a-z0-9]*\b[^>]*>", re.I)


def _reconcile_page(auth_client, db, seed_user):
    """Return the Reconcile page rendered over a card that has an act.

    A card with a WORKING verb is what puts a primary button in the footer,
    and the page's own footer carries the keyboard hints whatever the cards
    hold -- so one page exercises both families.

    Args:
        auth_client: The logged-in client.
        db: The session fixture.
        seed_user: The seeded user bundle.

    Returns:
        The rendered page.
    """
    envelope = an_envelope(seed_user, name="Home Improvement")
    an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-35.72")
    db.session.commit()
    a_rule(seed_user, "Lowe's", template_id=envelope.template_id)
    db.session.commit()

    response = auth_client.get(
        f"/accounts/{seed_user['account'].id}/statements/reconcile",
    )
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _hidden_marked_tags(page):
    """Return every tag the page renders ``hidden`` carrying a rec marker.

    Args:
        page: The rendered page.

    Returns:
        The opening tags, verbatim.
    """
    return [
        tag for tag in _TAG.findall(page)
        if re.search(r"\bhidden\b", tag) and "data-rec-" in tag
    ]


class TestAScriptedOnlyControlIsReachedByTheScript:
    """Marker implies ``hidden``, and the script's selector implies both."""

    def test_the_page_renders_some_scripted_only_control(
        self, auth_client, db, seed_user,
    ):
        """The negative arms below are satisfied by zero without this.

        Every other case here quantifies over a set the page produces, and an
        empty set makes all of them vacuously true -- which is how a gate
        stops measuring without failing.
        """
        page = _reconcile_page(auth_client, db, seed_user)

        assert _hidden_marked_tags(page), (
            "the Reconcile page rendered no hidden rec control at all, so "
            "every arm in this file is grading an empty set"
        )

    def test_every_marked_control_is_also_hidden(
        self, auth_client, db, seed_user,
    ):
        """A marker with no ``hidden`` ships the control visible and dead.

        The pair is the whole design: the server states *this needs the
        script* and states it by withholding the control.
        """
        page = _reconcile_page(auth_client, db, seed_user)

        marked = [tag for tag in _TAG.findall(page)
                  if "data-rec-scripted" in tag]
        assert marked
        unhidden = [tag for tag in marked if not re.search(r"\bhidden\b", tag)]
        assert not unhidden, (
            f"a scripted-only control is rendered visible: {unhidden[:2]}"
        )

    def test_the_script_s_selector_reaches_every_one_of_them(
        self, auth_client, db, seed_user,
    ):
        """The half nothing else measures.

        ``statement_reconcile.js`` reveals what it selects, so a control the
        selector misses stays hidden forever -- and no route test can see
        that, because the markup is correct either way.
        """
        page = _reconcile_page(auth_client, db, seed_user)
        source = _JS.read_text(encoding="utf-8")

        stated = re.search(r'SCRIPTED_ONLY\s*=\s*"([^"]+)"', source)
        assert stated is not None, (
            "statement_reconcile.js states no SCRIPTED_ONLY selector; the "
            "reveal has moved and this gate must move with it"
        )
        attributes = re.findall(r"\[([A-Za-z0-9_-]+)\]", stated.group(1))
        assert attributes, (
            f"SCRIPTED_ONLY is no longer a list of attribute selectors "
            f"({stated.group(1)!r}); this gate reads it as one, so widen it "
            f"deliberately rather than leaving the reveal ungraded"
        )

        for tag in _hidden_marked_tags(page):
            assert any(f" {name}" in tag for name in attributes), (
                f"this control is hidden and the script's selector "
                f"{stated.group(1)!r} does not reach it, so it can never be "
                f"shown: {tag}"
            )

    def test_the_selector_names_no_attribute_the_page_never_emits(
        self, auth_client, db, seed_user,
    ):
        """The other direction, which membership alone cannot see.

        A selector token left behind by a rename reads as coverage and
        reaches nothing -- the set-defined-by-subtraction shape this project
        has already paid for.
        """
        page = _reconcile_page(auth_client, db, seed_user)
        stated = re.search(
            r'SCRIPTED_ONLY\s*=\s*"([^"]+)"', _JS.read_text(encoding="utf-8"),
        )
        assert stated is not None
        for name in re.findall(r"\[([A-Za-z0-9_-]+)\]", stated.group(1)):
            assert f" {name}" in page, (
                f"the script reveals {name!r} and no page emits it; the "
                f"selector is carrying a name a rename left behind"
            )


class TestThePanelsPrimaryButtonHasARuleThatShowsIt:
    """One button per OPEN verb, revealed by the verb radio its tab belongs to.

    The pairing is keyed by the verb's own value rather than by position,
    because only open verbs get a button -- so a button whose verb has no rule
    is `display: none` with nothing that can ever undo it.
    """

    def test_every_rendered_button_carries_a_verb(
        self, auth_client, db, seed_user,
    ):
        """The key the CSS pairs on, present on every one."""
        page = _reconcile_page(auth_client, db, seed_user)

        buttons = [tag for tag in _TAG.findall(page) if "rec-cta" in tag]
        assert buttons, "no card offered the panel's primary button"
        for tag in buttons:
            assert re.search(r'data-verb="[a-z]+"', tag), (
                f"a primary button carries no verb, so no rule can pair with "
                f"it: {tag}"
            )

    def test_the_stylesheet_reveals_every_verb_the_page_renders(
        self, auth_client, db, seed_user,
    ):
        """A button with no reveal rule is invisible on the tab it belongs to.

        `display: none` is the default for the whole class, so the rule is
        the only thing that shows any of them.
        """
        page = _reconcile_page(auth_client, db, seed_user)
        css = _CSS.read_text(encoding="utf-8")

        verbs = set(re.findall(
            r'rec-cta"?[^>]*data-verb="([a-z]+)"',
            " ".join(tag for tag in _TAG.findall(page) if "rec-cta" in tag),
        ))
        assert verbs, "no verb was rendered, so this arm grades nothing"
        for verb in sorted(verbs):
            assert re.search(
                r'\.rec-verb\[value="' + verb + r'"\]:checked\s*~\s*'
                r'\.rec-panel-foot\s+\.rec-cta\[data-verb="' + verb + r'"\]',
                css,
            ), (
                f"the page renders a {verb!r} button and accounts.css carries "
                f"no rule that shows it, so that verb's tab offers nothing"
            )


def _merchants_page(auth_client, db, seed_user):
    """Return the merchants page with one UNANSWERED merchant's row open.

    **Unanswered is the state the defect lived in**: the class was emitted
    only where the row had no rule, so a page showing an answered merchant
    would grade the arm that was never broken.

    Args:
        auth_client: The logged-in client.
        db: The session fixture.
        seed_user: The seeded user bundle.

    Returns:
        The rendered page.
    """
    merchant = a_merchant(seed_user, "Walmart")
    db.session.commit()

    response = auth_client.get(
        f"/accounts/{seed_user['account'].id}/statements/merchants"
        f"?edit={merchant.id}",
    )
    assert response.status_code == 200
    return response.get_data(as_text=True)


class TestTheNewEnvelopeFieldsHaveARuleThatShowsThem:
    """Finding **bank_import:N-403**: hide and reveal are one pairing.

    The hide rule alone is the defect.  So this grades that BOTH exist, that
    both name what the page actually emits, and that neither can be left
    outside the ``@supports`` guard -- which is the arrangement that makes a
    browser unable to read a select's chosen option fall back to showing the
    fields rather than to hiding them forever.
    """

    #: The guarded block, as one string.  Read rather than asserted on in
    #: pieces, because what this grades is a RELATIONSHIP between two rules and
    #: a guard, and three separate searches would pass over any pair of them.
    def _guarded(self):
        """Return the body of the ``@supports`` block that owns this pairing.

        Returns:
            The block's text.
        """
        css = _CSS.read_text(encoding="utf-8")
        found = re.search(
            r"@supports\s+selector\([^)]*:has\([^)]*\)[^)]*\)\s*\{"
            r"(?P<body>(?:[^{}]|\{[^{}]*\})*)\}",
            css,
        )
        assert found is not None, (
            "accounts.css carries no @supports selector(...:has(...)) block, "
            "so the new-envelope reveal has moved and this gate must move "
            "with it"
        )
        return found.group("body")

    def test_the_page_renders_the_fields_at_all(
        self, auth_client, db, seed_user,
    ):
        """Every arm below is satisfied by zero without this."""
        page = _merchants_page(auth_client, db, seed_user)

        assert page.count("data-rule-new-field") == 2, (
            "the merchants page rendered no new-envelope field pair, so "
            "every arm in this class is grading an empty set"
        )

    def test_no_CLASS_hides_them_any_more(
        self, auth_client, db, seed_user,
    ):
        """The defect itself: the server emitted ``d-none`` and a script lifted it.

        Asserted on the rendered page rather than on the macro, because what
        broke was what a browser received.
        """
        page = _merchants_page(auth_client, db, seed_user)

        assert not re.search(
            r'class="[^"]*\bd-none\b[^"]*"[^>]*data-rule-new-field', page,
        ), "a new-envelope field container is hidden by a class again"

    def test_the_stylesheet_both_hides_and_reveals_them(
        self, auth_client, db, seed_user,
    ):
        """One rule without the other is the defect, in one direction or none.

        Hide with no reveal is N-403 restored -- invisible in every browser
        that can apply the hide.  Reveal with no hide is merely untidy: the
        fields are always visible and the answer still lands.  So both are
        required and the FAILURE MESSAGE says which is missing.
        """
        body = self._guarded()

        hides = re.search(
            r"\[data-rule-new-field\][^{}]*\{[^{}]*display:\s*none", body,
        )
        reveals = re.search(
            r':has\([^{}]*option\[value="new"\]:checked[^{}]*\)'
            r'[^{}]*\[data-rule-new-field\][^{}]*\{[^{}]*display:\s*(?!none)',
            body,
        )
        assert hides is not None, (
            "nothing hides the new-envelope fields; if that is deliberate, "
            "this gate and the macro's comment both have to say so"
        )
        assert reveals is not None, (
            "the fields are hidden and NO rule pairs the answer select's "
            "`new` option with them, so choosing a new envelope reveals "
            "nothing -- which is finding N-403 exactly"
        )

    def test_the_HIDE_never_ships_outside_the_guard(
        self, auth_client, db, seed_user,
    ):
        """The arrangement the comment calls load-bearing, graded.

        A browser that cannot read a select's chosen option applies NEITHER
        rule and shows both fields, which still saves.  Hoisting the hide out
        of the guard -- a plausible tidying edit -- leaves exactly the old
        defect in exactly the browsers that cannot fix it.
        """
        css = _CSS.read_text(encoding="utf-8")
        guarded = self._guarded()
        outside = css.replace(guarded, "")

        assert not re.search(
            r"\[data-rule-new-field\][^{}]*\{[^{}]*display:\s*none",
            outside,
        ), (
            "a rule hiding the new-envelope fields sits OUTSIDE the @supports "
            "guard, so a browser that cannot reveal them still hides them"
        )

    def test_both_selectors_name_what_the_PAGE_emits(
        self, auth_client, db, seed_user,
    ):
        """A rename on either side, caught from whichever side moved.

        The reveal pairs three things the template emits -- the row wrapper,
        the answer select and its ``new`` option -- and a selector naming one
        the page has stopped rendering reaches nothing while reading as
        coverage.
        """
        page = _merchants_page(auth_client, db, seed_user)
        body = self._guarded()

        for token, rendered in (
            ("data-rule-row", "data-rule-row"),
            ("data-rule", " data-rule"),
            ('option[value="new"]', 'value="new"'),
            ("data-rule-new-field", "data-rule-new-field"),
        ):
            assert token in body, (
                f"the reveal no longer names {token!r}; either the control "
                f"was renamed and this rule was left behind, or the pairing "
                f"has been narrowed"
            )
            assert rendered in page, (
                f"the stylesheet pairs on {token!r} and the page emits no "
                f"{rendered!r}; the selector is carrying a name a rename left "
                f"behind"
            )
