"""
Shekel Budget App -- Anchor Release Model (budget schema)

A RELEASE withdraws one bank level from the level relation without editing it
(plan step ``balance:X-bj-1``, rulings **R-IS** and **R-JN**; the shape ruled
2026-09-16).

**What a release is.**  A bank level
(:class:`~app.models.account.AccountAnchorHistory` with a
``statement_import_id``) is a conclusion solved from the lines recorded at or
before its own day.  Recording a line at or before that day means the level was
solved without it; deleting an import means the lines it was solved against
are gone.  Either event withdraws the conclusion -- the import package calls it
*releasing an anchor* -- and both were reproduced as silently wrong openings
(``$150.00`` on the delete path) before the release existed
(``statement_import._anchor.release_anchors_from``).

**Why it is its own relation and not a row in the level relation.**  The level
relation is append-only at the database tier (rulings **R-HY**, **R-IC**), so
the release can no longer be the UPDATE it was -- ``balance_effective_on = NULL``
on the import row, which also destroyed the record that a placement had ever
existed and is why the statements page's badge could not name its cause
(finding **BAL-485**).  An append-only log of OBSERVATIONS cannot un-observe;
a withdrawal is a different kind of fact, so it gets a relation of its own,
and the level relation keeps every row an observation with an amount.  A
superseding row inside the level relation would observe nothing, so its amount
would go nullable and every reader of standing levels would carry that branch
for good.

**A level STANDS when no release names it.**  ``uq_anchor_releases_anchor``
makes "withdrawn at most once" structural, and the readers that need standing
levels ask one LEFT JOIN (``statement_import._balance.bank_levels``).

**The cause is recorded, and the record survives the cause.**
:attr:`released_by_import_id` names the import whose fresh lines undercut the
level; the delete door names the DOOMED import before destroying it, so
``system.audit_log`` holds the cause even though the key's
``ON DELETE SET NULL (released_by_import_id)`` then nulls it here.  NULL
therefore means exactly one thing: the import whose lines changed no longer
exists -- a delete released this level, or the import that released it was
itself deleted later -- and the badge says that rather than guessing.
:attr:`lines_changed_from` is the earliest day whose lines changed, the one
fact both release doors pass, and it stays whatever happens to the import.

**Append-only, with ONE admitted transition.**  The shared trigger
``budget.refuse_append_only_change`` (:mod:`app.append_only_infrastructure`)
refuses every UPDATE except the key's own SET NULL (old cause present, every
other column equal, and the import it named already gone -- so a hand-written
UPDATE erasing a standing cause is refused too), refuses DELETE while the
released level still stands (a
release goes only with its level, as a level goes with its file and history
with its account), and refuses TRUNCATE outright; the ORM listener pair
:func:`~app.models.append_only.install_append_only_guards` installs is the
named half of the same refusal.  ``system.audit_log`` records every row.
"""

from app.extensions import db
from app.models.append_only import (
    AppendOnlyViolation,
    install_append_only_guards,
)
from app.models.mixins import AccountScopedMixin, CreatedAtMixin


class AnchorReleaseImmutableError(AppendOnlyViolation):
    """Raised when ORM code tries to UPDATE or DELETE a release.

    The fourth member of the append-only family
    (:class:`~app.models.account.AccountAnchorHistoryImmutableError` and its
    two siblings).  A release is the record that a level was withdrawn and
    why; editing it would let a level be silently reinstated, which is the
    ``$150.00`` hole the release exists to close.  The database trigger admits
    exactly one UPDATE -- the referencing key's own SET NULL when the causing
    import is deleted -- and that action runs at the database tier, where this
    listener never sees it.
    """


class AnchorRelease(AccountScopedMixin, CreatedAtMixin, db.Model):
    """One withdrawal of one bank level, and what caused it.

    See the module docstring for what a release is and why it is its own
    relation.

    Attributes:
        anchor_id: The level withdrawn -- a ``budget.account_anchor_history``
            row carrying a ``statement_import_id``.  Composite with
            ``account_id`` onto ``uq_anchor_history_account_id``, so a release
            cannot name another account's level.  CASCADE: a withdrawal goes
            with its level.
        released_by_import_id: The import whose fresh lines undercut the
            level, or NULL once that import no longer exists (see the module
            docstring for the two events NULL covers).  Composite with
            ``account_id`` onto ``uq_statement_imports_id_account``; the
            column-subset ``SET NULL`` is what lets the key be composite at
            all, since a bare composite SET NULL would null ``account_id`` too
            and fail on NOT NULL.
        lines_changed_from: The earliest day whose recorded lines changed --
            the day the releasing door passed, whichever door it was.  What
            the badge names when the import cannot be.
    """

    __tablename__ = "anchor_releases"
    __table_args__ = (
        # Withdrawn at most once.  ``resting_on`` reads STANDING levels, so a
        # second release of one level is a state no door produces; the key
        # makes it a state no writer can.
        db.UniqueConstraint("anchor_id", name="uq_anchor_releases_anchor"),
        db.ForeignKeyConstraint(
            ["account_id", "anchor_id"],
            ["budget.account_anchor_history.account_id",
             "budget.account_anchor_history.id"],
            name="fk_anchor_releases_anchor_account",
            ondelete="CASCADE",
        ),
        db.ForeignKeyConstraint(
            ["released_by_import_id", "account_id"],
            ["budget.statement_imports.id",
             "budget.statement_imports.account_id"],
            name="fk_anchor_releases_import_account",
            ondelete="SET NULL (released_by_import_id)",
        ),
        db.Index("idx_anchor_releases_import", "released_by_import_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    anchor_id = db.Column(db.Integer, nullable=False)
    released_by_import_id = db.Column(db.Integer)
    lines_changed_from = db.Column(db.Date, nullable=False)

    def __repr__(self):
        return (
            f"<AnchorRelease anchor={self.anchor_id} "
            f"by_import={self.released_by_import_id} "
            f"from={self.lines_changed_from}>"
        )


install_append_only_guards(AnchorRelease, AnchorReleaseImmutableError)
