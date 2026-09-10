# Standing constraints

**The SessionStart hook prints every BULLET line below**, so a session that has just cleared its
context meets these before its first action. Only bullets are printed; the rest of this file is
guidance for whoever edits it.

**What belongs here:** an operational constraint a session cannot DERIVE from its own tree, and
would otherwise learn only from a coordinator message. A message is invisible to the next context
clear, and clearing context is ordinary.

**What does not:** anything the tree already implies (the hook derives the release-branch pin
itself), anything with a home in a registry (a finding is `ledger.md`, a decision is `rulings.md`,
what to do next is `steps.md`), and anything permanent -- a permanent rule belongs in `CLAUDE.md` or
a `.claude/rules/` file, which are loaded rather than printed.

**EMPTY IS THE NORMAL STATE.** The coordinator adds a line when a constraint starts and DELETES it
the moment it stops. A file that is usually empty gets read; one that accumulates gets skimmed, and
then it is a message again. Date every line, so a stale one is visible as stale rather than as
current.

- **2026-09-09: RESERVE every new ledger, ruling and step id through the LEAD COORDINATOR session,
  never by grepping `rulings.md` and never from a sibling worktree.** `balance:R-BAL10` was minted
  TWICE this evening for two different rulings -- one session grepped `rulings.md` for used ids (the
  move `CLAUDE.md` forbids) and one read `docs/design/coordination_id_register.md`'s ceiling without
  claiming it -- and
  **the plan gate's unique-key arm reads ONE tree, so it cannot fire until the merge**, by which
  point the loser's id is cited in immutable commit messages. The developer ruled the same evening
  that `shekel-5c` leads the coordinator role in a conflict. DELETE this line once a cross-tree
  duplicate-id check exists.
