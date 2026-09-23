"""
Shekel Budget App -- The Rows a Definition's Unarchive Brings Back

ONE statement of which rows unarchiving a recurring definition restores (plan
step ``pay_calendar:C18-a``, ruling **R-PC93**, developer 2026-09-23), read by
the two doors that restore them -- ``routes/templates/crud.unarchive_template``
and ``routes/transfers/lifecycle.unarchive_transfer_template`` -- and by the
doors that refuse to strand a planned row below an account's books
(``app.services.planned_rows_books``).

**Why the refusal reads it.**  Archiving a definition hides its still-Projected
rows, and unarchiving brings every one of them back.  A books move made while
the definition was archived saw no live row to strand, so it went through; the
unarchive then restored rows inside the opening balance, and a recurring
transfer's unarchive -- whose maintain pass retires a row the rule no longer
names -- deleted the current paycheck's row without a word, measured by the
C18-a round-2 review.  R-PC93 treats the rows an unarchive would bring back as
planned rows at every door that moves a definition's books, so no unarchive can
restore a row below them and the unarchive needs no check of its own.  That
holds only while the refusal counts EXACTLY the rows the unarchive restores,
which is why both read this function: two spellings of one scope are two homes
that have to agree (``CLAUDE.md`` rule 14).

**It is read off the rows' soft-deleted state, not off what the archive did.**
An unarchive restores every still-Projected soft-deleted row of the definition,
including one its owner deleted by hand before archiving; the archive decides
which rows it HIDES, and may keep some live (plan step ``credit_card:CC-5-4a-4``
keeps a row holding a payment or purchase), but whatever it hid, this is what
comes back.

Services-boundary discipline (``CLAUDE.md`` Architecture): SQL criteria out, no
Flask symbol, no query, no write.
"""

from app.utils.balance_predicates import is_projected_clause


def rows_an_unarchive_restores(model, template_fk, template_id: int) -> tuple:
    """Return the SQL criteria selecting the rows unarchiving a definition restores.

    Args:
        model: ``Transaction`` for a transaction template, ``Transfer`` for a
            transfer template.
        template_fk: The column naming the row's definition --
            ``Transaction.template_id`` or ``Transfer.transfer_template_id``.
        template_id: The definition being unarchived.

    Returns:
        The criteria, for ``query.filter(*criteria)``: the definition's
        still-Projected rows that are soft-deleted.
    """
    return (
        template_fk == template_id,
        is_projected_clause(model),
        model.is_deleted.is_(True),
    )


__all__ = ["rows_an_unarchive_restores"]
