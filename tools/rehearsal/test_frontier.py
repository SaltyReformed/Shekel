"""The crawl's rules (step X-cx's K1c-1): from planted pages, exactly the GET requests a
browser and htmx 2.0 issue, what cannot be assembled named, the rule and the date bound.

``side/frontier.py`` is side code, run by path and never imported by name
(``tools/rehearsal/__init__.py``; ``test_imports.py`` holds the rule), so these tests load
it by path, as the crawl probe does.
"""
from __future__ import annotations

from datetime import date

import pytest

from tools.rehearsal import side_load

frontier = side_load.load("frontier")
#: The pay periods every bounded case runs against.
_SPAN = frontier.Span(date(2026, 1, 2), date(2027, 6, 30))


def _found(body: str, url: str = "/page", within: str | None = None):
    """What a planted page at ``url`` issues; with ``within``, it is a fragment swapped into
    the page ``/page`` whose body that is."""
    if within is None:
        return frontier.issued(frontier.Page(url, url, body))
    host = frontier.Page("/page", "/page", within)
    return frontier.issued(frontier.Page(url, "/page", body, host))


def _requests(body: str, url: str = "/page", within: str | None = None
              ) -> list[tuple[str, bool]]:
    """Each request a planted page issues as ``(url, htmx)``, in order."""
    return [(request.url, request.htmx) for request in _found(body, url, within).requests]


def _one(body: str, url: str = "/page", within: str | None = None):
    """The one request a planted page issues."""
    requests = _found(body, url, within).requests
    assert len(requests) == 1, requests
    return requests[0]


def test_a_link_is_every_href_to_a_path_of_this_site_resolved_against_the_document():
    """Relative, absolute-path and query-only hrefs resolve; a fragment is dropped; another
    site, a scheme-relative URL and a non-HTTP scheme are not followed."""
    body = ('<a href="b">1</a><a href="/c?x=1#top">2</a><a href="?y=2">3</a>'
            '<a href="https://other.example/d">4</a><a href="//other.example/e">5</a>'
            '<a href="mailto:a@b.c">6</a><a href="javascript:void(0)">7</a><a>8</a>')
    assert _requests(body, "/dir/page?z=3") == [
        ("/dir/b", False), ("/c?x=1", False), ("/dir/page?y=2", False)]


def test_a_fragments_link_resolves_against_the_page_it_was_swapped_into():
    """A fragment's relative href is read against the document's URL, not the fragment's."""
    assert _requests('<a href="?offset=2">next</a>', "/frag/x", within="<p></p>") == [
        ("/page?offset=2", False)]


def test_canonical_orders_the_query_by_key_and_keeps_a_repeated_keys_values_in_order():
    """Keys sorted, a repeated key's values kept in their order, encoding made one."""
    assert frontier.canonical("/p?b=2&a=1&b=1&c=%7E") == "/p?a=1&b=2&b=1&c=~"
    assert frontier.canonical("") == "/"
    assert frontier.canonical("http://x/p") is None


def test_the_same_url_by_link_and_by_htmx_is_two_requests():
    """A view answers htmx with a fragment and a link with a page, so each is issued."""
    found = _requests('<a href="/t">t</a><button hx-get="/t">t</button>')
    assert found == [("/t", False), ("/t", True)]


def test_an_hx_get_carries_the_headers_htmx_sends():
    """``HX-Request``, ``HX-Current-URL`` as the document's URL, the element's id and name,
    and the id of the element ``hx-target`` names."""
    request = _one('<div id="out"></div><input id="who" name="w" value="v" '
                   'hx-get="/f" hx-target="#out">', "/frag", within="<p></p>")
    assert request.url == "/f?w=v"
    assert dict(request.headers) == {
        "HX-Request": "true", "HX-Current-URL": "/page", "HX-Trigger": "who",
        "HX-Trigger-Name": "w", "HX-Target": "out"}
    assert request.document == "/page"


@pytest.mark.parametrize(("body", "target"), [
    ('<button id="me" hx-get="/f">x</button>', "me"),
    ('<div id="holder" hx-target="this"><button hx-get="/f">x</button></div>', "holder"),
    ('<table><tr><td id="cell"><button hx-get="/f" hx-target="closest td">x</button>'
     '</td></tr></table>', "cell"),
    ('<div hx-target="#far"><span><button hx-get="/f">x</button></span></div>'
     '<div id="far"></div>', "far"),
])
def test_hx_target_is_read_as_htmx_reads_it(body: str, target: str):
    """No ``hx-target``: the element itself; ``this``: the element declaring it; ``closest``;
    and an ancestor's, inherited."""
    assert dict(_one(body).headers).get("HX-Target") == target


def test_hx_disinherit_on_the_declaring_ancestor_keeps_its_target_to_itself():
    """An ancestor that names ``hx-target`` in its ``hx-disinherit`` lends its own target to
    no descendant (htmx's ``getAttributeValueWithDisinheritance``)."""
    request = _one('<form hx-post="/save" hx-target="this" id="f" hx-disinherit="hx-target">'
                   '<button id="preview" hx-get="/p">x</button></form>')
    assert dict(request.headers).get("HX-Target") == "preview"


def test_a_target_found_nowhere_is_named_and_the_request_kept_without_it():
    """The request is issued without ``HX-Target`` and the target is named."""
    found = _found('<button hx-get="/f" hx-target="#gone">x</button>')
    assert len(found.requests) == 1, found.requests
    request = found.requests[0]
    assert "HX-Target" not in dict(request.headers)
    assert found.unassembled == ["/page: <button id=None>: hx-target '#gone' is found "
                                 "nowhere; issued without HX-Target"]


def test_a_select_that_issues_an_hx_get_is_varied_one_option_at_a_time():
    """Its served value first, then each other option, an option's text its value when it
    has no value attribute, a repeated option fetched once."""
    body = ('<select name="year" hx-get="/tab"><option>2025</option>'
            '<option selected value="2026">this year</option><option value="2025">again'
            '</option></select>')
    assert _requests(body) == [("/tab?year=2026", True), ("/tab?year=2025", True)]


def test_a_select_with_none_selected_serves_its_first_enabled_option():
    """A single select with no ``selected`` submits its first option that is not disabled."""
    body = ('<form method="get" action="/s"><select name="m"><option disabled>pick'
            '</option><option value="3">March</option></select></form>')
    assert _requests(body)[0] == ("/s?m=3", False)


def test_a_get_form_serialises_its_controls_as_the_browser_does():
    """Hidden, text and checked inputs in order; unchecked, disabled, nameless, button kinds,
    files and a disabled fieldset's controls are not sent; a textarea sends its text; the
    action's own query is replaced."""
    body = ('<form action="/search?old=1"><input type="hidden" name="h" value="1">'
            '<input name="q" value="a b"><input type="checkbox" name="c1" checked>'
            '<input type="checkbox" name="c2"><input type="radio" name="r" value="x" checked>'
            '<input name="d" value="no" disabled><input value="nameless">'
            '<input type="submit" name="go" value="Go"><button name="b">B</button>'
            '<input type="file" name="f"><fieldset disabled><input name="fs" value="1">'
            '</fieldset><textarea name="t">\nline</textarea></form>')
    assert _requests(body) == [("/search?c1=on&h=1&q=a+b&r=x&t=line", False)]


def test_a_get_forms_selects_are_varied_one_at_a_time_never_their_product():
    """The served values, then each select's other options with every other control at the
    page's value: 1 + 2 + 1 requests for selects of three and two options, not 3 x 2."""
    body = ('<form method="GET" action="/r"><select name="a"><option>1</option>'
            '<option>2</option><option>3</option></select><select name="b">'
            '<option selected>x</option><option>y</option></select>'
            '<select name="m" multiple><option selected>p</option><option>q</option>'
            '</select></form>')
    assert _requests(body) == [("/r?a=1&b=x&m=p", False), ("/r?a=2&b=x&m=p", False),
                               ("/r?a=3&b=x&m=p", False), ("/r?a=1&b=y&m=p", False)]


def test_a_form_htmx_submits_is_no_get_form_and_a_form_without_a_method_is_one():
    """``hx-post`` and ``method="post"`` forms issue no GET; a form with no method does,
    to the document's URL when it has no action."""
    body = ('<form hx-post="/a"><input name="x" value="1"></form>'
            '<form method="post" action="/b"><input name="y" value="2"></form>'
            '<form><input name="z" value="3"></form>')
    assert _requests(body, "/here?keep=0") == [("/here?z=3", False)]


def test_an_hx_get_form_sends_its_controls_and_the_url_keeps_its_own_query():
    """A form that issues an hx-get sends its controls, appended to the hx-get URL's query."""
    body = '<form hx-get="/f?fixed=1"><input name="x" value="2"><select name="s">' \
           '<option>a</option></select></form>'
    assert _requests(body) == [("/f?fixed=1&s=a&x=2", True)]


def test_hx_include_reads_each_selector_form_the_templates_use():
    """``closest form`` (its controls), ids, ``[name=...]``, classes (a non-form's controls
    within it), each control read once."""
    body = ('<input id="a" name="a" value="1"><input id="b" name="b" value="2">'
            '<select name="month"><option>4</option></select>'
            '<div class="card"><input name="in_card" value="c"></div>'
            '<form method="post"><input name="f" value="5">'
            '<button hx-get="/x" hx-include="closest form, #a, #b, [name=month], .card, #a">'
            'x</button></form>')
    assert _one(body).url == "/x?a=1&b=2&f=5&in_card=c&month=4"


def test_hx_include_is_inherited_and_stopped_by_hx_disinherit():
    """A descendant inherits an ancestor's ``hx-include`` unless an ancestor between
    disinherits it."""
    body = ('<input id="a" name="a" value="1">'
            '<div hx-include="#a"><button hx-get="/in">x</button>'
            '<div hx-disinherit="*"><button hx-get="/out">y</button></div></div>')
    assert _requests(body) == [("/in?a=1", True), ("/out", True)]


def test_a_selector_the_fragment_misses_is_looked_for_in_the_page_as_served():
    """A plain selector matching nothing in the fragment is read in the page it was swapped
    into; one matching in the fragment is not."""
    page = '<input id="h" name="h" value="page"><input id="k" name="k" value="page">'
    body = ('<input id="k" name="k" value="fragment">'
            '<button hx-get="/x" hx-include="#h, #k">x</button>')
    assert _one(body, "/frag", within=page).url == "/x?h=page&k=fragment"


def test_a_page_is_parsed_once_however_many_of_its_fragments_are_read(monkeypatch):
    """The page three fragments are swapped into is parsed once, and each fragment once,
    though every fragment reads the page (K1c-1's review, finding 8: about 55 ms per MB for
    every fragment read)."""
    parsed: list[str] = []
    parse = frontier.parse

    def counted(body: str):
        parsed.append(body)
        return parse(body)

    monkeypatch.setattr(frontier, "parse", counted)
    page = frontier.Page("/page", "/page", '<input id="h" name="h" value="page">')
    fragments = [f'<button hx-get="/x{index}" hx-include="#h">x</button>' for index in range(3)]
    urls = [request.url for index, body in enumerate(fragments) for request in frontier.issued(
        frontier.Page(f"/frag{index}", "/page", body, page)).requests]
    assert urls == ["/x0?h=page", "/x1?h=page", "/x2?h=page"]
    assert sorted(parsed) == sorted([page.body, *fragments])


def test_a_selector_in_a_form_not_read_is_named_and_selects_nothing():
    """A descendant combinator and an htmx keyword this module does not read are named."""
    found = _found('<button hx-get="/x" hx-include="div input, next">x</button>')
    assert [request.url for request in found.requests] == ["/x"]
    assert found.unassembled == ["/page: <button id=None>: the selector 'div input' is not read",
                                 "/page: <button id=None>: the selector 'next' is not read"]


def test_hx_vals_merge_up_the_ancestors_the_nearest_key_winning_over_any_control():
    """Each ancestor's JSON object adds its keys, the element's own first; a key replaces
    every control of its name; values are stringified as ``FormData`` does, a top-level
    object as the module writes it (``[object Object]``; htmx JSON-encodes one, a departure
    its docstring states)."""
    body = ('<div hx-vals=\'{"far": "1", "near": "outer"}\'>'
            '<form method="post"><input name="near" value="control">'
            '<input name="other" value="o">'
            '<button hx-get="/v" hx-include="closest form" hx-vals=\'{"near": "inner", '
            '"n": 2, "f": 2.0, "t": true, "z": null, "l": [1, "b"], "o": {"k": 1}}\'>v'
            '</button></form></div>')
    assert _one(body).url == ("/v?f=2&far=1&l=1&l=b&n=2&near=inner&o=%5Bobject+Object%5D"
                              "&other=o&t=true&z=null")


def test_hx_vals_evaluated_as_script_or_not_json_is_named_and_adds_nothing():
    """``js:`` and ``javascript:`` are never run; a value that is not JSON is named."""
    found = _found('<button hx-get="/a" hx-vals="js:{x: 1}">a</button>'
                   '<button hx-get="/b" hx-vals="{bad">b</button>')
    assert [request.url for request in found.requests] == ["/a", "/b"]
    assert found.unassembled == [
        "/page: <button id=None>: hx-vals evaluated as script: 'js:{x: 1}'",
        "/page: <button id=None>: hx-vals is not JSON: '{bad'"]


def test_a_button_that_issues_an_hx_get_sends_its_own_name_and_value():
    """htmx adds the triggering button's name and value, over a control of that name."""
    body = ('<form method="post"><input name="go" value="control"><button name="go" '
            'value="pressed" '
            'hx-get="/b" hx-include="closest form">b</button></form>')
    assert _one(body).url == "/b?go=pressed"


def test_hx_push_url_makes_the_requests_own_url_the_document():
    """A pushed request's response belongs to the request's URL; an unpushed one's to the
    document that issued it."""
    found = _found('<button hx-get="/tab?x=1" hx-push-url="true">t</button>'
                   '<button hx-get="/frag">f</button>', "/page?y=2")
    assert [request.document for request in found.requests] == ["/tab?x=1", "/page?y=2"]


def test_requests_come_in_document_order():
    """Links, forms and hx-gets interleave as the page holds them."""
    body = ('<button hx-get="/1">1</button><a href="/2">2</a>'
            '<form action="/3"></form><div hx-get="/4" hx-trigger="load"></div>')
    assert _requests(body) == [("/1", True), ("/2", False), ("/3", False), ("/4", True)]


def test_a_malformed_page_is_read_by_the_tree_rules():
    """A stray end tag is ignored, options left open read as closed ones, and an end tag
    closes everything opened inside its element."""
    body = ('</span><form action="/m"><select name="s"><option>a<option>b</select>'
            '<div><input name="x" value="1"></form><input name="outside" value="2">')
    assert _requests(body) == [("/m?s=a&x=1", False), ("/m?s=b&x=1", False)]


@pytest.mark.parametrize(("url", "followed"), [
    ("/cal?view=year&year=2026", True),
    ("/cal?view=year&year=2028", False),
    ("/cal?year=2027", True),
    ("/cal?year=2025", False),
    ("/spend?year=2025&month=12", False),
    ("/spend?year=2026&month=1", True),
    ("/spend?year=2027&month=7", False),
    ("/bs?as_of=2027-06-30", True),
    ("/bs?as_of=2027-07-01", False),
    ("/bs?as_of=2026-01-01", False),
    ("/m?when=2027-06", True),
    ("/m?when=2025-12", False),
    ("/x?when=2026-13-01&n=2026-02-30", True),
    ("/grid?offset=40&periods=6", True),
    ("/id?account_id=2024", True),
])
def test_a_query_naming_a_date_outside_the_pay_periods_is_not_followed(url: str,
                                                                       followed: bool):
    """An ISO day or month under any key, and ``year`` with its ``month`` (else the whole
    year), must meet the span; a value that is no calendar day is no date; other keys are
    not dates."""
    assert frontier.followed(frontier.Request(url, False, (), url), _SPAN) is followed


def test_a_user_with_no_pay_period_follows_no_dated_request():
    """With no span, every dated request is held back and every other followed."""
    dated = frontier.Request("/cal?year=2026", False, (), "/cal")
    plain = frontier.Request("/grid", False, (), "/grid")
    assert not frontier.followed(dated, None)
    assert frontier.followed(plain, None)


def _request(url: str) -> object:
    """A link to ``url``."""
    return frontier.Request(url, False, (), url)


def test_a_request_is_new_while_any_value_it_names_is_new_on_its_screen():
    """The first of a screen is new, the same again is not; a value new under a key already
    carried is new; a request whose every value earlier ones carried, split between them, is
    not (an offset and a column count already each fetched)."""
    asked = frontier.Asked()
    assert asked.new("grid", _request("/grid?offset=1&periods=6")) is True
    assert asked.new("grid", _request("/grid?offset=1&periods=6")) is False
    assert asked.new("grid", _request("/grid?offset=2&periods=6")) is True
    assert asked.new("grid", _request("/grid?offset=1&periods=13")) is True
    assert asked.new("grid", _request("/grid?offset=2&periods=13")) is False
    assert asked.new("grid", _request("/grid?offset=2&periods=13&show_all=1")) is True


def test_a_request_with_no_query_is_new_once_per_screen_and_screens_are_apart():
    """No query names the screen itself, once; the same values on another screen (another
    rule, another object its path names, or the other of page and htmx) are new there."""
    asked = frontier.Asked()
    assert asked.new(("a", (("id", 1),), False), _request("/a/1")) is True
    assert asked.new(("a", (("id", 1),), False), _request("/a/1")) is False
    assert asked.new(("a", (("id", 1),), False), _request("/a/1?day=2026-01-02")) is True
    assert asked.new(("a", (("id", 2),), False), _request("/a/2?day=2026-01-02")) is True
    assert asked.new(("a", (("id", 2),), True), _request("/a/2?day=2026-01-02")) is True
    assert asked.new(("a", (("id", 2),), True), _request("/a/2?day=2026-01-02")) is False


def test_a_year_with_its_month_is_one_month_new_while_that_month_is():
    """``balance:R-BAL343``: a ``year`` with its ``month`` names ONE month, as THE BOUND reads
    it, so a month whose year (a year view) and whose month (another year's) were each carried
    apart is new; that month again is not."""
    asked = frontier.Asked()
    assert asked.new("cal", _request("/cal?view=year&year=2027")) is True
    assert asked.new("cal", _request("/cal?month=1&view=month&year=2026")) is True
    assert asked.new("cal", _request("/cal?month=1&view=month&year=2027")) is True
    assert asked.new("cal", _request("/cal?month=1&view=month&year=2027")) is False
