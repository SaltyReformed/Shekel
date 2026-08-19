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
* :func:`destinations_for` -- the budget lines that door may write into, which
  is the SAME set the screen offers, and :func:`matched_subjects` /
  :func:`unmatched_destinations`, which are the one statement of what an
  accepted match has already claimed.  **What this exports is what something
  outside the package imports**: ``AppliedItem``, ``RefusedItem``,
  ``MatchedSubjects`` and ``unmatched_rows`` were exported for symmetry and had
  no importer at all, which is a surface nobody asked for.
* :func:`release_match` -- the undo, which restores the QUESTION rather than
  the days.
* The value types :class:`MatchProposal`, :class:`MatchSubmission`,
  :class:`CandidateRow`, :class:`BankLine`, :class:`RowKind`,
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

from ._accept import AcceptedMatch, accept_match, release_match
from ._batch import BatchOutcome, ReviewedBatch, apply_reviewed
from ._candidates import (
    candidates_for,
    destinations_for,
    matched_subjects,
    unmatched_destinations,
)
from ._create import CreatedPurchase, create_purchase_from_line
from ._offers import (
    BankLine,
    CandidateRow,
    Candidates,
    MatchDays,
    MatchProposal,
    MatchSubmission,
    NewEnvelope,
    PurchaseCreation,
    PurchaseDestination,
    RowKind,
    corrected_purchase_day,
    merchant_of,
)
from ._propose import DAY_WINDOW, ProposedMatches, propose
from ._reads import (
    AcceptedGroup,
    AcceptedRow,
    CreatableLine,
    ReviewBounds,
    ReviewSet,
    review_set,
)
from ._scope import ReviewScope

__all__ = [
    "AcceptedGroup",
    "AcceptedMatch",
    "AcceptedRow",
    "BankLine",
    "BatchOutcome",
    "CandidateRow",
    "Candidates",
    "CreatableLine",
    "CreatedPurchase",
    "DAY_WINDOW",
    "MatchDays",
    "MatchProposal",
    "MatchSubmission",
    "NewEnvelope",
    "ProposedMatches",
    "PurchaseCreation",
    "PurchaseDestination",
    "ReviewBounds",
    "ReviewScope",
    "ReviewSet",
    "ReviewedBatch",
    "RowKind",
    "accept_match",
    "apply_reviewed",
    "candidates_for",
    "corrected_purchase_day",
    "create_purchase_from_line",
    "destinations_for",
    "matched_subjects",
    "merchant_of",
    "propose",
    "release_match",
    "review_set",
    "unmatched_destinations",
]
