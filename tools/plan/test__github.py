"""The GitHub client and the App's token, against recorded responses; nothing calls GitHub."""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from _github import API, GitHub, GitHubError, app_jwt


class _Response:
    """The slice of ``requests.Response`` the client reads."""

    def __init__(self, status_code, body=None):
        """Record one response: its status and JSON body."""
        self.status_code = status_code
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()
        self._body = body

    def json(self):
        """The recorded body."""
        return self._body


class _Session:
    """Replays responses in order and records every request."""

    def __init__(self, *responses):
        """Hold the responses to replay, in order."""
        self.responses = list(responses)
        self.sent = []

    def request(self, method, url, **kwargs):
        """Record the request; answer with the next recorded response."""
        self.sent.append((method, url, kwargs))
        return self.responses.pop(0)


def _decode(segment: str) -> dict:
    """One JWT segment's JSON."""
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def _pem(key) -> bytes:
    """A private key as the PEM GitHub issues."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def test_app_jwt_is_rs256_signed_by_the_key_with_the_documented_claims():
    """GitHub verifies the token with the App's public key; so does this test."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = app_jwt("Iv23client", _pem(key), now=1_000_000)
    header, claims, signature = token.split(".")
    assert _decode(header) == {"alg": "RS256", "typ": "JWT"}
    assert _decode(claims) == {"iat": 999_940, "exp": 1_000_540, "iss": "Iv23client"}
    raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    key.public_key().verify(
        raw, f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    tampered = f"{header}.{claims[:-2]}xx".encode()
    with pytest.raises(InvalidSignature):
        key.public_key().verify(raw, tampered, padding.PKCS1v15(), hashes.SHA256())


def test_app_jwt_refuses_a_key_that_is_not_rsa():
    """RS256 is the only algorithm GitHub accepts for an App; another key is a wrong file."""
    key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(ValueError, match="RSA"):
        app_jwt("Iv23client", _pem(key), now=1_000_000)


def test_rest_sends_the_token_and_api_version_and_decodes_the_body():
    """Every call carries the bearer token and pins the API version."""
    session = _Session(_Response(200, {"name": "shekel-plan"}))
    assert GitHub("t0k", session).rest("GET", "/repos/o/r") == {"name": "shekel-plan"}
    method, url, kwargs = session.sent[0]
    assert (method, url) == ("GET", f"{API}/repos/o/r")
    assert kwargs["headers"]["Authorization"] == "Bearer t0k"
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2022-11-28"


def test_rest_answers_none_for_an_empty_body():
    """A DELETE answers 204 with no body."""
    session = _Session(_Response(204))
    assert GitHub("t", session).rest("DELETE", "/repos/o/r/labels/bug") is None


def test_rest_raises_with_the_status_on_an_error():
    """A 404 is a fact a caller branches on (the repository does not exist yet)."""
    session = _Session(_Response(404, {"message": "Not Found"}))
    with pytest.raises(GitHubError) as raised:
        GitHub("t", session).rest("GET", "/repos/o/missing")
    assert raised.value.status == 404
    assert "Not Found" in str(raised.value)


def test_graphql_raises_on_errors_reported_inside_a_200():
    """GraphQL reports a refused mutation with status 200 and an ``errors`` list."""
    session = _Session(_Response(200, {"data": None, "errors": [{"message": "nope"}]}))
    with pytest.raises(GitHubError, match="nope"):
        GitHub("t", session).graphql("mutation { x }")


def test_graphql_lookup_reads_a_missing_field_as_absent_but_raises_on_a_missing_repository():
    """Review cp3 L-c: every NOT_FOUND was dropped, so a repository GitHub could not find left
    ``data.repository`` null and the caller hit a TypeError.  Only a field below the top-level
    object is absent; GitHub names each by its ``path`` (both shapes recorded 2026-10-04 in
    ``recorded/tracker.json``, graded against it in ``test__tracker.py``)."""
    below = {"data": {"repository": {"c1": None}}, "errors": [
        {"type": "NOT_FOUND", "path": ["repository", "c1"], "message": "no issue 1"}]}
    assert GitHub("t", _Session(_Response(200, below))).graphql_lookup("q") == below["data"]
    top = {"data": {"repository": None}, "errors": [
        {"type": "NOT_FOUND", "path": ["repository"], "message": "no repository x"}]}
    with pytest.raises(GitHubError, match="no repository x"):
        GitHub("t", _Session(_Response(200, top))).graphql_lookup("q")
