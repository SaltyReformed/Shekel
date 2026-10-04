"""The one HTTP path the plan tools take to GitHub, under either of two identities.

The private tracker (ruling ``balance:R-BAL170``) is written by two identities
on purpose.  The developer's own login -- the token ``gh`` already holds --
configures the tracker (:mod:`setup_tracker`).  Every CARD write goes through
the plan tool's GitHub App instead, a separate identity, so a check can tell a
tool write from the developer's own edit and never act on his.

An App proves who it is with a JSON Web Token signed by its private key, and
trades that token for an installation token scoped to the one repository it is
installed on.  The signing is done here with ``cryptography`` (pinned in
``requirements.txt``) rather than a JWT package, because RS256 over two JSON
segments is the whole of what the App flow needs.

Nothing in this module reads or writes the code repository.  Its tests feed it
recorded responses and never call GitHub.
"""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

API = "https://api.github.com"
API_VERSION = "2022-11-28"
TIMEOUT_SECONDS = 30

#: Where the App's two credentials live: OUTSIDE every repository, readable by
#: the developer's account only.  ``app.json`` holds the App's client ID (an
#: identifier, not a secret); ``app.pem`` is the private key GitHub issued.
APP_DIR = Path.home() / ".config" / "shekel-plan"

#: GitHub refuses a token whose ``iat`` is in its future, and this host's clock
#: and GitHub's need not agree to the second; the App documentation's own
#: example backdates by 60 seconds.  ``exp`` may be at most ten minutes ahead.
_CLOCK_SKEW_SECONDS = 60
_JWT_LIFETIME_SECONDS = 540


class GitHubError(RuntimeError):
    """A request GitHub answered with an error, carrying the HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        """Keep the HTTP status beside the message (200 for a GraphQL error)."""
        super().__init__(message)
        self.status = status


class GitHub:
    """GitHub's REST and GraphQL APIs under one bearer token.

    ``session`` is any object with ``requests.Session.request``'s signature;
    the tests pass one that replays recorded responses.
    """

    def __init__(self, token: str, session: requests.Session | None = None) -> None:
        """Send every request with ``token``, through ``session`` when one is given."""
        self._session = session or requests.Session()
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    def _send(self, method: str, url: str, body: dict | None) -> requests.Response:
        """Send one request; raise :class:`GitHubError` on any 4xx or 5xx."""
        response = self._session.request(
            method, url, headers=self._headers, json=body, timeout=TIMEOUT_SECONDS
        )
        if response.status_code >= 400:
            raise GitHubError(
                response.status_code,
                f"{method} {url} -> {response.status_code}: {response.text[:500]}",
            )
        return response

    def rest(self, method: str, path: str, body: dict | None = None):
        """One REST call; the decoded JSON body, or None for an empty one."""
        response = self._send(method, API + path, body)
        return response.json() if response.content else None

    def graphql(self, query: str, **variables) -> dict:
        """One GraphQL call; its ``data``.  GraphQL reports errors in a 200."""
        payload = self.rest("POST", "/graphql", {"query": query, "variables": variables})
        if payload.get("errors"):
            raise GitHubError(200, f"graphql: {json.dumps(payload['errors'])[:500]}")
        return payload["data"]

    def graphql_lookup(self, query: str, **variables) -> dict:
        """One GraphQL call whose unresolvable fields are ABSENT, not errors.

        Asked for an issue number nobody filed, GitHub answers 200 with that
        field ``null`` beside the others and a ``NOT_FOUND`` error naming it
        (measured 2026-10-04); this answers the ``data`` with the field None.
        Any other error still raises.
        """
        payload = self.rest("POST", "/graphql", {"query": query, "variables": variables})
        others = [e for e in payload.get("errors") or [] if e.get("type") != "NOT_FOUND"]
        if others or payload.get("data") is None:
            raise GitHubError(200, f"graphql: {json.dumps(payload.get('errors'))[:500]}")
        return payload["data"]


def user_token() -> str:
    """The developer's own token, as ``gh`` holds it."""
    result = subprocess.run(
        ["gh", "auth", "token"], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _segment(raw: bytes) -> str:
    """Base64url without padding, the encoding every JWT segment uses."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def app_jwt(client_id: str, private_key_pem: bytes, now: int) -> str:
    """A JSON Web Token proving the App's identity, valid nine minutes from ``now``.

    RS256 (RSASSA-PKCS1-v1_5 over SHA-256), issuer the App's client ID, which
    GitHub's App documentation recommends over the numeric App ID.
    """
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("a GitHub App private key is an RSA key")
    header = _segment(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = {
        "iat": now - _CLOCK_SKEW_SECONDS,
        "exp": now + _JWT_LIFETIME_SECONDS,
        "iss": client_id,
    }
    signing_input = f"{header}.{_segment(json.dumps(claims).encode())}"
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_segment(signature)}"


def app_credentials(app_dir: Path = APP_DIR) -> tuple[str, bytes]:
    """The App's client ID and private key, read from ``app_dir``."""
    client_id = json.loads((app_dir / "app.json").read_text())["client_id"]
    return client_id, (app_dir / "app.pem").read_bytes()


def app_installation(org: str, app_token: str, session=None) -> dict:
    """The App's installation on ``org``, as GitHub describes it (id, permissions, ...)."""
    return GitHub(app_token, session).rest("GET", f"/orgs/{org}/installation")


def installation_token(installation_id: int, app_token: str, session=None) -> str:
    """A one-hour token acting as the App, limited to what its installation grants."""
    response = GitHub(app_token, session).rest(
        "POST", f"/app/installations/{installation_id}/access_tokens"
    )
    return response["token"]
