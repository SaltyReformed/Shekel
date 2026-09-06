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

- 2026-09-05: `shekel-dev-app` bind-mounts `/home/josh/projects/Shekel`, which is pinned to a
  release branch, so NO lane can run the dev app or a browser pass
  (`tests/manual/verify_recurrence_form.py`) until PR #263 lands. Owed by `recurrence:R7d-f-1`.
