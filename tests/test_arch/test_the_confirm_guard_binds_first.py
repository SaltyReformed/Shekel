"""Architecture test: the destructive-action dialog exists before its controls.

Plan step **bank_import:X-gc**, finding **N-345**.

What the guard is
-----------------

One dialog stands between a click and an act the app cannot take back: the
statement Undo that destroys money records, the grid Delete that takes bank
lines with it, the import Delete that releases every match it recorded.  All of
them are wired the same way -- ``data-confirm`` on a form, or ``hx-confirm`` on
an htmx element -- and one listener turns either into the modal.

What went wrong
---------------

That listener lived at the bottom of ``app.js``, which ``base.html`` loads at
the END of ``<body>``, after the entire document.  So every control it guards
was clickable BEFORE the listener that guards it existed.  The statement review
page is 537 KB of HTML on the developer's own account, and N-345 records an
Undo submitted before the modal bound: the act ran, with no dialog shown.

Why this test is STRUCTURAL, not a name list
--------------------------------------------

The obvious gate is "assert app.js does not contain the listener", which
protects exactly the file someone already thought about and says nothing about
the property that matters.  What matters is an ORDERING in the rendered
document: the guard's ``<script>`` must appear before the first element
carrying a confirm attribute, on a real page that renders one.  Every future
template gets that for free, and moving the tag back into ``<body>`` fails here
whatever the file is called.

22 controls across 17 templates depend on this listener (counted 2026-08-25
over ``data-confirm=`` / ``hx-confirm=`` ATTRIBUTES; a first count said 29 and
had included two documentation examples and one Jinja comment).

It reads the RENDERED page rather than ``base.html``'s source, deliberately: a
template inherits its head from a parent it does not restate, so a source-level
assertion grades the wrong document.
"""

import re

from app.models.statement_import import BankStatementLine, StatementImport
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    an_import,
)

#: The one script whose position is load-bearing.
_GUARD = "js/confirm.js"

#: Every way a template arms the shared dialog.
_CONFIRM_ATTRIBUTES = ("data-confirm", "hx-confirm")


def _guard_position(body):
    """Return where the guard's script tag starts, refusing if it is absent.

    Args:
        body: A rendered page.

    Returns:
        The index of the ``<script>`` tag that loads the guard.
    """
    match = re.search(r'<script src="[^"]*' + re.escape(_GUARD) + r'[^"]*"',
                      body)
    assert match is not None, (
        f"no page loaded {_GUARD}; the destructive-action dialog has no "
        f"listener at all"
    )
    return match.start()


class TestTheGuardIsBoundBeforeAnythingItGuards:
    """The ordering property, on pages that really render a confirm control."""

    def test_it_loads_inside_the_HEAD(self, auth_client, seed_user):
        """Before ``</head>`` is what makes it precede every control.

        A blocking script in the head runs before the parser reaches
        ``<body>``, so no element carrying a confirm attribute can exist before
        the listener does.  Asserted against ``</head>`` rather than against a
        line number: the head grows, and the property is containment.
        """
        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        assert _guard_position(body) < body.index("</head>")

    def test_it_is_NOT_deferred_or_async(self, auth_client, seed_user):
        """``defer`` would put it after the parse, which is the defect again.

        A deferred script runs after the document is parsed -- so the controls
        exist first, which is exactly the window N-345 was observed in.  The
        whole point of this file's position is that it blocks.
        """
        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        start = _guard_position(body)
        tag = body[start:body.index(">", start)]
        assert " defer" not in tag
        assert " async" not in tag

    def test_it_precedes_the_FIRST_confirm_control_on_a_real_page(
        self, auth_client, db, seed_user,
    ):
        """The property itself, graded on a page that arms the dialog.

        The statements page renders a delete form carrying ``data-confirm``
        once an import exists, so this seeds one rather than asserting over a
        page with nothing to guard -- which would pass with the guard deleted.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-11.11",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEBIT SOMETHING",
        )
        db.session.commit()

        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        armed = [
            body.index(attribute) for attribute in _CONFIRM_ATTRIBUTES
            if attribute in body
        ]
        assert armed, (
            "this page armed no confirm control, so the ordering it is "
            "supposed to grade was never exercised"
        )
        assert _guard_position(body) < min(armed)
        assert db.session.query(StatementImport).count() == 1
        assert db.session.query(BankStatementLine).count() == 1

    def test_the_listener_is_registered_ONCE(self, auth_client, seed_user):
        """Two copies would ask the same question twice, or arm two dialogs.

        The block was MOVED out of ``app.js`` rather than copied; a page that
        loaded both would register two ``submit`` listeners, and the second
        would find the attribute the first had already removed -- so the act
        would run with the dialog still open.

        **It reads the SERVED ASSET, because counting script tags does not
        grade this.**  A first version asserted that the page names the guard
        exactly once, which stays true with the whole block pasted back into
        ``app.js`` -- the hazard the docstring names was ungraded (found by two
        independent adversarial reviews, 2026-08-25).  Fetching the file
        through the app's own static route grades the thing that would actually
        double-register.
        """
        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)
        app_js = auth_client.get(
            "/static/js/app.js"
        ).get_data(as_text=True)

        assert body.count(_GUARD) == 1
        assert "data-confirm" not in app_js
        assert "htmx:confirm" not in app_js
