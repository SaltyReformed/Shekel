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

* :func:`review_set` -- everything the review screen shows for one account:
  what the app proposes, what it could not explain, what is out of reach, and
  what has already been accepted.
* :func:`accept_match` -- the ONE write door.  **It MOVES MONEY**: every member
  row takes the bank's posted day, which settles a still-Projected row and
  corrects a wrongly-dated settled one.
* :func:`release_match` -- the undo, which restores the QUESTION rather than
  the days.
* The value types :class:`MatchProposal`, :class:`MatchSubmission`,
  :class:`CandidateRow`, :class:`BankLine`, :class:`RowKind`,
  :class:`AcceptedMatch`, :class:`AcceptedGroup`, :class:`AcceptedRow` and
  :class:`ReviewSet`.

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
   (finding **N-299**), and a tolerance would silence the instrument that
   found it.
3. **A match may SETTLE a still-Projected row**, because a statement is
   evidence that money moved.  11 rows inside the developer's own statement
   span had never been marked as having happened.

**Every proposal is a question** (**R-FP**): nothing here applies a match the
owner has not accepted, and :mod:`._propose` cannot write at all.
"""

from ._accept import AcceptedMatch, accept_match, release_match
from ._candidates import candidates_for
from ._offers import (
    BankLine,
    CandidateRow,
    Candidates,
    MatchProposal,
    MatchSubmission,
    RowKind,
)
from ._propose import DAY_WINDOW, propose
from ._reads import (
    AcceptedGroup,
    AcceptedRow,
    ReviewBounds,
    ReviewSet,
    review_set,
)

__all__ = [
    "AcceptedGroup",
    "AcceptedMatch",
    "AcceptedRow",
    "BankLine",
    "CandidateRow",
    "Candidates",
    "DAY_WINDOW",
    "MatchProposal",
    "MatchSubmission",
    "ReviewBounds",
    "ReviewSet",
    "RowKind",
    "accept_match",
    "candidates_for",
    "propose",
    "release_match",
    "review_set",
]
