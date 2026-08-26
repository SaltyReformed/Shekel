"""
Shekel Budget App -- Statement Match: which of your rows a bank line IS.

**The app's own records of when money moved are guesses, and X-f6a-1 recorded
the answer without yet applying it.**  This package applies it.  Measured on
the developer's own 2026-08-16 SECU export against a production clone: of 58
bank lines an exact-amount predicate pairs uniquely with an app row, only 23
carry the day the app had recorded -- so 35 of 58 are corrections waiting to
happen, by as much as 8 days (finding **N-173**).

Plan step ``bank_import:X-f6a-2``, rulings **R-FS**, **R-FP** and **R-FV**.

The public surface, and what each piece is for:

* :class:`ReviewScope` -- ONE derivation of what a pass over one account may
  act on, built once by the route and threaded through everything below it.
  Plan step ``bank_import:X-f6a-3c-2`` exists because it was not: every act
  derived the account again for itself, at 3.593 s a time over the 215 acts the
  developer's own statement offers -- 12.88 minutes of derivation to work one
  statement, against 5.80 s for the whole pass now.
* :func:`review_set` -- everything the review screen shows for one account:
  what the app proposes, what it could not explain, what is out of reach, and
  what has already been accepted.
* :func:`apply_reviewed` -- the batch door, and the one the screen posts to.
  **It MOVES MONEY.**  It applies every act the owner ticked, each in its own
  SAVEPOINT so a refused item leaves nothing behind and the rest still land,
  and reports what each one did (:class:`BatchOutcome`).  It is not "accept
  everything" (**R-FP**): nothing is applied that was not ticked.
* :func:`accept_match` -- one correspondence between things that already exist.
  Every member row takes the bank's posted day, which settles a still-Projected
  row and corrects a wrongly-dated settled one.
* :func:`create_purchase_from_line` -- ruling **R-FS**'s THIRD shape (plan step
  ``bank_import:X-f6a-3b``): a bank line the app has no row for BECOMES a
  purchase against a budget line the owner picks, or against one this door
  creates for it.  **It MOVES MONEY** in the other direction from the first --
  it records a movement the app did not have at all, where a match re-dates one
  it did.  It records the correspondence through the same function
  :func:`accept_match` does, so there is still exactly one place a match is
  written.
* :class:`CreationBars` -- which of an account's merchants may NOT become
  purchases at all, and why (ruling **R-GJ**, plan step ``bank_import:X-ga``).
  Two bars: the owner answered *never a purchase*, or a SOURCE files the
  merchant as a payment to a credit card and they have not answered for it at
  all.  A barred line is not offered a create control and is refused at the
  door; it is listed as a :class:`ParkedLine` beside the hand-build form, where
  the group match this ruling leaves open is made.  Until this step *never a
  purchase* only withheld a sweep value, and one YTD pass recorded
  **`$7,412.94`** of card payments the app already held as ``CC Payback`` rows
  through the select printed beneath the warning.
* :func:`destinations_for` -- the budget lines that door may write into, which
  is the SAME set the screen offers, and :func:`matched_subjects` /
  :func:`unmatched_destinations`, which are the one statement of what an
  accepted match has already claimed.  **What this exports is what something
  outside the package imports**: ``AppliedItem``, ``RefusedItem``,
  ``MatchedSubjects`` and ``unmatched_rows`` were exported for symmetry and had
  no importer at all, which is a surface nobody asked for.
* :func:`preview_hand_build` -- what the hand-build form's two sides come to
  RIGHT NOW, so ruling **R-FN**'s *a difference is a transaction the user
  accepts* has a figure to accept.  It runs the accept door's own reads and
  refusals without the writes, so the screen and the door cannot state
  different totals (plan step ``bank_import:X-f6d-4``).
* :func:`release_match` -- the undo.  It restores the QUESTION rather than
  the days, and removes what the act CREATED: the purchase a bank line became,
  a group's recorded difference, and the budget line minted to hold a purchase
  where nothing is left in it (plan step ``bank_import:X-f6f``, ruling
  **R-GG**).  :class:`PlannedRemoval` is what the screen prints beside the
  Undo button, from the door's own derivation, so the control names the money
  it is about to destroy.
* The value types :class:`MatchProposal`, :class:`MatchSubmission`,
  :class:`ReviewedRow`, :class:`CandidateRow`, :class:`BankLine`,
  :class:`RowKind`,
  :class:`AcceptedMatch`, :class:`AcceptedGroup`, :class:`AcceptedRow`,
  :class:`ReviewedBatch`, :class:`BatchOutcome` and :class:`ReviewSet`.

**Three rules this package is built on, each of them the developer's ruling of
2026-08-17 rather than an implementation choice:**

1. **A match is stored as IDENTITY and nothing else** (**R-FV**).  It does not
   write ``reconciled_by_id``: that column names a balance the owner asserted
   by hand, a bank line is not one, and what it records is derivable from the
   match once a statement carries the line.  The settle doors RELEASE it as
   they move a day, which is right -- the bank has just contradicted the day
   that link was recorded against.
2. **An unbalanced group is REFUSED and the difference is NAMED**, never
   apportioned and never absorbed by a tolerance.  6 of 16 payroll deposits on
   the developer's own statement sit `$0.05`-`$0.06` from the app's rows
   (finding **N-239**), and a tolerance would silence the instrument that
   found it.
3. **A match may SETTLE a still-Projected row**, because a statement is
   evidence that money moved.  11 rows inside the developer's own statement
   span had never been marked as having happened.

**Every proposal is a question** (**R-FP**): nothing here applies a match the
owner has not accepted, and :mod:`._propose` cannot write at all.
"""

from ._accept import AcceptedMatch, accept_match
from ._bars import CreationBar, CreationBars, ParkedLine
from ._release import (
    PlannedRemoval,
    PlannedRemovals,
    ReleasedMatch,
    release_match,
    removals_by_match,
)
from ._batch import BatchOutcome, ReviewedBatch, apply_reviewed
from ._candidates import (
    candidates_for,
    destinations_for,
    matched_subjects,
    unmatched_destinations,
)
from ._create import CreatedPurchase, create_purchase_from_line
from ._creations import (
    NEW_ENVELOPE,
    NewEnvelope,
    PurchaseCreation,
    PurchaseDestination,
)
from ._offers import (
    BankLine,
    CandidateRow,
    Candidates,
    MatchDays,
    MatchProposal,
    RowKind,
    corrected_purchase_day,
    merchant_label,
)
from ._submission import (
    MatchSubmission,
    ReviewedRow,
    as_reviewed,
    parse_figure,
)
from ._near import NEAR_MISS_BOUND
from ._pairing import DAY_WINDOW
from ._propose import ProposedMatches, propose
from ._accepted_view import AcceptedGroup, AcceptedRow
from ._placement import Placement, PlacementKind
from ._preview import HandTotals, preview_hand_build
from ._rules import (
    StandingRule,
    RuleAnswer,
    RuleSubmission,
    RuleView,
    StatedRules,
    account_merchants,
    state_rules,
)
from ._reads import (
    CreatableLine,
    ReviewBounds,
    ReviewSet,
    awaiting_review_count,
    review_set,
)
from ._section import MerchantSection, MerchantSummary
from ._scope import ReviewScope

__all__ = [
    "NEW_ENVELOPE",
    "AcceptedGroup",
    "AcceptedMatch",
    "AcceptedRow",
    "BankLine",
    "BatchOutcome",
    "CandidateRow",
    "Candidates",
    "CreatableLine",
    "CreatedPurchase",
    "CreationBar",
    "CreationBars",
    "HandTotals",
    "DAY_WINDOW",
    "NEAR_MISS_BOUND",
    "MatchDays",
    "StandingRule",
    "MerchantSection",
    "MerchantSummary",
    "MatchProposal",
    "MatchSubmission",
    "NewEnvelope",
    "ParkedLine",
    "Placement",
    "PlacementKind",
    "PlannedRemoval",
    "PlannedRemovals",
    "RuleAnswer",
    "RuleSubmission",
    "RuleView",
    "ProposedMatches",
    "PurchaseCreation",
    "PurchaseDestination",
    "ReleasedMatch",
    "ReviewBounds",
    "ReviewScope",
    "ReviewSet",
    "ReviewedBatch",
    "ReviewedRow",
    "RowKind",
    "StatedRules",
    "accept_match",
    "account_merchants",
    "apply_reviewed",
    "as_reviewed",
    "awaiting_review_count",
    "candidates_for",
    "corrected_purchase_day",
    "create_purchase_from_line",
    "destinations_for",
    "matched_subjects",
    "parse_figure",
    "merchant_label",
    "preview_hand_build",
    "propose",
    "release_match",
    "removals_by_match",
    "review_set",
    "state_rules",
    "unmatched_destinations",
]
