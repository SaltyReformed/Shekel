"""
Shekel Budget App -- Shared Redirect-Target Value Type

A single frozen value object naming a Flask redirect destination: an
endpoint name plus the ``url_for`` keyword arguments that complete it.
The form-mutation route helpers all converge on the same
``redirect(url_for(endpoint, **(kwargs or {})))`` idiom for their
recoverable-failure exits (invalid pattern, stale-lock conflict, name
collision, inactive source account); before this type each helper took a
loose ``redirect_endpoint`` / ``redirect_endpoint_kwargs`` (or
``redirect_kwargs`` -- the naming had drifted) pair.  Bundling the pair
here lets the larger context objects
(:class:`app.routes._commit_helpers.StaleConflictContext`,
:class:`app.routes._recurrence_form_helpers.RecurrenceFormContext`)
compose one redirect target instead of re-declaring the two fields, and
collapses the per-call argument count that tripped pylint's
``too-many-arguments`` across the helper layer.

Route-layer module (leading underscore = route-internal) rather than a
service: :meth:`RedirectTarget.to_response` consumes Flask
``redirect`` / ``url_for``, and ``CLAUDE.md::Architecture`` keeps
services free of Flask globals.
"""
from dataclasses import dataclass
from typing import Any

from flask import Response, redirect, url_for


@dataclass(frozen=True)
class RedirectTarget:
    """A Flask redirect destination: an endpoint and its ``url_for`` kwargs.

    Attributes:
        endpoint: Flask endpoint name (e.g. ``"templates.edit_template"``).
        kwargs: ``url_for`` keyword arguments for the endpoint (e.g.
            ``{"template_id": 7}``); ``None`` for endpoints that take no
            view arguments, treated as an empty mapping by
            :meth:`to_response`.
    """

    endpoint: str
    kwargs: dict[str, Any] | None = None

    def to_response(self) -> Response:
        """Build the Flask redirect :class:`Response` for this target.

        The single home for the
        ``redirect(url_for(endpoint, **(kwargs or {})))`` idiom the
        form-mutation helpers share, so the ``kwargs or {}`` guard and
        the ``url_for`` splat live in exactly one place.

        Returns:
            A 302 redirect :class:`Response` to ``endpoint`` with
            ``kwargs`` applied; the caller returns it directly so the
            route's control flow is unchanged.
        """
        return redirect(url_for(self.endpoint, **(self.kwargs or {})))
