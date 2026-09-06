---
name: design-forks
description: >
  Josh's standing decision brief for presenting a design fork: the full option
  space, worked numeric examples for financial forks, and a firm from-scratch
  recommendation. Invoke before presenting options or an architectural choice.
---

# Presenting a design fork

When presenting options for any design decision:

1. **Map the FULL option space**, never a narrow A/B. Name the options you considered and rejected,
   and why. "Which option or combination of options" is always part of the question.
2. **Grade every option** against DRY, SOLID, normalization, robustness, maintainability,
   future-proofing, and financial correctness -- and name which fences, checkers, or allowlists
   each option makes structurally unnecessary.
3. **Financial forks get worked numeric examples**, one per option, BEFORE the question is asked.
   Walk the same concrete dollars through each option so the difference is visible in the answers.
4. **Lead with the from-scratch design and recommend it firmly.** State what you would build if
   nothing existed; risk and migration cost are sequencing questions, never reasons to compromise
   the design. Correctness outranks time and effort.
5. **Plain language first.** Walk through the fork in plain words before ids and jargon; define
   every identifier on first use. An AskUserQuestion must stand alone, because the text before it
   may never render.
6. **Prior decisions are revisitable** when evidence favors a better way; say so when it does.
