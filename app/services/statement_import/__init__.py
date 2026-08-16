"""
Shekel Budget App -- Statement Import: the bank says what happened.

**The app's own records of when money moved are guesses, and this package is
where it stops guessing.**  Measured on the developer's own bank exports: of 110
movements matched to bank lines on exact amount, only 33 carried the day the app
had recorded, and 88 of 135 settled Checking rows share a click-minute with
another row -- a settle date is a bookkeeping-session artifact, not an
observation (finding **N-173**).

Plan step ``bank_import:X-f6a``, ruling **R-FP**.  This leaf RECORDS a
statement; it does not yet decide which of the app's own rows a line explains.
That separation is deliberate and it is what makes this commit gradeable by a
property rather than by inspection: no balance moves, because no balance input
is written.

The public surface, and what each piece is for:

* :class:`StatementLine` -- the ONE normalized line shape every adapter
  produces, so everything downstream of a parser is source-independent.
* :func:`supported_sources` / :func:`parse_statement` -- the adapter registry.
* :func:`assign_sequences` / :func:`line_identity` -- the identity rule, which
  is positional and therefore serves a source carrying no id of its own.
* :func:`verify_running_balance` -- the self-check a source with a running
  balance affords, and the reason the CSV was chosen over the OFX.
* :func:`record_statement` -- the one write door, returning
  :class:`ImportOutcome`.

**What the recorded lines are FOR is the steps after this one**, named here
because the schema was designed for all four rather than for the first: the
match and its review (``X-f6a-2``), a bank line that becomes a purchase
(``X-f6a-3``), the walked-statement SILENCE that makes an unshown line NOT
CLEARED rather than unknown (``balance:X-f3a-2``), and the re-openable recorded
difference at the cash cutover (``balance:X-f3c``).
"""

from ._adapters import parse_statement, supported_sources
from ._integrity import (
    carries_running_balance,
    closing_balance,
    opening_balance,
    verify_running_balance,
)
from ._line import KeyedLine, StatementLine, assign_sequences, line_identity
from ._reads import (
    RecordedSpan,
    SourceOption,
    available_sources,
    import_history,
    recent_lines,
    recorded_span,
)
from ._record import ImportOutcome, record_statement

__all__ = [
    "ImportOutcome",
    "KeyedLine",
    "RecordedSpan",
    "SourceOption",
    "StatementLine",
    "assign_sequences",
    "available_sources",
    "carries_running_balance",
    "closing_balance",
    "import_history",
    "line_identity",
    "opening_balance",
    "parse_statement",
    "recent_lines",
    "record_statement",
    "recorded_span",
    "supported_sources",
    "verify_running_balance",
]
