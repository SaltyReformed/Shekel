"""
Shekel Budget App -- A hidden row: what a refusal may say about a row no screen shows.

Plan step ``credit_card:CC-5-4a-4``.  A deleted row takes no money (ruling
**R-CC89**), and a stale page's Mark Paid, Save or add purchase on one is told
so by name (rulings **R-CC101**, **R-CC104**; never by id, **R-CC98**).  Two
acts hide a row, and the sentence says which (ruling **R-CC107**, developer
2026-09-24, "Say archived": *"When the row's recurring item is archived, the
sentence says 'Gym was archived: a payment cannot be recorded under it.  Reload
the page.' (and the same for a Save or a purchase). A row you deleted still says
'was deleted'. If you deleted one row and later archived the whole item, it says
'archived', which is also true."*): the row's own delete, and its recurring
item's archive, which hides the item's empty Projected rows
(``routes/templates/crud._soft_delete_projected_rows``) and whose un-archive
brings them back (ruling **R-CC86**).

It lives in ``app/utils`` rather than beside the ownership door that first
answered with a name (``auth_helpers``, which imports Flask) because the
services that refuse the same rows -- the status seam, the settle verbs, the
purchase door -- say the same sentence, and a service imports no Flask
(``CLAUDE.md`` Architecture).
"""

from dataclasses import dataclass

from sqlalchemy import select

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate


@dataclass(frozen=True)
class HiddenRow:
    """A hidden row: its name, and whether its recurring item was archived.

    What a door holds for a row it refuses because it is hidden, and all it
    holds: the NAME, so a door holding one has no row to write money under --
    the refusal ruling **R-CC89** exists for stays structural rather than a
    check each door must remember -- and which act hid it, so the sentence can
    say so (ruling **R-CC107**).  Every sentence an app door says about a
    hidden row takes one: the status seam's ``deleted_row_payment_refusal``,
    ``entry_service.deleted_row_purchase_refusal`` and the Save door's
    ``routes/transactions/_helpers._deleted_row_change_refusal``.  The
    database's own refusal (``app/deleted_row_infrastructure``, which only a
    writer that skips every door meets) takes none and says "was deleted"
    whichever act hid the row (finding **CC-377**).

    Attributes:
        name: The row's name (ruling **R-CC98**: never its id).
        archived: Whether the row's recurring item is archived.  ``False``
            for a row gone from the table: a one-off's delete removes its row,
            and nothing is archived by removing a row.
    """

    name: str
    archived: bool = False

    @property
    def went(self) -> str:
        """The words for how the row went: ``"was archived"`` or ``"was deleted"``."""
        return "was archived" if self.archived else "was deleted"

    @classmethod
    def of(cls, row: Transaction) -> "HiddenRow":
        """Return what a refusal may say about *row*, a hidden row still in the table.

        **The ONE producer of** :attr:`archived`: the row's recurring item is
        inactive.  A row whose item is archived says so, however it was hidden
        (ruling **R-CC107**'s last sentence).  A row with no item -- a CC
        payback, a transfer shadow -- is never archived.

        **Read FRESH, by a statement of its own, and never through**
        ``row.template``.  A row that loaded its definition before the door's
        row lock -- a companion's visibility check loads it -- keeps it, and
        the settle verb's and the seam's lock (``row_write_lock.lock_row``)
        re-reads ``is_deleted`` alone: an archive that committed while such a
        door waited for the lock (the race ruling **R-CC96** lets a door see)
        would still read active there.  Measured by Mark Paid racing the
        archive (``test_cc5_4a4_row_lock_races.TestMarkPaidAgainstArchive``).
        The purchase door's ``lock_and_read`` reloads the row and drops its
        ``template``, and the route doors' race arms read after a rollback, so
        those read fresh either way; the case is the SETTLE's sentence, which a
        service caller is told.

        **The read flushes nothing** (``no_autoflush``), so asking writes none
        of the caller's staged state.  The three settle verbs ask it FIRST,
        through ``reject_unsettleable``, ahead of their lock and of every read
        that would flush, so that a refused call leaves a caller's staged state
        unwritten; an autoflushing read here broke that for a row loaded
        deleted beside a staged change, and all three flushed it before
        raising (review 7 of this step, measured 2026-09-24; graded by
        ``test_cc5_4a4_hidden_row_doors.TestTheHiddenRowsWordsFlushNothing``).
        What it reads is the definition's ``is_active`` as the database holds
        it, so a caller that staged an archive and had not flushed it would
        read the item active.  None does: the two route modules that write
        ``TransactionTemplate.is_active`` (``routes/templates/crud``,
        ``routes/salary/profiles``) reach no caller of this, directly or
        through the services they call, before their commit (a caller
        census, 2026-09-24).

        Args:
            row: A session-attached row the caller has found hidden.  A
                ``Transaction``: a transfer's words are the status seam's own
                (``reject_settlement_on_a_deleted_row``).

        Returns:
            The row's name, and whether its item is archived.
        """
        if row.template_id is None:
            return cls(row.name)
        with db.session.no_autoflush:
            active = db.session.execute(
                select(TransactionTemplate.is_active)
                .where(TransactionTemplate.id == row.template_id)
            ).scalar_one_or_none()
        return cls(row.name, archived=active is False)

    @classmethod
    def of_reread(cls, row: "Transaction | None", name: str) -> "HiddenRow":
        """Return what a refusal may say about a row a door re-read after its rollback.

        The race arm's answer at the two doors that name the row they lost to
        a delete (``routes/transactions/_helpers._door_naming_a_gone_row``
        and ``routes/entries._purchase_refused_response``): *row* is the
        re-read, ``None`` when a one-off's delete took it out of the table --
        named then by the *name* the door read while it was live, and never
        archived -- and otherwise :meth:`of` the row.

        Args:
            row: The row as re-read, or ``None``.
            name: The row's name, read before the door acted on it.

        Returns:
            The :class:`HiddenRow`.
        """
        return cls(name) if row is None else cls.of(row)
