"""Independent second opinions the suite grades the application against.

Unlike ``tests/manual/``, whose harnesses are run by hand against a real
database, an oracle in this package runs in the SUITE on every commit.  Two
shapes live here, and both exist so a producer is never its own grader:

* a frozen BASELINE -- it regenerates a blob from the code under test and
  compares it to a committed snapshot, so a behaviour change cannot land
  unnoticed (``recurrence_baseline``, ``pay_calendar_derivation``);
* a RETIRED PRODUCER kept as a second opinion -- an implementation the
  application has replaced, whose agreement with its replacement over the inputs
  both are correct for IS the equivalence claim, measured rather than asserted
  (``loan_monthly_composition``, plan step ``balance:X-au-g-2c-3b-2``).  Deleting
  such an implementation outright leaves the new producer grading itself, which
  is the "green gate measuring nothing" shape this project keeps paying for.
"""
