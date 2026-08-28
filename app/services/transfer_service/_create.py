"""
Shekel Budget App -- Transfer Service: the CREATE verb

The one path that brings a transfer and its two shadow
:class:`~app.models.transaction.Transaction` rows into existence, and the
:class:`TransferSpec` value that describes one such request.  Transfer
Invariants 1 and 3-5 are established here: the pair is created together, in
the parent's period, scenario, status, category, amount and due date.

**This leaf is the ONE holder of the W9907 status allowlist entry.**  Both
writes it makes are CONSTRUCTOR writes -- ``Transaction(status_id=...)`` in
:func:`_build_shadow` and ``Transfer(status_id=...)`` in
:func:`create_transfer` -- which the status fence exempts by naming this
module.  Every other status write in the package goes through
:func:`app.services.status_seam.apply_status_change`
(:mod:`app.services.transfer_service._status`), so the allowlist stops at
this file rather than covering the package.  Plan step X-aj2 makes the write
door structural and deletes the entry.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import SettlementBasisEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import posting_service
from app.services import status_seam
from app.services.settle_day import SettleDay
from app.services.status_seam import reject_settle_day_without_settled_status
from app.services.transfer_service._loan_posting import (
    _reject_payment_before_origination,
    _reject_transfer_out_of_loan,
    _sync_loan_postings_if_loan,
)
from app.services.transfer_service._ownership import (
    _get_owned_account,
    _get_owned_category,
    _get_owned_period,
    _get_owned_scenario,
    _get_owned_transfer_template,
)
from app.services.transfer_service._status import apply_settle_day_to_pair
from app.services.transfer_service._validation import _validate_positive_amount
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_CREATED,
    log_event,
)

logger = logging.getLogger(__name__)


def shadow_names(
    from_account: Account, to_account: Account
) -> "tuple[str, str]":
    """Return the ``(expense, income)`` shadow display names for two endpoints.

    **A shadow's name is DERIVED from the pair's endpoints, and this is the one
    place it is COMPOSED** -- here, beside the constructor that first applies
    it, and called again by
    :mod:`app.services.transfer_service._endpoints`, which re-derives both names
    when a transfer moves between accounts.  Until plan step R10-b there was no
    second writer, because there was no way to move a transfer's endpoints at
    all: the recurrence engine applied a definition's account change by DELETING
    every generated row and building replacements, which re-ran this rule by
    re-running the create.

    **One reader PARSES the format back out, and it does not go through here**
    (adversarial review of R10-b).  ``grid_view_service._short_display_name``
    strips ``"Transfer to "`` / ``"Transfer from "`` by literal prefix and
    length, so the grid's row label silently mis-renders if this format ever
    moves -- and R10-b raises that stake rather than lowering it, by making a
    shadow's name MUTABLE after creation.  Reported rather than fixed here: the
    coupling is display-only, the fix is a shared prefix constant that arm reads,
    and it is not this step's to change.

    Leaving a moved pair's names alone would show "Transfer to Fidelity Money
    Market" on a row whose money now arrives at Emergency Fund -- a label that
    contradicts its own row, which is the class of defect the recurrence arc
    keeps finding one table at a time.

    The PARENT's name is deliberately not derived here.  A transfer's own
    ``name`` is the caller's: :func:`create_transfer` defaults it from the
    endpoints only when the caller states none, and every template-linked
    transfer carries its definition's name instead -- so re-deriving it on a
    move would overwrite a stated fact with a default.

    Args:
        from_account: The source account -- the expense leg's account, and the
            account the INCOME shadow's name says the money came from.
        to_account: The destination account -- the income leg's account, and
            the account the EXPENSE shadow's name says the money went to.

    Returns:
        ``(expense_shadow_name, income_shadow_name)``, in the order
        :class:`~app.services.transfer_service._validation.TransferRows`
        declares its legs.
    """
    return (
        f"Transfer to {to_account.name}",
        f"Transfer from {from_account.name}",
    )


def _build_shadow(
    xfer: Transfer, account_id: int, name: str, transaction_type_id: int
) -> Transaction:
    """Construct one shadow ``Transaction`` mirroring the parent transfer.

    Both shadows are transfer-generated (``template_id=None``,
    ``credit_payback_for_id=None``, no independent ``notes``) and inherit
    period / scenario / status / category / amount / due_date from the
    just-created ``xfer`` so the three rows stay equal (Transfer
    Invariants 1 and 3).  Only the per-side fields vary.

    Args:
        xfer: The parent :class:`Transfer`, already flushed so
            ``xfer.id`` is set (the shadow's ``transfer_id`` FK).
        account_id: The account this shadow lives in (``from_account``
            for the expense side, ``to_account`` for the income side).
        name: The shadow's display name.
        transaction_type_id: ``ref.transaction_types.id`` for the side
            (expense or income).

    Returns:
        An unsaved :class:`Transaction`; the caller adds it to the
        session.
    """
    return Transaction(
        account_id=account_id,
        template_id=None,       # Shadows are transfer-generated, not template-generated.
        transfer_id=xfer.id,
        pay_period_id=xfer.pay_period_id,
        scenario_id=xfer.scenario_id,
        status_id=xfer.status_id,
        name=name,
        category_id=xfer.category_id,
        transaction_type_id=transaction_type_id,
        estimated_amount=xfer.amount,
        settled_amount=None,
        settled_basis_id=None,
        # The settle DAY and its basis are the ASSERTION, and a shadow is
        # born asserting nothing: a born-SETTLED transfer's day is written
        # by ``apply_settle_day_to_pair`` below, through the seam, so this
        # constructor never states one (plan step **X-az**).
        settled_on=None,
        settled_day_basis_id=None,
        is_override=False,
        is_deleted=False,
        credit_payback_for_id=None,
        notes=None,
        due_date=xfer.due_date,
    )


@dataclass(frozen=True)
class TransferSpec:  # pylint: disable=too-many-instance-attributes
    """The canonical inputs for creating a transfer.

    Bundles the fourteen fields :func:`create_transfer` needs into one
    cohesive value object so the sole transfer-creation path takes a
    single argument rather than a fourteen-field signature.  Every field
    is read by ``create_transfer`` and supplied together by every caller
    (the new-transfer route, the recurrence engine, the materialize path)
    -- this is one "transfer to create" request, mirroring the columns
    of the ``Transfer`` row it produces.

    Pylint: ``too-many-instance-attributes`` (14/7) -- these are the
    irreducible inputs of one creation request, read as a flat unit by
    the single consumer; there is NO cohesive sub-group to nest, so
    splitting would fragment one concept for no gain.  Mirrors the
    ``AmortizationRow`` / ``PayoffScenarios`` precedent.  Frozen so a
    constructed spec is an immutable record of one request.

    Attributes:
        user_id: Owner of the transfer.
        from_account_id: Account money leaves (expense side).
        to_account_id: Account money enters (income side).
        pay_period_id: Pay period for the transfer.
        scenario_id: Budget scenario.
        amount: Transfer amount (positive Decimal).
        status_id: Initial status (typically 'projected').
        category_id: Optional spending category mirrored to both
            shadows.  May be None.
        notes: Optional notes on the transfer (not mirrored to shadows).
        transfer_template_id: Optional link to the generating transfer
            template (for recurrence).
        name: Optional display name.  If None, generated from the
            account names.
        due_date: Optional due date stored on the transfer and mirrored
            to both shadow transactions.
        settle_day: Optional settle DAY, and HOW that day is known
            (:class:`app.services.settle_day.SettleDay`), for a transfer
            created ALREADY settled (plan step E1a): mirrored to both shadows
            exactly as the update path's explicit day is, with the same
            default -- a born-settled transfer without one settled TODAY on
            the ``entered`` basis (the F-048 / C-22 rule, on the user's clock
            and on nobody's word but theirs).  Meaningless for an unsettled
            status, so :func:`create_transfer` rejects that combination loudly
            rather than recording a settle day for a payment that has not
            happened.
        occurs_on: WHICH OCCURRENCE of its template's cadence this transfer
            answers, or ``None`` for a transfer no cadence named -- an ad-hoc
            one, or the one-time branch of ``routes/transfers/_instances``.
            Only the transfer recurrence engine states it, and it is NOT
            mirrored to the shadows: a shadow is created from its parent rather
            than from an occurrence, and no generate pass asks a shadow whether
            an occurrence has been written (plan step **R17**).
    """

    user_id: int
    from_account_id: int
    to_account_id: int
    pay_period_id: int
    scenario_id: int
    amount: Decimal
    status_id: int
    category_id: int | None
    notes: str | None = None
    transfer_template_id: int | None = None
    name: str | None = None
    due_date: date | None = None
    settle_day: SettleDay | None = None
    occurs_on: date | None = None


def create_transfer(spec: TransferSpec) -> Transfer:
    """Create a transfer and its two shadow transactions atomically.

    This is the ONLY code path that should create rows in
    budget.transfers.  It enforces invariants 1-5 from design doc
    section 4.5.

    Args:
        spec: The :class:`TransferSpec` carrying the owner, endpoints,
            placement (period/scenario), amount, status, category, and
            optional metadata (notes/name/template link/due date) for
            the transfer to create.

    Returns:
        The created Transfer object (shadows accessible via
        transfer.shadow_transactions backref).

    Raises:
        ValidationError: If amount is non-positive, accounts are the
            same, or any business rule is violated.
        NotFoundError: If any referenced entity does not exist or
            does not belong to user_id.
    """
    # ── Validate inputs ────────────────────────────────────────────
    amount = _validate_positive_amount(spec.amount)

    if spec.from_account_id == spec.to_account_id:
        raise ValidationError(
            "Source and destination accounts must be different."
        )

    from_account = _get_owned_account(
        spec.from_account_id, spec.user_id, label="Source account"
    )
    to_account = _get_owned_account(
        spec.to_account_id, spec.user_id, label="Destination account"
    )
    _reject_transfer_out_of_loan(from_account)
    _get_owned_period(spec.pay_period_id, spec.user_id)
    # R-C: a loan cannot receive a payment before it originates -- the fold
    # would erase it while the cash side still debits the funding account.
    # Deliberately AFTER ``_get_owned_period``: this guard reads that period's
    # ``start_date`` (the installment fallback), so running it first would read
    # an unowned row and answer a cross-user id with a 400 carrying a date from
    # it, where the ownership rule requires an indistinguishable 404.
    _reject_payment_before_origination(
        to_account, spec.pay_period_id, spec.due_date,
    )
    _get_owned_scenario(spec.scenario_id, spec.user_id)
    _get_owned_category(spec.category_id, spec.user_id)
    _get_owned_transfer_template(spec.transfer_template_id, spec.user_id)
    created_status = db.session.get(Status, spec.status_id)
    # The settled-iff-dated rule, asked BEFORE any row exists.  It is the seam's
    # own predicate rather than a second statement of it (plan step X-f1b,
    # finding **N-183**): the seam cannot answer this case itself, because the
    # born-settled branch below is gated on the status being settled, so an
    # unsettled create carrying a day never reaches it and the day would be
    # dropped in silence.  One rule, two moments.
    reject_settle_day_without_settled_status(spec.status_id, spec.settle_day)

    # ── Ref data lookups ───────────────────────────────────────────
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    # ── Determine names ────────────────────────────────────────────
    transfer_name = spec.name or f"{from_account.name} to {to_account.name}"
    expense_shadow_name, income_shadow_name = shadow_names(
        from_account, to_account,
    )

    # ── Create transfer record ─────────────────────────────────────
    xfer = Transfer(
        user_id=spec.user_id,
        from_account_id=spec.from_account_id,
        to_account_id=spec.to_account_id,
        pay_period_id=spec.pay_period_id,
        scenario_id=spec.scenario_id,
        status_id=spec.status_id,
        transfer_template_id=spec.transfer_template_id,
        name=transfer_name,
        amount=amount,
        category_id=spec.category_id,
        notes=spec.notes,
        due_date=spec.due_date,
        occurs_on=spec.occurs_on,
        is_override=False,
        is_deleted=False,
    )
    db.session.add(xfer)
    # Flush to get transfer.id -- required before creating shadows
    # that reference it via transfer_id FK.
    db.session.flush()

    # ── Create the two shadows (expense from_account, income to_account) ──
    expense_shadow = _build_shadow(
        xfer, spec.from_account_id, expense_shadow_name, expense_type_id
    )
    db.session.add(expense_shadow)
    income_shadow = _build_shadow(
        xfer, spec.to_account_id, income_shadow_name, income_type_id
    )
    db.session.add(income_shadow)
    db.session.flush()

    # ── Born-settled coherence (plan step E1a) ─────────────────────
    # A transfer BORN settled used to book NO cash entry and carry no settle
    # day -- a settled effect the ledger never saw, which the
    # checked-projection assert refuses the moment the loan syncs.  So the
    # create chokepoint applies update_transfer's two settle rules:
    # ``settled_on`` is the caller's explicit day or the user's today (the
    # F-048 / C-22 defense -- a transfer created settled settled at creation),
    # and the posting reconcile runs (the cash entry + the loan genesis
    # reconcile).  ``created_status`` was loaded in the validation block, which
    # also rejects a ``settled_on`` on an unsettled create before any row
    # exists.  ``display_today()`` rather than the server's day: this value IS
    # the ``entry_date`` the postings below are filed under (step C2's one
    # clock), and that day is the user's (ruling R-DH (b)) -- and the SEAM is
    # what applies it, since plan step X-f1b made that the column's one writer
    # (finding N-183).  The shadows are born in the parent's status, so this is
    # an identity status change carrying the day and moves nothing else.
    if created_status is not None and created_status.is_settled:
        # The shadows are BORN in the settled status, so the seam sees an
        # identity transition and cannot demand a record of what moved -- but a
        # settled row that records nothing is one
        # ``row_valuation.settled_figure`` refuses to value.  So the create
        # supplies one: the figure
        # is the transfer's own amount, which is what a born-settled transfer
        # says moved, and the basis is ``derived`` because the app resolved it
        # from the row rather than a human correcting what the app booked (plan
        # step X-au-c3).
        apply_settle_day_to_pair(
            expense_shadow, income_shadow, spec.settle_day,
            settlement=status_seam.Settlement(
                amount=amount, basis=SettlementBasisEnum.DERIVED,
            ),
        )
        db.session.flush()
        posting_service.sync_transfer_postings(xfer, settled=True)
        _sync_loan_postings_if_loan(xfer)

    log_event(
        logger, logging.INFO, EVT_TRANSFER_CREATED, BUSINESS,
        "Transfer created with shadow transactions",
        user_id=spec.user_id,
        transfer_id=xfer.id,
        from_account_id=spec.from_account_id,
        to_account_id=spec.to_account_id,
        pay_period_id=spec.pay_period_id,
        scenario_id=spec.scenario_id,
        amount=str(amount),
        status_id=spec.status_id,
        category_id=spec.category_id,
        transfer_template_id=spec.transfer_template_id,
        expense_shadow_id=expense_shadow.id,
        income_shadow_id=income_shadow.id,
    )
    return xfer
