"""
Shekel Budget App -- Reference Table Seed Data

Single source of truth for account type seed data.  Used by both
the application factory (dev/test convenience seeding) and the
standalone seed script (production).

Each entry: (name, category_name, has_parameters, has_amortization,
             icon_class, max_term_months)
"""

ACCT_TYPE_SEEDS = [
    ("Checking",        "Asset",      False, False, "bi-wallet2",        None),
    ("Savings",         "Asset",      False, False, "bi-piggy-bank",     None),
    ("HYSA",            "Asset",      True,  False, "bi-piggy-bank",     None),
    ("Money Market",    "Asset",      False, False, "bi-cash-stack",     None),
    ("CD",              "Asset",      False, False, "bi-safe",           None),
    ("HSA",             "Asset",      False, False, "bi-heart-pulse",    None),
    ("Credit Card",     "Liability",  False, False, "bi-credit-card",    None),
    ("Mortgage",        "Liability",  True,  True,  "bi-house",          600),
    ("Auto Loan",       "Liability",  True,  True,  "bi-car-front",      120),
    ("Student Loan",    "Liability",  True,  True,  "bi-mortarboard",    300),
    ("Personal Loan",   "Liability",  True,  True,  "bi-cash-coin",      120),
    ("HELOC",           "Liability",  True,  True,  "bi-bank",           360),
    ("401(k)",          "Retirement", True,  False, "bi-graph-up-arrow", None),
    ("Roth 401(k)",     "Retirement", True,  False, "bi-graph-up-arrow", None),
    ("Traditional IRA", "Retirement", True,  False, "bi-graph-up-arrow", None),
    ("Roth IRA",        "Retirement", True,  False, "bi-graph-up-arrow", None),
    ("Brokerage",       "Investment", True,  False, "bi-bar-chart-line", None),
    ("529 Plan",        "Investment", False, False, "bi-mortarboard",    None),
]
