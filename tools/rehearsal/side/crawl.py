"""The side probe's crawl (step X-cx's K1c-2): every page a user's browser reaches from ``/``,
fetched from a side's app on a deployed copy under the pinned instant, each user logged in by
the app's own two calls, and every response written down for the orchestrator to read.

Run BY PATH under the side's venv, with the side's worktree as its directory, in the
environment :func:`tools.rehearsal.probes.side_environment` builds::

    <side venv>/bin/python <kit>/tools/rehearsal/side/crawl.py <ISO 8601 instant> <directory>

The directory holds the plan, ``plan.json``: ``{"cap": <requests per user>, "users": [<user
id>, ...]}``; what the crawl writes goes beside it.  ``app`` IS THE SIDE'S and the clock is the
pinned one, as ``side/boot.py`` has them: the worktree is put first on ``sys.path`` and the
clock frozen before ``app`` is imported.  The crawl's rules are ``side/frontier.py``, LOADED
BY PATH: pylint resolves ``side/`` as a package, so a sibling imported by name is E0401
(measured 2026-10-20, ``k1c/plan_corrections.md`` entry 2).

THE LIMITER IS OFF (K1c's plan s.2, option A): the app's ``limiter`` is disabled once the app
is built, before its first request.  Its default limits reach every crawled GET, its store is
one no side probe can reach, and the pinned clock would never let a window expire.  So a cut
that breaks the limiter's store, or changes a limit, is not seen here.

ONE USER AT A TIME, in the plan's order:

* THE LOGIN CONTROL, on a client of its own: an anonymous ``/`` must be an AUTHENTICATION
  REDIRECT (below), so the one rule that stops a crawl which lost its login is shown firing on
  every run, before it is relied on.
* THE LOGIN, as ``/login``'s POST completes one: ``login_user``, then ``stamp_login_session``,
  in a request context carrying the crawl client's own address and agent (strong session
  protection compares them on every request), the session then written into that client's.
  Every request the client makes is ``https://localhost``, since the session cookie is
  ``Secure``.
* THE SPAN: the user's pay periods, the first one's first day to the last one's last, from the
  side's own calendar (``calendar_for``, ``opening_payday``, ``final_covered_day``), so a cut's
  calendar is crawled under itself (``k1c/plan_corrections.md`` entry 3).  A user the
  calendar refuses, or who holds no pay period, has no span: no dated request is followed, and
  why is written down.
* THE WALK, breadth first from ``/``, over :func:`frontier.issued` and :func:`frontier.followed`,
  each request fetched once (:meth:`frontier.Request.key`):

  - a 3xx's ``Location`` is followed as the same kind of request: htmx keeps its headers, as
    the browser's XHR does, and its response is swapped into the same page;
  - an ``HX-Redirect`` is followed as a page, since htmx sends the browser there;
  - an HTML response is read as a page unless htmx asked for it, and then only a 2xx is read,
    as a fragment of the page it is swapped into (htmx 2 swaps no 4xx or 5xx); a redirect is
    followed, never read;
  - AN AUTHENTICATION REDIRECT ends the crawl, exit 2, naming both URLs: a ``Location`` or an
    ``HX-Redirect`` whose ``next`` names the URL it answered.  It is how the login gate and the
    reauth door send a browser away (the app's only two redirects that carry ``next``), so the
    session was lost or is stale, and the crawl would measure that door instead of the page
    (``k1c/plan_corrections.md`` entry 4).  The walk's first request is ``/``, so the login
    control's second case, that a logged-in ``/`` is NOT one, is this rule;
  - THE CAP: a walk still holding requests when it has fetched the plan's cap ends, exit 2,
    naming the family (the path, and page or htmx) that fetched the most.

WHAT IT WRITES: ``<directory>/<user>.jsonl``, one line per response in the order fetched (the
request, the status, the ``Location``, the response's ``HX-*`` headers, and the body: text and
JSON whole, anything else by its length and sha256); then ``<directory>/summary.json``: per
user the requests, seconds (``time.monotonic``, which the pin does not freeze), bytes, span, top
families and what the rules could not assemble, and the app's GET rules no fetched URL
matched.  It exits
0 once every user is crawled, 2 with the reason on a refusal; any other failure ends it with its
traceback.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import os
import sys
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import time_machine
from flask import Flask, Response, request, session
from flask.testing import FlaskClient
from flask_login import login_user

#: The crawl's rules, beside this file.
_RULES = Path(__file__).with_name("frontier.py")
#: The origin every request is made on: ``https``, since the session cookie is ``Secure``.
_ORIGIN = "https://localhost"
#: The one address (TEST-NET-1, RFC 5737) and agent every request of the crawl carries.
_CLIENT = {"REMOTE_ADDR": "192.0.2.1", "HTTP_USER_AGENT": "shekel-rehearsal-crawl"}
#: The plan's file, in the crawl's directory.
PLAN = "plan.json"
#: The summary's file, in the crawl's directory.
SUMMARY = "summary.json"
#: Where a user's walk starts.
_START = "/"
#: How many of a user's busiest families the summary names.
_TOP_FAMILIES = 10
#: The exit status of a crawl that refused to measure.
_REFUSED = 2


def _load_rules() -> ModuleType:
    """``side/frontier.py``, loaded by path under a name no package holds, registered in
    ``sys.modules`` before it runs (its dataclasses look their module up there)."""
    spec = importlib.util.spec_from_file_location("rehearsal_side_frontier", _RULES)
    if spec is None or spec.loader is None:
        raise ImportError(f"{_RULES} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


frontier = _load_rules()


#: A request the walk will fetch, with the page its response is swapped into (None for a page).
Queued = tuple["frontier.Request", "frontier.Page | None"]


class Refused(Exception):
    """A crawl that cannot measure what it was asked to; it exits 2 with this reason."""


@dataclass(frozen=True)
class Calendar:
    """The side's pay calendar: its producer of a user's calendar, the first and last days of
    its periods, and the error it refuses a user with."""

    of: Callable[[int], object]
    opening: Callable[[object], datetime.date | None]
    final: Callable[[object], datetime.date | None]
    refusal: type[Exception]


@dataclass(frozen=True)
class Side:
    """The side's app, built with its limiter off, and the parts of ``app`` the crawl calls."""

    app: Flask
    db: object
    user: type
    stamp: Callable[[datetime.datetime], None]
    calendar: Calendar


@dataclass
class Walked:
    """One user's walk: how many requests it fetched, their bytes, each family's count, the
    URLs fetched, and what the rules could not assemble."""

    fetched: int = 0
    bytes: int = 0
    families: Counter[tuple[str, str]] = field(default_factory=Counter)
    urls: list[str] = field(default_factory=list)
    unassembled: list[str] = field(default_factory=list)

    def took(self, asked: frontier.Request, body: bytes) -> None:
        """Count one fetched request and its body."""
        self.fetched += 1
        self.bytes += len(body)
        self.families[_family(asked)] += 1
        self.urls.append(asked.url)

    def capped(self, cap: int, queued: int) -> Refused:
        """The refusal of a walk that fetched ``cap`` with ``queued`` requests left, naming the
        family that fetched the most."""
        (path, kind), count = self.families.most_common(1)[0]
        return Refused(f"the cap of {cap} requests was reached with {queued} still queued; the "
                       f"family that fetched the most is {path} as a {kind} ({count})")


def sent_to(response: Response) -> str | None:
    """Where ``response`` sends the browser: a 3xx's ``Location``, else its ``HX-Redirect``
    (htmx sets the browser's location to it), else None."""
    if 300 <= response.status_code < 400:
        return response.headers.get("Location")
    return response.headers.get("HX-Redirect")


def _asked(url: str) -> str | None:
    """``url``'s path and query, canonical (:func:`frontier.canonical`), its origin dropped."""
    parts = urlsplit(url)
    return frontier.canonical(urlunsplit(("", "", parts.path, parts.query, "")))


def authenticating(target: str, asked: str) -> bool:
    """Whether a redirect to ``target``, answering the canonical URL ``asked``, is an
    authentication redirect: one whose ``next`` names ``asked``."""
    return any(name == "next" and _asked(value) == asked
               for name, value in parse_qsl(urlsplit(target).query, keep_blank_values=True))


def _family(asked: frontier.Request) -> tuple[str, str]:
    """The family ``asked`` counts in: its path, and whether htmx or the browser asked."""
    return urlsplit(asked.url).path, "htmx" if asked.htmx else "page"


def line(asked: frontier.Request, response: Response, body: bytes) -> dict:
    """What the JSONL holds for one response: the request, the status, the ``Location``, the
    response's ``HX-*`` headers, and the body (text and JSON whole, else its length and
    sha256)."""
    written = {
        "url": asked.url, "htmx": asked.htmx, "headers": [list(pair) for pair in asked.headers],
        "document": asked.document, "status": response.status_code,
        "location": response.headers.get("Location"),
        "hx": {name: value for name, value in response.headers.items()
               if name.lower().startswith("hx-")},
        "type": response.mimetype, "length": len(body),
    }
    if response.mimetype.startswith("text/") or response.is_json:
        written["body"] = body.decode("utf-8")
    else:
        written["sha256"] = hashlib.sha256(body).hexdigest()
    return written


def _redirected(asked: frontier.Request, host: frontier.Page | None, response: Response,
                target: str) -> list[Queued]:
    """The one request a redirect to ``target`` makes, with the page its response is swapped
    into: a ``Location`` as the same kind of request, an ``HX-Redirect`` as a page."""
    if 300 <= response.status_code < 400:
        url = frontier.canonical(urljoin(asked.url, target))
        if url is None:
            return []
        if asked.htmx:
            return [(frontier.Request(url, True, asked.headers, asked.document), host)]
        return [(frontier.Request(url, False, (), url), None)]
    url = frontier.canonical(urljoin(asked.document, target))
    return [] if url is None else [(frontier.Request(url, False, (), url), None)]


def _onward(asked: frontier.Request, host: frontier.Page | None, response: Response,
           body: bytes, walked: Walked) -> list[Queued]:
    """Every request the response to ``asked`` makes next, each with the page its own response
    is swapped into (None for a page).

    Raises:
        Refused: when the response is an authentication redirect.
    """
    target = sent_to(response)
    if target is not None:
        if authenticating(target, asked.url):
            raise Refused(f"{asked.url} was sent to authenticate at {target}: the crawl's "
                          "session was lost or is no longer fresh")
        return _redirected(asked, host, response, target)
    if response.mimetype != "text/html" or (asked.htmx
                                            and not 200 <= response.status_code < 300):
        return []
    page = frontier.Page(asked.url, asked.document, body.decode("utf-8"),
                         host if asked.htmx else None)
    found = frontier.issued(page)
    walked.unassembled.extend(found.unassembled)
    swapped_into = page.within or page
    return [(issued, swapped_into if issued.htmx else None) for issued in found.requests]


def walk(client: FlaskClient, span: frontier.Span | None, cap: int,
         write: Callable[[dict], None]) -> Walked:
    """Fetch every request a user's browser reaches from ``/`` through ``client``, breadth
    first, each once, a dated one only inside ``span``; ``write`` takes each response's line.

    Raises:
        Refused: on an authentication redirect, or when the walk still holds requests once it
            has fetched ``cap``.
    """
    start = frontier.Request(_START, False, (), _START)
    queue: deque[Queued] = deque([(start, None)])
    seen = {start.key()}
    walked = Walked()
    while queue:
        if walked.fetched >= cap:
            raise walked.capped(cap, len(queue))
        asked, host = queue.popleft()
        response = client.get(asked.url, base_url=_ORIGIN, headers=list(asked.headers))
        body = response.get_data()
        walked.took(asked, body)
        write(line(asked, response, body))
        for onward, its_host in _onward(asked, host, response, body, walked):
            if onward.key() not in seen and frontier.followed(onward, span):
                seen.add(onward.key())
                queue.append((onward, its_host))
    return walked


def _import_side() -> Side:
    """The side's app, built, its limiter switched off before its first request, with the
    parts of ``app`` the crawl calls."""
    # Pylint: ``import-outside-toplevel`` -- ``app`` must be the side's, which resolves only
    # once ``main`` has put its tree first on ``sys.path``, and its import-time code must read
    # the pinned clock.
    # pylint: disable=import-outside-toplevel
    from app import create_app
    from app.extensions import db, limiter
    from app.models.user import User
    from app.services.pay_calendar import (PayCalendarError, calendar_for, final_covered_day,
                                           opening_payday)
    from app.utils.session_helpers import stamp_login_session
    flask_app = create_app()
    limiter.enabled = False
    return Side(flask_app, db, User, stamp_login_session,
                Calendar(calendar_for, opening_payday, final_covered_day, PayCalendarError))


def _login_control(side: Side) -> None:
    """An anonymous ``/``, on a client of its own, must be an authentication redirect.

    Raises:
        Refused: when it is not.
    """
    anonymous = side.app.test_client()
    anonymous.environ_base.update(_CLIENT)
    response = anonymous.get(_START, base_url=_ORIGIN)
    target = sent_to(response)
    if target is None or not authenticating(target, _START):
        raise Refused(f"the login control: an anonymous {_START} answered "
                      f"{response.status_code}, sending to {target!r}, not an authentication "
                      "redirect, so a lost login could not be told from a page")


def _log_in(side: Side, client: FlaskClient, user_id: int) -> None:
    """Log ``user_id`` in on ``client`` as ``/login``'s POST completes a login.

    Raises:
        Refused: when the user does not exist or is not active.
    """
    with side.app.test_request_context(base_url=_ORIGIN, environ_base=_CLIENT):
        user = side.db.session.get(side.user, user_id)
        if user is None or not login_user(user):
            raise Refused(f"user {user_id} cannot be logged in: "
                          f"{'no such user' if user is None else 'not active'}")
        side.stamp(datetime.datetime.now(datetime.timezone.utc))
        state = dict(session)
    with client.session_transaction(base_url=_ORIGIN) as written:
        written.update(state)


def _span(side: Side, user_id: int) -> tuple[frontier.Span | None, str | None]:
    """The user's pay periods as a :class:`frontier.Span` and None, or None and why there is
    none."""
    with side.app.app_context():
        try:
            periods = side.calendar.of(user_id).periods
        except side.calendar.refusal as refused:
            return None, f"the calendar refused: {refused}"
        first, last = side.calendar.opening(periods), side.calendar.final(periods)
    if first is None or last is None:
        return None, "no pay period"
    return frontier.Span(first, last), None


def _crawl_user(side: Side, user_id: int, cap: int, out: Path) -> tuple[dict, list[str]]:
    """Log ``user_id`` in and walk; the user's summary, and the URLs it fetched."""
    client = side.app.test_client()
    client.environ_base.update(_CLIENT)
    _log_in(side, client, user_id)
    span, no_span = _span(side, user_id)
    started = time.monotonic()
    with (out / f"{user_id}.jsonl").open("w", encoding="utf-8") as log:
        walked = walk(client, span, cap, lambda written: log.write(json.dumps(written) + "\n"))
    return {
        "requests": walked.fetched, "seconds": round(time.monotonic() - started, 3),
        "bytes": walked.bytes,
        "span": None if span is None else [span.first.isoformat(), span.last.isoformat()],
        "no_span": no_span,
        "families": [[path, kind, count] for (path, kind), count
                     in walked.families.most_common(_TOP_FAMILIES)],
        "unassembled": walked.unassembled,
    }, walked.urls


def census(app: Flask, urls: set[str]) -> dict:
    """How many of the app's GET rules a fetched URL matched, and every rule none did, each
    named by its pattern and endpoint (a rule compares by value, so it cannot be hashed)."""
    reached: set[str] = set()
    for url in urls:
        with app.test_request_context(url, base_url=_ORIGIN):
            if request.url_rule is not None:
                reached.add(_named(request.url_rule))
    rules = [_named(rule) for rule in app.url_map.iter_rules() if "GET" in (rule.methods or ())]
    return {"get": len(rules), "reached": sum(rule in reached for rule in rules),
            "unreached": sorted(rule for rule in rules if rule not in reached)}


def _named(rule) -> str:
    """A rule as its pattern and its endpoint."""
    return f"{rule.rule} ({rule.endpoint})"


def crawl(side: Side, users: list[int], cap: int, out: Path) -> dict:
    """The login control, then each user crawled in turn; the state's summary.

    Raises:
        Refused: as :func:`walk`, :func:`_log_in` and the login control refuse.
    """
    if cap < 1:
        raise Refused(f"the cap is {cap}, which fetches nothing")
    _login_control(side)
    summary: dict[str, dict] = {}
    fetched: set[str] = set()
    for user_id in users:
        summary[str(user_id)], urls = _crawl_user(side, user_id, cap, out)
        fetched.update(urls)
    return {"users": summary, "rules": census(side.app, fetched)}


def main() -> int:
    """Freeze the clock at ``sys.argv[1]``, import the side's app, and crawl as the plan in the
    directory ``sys.argv[2]`` says, into that directory."""
    instant = datetime.datetime.fromisoformat(sys.argv[1])
    out = Path(sys.argv[2])
    plan = json.loads((out / PLAN).read_text(encoding="utf-8"))
    sys.path[0] = os.getcwd()
    with time_machine.travel(instant, tick=False):
        side = _import_side()
        try:
            summary = crawl(side, plan["users"], plan["cap"], out)
        except Refused as refused:
            print(f"the crawl refused: {refused}", file=sys.stderr)
            return _REFUSED
    (out / SUMMARY).write_text(json.dumps(summary, indent=1, sort_keys=True),
                                      encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
