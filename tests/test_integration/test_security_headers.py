"""
Shekel Budget App -- Security Headers Integration Tests

Asserts that every authenticated response carries the security
headers introduced or tightened by audit Commit C-02:

    F-018  Strict-Transport-Security
    F-019  Cache-Control / Pragma
    F-036  Content-Security-Policy ``style-src`` without unsafe-inline
    F-037  Content-Security-Policy ``script-src`` / ``style-src`` /
           ``font-src`` without external CDN origins
    F-097  Content-Security-Policy ``frame-ancestors 'none'``

Plus the structural defenses:

    base-uri 'self'
    form-action 'self'

These tests use the existing ``auth_client`` fixture, which logs in
as ``test@shekel.local`` via the form login flow, so the response
path exercised is the same one a real user hits.

Each test asserts on a single header or directive so a regression
points at the exact policy that drifted.
"""

import re

import pytest

from app import create_app


# Routes accessible to an authenticated user with no extra setup.
# /dashboard is the canonical post-login landing page; /grid/no_setup
# is shown when the user has no pay periods (the default state for the
# auth_client fixture's seed_user).  Any of them must satisfy the
# header requirements -- the headers are set unconditionally in the
# after_request hook, so the choice is for coverage breadth, not for
# isolating a route-specific bug.
AUTHENTICATED_ROUTES = ("/dashboard", "/accounts", "/settings")


@pytest.mark.parametrize("path", AUTHENTICATED_ROUTES)
def test_hsts_header_present(auth_client, path):
    """``Strict-Transport-Security`` is set with a 1-year max-age and
    ``includeSubDomains`` on every authenticated response.  ``preload``
    is intentionally absent (the operator must opt in by submitting
    to hstspreload.org); see F-018 and the runbook entry on HSTS
    preload before adding it.
    """
    resp = auth_client.get(path, follow_redirects=True)
    assert resp.status_code == 200
    hsts = resp.headers.get("Strict-Transport-Security")
    assert hsts is not None, f"HSTS header missing on {path}"
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts
    # Preload is a one-way commitment: must NOT be present until
    # operator explicitly opts in.
    assert "preload" not in hsts.lower()


@pytest.mark.parametrize("path", AUTHENTICATED_ROUTES)
def test_cache_control_no_store_on_authenticated_page(auth_client, path):
    """Authenticated responses set ``Cache-Control: no-store, no-cache,
    must-revalidate`` so the browser back button cannot reconstruct
    sensitive financial pages from history after logout.  See F-019."""
    resp = auth_client.get(path, follow_redirects=True)
    assert resp.status_code == 200
    cache_control = resp.headers.get("Cache-Control", "")
    assert "no-store" in cache_control, (
        f"Cache-Control missing 'no-store' on {path}: {cache_control!r}"
    )
    assert "no-cache" in cache_control
    assert "must-revalidate" in cache_control
    # HTTP/1.0 fallback for caches that ignore Cache-Control.
    assert resp.headers.get("Pragma") == "no-cache"


@pytest.mark.parametrize("path", AUTHENTICATED_ROUTES)
def test_csp_no_unsafe_inline_in_style_src(auth_client, path):
    """``style-src`` must NOT include ``'unsafe-inline'``.  Closes the
    CSS attribute-selector keylogging path in F-036."""
    resp = auth_client.get(path, follow_redirects=True)
    csp = resp.headers.get("Content-Security-Policy", "")
    style_src = _csp_directive(csp, "style-src")
    assert style_src is not None, "style-src directive missing"
    assert "'unsafe-inline'" not in style_src
    assert "unsafe-inline" not in style_src
    # And no CDN origins.
    assert "cdn.jsdelivr.net" not in style_src
    assert "fonts.googleapis.com" not in style_src
    assert "unpkg.com" not in style_src


@pytest.mark.parametrize("path", AUTHENTICATED_ROUTES)
def test_csp_no_external_origins_in_script_src(auth_client, path):
    """``script-src`` must NOT include any external origin and must NOT
    include ``'unsafe-inline'`` or ``'unsafe-eval'``.  Closes F-037."""
    resp = auth_client.get(path, follow_redirects=True)
    csp = resp.headers.get("Content-Security-Policy", "")
    script_src = _csp_directive(csp, "script-src")
    assert script_src is not None, "script-src directive missing"
    assert "'self'" in script_src
    # External origins forbidden.
    assert "cdn.jsdelivr.net" not in script_src
    assert "unpkg.com" not in script_src
    # Inline + eval forbidden.
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src


@pytest.mark.parametrize("path", AUTHENTICATED_ROUTES)
def test_csp_no_external_origins_in_font_src(auth_client, path):
    """``font-src`` must be ``'self'`` only.  Inter and JetBrains Mono
    are vendored under ``app/static/vendor/fonts/``; Google Fonts
    origins are no longer permitted.  Closes part of F-037."""
    resp = auth_client.get(path, follow_redirects=True)
    csp = resp.headers.get("Content-Security-Policy", "")
    font_src = _csp_directive(csp, "font-src")
    assert font_src is not None, "font-src directive missing"
    assert "'self'" in font_src
    assert "fonts.gstatic.com" not in font_src
    assert "cdn.jsdelivr.net" not in font_src


@pytest.mark.parametrize("path", AUTHENTICATED_ROUTES)
def test_csp_frame_ancestors_none(auth_client, path):
    """CSP ``frame-ancestors 'none'`` closes the modern clickjacking
    control.  X-Frame-Options is the legacy fallback (still set by
    the same after_request hook, see test below)."""
    resp = auth_client.get(path, follow_redirects=True)
    csp = resp.headers.get("Content-Security-Policy", "")
    frame_ancestors = _csp_directive(csp, "frame-ancestors")
    assert frame_ancestors is not None
    assert "'none'" in frame_ancestors


@pytest.mark.parametrize("path", AUTHENTICATED_ROUTES)
def test_csp_base_uri_self(auth_client, path):
    """``base-uri 'self'`` prevents an injected ``<base href=>`` from
    redirecting relative URLs through an attacker-controlled prefix."""
    resp = auth_client.get(path, follow_redirects=True)
    csp = resp.headers.get("Content-Security-Policy", "")
    base_uri = _csp_directive(csp, "base-uri")
    assert base_uri is not None
    assert "'self'" in base_uri


@pytest.mark.parametrize("path", AUTHENTICATED_ROUTES)
def test_csp_form_action_self(auth_client, path):
    """``form-action 'self'`` blocks injected ``<form action=evil>`` tags
    from posting credentials to a third-party origin."""
    resp = auth_client.get(path, follow_redirects=True)
    csp = resp.headers.get("Content-Security-Policy", "")
    form_action = _csp_directive(csp, "form-action")
    assert form_action is not None
    assert "'self'" in form_action


def test_frame_ancestors_and_x_frame_options_both_set(auth_client):
    """Both the modern (CSP frame-ancestors) AND legacy
    (X-Frame-Options) clickjacking controls must be present.  CSP
    takes precedence on browsers that honor it; X-Frame-Options is
    the fallback for older browsers and a defense-in-depth backup."""
    resp = auth_client.get("/dashboard", follow_redirects=True)
    assert resp.headers.get("X-Frame-Options") == "DENY"
    csp = resp.headers.get("Content-Security-Policy", "")
    frame_ancestors = _csp_directive(csp, "frame-ancestors")
    assert frame_ancestors is not None
    assert "'none'" in frame_ancestors


def test_no_cdn_origins_remain_in_rendered_html(auth_client):
    """Rendered HTML must not reference any CDN origin (defense-in-depth
    against accidental re-introduction of an external <script>/<link>
    tag in a future template).  The CSP would block such a tag, but
    a missing reference here means we don't even hit the CSP report."""
    resp = auth_client.get("/dashboard", follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "cdn.jsdelivr.net" not in body, (
        "Unexpected CDN reference (cdn.jsdelivr.net) in rendered HTML"
    )
    assert "unpkg.com" not in body
    assert "fonts.googleapis.com" not in body
    assert "fonts.gstatic.com" not in body


def test_no_inline_style_attribute_in_rendered_html(auth_client):
    """No template renders a ``style="..."`` attribute.  The CSP
    forbids inline styles, so any such attribute is silently ignored
    by the browser -- this test catches templates that accidentally
    re-introduce one before the visual regression is noticed.

    Anchors on whitespace before ``style`` so ``data-style=...`` (a
    different attribute that happens to contain ``style``) does not
    trigger a false positive.  HTML attribute names are always
    preceded by whitespace because they sit inside a tag.
    """
    resp = auth_client.get("/dashboard", follow_redirects=True)
    body = resp.get_data(as_text=True)
    inline_style_re = re.compile(r'\sstyle\s*=\s*["\']', re.IGNORECASE)
    matches = inline_style_re.findall(body)
    assert matches == [], (
        f"Inline style= attribute present in HTML: {matches[:3]}"
    )


# Routes whose rendered HTML must contain ZERO inline event-handler
# attributes (onclick=, onchange=, onkeydown=, ...).  The CSP forbids
# inline scripts (``script-src 'self'`` without ``'unsafe-inline'``),
# so any such attribute is silently ignored by the browser and the
# associated UI is broken without a visible error.  The migration in
# the C-02 follow-up moved every handler to delegated listeners in
# external JS files; this test locks the migration in.
INLINE_HANDLER_ROUTES = (
    "/dashboard",
    "/accounts",
    "/settings",
    "/settings?section=categories",
)


# Common DOM event-handler attribute names.  Not exhaustive -- the
# regex below uses a generic ``on[a-z]+`` pattern, but listing the
# ones that historically appeared in the codebase here helps the
# error message be readable when the test fires.
KNOWN_INLINE_HANDLERS = (
    "onclick", "onchange", "oninput", "onsubmit", "onkeydown",
    "onkeypress", "onkeyup", "onmouseover", "onmouseout", "onfocus",
    "onblur", "onload", "onerror",
)


@pytest.mark.parametrize("path", INLINE_HANDLER_ROUTES)
def test_no_inline_event_handler_attributes_in_rendered_html(auth_client, path):
    """No template renders an ``on<event>="..."`` attribute.

    Matches the generic shape ``\\son[a-z]+\\s*=\\s*['\"]`` so a new
    handler name nobody anticipated will still trip the test.  The
    leading whitespace requirement avoids false positives on
    ``data-on-foo=`` and similar attribute names that happen to
    contain the substring ``on``.
    """
    resp = auth_client.get(path, follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    inline_handler_re = re.compile(
        r'\son[a-z]+\s*=\s*["\']', re.IGNORECASE
    )
    matches = inline_handler_re.findall(body)
    # Surface the offending handler name(s) in the error message so
    # the failure points at exactly which one was reintroduced.
    if matches:
        # Strip the leading whitespace and trailing quote from each
        # match for a clean error message.
        sample = [m.strip() for m in matches[:5]]
        known = [m for m in sample if any(
            m.lower().lstrip().startswith(name) for name in KNOWN_INLINE_HANDLERS
        )]
        raise AssertionError(
            f"Inline event-handler attribute(s) found on {path}. "
            f"Sample: {sample}. Known-name matches: {known}. "
            f"All handlers must live in app/static/js/ via delegation."
        )


def test_categories_edit_button_uses_data_action(auth_client):
    """The categories edit row exposes ``data-action="cat-edit-show"``
    so categories.js can wire up the display→edit toggle.  Negative
    test (no onclick=) is covered above; this one proves the
    positive replacement is actually emitted."""
    # Seed at least one category for the user.  The default seed_user
    # fixture creates several; assert one is rendered.
    resp = auth_client.get(
        "/settings?section=categories", follow_redirects=True
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-action="cat-edit-show"' in body, (
        "categories template must emit data-action='cat-edit-show' "
        "for categories.js to attach the edit-toggle handler"
    )
    assert 'data-action="cat-edit-cancel"' in body
    assert 'data-action="cat-group-change"' in body
    # The Add Category form's group dropdown also uses cat-group-change.
    # data-custom-id and data-hidden-id are required peers; assert
    # at least one of each appears (template emits one per row plus
    # one for the add form).
    assert "data-custom-id=" in body
    assert "data-hidden-id=" in body


def test_x_content_type_options_nosniff(auth_client):
    """X-Content-Type-Options: nosniff blocks MIME sniffing.  Not new
    in C-02 but locked in by this test because the security-headers
    hook is the single source of truth."""
    resp = auth_client.get("/dashboard", follow_redirects=True)
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_referrer_policy(auth_client):
    """Referrer-Policy: strict-origin-when-cross-origin avoids leaking
    full URLs to third-party origins on outbound clicks.  Pre-existing
    but locked in for the same reason as the previous test."""
    resp = auth_client.get("/dashboard", follow_redirects=True)
    assert (
        resp.headers.get("Referrer-Policy")
        == "strict-origin-when-cross-origin"
    )


def test_permissions_policy(auth_client):
    """Permissions-Policy disables camera, microphone, geolocation."""
    resp = auth_client.get("/dashboard", follow_redirects=True)
    pp = resp.headers.get("Permissions-Policy", "")
    assert "camera=()" in pp
    assert "microphone=()" in pp
    assert "geolocation=()" in pp


def test_security_headers_on_unauthenticated_route(client):
    """Even unauthenticated routes (login page) carry the full header
    set.  HSTS in particular must be set on every response so a user
    arriving via http:// gets pinned to https:// before they ever
    log in."""
    resp = client.get("/login")
    # /login may render the page (200) or redirect to /dashboard if
    # already authenticated (302); both must carry the headers.
    assert resp.status_code in (200, 302)
    assert "Strict-Transport-Security" in resp.headers
    assert "Content-Security-Policy" in resp.headers
    assert resp.headers.get("X-Frame-Options") == "DENY"


def test_security_headers_on_404(client):
    """404 responses carry the same security headers.  Error pages
    are rendered through the Flask error-handler stack, which still
    runs after_request hooks; this test catches a misconfiguration
    that would skip the hook for non-2xx responses."""
    resp = client.get("/this-path-does-not-exist-anywhere")
    assert resp.status_code == 404
    assert "Strict-Transport-Security" in resp.headers
    assert "Content-Security-Policy" in resp.headers


def _csp_directive(csp_header: str, directive: str) -> str | None:
    """Extract the value of a single CSP directive from a header string.

    Args:
        csp_header: Full ``Content-Security-Policy`` header value.
        directive: Directive name (e.g. ``"script-src"``).

    Returns:
        The directive value (the part after ``<directive>``) with
        leading/trailing whitespace stripped, or ``None`` if the
        directive is not present.  Matches token-boundary on the
        directive name so ``style-src`` does not match ``style-src-attr``.
    """
    pattern = re.compile(
        rf"(?:^|;)\s*{re.escape(directive)}\s+([^;]*)", re.IGNORECASE
    )
    match = pattern.search(csp_header)
    if match is None:
        return None
    return match.group(1).strip()


def test_helper_csp_directive_returns_none_for_missing():
    """Sanity check the test helper: missing directive returns None."""
    csp = "default-src 'self'; script-src 'self'"
    assert _csp_directive(csp, "missing-directive") is None


def test_helper_csp_directive_extracts_value():
    """Sanity check: present directive returns its value."""
    csp = "default-src 'self'; script-src 'self' 'unsafe-eval'"
    assert _csp_directive(csp, "script-src") == "'self' 'unsafe-eval'"


def test_helper_csp_directive_token_boundary():
    """Sanity check: a directive name does not match a longer name
    that starts with the same prefix.  ``style-src`` must not match
    inside ``style-src-attr``."""
    csp = "style-src-attr 'unsafe-inline'; style-src 'self'"
    # The helper should match the standalone ``style-src`` directive,
    # not ``style-src-attr`` -- standalone has value "'self'".
    assert _csp_directive(csp, "style-src") == "'self'"
