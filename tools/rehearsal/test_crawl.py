"""The crawl's walk (step X-cx's K1c-2): over a planted site answered by a stub client, every
request a browser reaches fetched once, in order, as the right kind; redirects followed as
the browser and htmx follow them; an authentication redirect and the cap refused by name.

``side/crawl.py`` is side code, run by path and never imported by name
(``tools/rehearsal/__init__.py``; ``test_imports.py`` holds the rule), so these tests load it
by path (:func:`tools.rehearsal.side_load.load`).  What needs a side's real app -- the login
control, the login, the span and the rule census -- is graded by the rows run's cluster test.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

import pytest

from tools.rehearsal import side_load

crawl = side_load.load("crawl")


@dataclass
class Answer:
    """What the planted site answers one request with."""

    status: int = 200
    body: str = ""
    mimetype: str = "text/html"
    headers: dict[str, str] = field(default_factory=dict)


class _Response:
    """The part of a Flask test response the walk reads."""

    def __init__(self, answer: Answer) -> None:
        """The response ``answer`` plants."""
        self.status_code = answer.status
        self.headers = answer.headers
        self.mimetype = answer.mimetype
        self.is_json = answer.mimetype == "application/json"
        self._body = answer.body.encode("utf-8")

    def get_data(self) -> bytes:
        """The body's bytes."""
        return self._body


class _Client:
    """A Flask test client's ``get`` over a planted site keyed by ``(url, htmx)``; anything
    unplanted is a 404 with no body.  Every request it was asked is kept, with its headers."""

    def __init__(self, site: dict[tuple[str, bool], Answer]) -> None:
        """A client of the planted ``site``, asked nothing yet."""
        self.site = site
        self.asked: list[tuple[str, bool, tuple[tuple[str, str], ...]]] = []

    def get(self, url: str, base_url: str, headers: list[tuple[str, str]]) -> _Response:
        """Answer one request as the site is planted."""
        assert base_url == "https://localhost"
        htmx = ("HX-Request", "true") in headers
        self.asked.append((url, htmx, tuple(headers)))
        return _Response(self.site.get((url, htmx), Answer(404)))


def _by_path(asked) -> tuple[str, bool]:
    """A planted site's screen: each path, as a page or as htmx, is one."""
    return urlsplit(asked.url).path, asked.htmx


def _walk(site: dict[tuple[str, bool], Answer], cap: int = 100, span=None, screen=_by_path):
    """Walk the planted site; the client, the JSONL lines and the walk's counts."""
    client = _Client(site)
    lines: list[dict] = []
    walked = crawl.walk(client, span, cap, lines.append, screen)
    return client, lines, walked


def _keys(client: _Client) -> list[tuple[str, bool]]:
    """Each request asked as ``(url, htmx)``, in order."""
    return [(url, htmx) for url, htmx, _ in client.asked]


@pytest.mark.parametrize("target, asked, expected", [
    ("/login?next=%2F", "/", True),
    ("/login?next=%2Fgrid%3Fb%3D2%26a%3D1", "/grid?a=1&b=2", True),
    ("/reauth?next=https%3A%2F%2Flocalhost%2Fgrid%3Fa%3D1%26b%3D2", "/grid?a=1&b=2", True),
    ("/login?next=%2Fother", "/grid", False),
    ("/login", "/", False),
    ("/grid", "/", False),
])
def test_an_authentication_redirect_is_one_whose_next_names_the_url_it_answered(
        target, asked, expected):
    """The login gate's ``next`` (a path) and the reauth door's (a whole URL) both name the
    request, in any key order; a ``next`` naming another URL, or none, is not one."""
    assert crawl.authenticating(target, asked) is expected


def test_the_walk_is_breadth_first_and_a_redirect_is_followed_as_the_same_kind():
    """A page's link and its hx-get are fetched in document order; a plain redirect is a
    page, an htmx one keeps htmx's headers and is read as a fragment of the same page, whose
    link resolves against that page."""
    site = {
        ("/", False): Answer(body='<a href="/a"></a><div id="d" hx-get="/f"></div>'),
        ("/a", False): Answer(302, headers={"Location": "/b"}),
        ("/b", False): Answer(body="<p>b</p>"),
        ("/f", True): Answer(302, headers={"Location": "/g"}),
        ("/g", True): Answer(body='<a href="h"></a>'),
        ("/h", False): Answer(),
    }
    client, lines, walked = _walk(site)
    assert _keys(client) == [("/", False), ("/a", False), ("/f", True), ("/b", False),
                             ("/g", True), ("/h", False)]
    assert client.asked[4][2] == client.asked[2][2]
    assert lines[4]["document"] == "/"
    assert walked.fetched == 6
    assert walked.families[("/a", "page")] == 1
    assert walked.families[("/f", "htmx")] == 1


def test_an_hx_redirect_is_followed_as_a_page():
    """htmx sends the browser to an ``HX-Redirect``, so it is fetched as a page of its own."""
    site = {
        ("/", False): Answer(body='<div hx-get="/x"></div>'),
        ("/x", True): Answer(204, headers={"HX-Redirect": "/y"}),
        ("/y", False): Answer(body='<a href="/z"></a>'),
    }
    client, _, _ = _walk(site)
    assert _keys(client) == [("/", False), ("/x", True), ("/y", False), ("/z", False)]


@pytest.mark.parametrize("answer", [
    Answer(302, headers={"Location": "/login?next=%2Fa%3Fb%3D2%26a%3D1"}),
    Answer(204, headers={"HX-Redirect": "/reauth?next=https%3A%2F%2Flocalhost%2Fa%3Fa%3D1%26b"
                                        "%3D2"}),
], ids=["login", "reauth"])
def test_an_authentication_redirect_mid_walk_is_refused_naming_both_urls(answer):
    """The session was lost or is stale: the walk ends, naming the request and the door."""
    site = {("/", False): Answer(body='<a href="/a?b=2&a=1"></a>'),
            ("/a?a=1&b=2", False): answer}
    with pytest.raises(crawl.Refused) as refused:
        _walk(site)
    assert "/a?a=1&b=2 was sent to authenticate at " in str(refused.value)
    assert (answer.headers.get("Location") or answer.headers["HX-Redirect"]) in str(
        refused.value)


def test_a_redirect_whose_next_names_another_url_is_followed():
    """Only a ``next`` naming the request itself is an authentication redirect."""
    site = {("/", False): Answer(302, headers={"Location": "/login?next=%2Fother"})}
    client, _, _ = _walk(site)
    assert _keys(client) == [("/", False), ("/login?next=%2Fother", False)]


def test_the_cap_refuses_a_walk_still_holding_requests_naming_its_busiest_family():
    """At the cap with requests left the walk ends, naming the family that fetched the most;
    a walk that fetches exactly the cap and holds nothing more is not refused."""
    site = {("/", False): Answer(body="".join(f'<a href="/p?i={i}"></a>' for i in (1, 2, 3)))}
    with pytest.raises(crawl.Refused) as refused:
        _walk(site, cap=3)
    assert str(refused.value) == ("the cap of 3 requests was reached with 1 still queued; the "
                                  "family that fetched the most is /p as a page (2)")
    _, _, walked = _walk(site, cap=4)
    assert walked.fetched == 4


def test_an_error_page_is_read_but_an_htmx_error_is_not_swapped():
    """A browser shows an error page and its links; htmx 2 swaps no 4xx or 5xx."""
    site = {
        ("/", False): Answer(body='<div hx-get="/e"></div><a href="/missing"></a>'),
        ("/e", True): Answer(500, '<a href="/never"></a>'),
        ("/missing", False): Answer(404, '<a href="/found"></a>'),
    }
    client, _, _ = _walk(site)
    assert _keys(client) == [("/", False), ("/e", True), ("/missing", False),
                             ("/found", False)]


def test_each_response_is_a_line_with_text_whole_and_anything_else_by_its_hash():
    """The line carries the request, the status, the ``Location`` and the ``HX-*`` headers;
    a text body whole, any other by its length and sha256."""
    page = '<a href="/img"></a><a href="/moved"></a>'
    site = {
        ("/", False): Answer(body=page, headers={"HX-Trigger": "loaded", "X-Other": "no"}),
        ("/img", False): Answer(body="PNG", mimetype="image/png"),
        ("/moved", False): Answer(301, headers={"Location": "https://elsewhere.example/"}),
    }
    _, lines, walked = _walk(site)
    assert lines[0] == {"url": "/", "htmx": False, "headers": [], "document": "/",
                        "status": 200, "location": None, "hx": {"HX-Trigger": "loaded"},
                        "type": "text/html", "length": len(page), "body": page}
    assert "body" not in lines[1]
    assert lines[1]["sha256"] == hashlib.sha256(b"PNG").hexdigest()
    assert lines[2]["location"] == "https://elsewhere.example/"
    assert walked.bytes == len(page) + 3
    assert len(lines) == 3


def test_a_dated_request_outside_the_span_is_not_fetched():
    """The walk follows only what :func:`frontier.followed` follows."""
    site = {("/", False): Answer(body='<a href="/cal?year=2030"></a><a href="/cal?year=2026">'
                                      '</a>')}
    span = crawl.frontier.Span(date(2026, 1, 2), date(2026, 12, 31))
    client, _, _ = _walk(site, span=span)
    assert _keys(client) == [("/", False), ("/cal?year=2026", False)]


def test_a_request_naming_nothing_new_on_its_screen_is_not_fetched():
    """The grid's scroll positions times its column counts are fetched once per value, never
    their product, and a link found twice is fetched once (:class:`frontier.Asked`)."""
    links = [f"/g?offset={offset}&periods={periods}" for periods in (6, 13) for offset in (1, 2)]
    site = {("/", False): Answer(body="".join(f'<a href="{url}"></a>' for url in links + links))}
    client, _, _ = _walk(site)
    assert _keys(client) == [("/", False), ("/g?offset=1&periods=6", False),
                             ("/g?offset=2&periods=6", False), ("/g?offset=1&periods=13", False)]


def _app():
    """An app of GET rules, one naming an account in its path and two on one view, and one POST
    rule, built by the side's own Flask (this half of the kit imports none:
    ``test_imports.py``)."""
    app = crawl.Flask(__name__)

    def home() -> str:
        """The one view both rules name."""
        return ""
    app.add_url_rule("/", "home", home)
    app.add_url_rule("/home", "home", home)
    app.add_url_rule("/accounts/<int:account_id>/day", "day", lambda account_id: "")
    app.add_url_rule("/grid", "grid", lambda: "")
    app.add_url_rule("/save", "save", lambda: "", methods=["POST"])
    return app


def test_a_screen_is_the_apps_own_rule_with_the_ids_its_path_names():
    """The rule's endpoint, the values its path names and htmx or not; a path no GET rule
    answers is a screen of its own."""
    screen = crawl.screen_of(_app())
    request = crawl.frontier.Request
    assert screen(request("/accounts/2/day?day=2026-01-02", False, (), "/")) == (
        "/accounts/<int:account_id>/day (day)", (("account_id", 2),), False)
    assert screen(request("/grid?offset=3", True, (), "/")) == ("/grid (grid)", (), True)
    assert screen(request("/save", False, (), "/")) == ("/save", False)
    assert screen(request("/nowhere", False, (), "/")) == ("/nowhere", False)


def test_two_accounts_on_one_screen_are_two_objects_each_read_on_every_day():
    """The ids a path names keep each account's days its own: a day fetched for one account is
    fetched for the other, and only once for each."""
    days = [f"/accounts/{account}/day?day={day}" for account in (1, 2)
            for day in ("2026-01-02", "2026-01-03")]
    site = {("/", False): Answer(body="".join(f'<a href="{url}"></a>' for url in days + days))}
    span = crawl.frontier.Span(date(2026, 1, 2), date(2026, 12, 31))
    client, _, _ = _walk(site, span=span, screen=crawl.screen_of(_app()))
    assert _keys(client) == [("/", False)] + [(url, False) for url in days]


def test_two_rules_of_one_view_are_two_screens():
    """``/`` and ``/dashboard`` are one view under two rules: each is fetched, as the census
    counts each rule reached."""
    site = {("/", False): Answer(body='<a href="/home"></a><a href="/"></a>')}
    client, _, _ = _walk(site, screen=crawl.screen_of(_app()))
    assert _keys(client) == [("/", False), ("/home", False)]
