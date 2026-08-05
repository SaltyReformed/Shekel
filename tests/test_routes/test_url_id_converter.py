"""The path layer answers "what row does this digit string name" once.

Plan step X-ae / finding N-140.  Werkzeug's stock ``<int:>`` converter has
``regex = r"\\d+"`` compiled without ``re.ASCII`` and a ``to_python`` of bare
``int()``, so before :mod:`app.url_converters` the route tree's 123 path
parameters carried both halves of the defect finding N-136 closed for form
bodies: one row id under many spellings, and an unhandled ``ValueError`` on a
long enough digit run.

**The 500 arm is the more serious of the two and these tests are the only
place it is graded**, because it raises inside ``url_adapter.match()`` -- ahead
of the view, ahead of ``@login_required``, ahead of any session -- so no route
test could reach it and no authentication is needed to trigger it.
"""

import pytest
from werkzeug.routing import Map, Rule

from app.url_converters import RowIdConverter


# The longest ASCII digit run CPython will convert, plus one.  Read from the
# interpreter rather than hard-coded at 4,300: the ceiling is configurable at
# runtime (``sys.set_int_max_str_digits``), so a literal here would silently
# stop testing the boundary it names.
def _oversized_digits():
    """Return a digit run one longer than CPython will convert."""
    import sys  # pylint: disable=import-outside-toplevel

    return "1" * (sys.get_int_max_str_digits() + 1)


class TestTheConverterIsInstalled:
    """The override reaches the real application's real rules."""

    def test_the_app_uses_the_row_id_converter_for_int(self, app):
        """``int`` resolves to ours, not Werkzeug's.

        Asserted directly because every behavioural test below would pass
        vacuously against a stock converter that merely happened to agree on
        the input chosen -- and because the registration ORDER is the subtle
        part: Werkzeug binds a rule's converter when the rule is added, so a
        registration after ``_register_blueprints`` would leave every existing
        rule lax while this attribute still looked correct.
        """
        assert app.url_map.converters["int"] is RowIdConverter

    def test_every_int_rule_in_the_app_carries_it(self, app):
        """No rule escaped the override -- checked over the whole map.

        The failure this guards is a partial application: a blueprint
        registered before the converter, or a rule built on a second map,
        would keep the stock converter and stay lax with nothing to say so.
        """
        int_converters = [
            converter
            for rule in app.url_map.iter_rules()
            for converter in rule._converters.values()  # pylint: disable=protected-access
            if isinstance(converter, RowIdConverter)
        ]
        # Premise: the map really does carry the ~123 id parameters, so a
        # regression that emptied this list could not pass as "all clean".
        assert len(int_converters) > 100, (
            f"only {len(int_converters)} row-id path parameters found; the "
            "override may have run after the blueprints"
        )
        stock = [
            (rule.rule, name)
            for rule in app.url_map.iter_rules()
            for name, converter in rule._converters.items()  # pylint: disable=protected-access
            if type(converter).__name__ == "IntegerConverter"
        ]
        assert stock == [], f"rules still on the stock lax converter: {stock}"


class TestTheOversizedPathSegment:
    """The unauthenticated 500, and the reason this is a converter."""

    def test_an_oversized_digit_run_does_not_raise_out_of_routing(self, app):
        """It answers 404 instead of raising ``ValueError`` before the view.

        This is the arm that matters: the raise happened inside
        ``ctx.push()``, so it needed no session, no CSRF token and no account.
        ``app/error_handlers.py`` registers 400/403/404/429/500 and
        ``BaselineMissingError`` -- no ``ValueError`` arm -- so it surfaced as
        an unhandled 500 to an anonymous caller.
        """
        client = app.test_client()
        response = client.get(f"/accounts/{_oversized_digits()}/details")
        assert response.status_code == 404

    def test_the_public_and_submodule_converter_are_one_class(self):
        """The import path this module depends on is the documented one.

        `RowIdConverter` subclasses a third-party class, and Werkzeug is not
        pinned (finding N-143), so the narrower the surface the better.
        Importing from the public ``werkzeug.routing`` rather than the
        ``werkzeug.routing.converters`` submodule costs nothing -- this
        asserts they really are the same object, so the choice is a free
        reduction in exposure rather than a guess.
        """
        from werkzeug.routing import (  # pylint: disable=import-outside-toplevel
            IntegerConverter as PublicConverter,
        )
        from werkzeug.routing.converters import (  # pylint: disable=import-outside-toplevel
            IntegerConverter as SubmoduleConverter,
        )

        assert PublicConverter is SubmoduleConverter
        assert issubclass(RowIdConverter, PublicConverter)

    def test_the_stock_converter_really_did_raise(self):
        """The premise, on Werkzeug's own class rather than on our word.

        Without this the test above proves only that 404 is returned today;
        it would keep passing if the defect had never existed, and the whole
        step's justification rests on it having existed.
        """
        from werkzeug.routing import (  # pylint: disable=import-outside-toplevel
            IntegerConverter,
        )

        stock = Map([
            Rule("/x/<int:row_id>", endpoint="x"),
        ]).bind("localhost")
        assert isinstance(
            stock.map._rules[0]._converters["row_id"],  # pylint: disable=protected-access
            IntegerConverter,
        )
        with pytest.raises(ValueError, match="Exceeds the limit"):
            stock.match(f"/x/{_oversized_digits()}")

    def test_a_long_but_convertible_run_routes_and_then_finds_nothing(
        self, app, auth_client,
    ):
        """A 40-digit id is WELL-FORMED and simply names no row.

        The distinction the converter must not blur, and a first draft of
        this test got it wrong by asserting a routing refusal: ``'9' * 40``
        is canonical ASCII, above :data:`MIN_ROW_ID`, and round-trips
        through ``str``, so the converter has no ground to reject it.  It
        routes, reaches the view, and the ordinary ownership lookup answers
        404 -- which is also the measured proof that an oversized id does
        not overflow the ``int4`` ``id`` column on the way (psycopg sends it
        as ``numeric``).  Authenticated, because an anonymous caller would
        be redirected to login before the lookup ran.

        **The MAP is asserted before the response**, because a 404 alone
        cannot tell "routed and found nothing" from "refused by the
        converter" -- and those are opposite claims about this input.  An
        adversarial review caught the request-only version asserting the
        weaker of the two while its docstring claimed the stronger.
        """
        huge = "9" * 40
        endpoint, args = app.url_map.bind("localhost").match(
            f"/accounts/{huge}/details",
        )
        assert endpoint == "accounts.cash_detail"
        assert args == {"account_id": int(huge)}

        response = auth_client.get(f"/accounts/{huge}/details")
        assert response.status_code == 404


class TestTheSpellingOfAPathId:
    """One row id, one path spelling."""

    def test_a_non_ascii_digit_path_does_not_reach_the_route(
        self, app, auth_client, seed_user,
    ):
        """``/accounts/١/details`` no longer answers as ``/accounts/1/details``.

        Measured before the fix: byte-identical responses, because
        ``int('١')`` is ``1``.  The authenticated client is the point --
        this is not a login failure, it is a routing refusal, so the owner
        of the account gets the 404 too.
        """
        with app.app_context():
            account_id = seed_user["account"].id

        ascii_response = auth_client.get(f"/accounts/{account_id}/details")
        assert ascii_response.status_code == 200

        respelled = str(account_id).translate(
            str.maketrans("0123456789", "٠١٢٣٤"
                                        "٥٦٧٨٩"),
        )
        # The premise: same id, and the stock converter's regex matched it.
        assert int(respelled) == account_id
        assert respelled.isdigit()

        assert auth_client.get(f"/accounts/{respelled}/details").status_code == 404

    def test_a_zero_padded_path_id_is_refused(
        self, app, auth_client, seed_user,
    ):
        """``/accounts/007/details`` is a second spelling of row 7.

        ``url_for`` emits ``str(int)`` and never pads, so nothing the
        application generates is affected; a padded id can only be
        hand-made, and it names a row that already has a spelling.
        """
        with app.app_context():
            account_id = seed_user["account"].id

        padded = f"00{account_id}"
        assert int(padded) == account_id
        assert auth_client.get(f"/accounts/{padded}/details").status_code == 404

    def test_a_zero_id_is_refused_by_ROUTING_not_by_the_lookup(self, app):
        """No table in either database holds a row with ``id < 1``.

        Asserted at the MAP rather than through a request, because through a
        request this test is vacuous -- an adversarial review measured it
        passing against the stock lax converter, since ``/accounts/0/details``
        routes to the view and ``get_or_404`` answers 404 anyway.  A 404 from
        two different causes is not evidence about the converter.  Matching
        the map directly distinguishes them: the rule must not match at all.
        """
        from werkzeug.exceptions import (  # pylint: disable=import-outside-toplevel
            NotFound,
        )

        adapter = app.url_map.bind("localhost")
        with pytest.raises(NotFound):
            adapter.match("/accounts/0/details")
        # The control: the same rule DOES match a canonical id, so the
        # refusal above is about the value and not about the path.
        endpoint, args = adapter.match("/accounts/7/details")
        assert args == {"account_id": 7}
        assert endpoint == "accounts.cash_detail"

    def test_url_for_still_builds_every_id_url(self, app, seed_user):
        """The override must not break URL GENERATION, only matching.

        ``to_url`` is inherited unchanged, but a converter override is
        exactly the kind of change that can pass every match test and break
        every rendered link, so the build direction is asserted explicitly.
        """
        from flask import url_for  # pylint: disable=import-outside-toplevel

        with app.test_request_context():
            account_id = seed_user["account"].id
            assert url_for(
                "accounts.cash_detail", account_id=account_id,
            ).endswith(f"/accounts/{account_id}/details")


class TestARealIdStillWorks:
    """The regression that would matter most: ordinary routing is unchanged."""

    def test_the_owner_still_reaches_their_own_account(
        self, app, auth_client, seed_user,
    ):
        """A canonical id routes exactly as before, content included."""
        with app.app_context():
            account_name = seed_user["account"].name
            account_id = seed_user["account"].id

        response = auth_client.get(f"/accounts/{account_id}/details")
        assert response.status_code == 200
        assert account_name.encode() in response.data
