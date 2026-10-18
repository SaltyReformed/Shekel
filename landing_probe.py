"""THROWAWAY (X-cx L9d a3, the coordinator's measurement (e), 2026-10-17): never merged.

Reads cards #1-#30 and #999, and every card they link to, through the card-only reader and
the token in PLAN_READ_TOKEN, and prints per card ONLY its number, the sha256 of its whole
read (every field of the BareCard, as sorted JSON) and counts of its shape: no title, body,
label name or login, because Shekel's CI logs are public.  The same script run locally
through quill's App must print the same lines byte for byte.
"""
import dataclasses
import hashlib
import json
import os

from tools.quill._command import with_closure
from tools.quill._github import GitHub
from tools.quill._tracker import CardReader
from tools.quill.setup_tracker import PLAN

NUMBERS = [*range(1, 31), 999]


def lines(token: str) -> list[str]:
    """One line per number asked and per card the closure adds."""
    reader = CardReader(GitHub(token), PLAN)
    held = reader.cards(NUMBERS)
    cards = with_closure(reader, dict(held))
    out = []
    for number in sorted(set(NUMBERS) | set(cards)):
        card = cards.get(number)
        if card is None:
            out.append(f"{number} absent")
            continue
        dump = json.dumps(dataclasses.asdict(card), sort_keys=True)
        digest = hashlib.sha256(dump.encode("utf-8")).hexdigest()
        out.append(f"{number} {digest} kind={card.kind} children={len(card.children)} "
                   f"parent={card.parent} labels={len(card.labels)} "
                   f"blocked_by={len(card.blocked_by)} open={card.is_open} "
                   f"state_reason={card.state_reason} closed_by_tool={card.closed_by_tool} "
                   f"touched_by_hand={card.touched_by_hand} outside={len(card.outside)}")
    return out


if __name__ == "__main__":
    print("\n".join(lines(os.environ["PLAN_READ_TOKEN"])))
