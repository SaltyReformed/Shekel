"""
Shekel Budget App -- Reference Table Seed Data

Single source of truth for account type seed data.  Used by both
the application factory (dev/test convenience seeding) and the
standalone seed script (production).

Each entry: (name, category_name, has_parameters, has_amortization,
             has_interest, is_pretax, is_liquid, icon_class,
             max_term_months)
"""

# fmt: off
# pylint: disable=line-too-long
#
# Columnar alignment is intentional for readability -- each row is
# one account type and the columns correspond to the tuple docstring
# above.  Wrapping individual rows harms scannability.

ACCT_TYPE_SEEDS = [
    # name              category      params amort  interest pretax liquid icon               max_term
    ("Checking",        "Asset",      False, False, False, False, True,  "bi-wallet2",        None),
    ("Savings",         "Asset",      False, False, False, False, True,  "bi-piggy-bank",     None),
    ("HYSA",            "Asset",      True,  False, True,  False, True,  "bi-piggy-bank",     None),
    ("Money Market",    "Asset",      False, False, False, False, True,  "bi-cash-stack",     None),
    ("CD",              "Asset",      False, False, False, False, False, "bi-safe",           None),
    ("HSA",             "Asset",      True,  False, True,  False, False, "bi-heart-pulse",    None),
    ("Credit Card",     "Liability",  False, False, False, False, False, "bi-credit-card",    None),
    ("Mortgage",        "Liability",  True,  True,  False, False, False, "bi-house",          600),
    ("Auto Loan",       "Liability",  True,  True,  False, False, False, "bi-car-front",      120),
    ("Student Loan",    "Liability",  True,  True,  False, False, False, "bi-mortarboard",    300),
    ("Personal Loan",   "Liability",  True,  True,  False, False, False, "bi-cash-coin",      120),
    ("HELOC",           "Liability",  True,  True,  False, False, False, "bi-bank",           360),
    ("401(k)",          "Retirement", True,  False, False, True,  False, "bi-graph-up-arrow", None),
    ("Roth 401(k)",     "Retirement", True,  False, False, False, False, "bi-graph-up-arrow", None),
    ("Traditional IRA", "Retirement", True,  False, False, True,  False, "bi-graph-up-arrow", None),
    ("Roth IRA",        "Retirement", True,  False, False, False, False, "bi-graph-up-arrow", None),
    ("Brokerage",       "Investment", True,  False, False, False, False, "bi-bar-chart-line", None),
    ("529 Plan",        "Investment", True,  False, False, False, False, "bi-mortarboard",    None),
]
# pylint: enable=line-too-long
# fmt: on
