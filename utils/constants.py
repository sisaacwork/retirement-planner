# ─── Canadian registered account limits ───────────────────────────────────────

# Annual TFSA contribution limits by year (CRA)
TFSA_ANNUAL_LIMITS = {
    2009: 5000,
    2010: 5000,
    2011: 5000,
    2012: 5000,
    2013: 5500,
    2014: 5500,
    2015: 10000,
    2016: 5500,
    2017: 5500,
    2018: 5500,
    2019: 6000,
    2020: 6000,
    2021: 6000,
    2022: 6000,
    2023: 6500,
    2024: 7000,
    2025: 7000,
    2026: 7000,  # CRA announced
}

# Annual FHSA contribution limit (launched April 1, 2023)
FHSA_ANNUAL_LIMIT = 8000
FHSA_LIFETIME_LIMIT = 40000
FHSA_LAUNCH_YEAR = 2023

# Account types available in the app
ACCOUNT_TYPES = ["TFSA", "FHSA", "RRSP", "NRSP"]

# People tracked
PEOPLE = ["Isaac", "Katherine"]

# Google Sheets tab names
SHEET_CONTRIBUTIONS  = "contributions"
SHEET_RETURNS        = "returns"
SHEET_SNAPSHOTS      = "balance_snapshots"
SHEET_WITHDRAWALS    = "withdrawals"
SHEET_SETTINGS       = "settings"
SHEET_US_PAYSLIPS    = "us_payslips"       # Katherine's USD paycheques
SHEET_US_INSTALMENTS = "us_instalments"   # CRA quarterly instalment payments

# Column headers for each sheet
CONTRIBUTIONS_COLS  = ["id", "date", "amount", "account", "person", "notes"]
RETURNS_COLS        = ["id", "date", "amount", "account", "person", "notes"]
SNAPSHOTS_COLS      = ["id", "date", "account", "person", "balance", "source", "notes"]
WITHDRAWALS_COLS    = ["id", "date", "amount", "account", "person", "notes"]
SETTINGS_COLS       = ["key", "value"]
US_PAYSLIPS_COLS    = ["id", "date", "gross_usd", "il_tax_usd", "usd_cad_rate", "notes"]
US_INSTALMENTS_COLS = ["id", "date", "amount_cad", "quarter", "notes"]

# Default settings written on first run
DEFAULT_SETTINGS = {
    "isaac_birth_year":                    "1995",
    "katherine_birth_year":                "1995",
    "milestone_1":                         "250000",
    "milestone_2":                         "500000",
    "milestone_3":                         "1000000",
    "rrsp_room_isaac":                     "0",
    "rrsp_room_katherine":                 "0",
    "tfsa_prior_contributions_isaac":      "0",
    "tfsa_prior_contributions_katherine":  "0",
    "tfsa_prior_withdrawals_isaac":        "0",
    "tfsa_prior_withdrawals_katherine":    "0",
    "tfsa_eligible_year_isaac":            "2025",
    "tfsa_eligible_year_katherine":        "2026",
    "fhsa_prior_contributions_isaac":      "0",
    "fhsa_prior_contributions_katherine":  "0",
    "fhsa_open_year_isaac":                "2025",
    "fhsa_open_year_katherine":            "2026",
    "monthly_contribution_target":         "1000",
}
