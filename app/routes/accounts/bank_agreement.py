"""
Shekel Budget App -- The app's books beside the BANK's own record

"Does my bank agree with my book, and where does it stop agreeing?"  Plan step
**bank_import:X-f6e-2**, ruling **R-GF**.

**It reports and it never refuses.**  R-GF settled that the comparison is an
instrument rather than a gate, on a measurement: after 230 accepted matches on
the developer's own data, 54 of 229 days agreed and 175 did not, worst
``$3,888.69`` -- because his app's opening assertion is 2026-03-27 where his
statement starts 2026-01-02 (finding **N-314**, owned by ``balance:X-f3c``).  A
refusal there would deadlock, since importing is what fixes the disagreement
and a first import has matched nothing.  So nothing on this page blocks
anything, and no figure it shows moves a balance.

**Why it lives beside the statements page rather than on it.**  The subject is
the same one -- what the bank said -- but the page is a per-day comparison over
a span, where ``statements`` is an upload form and a receipt.  It is the cut
:mod:`app.routes.accounts.difference` already made out of ``anchor`` along the
same seam: the write door and the read-only comparison of what it wrote are two
subjects, and the 1000-line module ceiling arrives for whichever of them grows.

**It gates on the account KIND as well as on ownership**
(:func:`~app.routes.accounts._cash_page.load_cash_account_or_404`), for exactly
the reason its sibling does: a loan or a property has no bank statement, its own
detail page 404s, and a fragment written without that gate is what plan step
X-f2-b's adversarial review caught rendering cash copy for an amortizing
account.

Services boundary: this module owns the HTTP-shaped concerns and delegates
every read to :mod:`app.services.bank_agreement`.
"""

from datetime import date

from flask import abort, render_template, request
from marshmallow import ValidationError
from flask_login import current_user, login_required

from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.routes.accounts.statements import EVIDENCE_COPY
from app.enums import StatementBalanceEvidenceEnum
from app.schemas.validation.statements import AgreementDaySchema
from app.services import balance_at, bank_agreement
from app.utils.auth_helpers import require_owner

#: One schema instance, constructed at import like every sibling's.
_day_schema = AgreementDaySchema()


@accounts_bp.route("/accounts/<int:account_id>/statements/agreement")
@login_required
@require_owner
def statement_agreement(account_id):
    """Render this account's books beside the bank's, day by day.

    Args:
        account_id: The account to compare.

    Returns:
        The rendered page, or a 404 when the account is not the caller's or is
        a kind that has no bank statement -- the security response rule's
        answer for both "not found" and "not yours".
    """
    account = load_cash_account_or_404(account_id)
    agreement = bank_agreement.bank_agreement(
        account, balance_at.BalanceContext.build(current_user.id),
    )
    return render_template(
        "accounts/statement_agreement.html",
        account=account,
        agreement=agreement,
        evidence_copy=EVIDENCE_COPY,
        anchor_assumed=_anchor_is_assumed(agreement),
    )


@accounts_bp.route("/accounts/<int:account_id>/statements/agreement/day")
@login_required
@require_owner
def statement_agreement_day(account_id):
    """Render what makes up one day's difference, on both sides.

    The row's drill-down, fetched by htmx when the owner opens a day.

    Args:
        account_id: The account to explain.

    Returns:
        The rendered fragment, or a 404 for an account that is not the
        caller's, is a kind with no statement, or a ``day`` that is not a day.
        **A malformed day is a 404 rather than a rendered apology**: nothing
        composes this URL by hand, so a value that does not parse is a
        tampered or stale request rather than a user mid-edit -- the opposite
        of the difference preview's box, which a human is typing into.
    """
    account = load_cash_account_or_404(account_id)
    day = _requested_day()
    return render_template(
        "accounts/_statement_agreement_day.html",
        account=account,
        detail=bank_agreement.day_detail(
            account, balance_at.BalanceContext.build(current_user.id), day,
        ),
    )


def _requested_day() -> date:
    """Return the ``day`` query argument, or abort 404.

    Returns:
        The civil day the caller asked about.

    Raises:
        NotFound: When ``day`` is missing or does not parse.  Raised through
            the schema's own rules rather than a second date parser here, so
            the fragment and any future writer read a day by one set of rules.

    **Loaded ONCE.** Marshmallow's ``validate`` IS a ``load`` that swallows the
    error, so validating and then loading deserialises the whole payload twice
    per request -- the redundant-derivation shape, found by adversarial review
    2026-08-24. Catching :class:`~marshmallow.ValidationError` is a SPECIFIC
    exception and not the broad ``except`` CLAUDE.md rule 1 forbids.
    """
    try:
        return _day_schema.load(dict(request.args))["day"]
    except ValidationError:
        # ``abort`` raises ``NotFound``; nothing falls through.
        return abort(404)


def _anchor_is_assumed(agreement) -> bool:
    """Return whether the bank column rests on a figure nothing confirmed.

    Args:
        agreement: The :class:`~app.services.bank_agreement.BankAgreement`, or
            ``None``.

    Returns:
        True only when an anchor exists and its evidence is the weakest rung.

    **Decided in Python, on the enum MEMBER**, which is the project-wide
    IDs-for-logic rule at the place a template makes it easiest to break: a
    ``{% if evidence.name == 'UNCORROBORATED' %}`` reads naturally, compares a
    display string, and sits in the one language this project forbids financial
    reasoning in.  The same reason ``difference.difference_verdict`` hands its
    partial a computed name rather than a Decimal.
    """
    return (
        agreement is not None
        and agreement.anchor is not None
        and agreement.anchor.evidence
        is StatementBalanceEvidenceEnum.UNCORROBORATED
    )
