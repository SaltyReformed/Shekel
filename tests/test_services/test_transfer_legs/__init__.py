"""A projected transfer's legs are DERIVED from the parent, and the readers say so.

Plan step **balance:X-bi-6a**, ruling **R-BAL13** (developer ruling
**R-BAL38**).  Until this step every balance reader loaded a still-projected
transfer's two SHADOW rows and priced each through amount rule 5 (*a shadow is
worth its parent*).  Now the readers derive one
:class:`~app.services.transfer_legs.TransferLeg` per side from the parent
row in ``budget.transfers`` and no reader folding a projection reads a
projected shadow at all.

**What these cases grade, and why the byte-identical ones are not enough.**
Wherever Transfer Invariants 1-3 hold, the fold's answer is the same whether it
reads the shadow or the parent -- 0 of 350 shadow rows drifted on the
2026-09-15 production snapshot -- so a before/after diff of every figure the seam
answers proves the re-point moved nothing and CANNOT show which relation is
read.  The firing controls below write past Transfer Invariant 4 -- a shadow
soft-deleted alone, a shadow's due date moved alone -- and assert the fold
follows the PARENT.  Before this step each of those writes moved the fold;
after it, each is invisible to the plan, which is ruling **R-JA**'s direction
(the parent is the value's one home) and the property the step exists to buy.
"""
