"""
Shekel Budget App -- Did a HUMAN author this figure?

One question, asked identically by the three doors that accept a money figure:
the transaction popover and quick edit, the transfer popover and quick edit,
and the transaction PATCH that lands on a transfer SHADOW and is answered by
updating its parent.

**The payload CARRIES the answer; this module does not reconstruct it.**  An
HTML form submits every control it renders, so the arrival of a figure says
only that a box existed -- not that anyone typed in it.  Both doors used to
infer authorship anyway, and both inferences were defective in the same
direction:

* the TRANSFER door compared the submitted figure against the row's stored
  column (``routes/transfers/mutations.py``), which is right only while that
  column holds what the form displayed;
* the TRANSACTION door did not compare at all -- it asked whether the field
  was PRESENT (``routes/transactions/mutations.py``), which is true of every
  save that renders an amount box, including a notes-only one.  That is
  finding **N-248**: the popover submits its prefilled figure, the row takes
  ownership of a number nobody chose, and it stops tracking its definition.

Ruling **R-JR** (developer, 2026-09-03) replaced both with a fact the form
states: the door renders a figure into the box AND posts that same figure back
in a companion field, so the question becomes *is what came back different from
what we showed this person*.  Three properties follow, and the third is why it
is a payload fact rather than a better comparison:

1. **It cannot go vacuously true.**  The comparison never reads a stored
   column, so emptying one cannot make it answer YES unconditionally.  That is
   finding **N-436** -- ``budget.transfers.amount`` goes NULL for a generated
   transfer at plan step X-au-f, at which point ``submitted != stored`` is true
   for every call -- and finding **N-448**, the same defect one layer up in the
   route.  Neither is guarded here; both are unrepresentable.
2. **It cannot fail open.**  No client-side script decides it, so a browser
   that runs none still states the fact correctly.
3. **It asks about the HUMAN, not about the database.**  If a row is re-priced
   by its definition while the popover sits open, the figure the user saw and
   the figure now stored are different -- and the user authored neither.
   Comparing against what was SHOWN is the only one of the two that answers
   the question actually being asked.

**A figure with NO companion is REFUSED, and the refusal is the whole point**
(ruling **R-JR**, second sitting, after an adversarial review).  The first
implementation guessed instead: it assumed such a figure was authored, on the
argument that wrongly taking an echo is undone by the conflict resolver while a
discarded re-price is not.  **Both halves of that argument were measured
false.**  ``is_override`` appears in no schema in ``app/schemas/``, and
``_recurrence_conflict_chooser`` SUPPRESSES the chooser for a salary-linked
template -- which is exactly N-248's population (51 rows / `$4,897.50`), so a
wrongly-taken salary row has no in-app hand-back at all.  Meanwhile a discarded
figure is the most VISIBLE failure this app has: every door answers with
``hx-swap="innerHTML"`` into the row's own cell, so the old number reappears in
front of the user and they retype it.

So the guess was backwards, and the fix is not to guess the other way.  A
payload that states a figure without stating what was shown is MALFORMED, and
the schemas refuse it -- see ``_helpers`` in :mod:`app.schemas.validation`,
``reject_figure_without_its_rendered_companion``.  That leaves this module with
two answers instead of three, and deletes the question of which way to fail:

* **a door that forgets the companion fails LOUDLY on its first save**, rather
  than silently reproducing N-248 with every test still green.  That was the
  largest ungraded hazard in the first implementation: three of the four
  templates had no case asserting they emit the companion at all;
* **a form cached across the deploy** gets one 400 telling it to reload, which
  is a bounded, self-correcting cost paid once.

Flask-isolated in the sense that matters: it takes the loaded payload mapping
and returns a bool.  It lives under ``routes`` rather than ``services``
because a rendered baseline is a fact about a FORM, and no service should
learn that forms exist.
"""

from collections.abc import Mapping

from app.utils.rendered_figure import as_rendered_field


def figure_was_authored(data: Mapping[str, object], field: str) -> bool:
    """Return whether a human typed the figure *data* states for *field*.

    The single producer of that answer for every door (ruling **R-JR**), so no
    two of them can come to disagree about what "a human authored this figure"
    means.  See the module docstring for why the answer is carried rather than
    inferred, and why a payload that omits it is refused rather than guessed at.

    **Two cases, because the third was deleted rather than decided:**

    * **no figure submitted** -- the box was absent, disabled or empty, so this
      call states no amount and authors nothing.  ``False``.
    * **a figure WITH its rendered companion** -- authored exactly when the two
      differ.  The comparison is numeric, so re-submitting a rendered
      ``250.00`` as ``250.0`` is correctly not an authorship.

    A figure WITHOUT its companion never reaches this function: the schema
    refuses the payload before the door is entered.  This asks membership of
    the companion key rather than testing its value for ``None``, so the two
    absences are one question -- an earlier revision tested ``field not in
    data`` for one key and ``value is None`` for the other, which coincided
    only while the companion was not ``allow_none``, and would have diverged
    silently the moment it became so.

    Args:
        data: The schema-loaded payload mapping for this request.
        field: The money field's name within *data*.

    Returns:
        ``True`` when this call states a figure a human authored.
    """
    if field not in data:
        return False
    rendered_key = as_rendered_field(field)
    if rendered_key not in data:
        # Unreachable through a door: the schema refuses this payload. Answering
        # False rather than True is the safe residual -- it declines to claim
        # authorship it cannot evidence, leaving the row's amount untouched,
        # where the old True silently took ownership.
        return False
    return data[field] != data[rendered_key]
