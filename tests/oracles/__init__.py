"""Frozen behaviour baselines the recurrence redesign is diffed against.

Unlike ``tests/manual/``, whose harnesses are run by hand against a real
database, an oracle in this package runs in the SUITE on every commit: it
regenerates a blob from the code under test and compares it to a committed
snapshot, so a behaviour change cannot land unnoticed.
"""
