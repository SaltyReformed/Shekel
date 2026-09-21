"""Build Bridge-shaped account answers for the feed reader's tests.

**Shaped from the developer's real fetches rather than invented** (the
2026-09-18 and 2026-09-20 dumps of plan step ``bank_import:X-f6b-2``),
because a fixture that does not reproduce the answer's actual quirks cannot
grade a reader written for them: the v2 account object with ``balance`` as
a decimal STRING and ``balance-date`` an epoch; ``transactions`` NEWEST
FIRST, each ``posted`` an epoch at 12:00:00 UTC (08:00 America/New_York)
with ``transacted_at`` equal to it, ``amount`` a decimal string, ``mcc``
``None`` and ``memo`` empty; a cleaned description ``<merchant>
<Category/Sub>`` beside a raw one in the bank's own text; ids ``TRN-`` and
``ACT-`` plus a uuid, 40 characters.

The descriptions and ids here are synthetic; the DAY and AMOUNT structure of
the measured night (:func:`measured_night`) is the real one, line for line,
so the claim the reader derives from it is the figure the real fetch gives.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

#: Bridge's id for the developer's checking account, as the feed tests use it.
CHECKING = "ACT-125e0df6-8f88-4a46-888a-37e0342ed307"

#: A second account, for the one-with-no-transactions case.
SHARE = "ACT-d361df65-8f21-4be9-812e-8f270dcc7f5b"

#: The instant of every measured ``posted``: 12:00:00 UTC.
NOON_UTC = 12

_IDS = uuid.UUID("6f1b1d6e-9c3c-4d5f-8a1e-2b7c0d9e4f00")


def epoch(day: date, hour_utc: int = NOON_UTC) -> int:
    """Return the unix epoch of *day* at *hour_utc* UTC."""
    return int(
        datetime(day.year, day.month, day.day, hour_utc, tzinfo=timezone.utc)
        .timestamp(),
    )


def line(
    day: date, amount: str, description: str, *, hour_utc: int = NOON_UTC,
) -> dict:
    """Return one transaction object in Bridge's measured shape.

    The id is minted from the line's own facts, so a fixture stating the
    same line twice states the same id -- and two lines that differ only in
    description get two ids, as Bridge's do.
    """
    posted = epoch(day, hour_utc)
    return {
        "id": f"TRN-{uuid.uuid5(_IDS, f'{day}|{amount}|{description}')}",
        "posted": posted,
        "amount": amount,
        "description": description,
        "payee": description[:24],
        "memo": "",
        "transacted_at": posted,
        "mcc": None,
    }


def clean(day: date, amount: str, merchant: str, category: str) -> dict:
    """Return a line MX has cleaned: ``<merchant> <Category/Sub>``."""
    return line(day, amount, f"{merchant} {category}")


def raw(day: date, amount: str, text: str) -> dict:
    """Return a line still in the bank's own text (no category to split on)."""
    return line(day, amount, text)


def account(
    transactions: "list[dict]",
    *,
    balance: str = "2073.40",
    currency: str = "USD",
    external_id: str = CHECKING,
    name: str = "Checking (3820)",
) -> dict:
    """Return one account object, its transactions NEWEST FIRST as Bridge lists them."""
    return {
        "id": external_id,
        "name": name,
        "currency": currency,
        "balance": balance,
        "available-balance": balance,
        "balance-date": epoch(date(2026, 9, 20), 22),
        "holdings": [],
        "transactions": sorted(transactions, key=_posted_or_zero, reverse=True),
    }


def _posted_or_zero(item: object) -> float:
    """Return *item*'s ``posted`` for the newest-first sort, or 0 for a
    deliberately broken transaction (no ``posted``, or not an object) so the
    refusal tests can hand the reader what Bridge never sends."""
    if isinstance(item, dict) and isinstance(item.get("posted"), (int, float)):
        return item["posted"]
    return 0


def measured_night() -> "list[dict]":
    """The developer's 2026-09-20 fetch, the present window, by structure.

    Every DAY and AMOUNT is the real one (the dump of 2026-09-20 re-read
    2026-09-21 by the second adversarial pass; the merchants are renamed):
    no line on 09-13; eight on 09-14 and four on 09-15, clean; on 09-16 one
    raw line beside three clean ones; one clean line on 09-17; on 09-18
    four raw lines, one of them ``$0.00``; no line on 09-19; ``balance``
    2073.40.  Under ruling **R-BI34** the held lines are the eight on the
    two raw days (seven non-zero); under **R-BI35** the FIRST run
    ``09-13..09-15`` states the claim, Bridge's balance minus every line
    after 09-15 -- those eight and the 09-17 line, net ``265.18`` -- so it
    claims ``2073.40 - 265.18 = 1808.22`` as of 09-15, which is what the
    reader answers over the real account (the second pass ran it).
    """
    return [
        clean(date(2026, 9, 14), "-5.55", "Dollar Store", "Shopping/Retail"),
        clean(date(2026, 9, 14), "-62.16", "Club Fuel", "Transportation/Fuel"),
        clean(date(2026, 9, 14), "-105.80", "Big Box", "Shopping/Retail"),
        clean(date(2026, 9, 14), "-112.65", "Warehouse Club", "Shopping/Wholesale"),
        clean(date(2026, 9, 14), "-133.37", "Big Box", "Shopping/Retail"),
        clean(date(2026, 9, 14), "-38.88", "Warehouse Club", "Shopping/Wholesale"),
        clean(date(2026, 9, 14), "-156.03", "Warehouse Online", "Shopping/Wholesale"),
        clean(date(2026, 9, 14), "-364.60", "Power Company", "Utilities/Gas and Electric"),
        clean(date(2026, 9, 15), "-5.60", "Bookshop", "Shopping/Online"),
        clean(date(2026, 9, 15), "-28.77", "Bookshop", "Shopping/Online"),
        clean(date(2026, 9, 15), "-33.57", "Bookshop", "Shopping/Online"),
        clean(date(2026, 9, 15), "-41.84", "Bookshop", "Shopping/Online"),
        raw(date(2026, 9, 16), "-9.99", "APPLE.COM/BILL           CUPERTINO    CA"),
        clean(date(2026, 9, 16), "-43.40", "Cracker Barrel", "Food & Drink/Dining Out"),
        clean(date(2026, 9, 16), "-4.56", "Food Lion", "Food & Drink/Groceries"),
        clean(date(2026, 9, 16), "-18.08", "Paupers Books", "Shopping/Publications"),
        clean(date(2026, 9, 17), "-21.34", "Apple", "Shopping/Electronics"),
        raw(date(2026, 9, 18), "386.05", "DEPOSIT/BRANCH 000/S*0000N"),
        raw(date(2026, 9, 18), "-8.50", "KOBO (US) INC            WILMINGTON   DE"),
        raw(date(2026, 9, 18), "0.00", "VISA PROVISIONING SERVI"),
        raw(date(2026, 9, 18), "-15.00", "LINK.COM* SIMPLEFIN BR   +*******0000 CA"),
    ]
