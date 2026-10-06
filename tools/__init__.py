"""Shekel's repository tooling, one importable package tree.

Each folder is a package and every import names its full path from the
repository root (``from tools.ci import arcs``), so Python, pytest, pylint, CI
and pre-commit all resolve a module the same way and two folders can share
one module without a search-path edit.  Run a tool from the repository root
as a module: ``python -m tools.plan.plan``.
"""
