"""The PATCH handler's FIELD-APPLICATION step.

**Split out of :mod:`.mutations` at plan step ``balance:X-bi-7a``**, whose
re-key of the override flip pushed that module to ``max-module-lines``; the
cut is the one X-au-j made for :mod:`._gates`, one step later in the same
handler: the gates run BEFORE this step, and what follows it
(``apply_requested_status``, the posting reconcile, the commit) stays with the
handler.  A pure move: :data:`_SEAM_OWNED_FIELDS` and
:func:`_apply_field_updates` are the text that left, and
:func:`_apply_regular_update` calls the second exactly as before.

Boundary discipline: this writes the loaded row from the request's
already-schema-loaded ``data``; it issues no query of its own beyond the
lazy loads the row's accessors make, and it does not commit.
"""

from app.services import transaction_service
from app.services.amount_ownership import state_own_amount

from app.routes.transactions._helpers import _error_transaction_response


# The PATCH fields the status seam writes, so the generic ``setattr`` loop in
# :func:`_apply_regular_update` must NOT.  They are one fact -- a row is settled
# if and only if it carries the day its money moved -- and
# ``status_seam.apply_status_change`` writes them in a single statement after
# verifying the transition and expiring the ``status`` relationship.  A bare
# ``setattr`` would skip all of that, and for ``settled_on`` it would make the
# loop a SECOND writer of the column the seam is the single door to: exactly
# finding **N-183** (which ``update_transfer`` shipped and X-f1b closed), about
# to be re-run on the transaction side by this step's own edit door (finding
# **N-185**).  Naming the pair here is what keeps the loop from growing a third
# member silently.
#: The fields a SEAM owns, excluded from the field-application loop so it
#: cannot become a second writer of any of them.  ``settled_amount``
#: joined at plan step X-au-c3: it states WHAT MOVED, so a bare ``setattr``
#: could book money on a row that never settled and could do it beside a basis
#: saying something else.  It reaches the record only through
#: ``apply_requested_status``, which hands it to the settle verb or refuses it
#: with a designed 400 when no settle is happening.
#:
#: ``estimated_amount`` joined at plan step **X-au-k**, and it is the one
#: member this set no longer has to be TRUSTED about: the column is read-only
#: on the model now, so a ``setattr`` that reached it raises ``AttributeError``
#: rather than half-writing the amount-ownership pair.  It stays named here
#: because the loop must SKIP it deliberately rather than crash, and because a
#: reader asking which fields a seam owns should find all four in one place.
_SEAM_OWNED_FIELDS = frozenset(
    {"status_id", "settled_on", "settled_amount", "estimated_amount"}
)


def _apply_field_updates(txn, data, *, amount_authored, period_changed):
    """Write the submitted fields onto *txn*, refusing what may not be written.

    The FIELD half of :func:`_apply_regular_update`, extracted so the handler
    keeps one exit per concern rather than growing a branch per rule.  FOUR
    acts, and the order is load-bearing.  **This enumeration said "three" and
    listed three until plan step X-au-j**, by which time the body already
    performed four: an adversarial review found the count stale in the one
    helper whose whole discipline is that its order matters.  The one it had
    never named is marked NEW below.

    1. the ``setattr`` loop.  ``status_id`` and ``settled_on`` are BOTH excluded
       and routed through the status verb instead: a bare ``setattr`` would
       assign the column but skip the transition check, the settle-day
       stamp/clear, and the status-relationship expire that
       ``status_seam.apply_status_change`` owns -- and for ``settled_on`` it
       would make this loop a SECOND writer of a column the seam is the single
       door to (finding **N-185**, the re-run of N-183 on the transaction side).
    2. ``is_override``, which sits with the field writes and ABOVE both the
       refusal below and the status work.  **It had TWO reasons and has ONE
       since plan step X-au-d.**  The retired one: a settle asked the
       projection for a fresher amount and SKIPPED a row the user had
       overridden (a read-time repair plan step X-au-d deleted), so setting
       the flag afterwards would have let a salary row's recompute overwrite the
       estimate the same form just submitted -- there is no recompute and no
       cache left, because act 2b below makes a typed figure the row's OWN and
       the amount model reads ownership rather than this flag (finding
       **N-262**).  The one that stands: act 3 FLUSHES (see below), so a flag
       written after it is written after the UPDATE it belongs in -- which for
       a period move leaves the row inside the generation index's partial
       predicate (``is_override = FALSE``) while its period is already the new
       one.
       **Plan step R17 narrowed what that can collide with but did not remove
       it**: the dated index is keyed
       ``(template, scenario, occurs_on)``, and a move does not touch
       ``occurs_on``, so a DATED row no longer collides with whatever the
       engine put in the target period.  An UNDATED template-linked row
       (``occurs_on IS NULL``) is still keyed on its paycheck by
       ``idx_transactions_template_scenario_undated``, so for that row the
       ordering is exactly as load-bearing as it was -- and since plan step
       balance:X-bi-7a the flip is made only where the definition RECURS,
       so such a row of a rule-less definition is not taken out of that
       index at all (the shape ``carry_forward_service._execute`` states,
       with its measurement; ``recurrence:R19-b`` deletes it).
    2b. **(shipped at X-au-c2b, restated at X-au-k)** a typed figure is stated
       through ``amount_ownership.state_own_amount``: a hand-priced row OWNS
       its figure, so storing it RELEASES the relation that priced it.  This
       was two acts -- the loop wrote the column and this cleared the source --
       and their pairing was a convention the loop could break.  It is one act
       over one attribute now, so the release cannot be forgotten and the loop
       cannot reach the column at all (the field is in
       ``_SEAM_OWNED_FIELDS``).
    3. the derived-amount refusal, asked AFTER the loop so ``tracks_purchases``
       reads the RESULTING row: unchecking "Track individual purchases" in the
       same save legitimately gives the row its own amount back.  **It reads a
       LAZY relationship and therefore autoflushes**, which is why this whole
       helper is called from inside the caller's exception net rather than
       above it.

    **The refusal is ruling R-FF's**, the same sentence the reconcile panel
    already obeys ("a tick is correctable exactly when the settle verb takes its
    MANUAL branch"), applied to the second door that offers an amount box.  An
    envelope carrying purchases settles at ``sum(entries)``, so a figure typed
    beside it is not a correction the app can honour: it would be written here,
    overwritten by the settle, and never mentioned.  The popover no longer
    renders the input for such a row (``amount_correctable``), so this is the
    crafted-request and stale-form backstop.  Only a REAL figure is refused: an
    empty box loads as an explicit ``None`` (the field is ``allow_none``), which
    states no amount at all.

    A refused call has already staged the ``setattr`` loop's mutations;
    ``_error_transaction_response`` rolls them back, exactly as the settle-day
    refusal in the caller already relies on.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload, with the rendered-figure
            companion already removed by the caller.
        amount_authored: Whether a HUMAN typed the ``estimated_amount`` this
            payload carries (ruling **R-JR**).  Decides both whether the row
            takes ownership of the figure and whether it stops being the rule's.
        period_changed: Whether this payload MOVES the row to another period.
            Passed in because the caller computes it before the loop below
            rewrites ``pay_period_id``, at which point it can no longer be
            asked.

    Returns:
        A designed 400 response tuple, or ``None`` when every field was
        written.
    """
    # Read BEFORE the loop: ``recurs`` may lazy-load the template, and a load
    # after it autoflushes the moved period with the flag still False (act 3).
    #
    # **INTERIM, stated** (plan step balance:X-bi-7b, leaf 7b-1): a one-off's
    # ``name`` and ``category_id`` are its DEFINITION's (ruling R-BAL23), and
    # this loop still lands them on the ROW alone for such a row, so a crafted
    # PATCH (no form posts either field) renames the row and not its
    # definition until leaf 7b-2 routes them there -- the popover shows the
    # row, the merchant-rule picker (``offerable_templates``) the definition.
    recurs = txn.recurs
    for field, value in data.items():
        if field in _SEAM_OWNED_FIELDS:
            continue
        setattr(txn, field, value)

    if amount_authored:
        # A typed figure makes the row's amount its OWN, and storing it IS
        # releasing the relation that priced it (plan step X-au-k) -- the amount
        # model's own dispatch asserts exactly that: "a row a human RE-PRICED
        # owns its figure because the write door CLEARS its source".  The value
        # is never ``None`` here: ``estimated_amount`` is not ``allow_none`` on
        # any of the three transaction schemas, so ``_normalize_empty_inputs``
        # DROPS an empty box rather than loading it as an explicit nothing.
        #
        # **Gated on AUTHORSHIP rather than on the field's PRESENCE since plan
        # step X-au-h** (ruling **R-JR**).  The popover renders this box on
        # every correctable row and an HTML form posts every input it renders,
        # so presence was true of a notes-only save -- which took ownership of
        # a figure nobody chose and left the row no longer tracking its
        # definition.  Finding **N-248**.
        state_own_amount(txn, data["estimated_amount"])

    # **The flag says ONE thing since plan step X-au-h: this row is the OWNER's,
    # not the rule's.**  Both acts that make it so are stated here, and BOTH
    # were presence tests before -- the amount's is finding **N-248** above, and
    # the period's had the identical shape, because the popover renders a
    # period dropdown on every row and posts it whether or not it was touched.
    # ``period_changed`` is computed by the caller BEFORE the loop above
    # rewrites ``pay_period_id``, which is why it is passed in rather than
    # asked here.
    # **A MOVE flips only when the definition RECURS** (balance:X-bi-7a,
    # developer 2026-09-13): a rule-less definition runs no pass to keep off
    # the row; the flag would hide it from ``propagate_to_unruled_definition``
    # (the twin's defect **BAL-493**) and from a rule added later (R-BAL25).
    # A TYPED FIGURE flips on every linked row until ``X-bi-7b``'s second
    # leaf (7b-2) builds R-BAL21's restate: it lands OWN today, and the flag
    # is what keeps a definition edit from silently discarding it.  The first
    # leaf mints one-offs as rule-less definitions, so this interim now
    # reaches a row the grid itself created.
    if txn.template_id and (amount_authored or (period_changed and recurs)):
        txn.is_override = True

    if (
        data.get("settled_amount") is not None
        and transaction_service.settles_from_entries(txn)
    ):
        return _error_transaction_response(
            txn.id,
            "This row's actual comes from the purchases recorded against it, "
            "so an amount typed here would be discarded. Record the purchase "
            "instead, or correct one that is already there.",
        )
    return None
