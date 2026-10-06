"""
Shekel Budget App -- URL converters

The routing layer's half of "what does a submitted digit string mean"
(plan step X-ae, finding N-140).  :mod:`app.utils.digit_strings` answers it for
form fields and owns the rule; this applies that rule to the PATH, so a row id
has the same single spelling in a URL as in a form body.  (The query string is
NOT yet covered in SPELLING -- finding N-142; its RANGE is, through
:func:`~app.utils.digit_strings.integer_arg`.)

**Werkzeug's stock ``<int:>`` was lax in both of the ways this arc has already
paid for**, both measured against this application on Werkzeug 3.1.6 at plan
step X-ae and both unchanged through 3.1.8.  Werkzeug 3.1.9 closed the second
upstream and left the first:

* ``IntegerConverter.regex`` is ``r"\\d+"``, compiled WITHOUT ``re.ASCII``, and
  ``to_python`` hands the segment to ``int()``.  So ``/accounts/١/details``
  returned output byte-identical to ``/accounts/1/details``: the same row id
  under two spellings, which is exactly what finding N-136 closed for form
  bodies while leaving 123 path parameters open.  **Unchanged in Werkzeug
  3.1.9**, and asserted on the stock class in this module's tests, so the day
  Werkzeug changes it a test says so.
* Through Werkzeug 3.1.8, a path segment of more than
  ``sys.get_int_max_str_digits()`` ASCII digits (4,300 by default) made that
  ``int()`` raise ``ValueError`` **inside ``url_adapter.match()``** -- before
  the view function, before the login gate (``app/login_gate.py``), before
  any session existed.  ``app/error_handlers.py`` registers no ``ValueError``
  arm, so it was an **unauthenticated** unhandled 500, and it was reachable
  in production: ``gunicorn.conf.py`` sets ``limit_request_line = 8190``, and
  neither nginx config narrows the header buffer, so a ~4.4 kB request line
  reaches the application.  **Werkzeug 3.1.9 refuses the run itself**
  (pallets/werkzeug#3237): its ``NumberConverter.to_python`` turns that
  ``ValueError`` into the ``ValidationError`` described below.  This
  converter refused the run before then and still does, through
  :func:`~app.utils.digit_strings.parse_row_id`, so its answer is the same on
  both versions.

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
its own converter -- this one refuses all three by design, and would refuse
the request (see :class:`RowIdConverter` for which status) rather than doing
something surprising.
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
    form doors use.  (The query string's integers read through
    :func:`~app.utils.digit_strings.integer_arg`, which holds the range and
    not the spelling.)

    Two layers, and both are load-bearing:

    * ``regex`` narrows the MATCH to ASCII digits.  Werkzeug compiles this
      into the map's combined pattern, so a non-ASCII segment never becomes a
      candidate for this rule at all.
    * :meth:`to_python` then applies the full row-id rule and raises
      :class:`~werkzeug.routing.ValidationError` when the segment names no
      row.  (It is a ``ValueError`` SUBCLASS -- an earlier wording here said
      "not ``ValueError``", which is false; what matters is not the base
      class but that Werkzeug's matcher catches this one and lets any other
      ``ValueError`` escape ``match()``, which is how the stock
      ``to_python``'s bare ``int()`` was a 500 through Werkzeug 3.1.8.)  It
      is the signal ``MapAdapter.match`` already handles: **matching ends
      there** -- the matcher raises ``NoMatch`` and tries no other rule.  The
      request is then refused one of three ways, measured at plan step
      ``balance:X-dk``: an anonymous caller gets the login gate's redirect,
      because ``app/login_gate.py`` is a ``before_request`` hook and Flask
      raises the stored routing error only after those hooks run; a signed-in
      one gets a 404, or a 405 where a rule for another method on the same
      path was visited first (``DELETE /transactions/0`` beside the ``PATCH``
      rule).  None of the three reaches a view, where a canonical id naming
      no row gets the lookup's 404 instead.  Raising it is what keeps a
      segment naming no row, and an id no column holds, out of every view
      (see :data:`~app.utils.digit_strings.MAX_INTEGER_COLUMN` for the 500
      the second would otherwise be).

    The regex alone would not be enough: ``0``, ``007`` and a forty-digit run
    are all ASCII digits, and Werkzeug's own ``to_python`` reads them as row
    0, a second spelling of row 7, and an id no ``integer`` column holds,
    which psycopg 3's bind cast refuses with an unhandled 500 (ruling
    R-BAL211).  The parse alone would not be enough either: a rule whose
    regex admits a segment is the rule the matcher picks, so a stock ``\\d+``
    would hand a non-ASCII segment to this converter, whose refusal then ends
    matching before any later rule is tried.
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
                one), it is too long for CPython to convert, or it exceeds
                what an ``id`` column holds
                (:data:`~app.utils.digit_strings.MAX_INTEGER_COLUMN`).
                Matching ends there: Werkzeug tries no other rule, and the
                request is refused (the class docstring says which way).
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
