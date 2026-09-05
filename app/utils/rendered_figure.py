"""
Shekel Budget App -- The name of a money box's RENDERED-figure companion.

One word of shared vocabulary, and it lives here because THREE tiers spell it:
the Jinja templates that emit the input, the Marshmallow schemas that declare
and require it, and the route doors that read and drop it.  Ruling **R-JR**
(plan step balance:X-au-h).

**It is the NAME that is shared, not the question.**  Whether a human authored
a figure is a fact about a FORM, so ``app.routes._authored_figure`` owns it and
no service or schema learns that forms exist.  What a schema legitimately needs
is the KEY it must require -- which is vocabulary, not policy -- and reaching
into a private routes module to get it is a layer violation the
``shekel-private-module-import`` checker refuses.  Splitting the two is what
lets the schema state the requirement without importing the door.

The templates still spell the name literally, because a Jinja attribute name
cannot be computed from a Python constant without a macro.  What makes a
MISSPELLING safe is not this module but the schema's refusal: a form emitting
the wrong name posts no companion, and a payload carrying a figure without its
companion is rejected on the first save rather than silently taken as a human's.
"""

#: Suffix naming the companion field a form posts beside a money box, holding
#: the figure that box was rendered with.
AS_RENDERED_SUFFIX = "_as_rendered"


def as_rendered_field(field: str) -> str:
    """Return the name of the companion field carrying *field*'s rendered figure.

    Args:
        field: The money field's name, as the form posts it (for example
            ``"estimated_amount"`` or ``"amount"``).

    Returns:
        The companion field's name.
    """
    return f"{field}{AS_RENDERED_SUFFIX}"
