"""The crawl's rules (step X-cx's K1c-1): every GET request a user's browser issues from one
page the app served, assembled as the browser and htmx 2.0 assemble it, and the bound that
keeps the crawl finite (``design.md`` s.3 item 1; ``k1c/plan.md`` s.3).

Run BY PATH, never imported by name (``tools/rehearsal/__init__.py`` says why): the crawl
probe loads it from its own directory, and its tests load it by path.  Standard library only.

WHAT A PAGE ISSUES, in document order:

* a LINK: every ``<a href>`` that resolves to a path of this site, its fragment dropped;
* a GET FORM: a ``<form>`` whose method is GET (the browser's default) and that htmx does not
  submit, its controls serialised as the browser serialises them with no submit button, and
  each single ``<select>`` of it varied ONE OPTION AT A TIME, every other control at the page's
  value, never their product;
* an HX-GET, as htmx 2.0's ``issueAjaxRequest`` builds it for GET: the element's own value
  (a form's controls, for a form; a button's name and value, for a button), then
  ``hx-include`` (inherited, ``hx-disinherit`` honoured; ``this``, ``closest``, ``find`` and
  plain selectors; a non-form's controls within it), then ``hx-vals`` (JSON, merged up the
  ancestors, the nearest key winning, replacing every control of its name), appended to the
  URL's query; and the headers ``HX-Request``, ``HX-Current-URL``, ``HX-Trigger``,
  ``HX-Trigger-Name`` and ``HX-Target``.  A ``<select>`` that issues one is varied one option
  at a time.  Every trigger counts: a load and a click are both requests a user's browser
  issues.

THE DOCUMENT.  A fragment is swapped into the page that requested it, so its links resolve,
and its ``HX-Current-URL`` reads, against that page's URL; ``hx-push-url`` makes a request's
own URL the document's for what its response issues.  A plain selector is looked for in the
fragment, and only when nothing there matches, in the page it was swapped into AS SERVED: a
LIMIT, since earlier swaps may have changed that page in a browser.  A page is parsed once,
the first time it is read (:attr:`Page.tree`), so the crawl hands every fragment swapped into
one page that same :class:`Page`.

WHAT IT CANNOT ASSEMBLE IS NAMED, never guessed (:attr:`Found.unassembled`): an ``hx-vals``
evaluated as script, a selector in a form this module does not read, an ``hx-target`` found
nowhere (the request is kept, without ``HX-Target``, where htmx issues none: the page as
served may lack a target an earlier swap made).

WHERE IT DEPARTS FROM A BROWSER, each found by K1c-1's review against htmx 2.0.10's source;
on the seeded pages that review read, only the ``required`` one met a template
(``salary/stub_payday.html``, whose empty submit reads a 422):

* in a fragment, ``closest`` and every inherited attribute stop at the fragment's own root,
  where a browser's carry on into the page around the swap; a plain selector falls back to
  that page part by part, never as the union a browser's ``querySelectorAll`` returns;
* ``hx-include``'s parts are read in the order written, where htmx reads ``closest`` and
  ``find`` parts first, so a control outside a form that the form also names is sent once;
* ``hx-push-url`` is read on the element only, and any value but ``false`` pushes the
  request's own URL, where htmx inherits it and pushes a URL it is given;
* a disabled option is varied, and a form with an empty ``required`` control is submitted,
  where a browser allows neither;
* ``HX-Current-URL`` carries the document's path and query, not the browser's whole address
  (its one reader, ``app/routes/_render_helpers.py``, reads only the query);
* a ``year`` of ``0000``, an ISO month ``0000-01`` or a month at ``9999-12`` raises in
  :func:`followed`;
* :meth:`Node.walk` recurses, so a page nesting 1,000 elements raises ``RecursionError``
  (measured 2026-10-20 at Python's default limit: 950 reads);
* ``hx-vals``: see :func:`_vals` and :func:`_js_string`.

THE KEY (:meth:`Request.key`): a request is fetched once, keyed by its path and its query,
keys sorted (:func:`canonical`), and by whether htmx issues it, since a view answers htmx
with a fragment and a link with a page; its headers ride with its first finding.

THE BOUND (:func:`followed`): a request whose query names a date outside the user's
pay-period span is not followed: an ISO day or month value under any key, and a ``year``
value with its ``month``.  A user with no pay period follows no dated request.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cached_property
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

#: Elements that have no end tag, so the tree never opens them.
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                   "param", "source", "track", "wbr"})
#: The controls a form serialises and htmx reads values from (htmx's ``INPUT_SELECTOR``).
_CONTROLS = frozenset({"input", "select", "textarea"})
#: Control types never serialised: the button kinds (no submitter is pressed) and files.
_UNSENT = frozenset({"button", "submit", "image", "reset", "file"})
#: The verbs htmx issues; an element with any of them is submitted by htmx, not the browser.
_HX_VERBS = ("hx-get", "hx-post", "hx-put", "hx-patch", "hx-delete")
#: One part of a plain selector: ``#id``, ``.class`` or ``[attribute]`` / ``[attribute=v]``.
_PART = re.compile(r"#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=(\"[^\"]*\"|'[^']*'|[^\]]*))?\]")
#: The words htmx's ``querySelectorAllExt`` reads as places rather than tags, which this
#: module does not read (``closest`` and ``find`` it does).
_KEYWORDS = frozenset({"next", "nextElementSibling", "previous", "previousElementSibling",
                       "document", "window", "root", "host", "global"})
_TAG = re.compile(r"[a-zA-Z][\w-]*")
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISO_MONTH = re.compile(r"\d{4}-\d{2}")
_YEAR = re.compile(r"\d{4}")
#: A pair of a query or a form, in order.
Pair = tuple[str, str]


@dataclass(eq=False)
class Node:
    """One element of a page: its tag, its attributes (the first of a repeated one), its
    parent, its child elements in order, and the text directly inside it."""

    tag: str
    attrs: dict[str, str]
    parent: Node | None = None
    children: list[Node] = field(default_factory=list)
    text: list[str] = field(default_factory=list)

    def walk(self) -> Iterator[Node]:
        """This element's descendants in document order, itself excluded."""
        for child in self.children:
            yield child
            yield from child.walk()

    def lineage(self) -> Iterator[Node]:
        """This element, then each ancestor."""
        node: Node | None = self
        while node is not None:
            yield node
            node = node.parent


class _Tree(HTMLParser):
    """Reads a page into :class:`Node` elements under one root.  An end tag closes the
    nearest open element of its name and everything opened inside it; one with no open
    element of its name is ignored.  An ``<option>`` left open nests the next inside it,
    which reads the same: options are found among all a select's descendants, each by its
    own attributes and its own text."""

    def __init__(self) -> None:
        """An empty root, open."""
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self._open = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Add the element to the open one, and open it unless it is void."""
        given: dict[str, str] = {}
        for name, value in attrs:
            given.setdefault(name, value or "")
        node = Node(tag, given, self._open[-1])
        self._open[-1].children.append(node)
        if tag not in _VOID:
            self._open.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """A self-closed element is added and never opened."""
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self._open.pop()

    def handle_endtag(self, tag: str) -> None:
        """Close the nearest open element named ``tag``, if one is open."""
        for depth in range(len(self._open) - 1, 0, -1):
            if self._open[depth].tag == tag:
                del self._open[depth:]
                return

    def handle_data(self, data: str) -> None:
        """Keep the text in the element it sits in."""
        self._open[-1].text.append(data)


def parse(body: str) -> Node:
    """The page ``body`` as a tree, under its root."""
    tree = _Tree()
    tree.feed(body)
    tree.close()
    return tree.root


@dataclass(frozen=True)
class Page:
    """One response the crawl read: the canonical URL of the request that fetched it, the
    URL of the document it belongs to (its own, for a page; the requesting page's, for a
    fragment), its body, and, for a fragment, the page it was swapped into."""

    url: str
    document: str
    body: str
    within: Page | None = None

    @cached_property
    def tree(self) -> Node:
        """The body as a tree (:func:`parse`), parsed the first time it is read and kept: a page
        whose fragments are read one by one is parsed once, not once per fragment."""
        return parse(self.body)


@dataclass(frozen=True)
class Request:
    """One GET request a page issues: its canonical URL, whether htmx issues it, its headers,
    and the URL of the document its response belongs to."""

    url: str
    htmx: bool
    headers: tuple[Pair, ...]
    document: str

    def key(self) -> tuple[str, bool]:
        """What makes two requests one: the URL, and whether htmx issues it."""
        return self.url, self.htmx


@dataclass
class Found:
    """What a page issues: every request in document order, and what could not be assembled,
    each named."""

    requests: list[Request] = field(default_factory=list)
    unassembled: list[str] = field(default_factory=list)


def canonical(url: str) -> str | None:
    """``url`` as a path of this site with its query's pairs ordered by key (a repeated key's
    values in their order) and its fragment dropped; None when it names another site or a
    scheme (``mailto:``, ``javascript:``)."""
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return None
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True),
                             key=lambda pair: pair[0]))
    return urlunsplit(("", "", parts.path or "/", query, ""))


def _option_value(option: Node) -> str:
    """An ``<option>``'s value: its attribute, else its text with whitespace collapsed."""
    if "value" in option.attrs:
        return option.attrs["value"]
    return " ".join("".join(option.text).split())


def _options(select: Node) -> list[Node]:
    """A ``<select>``'s options in order, those inside an ``<optgroup>`` included."""
    return [node for node in select.walk() if node.tag == "option"]


def _selected(select: Node) -> list[str]:
    """What a ``<select>`` submits as served: its selected options' values, else, for a
    single select, its first option that is not disabled."""
    options = _options(select)
    chosen = [_option_value(option) for option in options if "selected" in option.attrs]
    if chosen or "multiple" in select.attrs:
        return chosen if "multiple" in select.attrs else chosen[-1:]
    enabled = [option for option in options if "disabled" not in option.attrs]
    return [_option_value(enabled[0])] if enabled else []


def _sent(control: Node) -> bool:
    """Whether a control is serialised: named, enabled, not a button kind or a file, and,
    for a checkbox or radio, checked."""
    kind = control.attrs.get("type", "").lower()
    if not control.attrs.get("name") or "disabled" in control.attrs or kind in _UNSENT:
        return False
    if any(node.tag == "fieldset" and "disabled" in node.attrs
           for node in control.lineage()):
        return False
    if control.tag == "input" and kind in ("checkbox", "radio"):
        return "checked" in control.attrs
    return control.tag in _CONTROLS


def _values(control: Node, chosen: dict[int, str]) -> list[str]:
    """A control's values, a ``<select>`` named in ``chosen`` (by ``id()``) at that option."""
    if control.tag == "select":
        return [chosen[id(control)]] if id(control) in chosen else _selected(control)
    if control.tag == "textarea":
        return ["".join(control.text).removeprefix("\n")]
    kind = control.attrs.get("type", "").lower()
    return [control.attrs.get("value", "on" if kind in ("checkbox", "radio") else "")]


def _pairs(control: Node, chosen: dict[int, str]) -> list[Pair]:
    """What one control contributes, when it is serialised."""
    if not _sent(control):
        return []
    return [(control.attrs["name"], value) for value in _values(control, chosen)]


def _form_pairs(form: Node, chosen: dict[int, str]) -> list[Pair]:
    """A form's controls serialised in document order."""
    return [pair for node in form.walk() if node.tag in _CONTROLS
            for pair in _pairs(node, chosen)]


def _override(pairs: list[Pair], donor: list[Pair]) -> list[Pair]:
    """htmx's ``overrideFormData``: every key of ``donor`` replaces that key's pairs."""
    keys = {name for name, _ in donor}
    return [pair for pair in pairs if pair[0] not in keys] + donor


def _matches(node: Node, selector: str) -> bool:
    """Whether ``node`` matches a plain compound selector (a tag, ids, classes, attributes)."""
    tag = _TAG.match(selector)
    rest = selector[tag.end():] if tag else selector
    if tag and tag.group(0).lower() != node.tag:
        return False
    position = 0
    for part in _PART.finditer(rest):
        if part.start() != position:
            return False
        position = part.end()
        ident, klass, name, value = part.groups()
        if ident is not None and node.attrs.get("id") != ident:
            return False
        if klass is not None and klass not in node.attrs.get("class", "").split():
            return False
        if name is not None and (name not in node.attrs or (
                value is not None and node.attrs[name] != value.strip("\"'"))):
            return False
    return position == len(rest)


def _plain(selector: str) -> bool:
    """Whether ``selector`` is a plain compound selector :func:`_matches` reads."""
    tag = _TAG.match(selector)
    rest = selector[tag.end():] if tag else selector
    spans = [part.span() for part in _PART.finditer(rest)]
    return bool(selector) and "".join(rest[a:b] for a, b in spans) == rest


@dataclass(frozen=True)
class _Scope:
    """Where a request is assembled: the fragment's tree, and the page it was swapped into
    as served (None for a document)."""

    tree: Node
    within: Node | None

    def select(self, element: Node, selector: str, found: Found, where: str) -> list[Node]:
        """htmx's ``querySelectorAllExt`` over the forms this module reads; a part in any
        other form is named in ``found`` and selects nothing."""
        result: list[Node] = []
        for part in (piece.strip() for piece in selector.split(",")):
            head, _, tail = part.partition(" ")
            if head in ("closest", "find") and _plain(tail):
                nodes = element.lineage() if head == "closest" else element.walk()
                first = next((node for node in nodes if _matches(node, tail)), None)
                if first is not None:
                    result.append(first)
            elif head not in _KEYWORDS and _plain(part):
                result.extend(self._everywhere(part))
            else:
                found.unassembled.append(f"{where}: the selector {part!r} is not read")
        return result

    def _everywhere(self, selector: str) -> list[Node]:
        """Every element matching ``selector`` in the fragment, else in the page as served."""
        here = [node for node in self.tree.walk() if _matches(node, selector)]
        if here or self.within is None:
            return here
        return [node for node in self.within.walk() if _matches(node, selector)]


def _inherited(element: Node, name: str) -> tuple[Node, str] | None:
    """htmx's ``getClosestAttributeValue``: the nearest of the element and its ancestors that
    carries attribute ``name`` (or ``data-name``), with its value; None when there is none, or
    when an ancestor's ``hx-disinherit`` names it (or ``*``) first, that ancestor's own value
    included (htmx's ``getAttributeValueWithDisinheritance``)."""
    for node in element.lineage():
        disinherit = node.attrs.get("hx-disinherit", "").split()
        if node is not element and ("*" in disinherit or name in disinherit):
            return None
        value = node.attrs.get(name, node.attrs.get(f"data-{name}"))
        if value is not None:
            return node, value
    return None


def _js_string(value: object) -> list[str]:
    """A JSON value as ``FormData.append`` stringifies it, an array one value per item.  An
    object is written ``[object Object]``, which is htmx's for an object inside an array; a
    top-level one htmx JSON-encodes (``formDataFromObject``), a departure no template's
    ``hx-vals`` exercises."""
    if isinstance(value, list):
        return [text for item in value for text in _js_string(item)]
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if value is None:
        return ["null"]
    if isinstance(value, float) and value.is_integer():
        return [str(int(value))]
    if isinstance(value, dict):
        return ["[object Object]"]
    return [str(value)]


def _vals(element: Node, found: Found, where: str) -> list[Pair]:
    """htmx's ``getExpressionVars`` for ``hx-vals``: each of the element's and its ancestors'
    JSON objects, the nearest key winning, a ``null`` included (htmx lets a farther value win
    over a nearest ``null``, a departure no template exercises); one evaluated as script is
    named and adds nothing."""
    merged: dict[str, object] = {}
    for node in element.lineage():
        text = node.attrs.get("hx-vals", node.attrs.get("data-hx-vals"))
        if text is None:
            continue
        text = text.strip()
        if text.startswith(("js:", "javascript:")):
            found.unassembled.append(f"{where}: hx-vals evaluated as script: {text!r}")
            continue
        try:
            given = json.loads(text if text.startswith("{") else "{" + text + "}")
        except json.JSONDecodeError:
            found.unassembled.append(f"{where}: hx-vals is not JSON: {text!r}")
            continue
        for key, value in given.items():
            merged.setdefault(key, value)
    return [(key, text) for key, value in merged.items() for text in _js_string(value)]


def _element_pairs(element: Node, chosen: dict[int, str], scope: _Scope, found: Found,
                   where: str) -> list[Pair]:
    """htmx's ``getInputValues`` for GET, then ``hx-vals`` over it: the element's own value
    (a form's controls), a button's name and value, and each ``hx-include``d element's (a
    non-form's controls within it), every control read once."""
    seen: set[int] = set()
    pairs: list[Pair] = []

    def take(node: Node) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        pairs.extend(_pairs(node, chosen))
        if node.tag == "form":
            form = _form_pairs(node, chosen)
            seen.update(id(control) for control in node.walk())
            pairs[:] = _override(pairs, form)

    take(element)
    priority: list[Pair] = []
    kind = element.attrs.get("type", "").lower()
    if element.tag == "button" or (element.tag == "input" and kind == "submit"):
        if element.attrs.get("name"):
            priority.append((element.attrs["name"], element.attrs.get("value", "")))
    include = _inherited(element, "hx-include")
    if include is not None:
        holder, selector = include
        for node in ([holder] if selector.strip() == "this"
                     else scope.select(element, selector, found, where)):
            take(node)
            if node.tag != "form":
                for control in node.walk():
                    if control.tag in _CONTROLS:
                        take(control)
    return _override(_override(pairs, priority), _vals(element, found, where))


def _with_query(url: str, pairs: list[Pair]) -> str:
    """``url`` with ``pairs`` appended to its query, as htmx appends a GET's values."""
    if not pairs:
        return url
    return url + ("&" if "?" in url else "?") + urlencode(pairs)


def _target_id(element: Node, scope: _Scope, found: Found, where: str) -> str | None:
    """The ``id`` of the element htmx swaps into (``hx-target``, inherited; the element itself
    without one); None when it has none or is found nowhere, the latter named."""
    target = _inherited(element, "hx-target")
    if target is None:
        return element.attrs.get("id")
    holder, selector = target
    nodes = ([holder] if selector.strip() == "this"
             else scope.select(element, selector, found, where))
    if not nodes:
        found.unassembled.append(f"{where}: hx-target {selector!r} is found nowhere; "
                                 "issued without HX-Target")
        return None
    return nodes[0].attrs.get("id")


def _variants(selects: list[Node]) -> list[dict[int, str]]:
    """The page's values, then each select at each of its other options in turn, one at a
    time, never their product (``design.md`` s.3 item 1's bound)."""
    variants: list[dict[int, str]] = [{}]
    for select in selects:
        served = _selected(select)
        for option in dict.fromkeys(_option_value(node) for node in _options(select)):
            if [option] != served:
                variants.append({id(select): option})
    return variants


def _single(node: Node) -> bool:
    """Whether ``node`` is a serialised single ``<select>``, which is varied."""
    return node.tag == "select" and "multiple" not in node.attrs and _sent(node)


def _hx_get(element: Node, page: Page, scope: _Scope, found: Found) -> Iterator[Request]:
    """The requests one ``hx-get`` element issues, a single select that issues it varied."""
    where = f"{page.url}: <{element.tag} id={element.attrs.get('id')!r}>"
    url = urljoin(page.document, element.attrs.get("hx-get", element.attrs.get("data-hx-get",
                                                                               "")))
    headers = [("HX-Request", "true"), ("HX-Current-URL", page.document)]
    for header, value in (("HX-Trigger", element.attrs.get("id")),
                          ("HX-Trigger-Name", element.attrs.get("name")),
                          ("HX-Target", _target_id(element, scope, found, where))):
        if value is not None:
            headers.append((header, value))
    pushed = element.attrs.get("hx-push-url", "false") not in ("false", "")
    for chosen in _variants([element] if _single(element) else []):
        target = canonical(_with_query(url, _element_pairs(element, chosen, scope, found,
                                                           where)))
        if target is not None:
            yield Request(target, True, tuple(headers), target if pushed else page.document)


def _get_form(form: Node, page: Page) -> Iterator[Request]:
    """The requests a browser GET form issues, each single select varied in turn."""
    action = urljoin(page.document, form.attrs.get("action", ""))
    base = action.split("?", 1)[0]
    selects = [node for node in form.walk() if _single(node)]
    for chosen in _variants(selects):
        target = canonical(_with_query(base, _form_pairs(form, chosen)))
        if target is not None:
            yield Request(target, False, (), target)


def issued(page: Page) -> Found:
    """Every GET request ``page`` issues, in document order, and what it could not assemble."""
    tree = page.tree
    scope = _Scope(tree, page.within.tree if page.within is not None else None)
    found = Found()
    for node in tree.walk():
        if node.tag == "a" and "href" in node.attrs:
            target = canonical(urljoin(page.document, node.attrs["href"]))
            if target is not None:
                found.requests.append(Request(target, False, (), target))
        if any(verb in node.attrs or f"data-{verb}" in node.attrs for verb in _HX_VERBS):
            if "hx-get" in node.attrs or "data-hx-get" in node.attrs:
                found.requests.extend(_hx_get(node, page, scope, found))
        elif node.tag == "form" and node.attrs.get("method", "get").lower() == "get":
            found.requests.extend(_get_form(node, page))
    return found


@dataclass(frozen=True)
class Span:
    """A user's pay periods: the first one's first day and the last one's last day."""

    first: date
    last: date

    def holds(self, start: date, end: date) -> bool:
        """Whether the days ``start`` to ``end`` meet the span."""
        return start <= self.last and end >= self.first


def _day(text: str) -> date | None:
    """``text`` as a calendar day, when it is one."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _month(year: int, month: int) -> tuple[date, date] | None:
    """The first and last day of a month, when it is one."""
    if not 1 <= month <= 12:
        return None
    first = date(year, month, 1)
    return first, (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _dates(pairs: list[Pair]) -> Iterator[tuple[date, date]]:
    """Each span of days a query names: an ISO day or month value under any key, and a
    ``year`` value (4 digits) with its ``month`` when one is given, else its whole year."""
    named = dict(pairs)
    for _, value in pairs:
        if _ISO_DAY.fullmatch(value) and (day := _day(value)) is not None:
            yield day, day
        elif _ISO_MONTH.fullmatch(value) and (month := _month(int(value[:4]),
                                                              int(value[5:]))) is not None:
            yield month
    year = named.get("year", "")
    if _YEAR.fullmatch(year):
        month_text = named.get("month", "")
        month = _month(int(year), int(month_text)) if month_text.isdigit() else None
        yield month or (date(int(year), 1, 1), date(int(year), 12, 31))


def followed(request: Request, span: Span | None) -> bool:
    """Whether the crawl follows ``request``: every span of days its query names meets the
    user's pay periods (``span``; None when the user has none, so no dated request is)."""
    named = list(_dates(parse_qsl(urlsplit(request.url).query, keep_blank_values=True)))
    return not named or (span is not None and all(span.holds(*days) for days in named))
