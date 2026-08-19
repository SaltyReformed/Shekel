"""A line's identity is positional, total, and the same for every source.

Plan step **bank_import:X-f6a-1**.  Ruling **R-FP** named ``FITID`` as the
idempotency key; measurement 2026-08-16 refined that to a POSITIONAL key --
``(account, posted_on, amount, sequence within that group)`` -- because only
some sources carry an id of their own, and across two SECU exports twelve days
apart the positional key reproduced the ``FITID`` key exactly over 342 shared
lines.

**The ordinal is the part that is not obvious, and it is the part that carries
money.**  Without it, a second genuinely distinct charge sharing a day and an
amount -- the same coffee twice -- would be recognised as a duplicate of the
first and silently dropped.  That is money the bank took and the app never
recorded, on the very mechanism built to prevent double-recording, so it is
tested directly rather than left to the constraint.
"""

from datetime import date
from decimal import Decimal

from app.services.statement_import import (
    StatementLine,
    assign_sequences,
    line_identity,
)


def _line(day, amount, description="X", running=None):
    """Return one :class:`StatementLine` with the fields identity reads."""
    return StatementLine(
        posted_on=day,
        transaction_on=day,
        amount=Decimal(amount),
        description=description,
        running_balance=None if running is None else Decimal(running),
    )


class TestTheOrdinalMakesTheKeyTotal:
    """Two lines may legitimately share a day and an amount."""

    def test_distinct_lines_sharing_day_and_amount_get_distinct_ordinals(self):
        """The same coffee twice on one day is TWO movements, not one.

        A key of ``(day, amount)`` alone would collapse them, and the second
        charge -- real money the bank took -- would never be recorded.
        """
        lines = [
            _line(date(2026, 3, 2), "-4.75", "COFFEE"),
            _line(date(2026, 3, 2), "-4.75", "COFFEE"),
        ]

        keyed = assign_sequences(lines)

        assert [k.sequence_in_group for k in keyed] == [0, 1]
        assert len({k.identity for k in keyed}) == 2

    def test_lines_differing_in_amount_each_start_at_zero(self):
        """The ordinal counts within a group, not across the file."""
        lines = [
            _line(date(2026, 3, 2), "-4.75"),
            _line(date(2026, 3, 2), "-9.50"),
            _line(date(2026, 3, 2), "-4.75"),
        ]

        keyed = assign_sequences(lines)

        assert [k.sequence_in_group for k in keyed] == [0, 0, 1]

    def test_lines_differing_in_day_each_start_at_zero(self):
        """Same amount on different days is two groups, not one."""
        lines = [
            _line(date(2026, 3, 2), "-4.75"),
            _line(date(2026, 3, 3), "-4.75"),
        ]

        keyed = assign_sequences(lines)

        assert [k.sequence_in_group for k in keyed] == [0, 0]

    def test_a_single_line_is_ordinal_zero(self):
        """The ordinary case pays nothing for the ordinal's existence."""
        keyed = assign_sequences([_line(date(2026, 3, 2), "-4.75")])

        assert keyed[0].sequence_in_group == 0

    def test_an_empty_file_keys_to_nothing(self):
        """Total over the empty input rather than raising on it."""
        assert assign_sequences([]) == []


class TestTheKeyIsStableAndOrderDependent:
    """Identity is positional, so the source's order is the precondition."""

    def test_the_same_lines_in_the_same_order_key_identically(self):
        """Re-importing an unchanged file must reproduce the same identities.

        This is the property idempotency rests on: the door looks a line up by
        this key, so a key that moved would record every line a second time.
        """
        lines = [
            _line(date(2026, 3, 2), "-4.75", "COFFEE"),
            _line(date(2026, 3, 2), "-4.75", "COFFEE"),
            _line(date(2026, 3, 3), "-9.50", "TEA"),
        ]

        first = [k.identity for k in assign_sequences(lines)]
        second = [k.identity for k in assign_sequences(list(lines))]

        assert first == second

    def test_it_preserves_the_input_order(self):
        """The caller's order is returned, so the chain check can rely on it."""
        lines = [
            _line(date(2026, 3, 2), "-1.00"),
            _line(date(2026, 3, 3), "-2.00"),
            _line(date(2026, 3, 4), "-3.00"),
        ]

        keyed = assign_sequences(lines)

        assert [k.line.amount for k in keyed] == [
            Decimal("-1.00"), Decimal("-2.00"), Decimal("-3.00"),
        ]

    def test_the_description_is_NOT_part_of_the_identity(self):
        """A richer description of the SAME line must not re-key it.

        Measured 2026-08-16: SECU's OFX truncates a description to 32
        characters where its CSV carries 96, and the CSV text starts with the
        OFX text on 306 of 306 shared lines.  If description were part of the
        key, importing one format after the other would record every line
        twice -- so identity deliberately reads only day, amount and ordinal.
        """
        short = assign_sequences([
            _line(date(2026, 3, 2), "-4.75", "POINT OF SALE DEBIT L340 DATE 12"),
        ])
        long = assign_sequences([
            _line(date(2026, 3, 2), "-4.75",
                  "POINT OF SALE DEBIT L340 DATE 12-31 Amazon.com (Amazon)"),
        ])

        assert short[0].identity == long[0].identity


class TestTheFullIdentityIsScopedByAccount:
    """One statement's line is not another account's."""

    def test_it_prefixes_the_account(self):
        """The stored key is per account, matching the UNIQUE constraint."""
        keyed = assign_sequences([_line(date(2026, 3, 2), "-4.75")])

        assert line_identity(7, keyed[0]) == (
            7, date(2026, 3, 2), Decimal("-4.75"), 0,
        )

    def test_the_same_line_under_two_accounts_keys_differently(self):
        """Two accounts may each show a $25 debit on one day."""
        keyed = assign_sequences([_line(date(2026, 3, 2), "-25.00")])

        assert line_identity(1, keyed[0]) != line_identity(2, keyed[0])
