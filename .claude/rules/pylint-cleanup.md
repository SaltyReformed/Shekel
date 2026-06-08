---
paths:
  - "docs/audits/pylint-cleanup/**/*"
---

# Pylint-cleanup rules (the quality-pass bar)

You are working in the pylint-10 cleanup. Two systems of record: `plan.md`
(the mechanical floor -- removes too-many-args / duplicate-code / too-many-locals)
and `quality-pass.md` (the design ceiling -- rubric, per-file worklist, findings
register). A file is NOT DONE in `plan.md` until it has ALSO passed the
quality-pass rubric review:

- Hand an INDEPENDENT reviewer (a fresh subagent -- you are anchored on the
  refactor just committed) the file + its tests + the cleanup diff
  (`git show <sha>`) + the rubric. Require it to argue BOTH "could this be
  simpler?" and "is this the right abstraction for the next feature?"
  Over-engineering findings are first-class, not an afterthought.
- Triage every finding to ACCEPT / REFINE / REVERT-OVERREACH with a `file:line`
  citation; record the verdict in the register.
- Apply REFINE / REVERT-OVERREACH fixes as their own commit; full suite is the
  gate. ACCEPT rows need no code change -- they are the audit trail that the
  design was actually examined.
- VERIFY every finding against the code before applying. The sweep's recurring
  lesson: reviewers are usually right on WHAT to fix but often wrong on HOW
  (severity, type precision, a "fix" that recreates another smell). An
  ACCEPT-with-rationale is a valid, first-class outcome (see B2-F1, B4-F2).

Two ratified anti-patterns: NEVER raise a design threshold to dodge a smell, and
a private helper's too-many-args/locals smell is a signal to DECOMPOSE, not to
wrap in a count-dodging param bag (bundle only for a genuine cohesive named
concept). See [[feedback_tm_args_param_object]].

Full rubric (sections A-G; A = right-abstraction, G = test-quality), worklist,
and register: `quality-pass.md`. "If you can't cite it, you can't claim it."
