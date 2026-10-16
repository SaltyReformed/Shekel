"""The per-test recorder's package (ruling ``balance:R-BAL303``, 2026-10-16: a moved test is
proved by RUNNING it before and after the move), which ``tools/split`` calls and the test
audit (step ``balance:X-es``, ruling ``R-BAL302``) is to reuse.  It holds what every run of
the suite wrapper (:data:`tools.ci.arcs.WRAPPER`) shares, whoever starts it: its run to its
end (:func:`tools.testrecord.record.wrapper_run`), and how a test ended from its phases'
reports (:func:`tools.testrecord.record.verdict`).  The signals a run must be stopped on
are turned into exceptions by its caller (:func:`tools.split.split.main` does).
"""
