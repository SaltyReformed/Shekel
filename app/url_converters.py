"""
Shekel Budget App -- URL converters

The routing layer's half of "what does a submitted digit string mean"
(plan step X-ae, finding N-140).  :mod:`app.utils.digit_strings` answers it for
form fields and owns the rule; this applies that rule to the PATH, so a row id
has the same single spelling in a URL as in a form body.  (The query string is
NOT yet covered -- 42 ``type=int`` sites, finding N-142.)

**Werkzeug's stock ``<int:>`` is lax in both of the ways this arc has already
paid for**, and both were measured against this application:

* ``IntegerConverter.regex`` is ``r"\\d+"``, compiled WITHOUT ``re.ASCII``, and
  ``to_python`` calls a bare ``int()``.  So ``/accounts/١/details`` returned
  output byte-identical to ``/accounts/1/details``: the same row id under two
  spellings, which is exactly what finding N-136 closed for form bodies while
  leaving 123 path parameters open.
* A path segment of more than ``sys.get_int_max_str_digits()`` ASCII digits
  (4,300 by default) makes that ``int()`` raise ``ValueError`` **inside
  ``url_adapter.match()``** -- before the view function, before
  ``@login_required``, before any session exists.  ``app/error_handlers.py``
  registers no ``ValueError`` arm, so it is an **unauthenticated** unhandled
  500, and it is reachable in production: ``gunicorn.conf.py`` sets
  ``limit_request_line = 8190``, and neither nginx config narrows the header
  buffer, so a ~4.4 kB request line reaches the application.

**Overriding the built-in ``int`` name is deliberate, and it is what makes
this one rule rather than 123 edits.**  Registering under a new name would
have required rewriting every ``<int:...>`` in the route tree and would leave
the lax converter available to the next route written.  A census of `app/`
supports the override: **all 123 path parameters are row ids** -- 46
``account_id``, 15 ``profile_id``, 13 ``txn_id``, and thirteen more names, **every
one of them a ``serial`` primary key, with no exception**.  None uses
``signed=True`` or ``fixed_digits``.

(An earlier wording here excepted ``version_id`` as "a counter whose own CHECK
constraint is ``> 0``" and an adversarial review refuted it: the ``> 0`` checks
sit on the optimistic-LOCKING counter columns, which are never path parameters,
while the two ``<int:version_id>`` parameters -- ``loan/escrow_rates.py:588``
and ``:631`` -- resolve to ``budget.escrow_component_versions.id``, an ordinary
serial PK.  The census's conclusion is stronger without the exception, and this
census is the whole justification for overriding a Flask built-in.)

**A future path parameter that is NOT a row id must not use ``<int:>``.**  If
one ever needs zero, a negative value, or a zero-padded fixed width, it needs
its own converter -- this one refuses all three by design, and would answer
404 rather than doing something surprising.
"""

# Both names are re-exported from the PUBLIC ``werkzeug.routing`` namespace and
# are the identical class objects the ``werkzeug.routing.converters`` submodule
# defines (asserted in this module's tests).  Importing the public path rather
# than the submodule keeps the dependency surface to names Werkzeug documents,
# which is the half of finding N-143 that costs nothing to close.
from flask import Flask
from werkzeug.routing import IntegerConverter, ValidationError

from app.utils.digit_strings import parse_row_id


class RowIdConverter(IntegerConverter):
    """Match a path segment that is the canonical spelling of a row id.

    Registered as the application's ``int`` converter, so every
    ``<int:...>`` rule in the route tree consumes
    :func:`~app.utils.digit_strings.parse_row_id` -- the same function the
    form and query doors use.

    Two layers, and both are load-bearing:

    * ``regex`` narrows the MATCH to ASCII digits.  Werkzeug compiles this
      into the map's combined pattern, so a non-ASCII segment never becomes a
      candidate for this rule at all.
    * :meth:`to_python` then applies the full row-id rule and raises
      :class:`~werkzeug.routing.ValidationError` when the segment names no
      row.  (It is a ``ValueError`` SUBCLASS -- an earlier wording here said
      "not ``ValueError``", which is false; what matters is not the base
      class but that Werkzeug's matcher catches this one and not a bare
      ``ValueError`` from ``int()``.)  It is the signal
      ``MapAdapter.match`` already handles: the rule simply does not match,
      routing continues, and the request ends at the ordinary 404 that a
      missing row would have produced anyway.  Raising it is what converts an
      unhandled 500 into the answer the application already had.

    The regex alone would not be enough: an oversized run of ASCII digits
    matches it and still makes ``int()`` raise.  The parse alone would not be
    enough either, because a rule whose regex admits a segment participates in
    matching even when its converter rejects it.
    """

    #: ASCII digits only.  ``\\d`` inside a ``str`` pattern is Unicode-wide
    #: unless ``re.ASCII`` is passed, and Werkzeug does not pass it -- so the
    #: stock ``r"\\d+"`` admits every digit script.
    regex = r"[0-9]+"

    def to_python(self, value: str) -> int:
        """Return the row id *value* names.

        Args:
            value: The matched path segment, already known to be ASCII
                digits by :attr:`regex`.

        Returns:
            The row id as an ``int``.

        Raises:
            ValidationError: *value* names no row -- it is zero, it carries
                leading zeros (a second spelling of a row that already has
                one), or it is too long for CPython to convert.  The rule
                does not match and routing continues.
        """
        row_id = parse_row_id(value)
        if row_id is None:
            raise ValidationError()
        # Deferred to the base class rather than returned directly so any
        # ``min`` / ``max`` bound declared on a rule still applies.
        return super().to_python(value)


def register_url_converters(app: Flask) -> None:
    """Install :class:`RowIdConverter` as the application's ``int`` converter.

    Called by :func:`app.create_app` BEFORE the blueprints are registered:
    Werkzeug resolves a rule's converters when the rule is added to the map,
    so a converter registered afterwards would not apply to the rules already
    there.

    Args:
        app: The Flask application being built.
    """
    app.url_map.converters["int"] = RowIdConverter
