"""Turning the source's merchant WORDS into this account's merchant ROWS.

Plan step ``bank_import:X-gd-1``, ruling **R-GR**.  An adapter reads what the
source calls a merchant and states it as a string on a
:class:`~._line.StatementLine` -- that is the source's own word about one line
and it is where the adapter's knowledge stops (ruling **R-FP**).  This is the
one place that word becomes a :class:`~app.models.merchant.Merchant`, so a
stated rule and a recorded line are ABOUT the same row rather than about two
copies of one string.

**One statement per PASS, never one per line.**  Measured on the developer's
own dev database 2026-08-25: **378 recorded lines naming 62 merchants**, so a
find-or-create per line would be 378 round trips to learn 62 facts -- the N+1
this package has already paid for once (finding **N-309**).  Both halves are
set-shaped: one INSERT of every name this pass mentions, and one SELECT of
every name back.

**The INSERT is idempotent at the DATABASE**, not by looking first.  Two
imports racing on one account would each see a name missing and each insert it;
``ON CONFLICT DO NOTHING`` against ``uq_merchants_account_name`` makes the
second a no-op instead of an ``IntegrityError`` that fails a whole statement
over a merchant it did not need to create.  Looking first and inserting after
is the check-then-act that has no atomic reading at all.

**It is CHUNKED, and the ceiling is libpq's rather than this app's.**  One
INSERT binds two parameters per name against a wire protocol limit of 65,535,
so "one statement" is only safe while the caller's own bound is small enough --
and ``_secu_csv.MAX_LINES`` is 20,000, which is 40,000 parameters and 1.6x of
headroom on a statement whose failure mode is an unhandled 500 on upload.  What
actually keeps a real file under it today is ``MAX_CONTENT_LENGTH``, a bound in
a different module that nothing ties to this one.  :data:`_PER_STATEMENT` makes
the budget local and stated instead of inherited, so a third column here or a
raise to either of those ceilings costs one more round trip rather than an
error.  Found by adversarial security review 2026-08-25.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, plain
data out, no Flask import, no clock read.  It INSERTS and does NOT commit --
the caller owns the unit of work.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.extensions import db
from app.models.merchant import Merchant


#: How many merchant names one INSERT may carry.
#:
#: Two bind parameters each against libpq's 65,535, kept an order of magnitude
#: under it so the budget is obviously safe rather than arithmetically safe.
#: The developer's own year-to-date import names 62 merchants, so a real pass
#: is one statement and this bound is never reached.
_PER_STATEMENT: int = 5_000


def resolve_merchants(
    account_id: int, names: "set[str]",
) -> "dict[str, int]":
    """Return the merchant row id for each of *names*, creating what is new.

    Args:
        account_id: The account whose statements name them.  A merchant is
            per-account, so this is half of the identity rather than a filter
            (:class:`~app.models.merchant.Merchant`).
        names: Every merchant word this pass mentions.  Empty issues no
            statement at all -- an INSERT of no rows and an ``IN ()`` are both
            statements with nothing to do.

    Returns:
        ``{name: merchant_id}``, TOTAL over *names*: every one is either found
        or created here, so a caller may index it directly rather than
        carrying a fallback for a name it just asked about.
    """
    if not names:
        return {}
    ordered = sorted(names)
    for start in range(0, len(ordered), _PER_STATEMENT):
        db.session.execute(
            pg_insert(Merchant)
            .values([
                {"account_id": account_id, "name": name}
                for name in ordered[start:start + _PER_STATEMENT]
            ])
            .on_conflict_do_nothing(
                index_elements=[Merchant.account_id, Merchant.name],
            )
        )
    rows = (
        db.session.query(Merchant.name, Merchant.id)
        .filter(
            Merchant.account_id == account_id,
            Merchant.name.in_(names),
        )
        .all()
    )
    return dict(rows)
