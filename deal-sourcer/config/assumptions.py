# config/assumptions.py
# ALL hardcoded underwriting assumptions live here.
# No src/ file may define a numeric constant — import from here.

# ---------------------------------------------------------------------------
# BUY BOX
# ---------------------------------------------------------------------------
CONVENTIONAL = {
    "min_units": 75,
    "max_units": 300,
    "min_vintage": 1985,
    "asset_type": "conventional",
}

BTR = {
    "min_units": 50,
    "max_units": 200,
    "min_vintage": 2015,
    "asset_type": "btr",
}

# ---------------------------------------------------------------------------
# VACANCY & CREDIT LOSS
# ---------------------------------------------------------------------------
VACANCY_RATE = 0.06               # always 6%
BAD_DEBT_YEAR1 = 0.01
BAD_DEBT_STABILIZED = 0.005
CONCESSIONS_YEAR1 = 0.01
CONCESSIONS_STABILIZED = 0.00
STABILIZED_OCCUPANCY = 0.94

# ---------------------------------------------------------------------------
# EXPENSE ASSUMPTIONS (annual per door)
# ---------------------------------------------------------------------------
ADMIN_PER_DOOR = 325
MARKETING_PER_DOOR = 350
PAYROLL_PER_DOOR = 1_650
RM_PER_DOOR = 600
CAPEX_PER_DOOR = 250
MGMT_FEE_PCT = 0.025              # % of EGI

# Defaults when not provided in deal data
OTHER_INCOME_PER_UNIT_MONTH = 75  # $/unit/month
INSURANCE_PER_UNIT_YEAR = 500     # $/unit/year
RE_TAX_PCT_OF_PRICE = 0.015       # 1.5% of asking price / units as fallback
UTILITIES_PER_UNIT_YEAR = 1_200   # $/unit/year

# ---------------------------------------------------------------------------
# EXPENSE RATIO FLAGS
# ---------------------------------------------------------------------------
EXPENSE_RATIO_GREEN = 0.60        # at or below = PASS
EXPENSE_RATIO_YELLOW = 0.65       # 60–65% = POSSIBLE
# above 65% = FAIL on expense ratio

# ---------------------------------------------------------------------------
# GROWTH ASSUMPTIONS
# ---------------------------------------------------------------------------
RENT_GROWTH = 0.03
OTHER_INCOME_GROWTH = 0.03
EXPENSE_GROWTH = 0.03             # Year 2 onward

# ---------------------------------------------------------------------------
# DEBT ASSUMPTIONS
# ---------------------------------------------------------------------------
LTV = 0.65
TREASURY_SPREAD_BPS = 175
FALLBACK_RATE = 0.0475
LOAN_TERM_YEARS = 5
AMORT_YEARS = 30
IO_PERIODS_MONTHS = 36

# ---------------------------------------------------------------------------
# CAP RATES
# ---------------------------------------------------------------------------
CAP_RATE_BTR_YEAR1 = 0.050
CAP_RATE_CONV_YEAR1 = 0.055
EXIT_CAP_TX_MAJOR = 0.0525
EXIT_CAP_OTHER = 0.055
TX_MAJOR_MARKETS = [
    "dallas", "dfw", "fort worth", "houston",
    "san antonio", "austin",
]

# ---------------------------------------------------------------------------
# EXIT / DISPOSITION
# ---------------------------------------------------------------------------
DISPOSITION_COST_PCT = 0.01

# ---------------------------------------------------------------------------
# HOLD PERIOD
# ---------------------------------------------------------------------------
HOLD_YEARS = 3

# ---------------------------------------------------------------------------
# RETURN THRESHOLDS
# ---------------------------------------------------------------------------
DEAL_IRR_MIN = 0.17
LP_IRR_MIN = 0.16
DSCR_MIN = 1.25
DEBT_YIELD_MIN_YR3 = 0.075
LTV_MAX = 0.65

# ---------------------------------------------------------------------------
# EQUITY STRUCTURE (screener)
# ---------------------------------------------------------------------------
PREFERRED_RETURN = 0.08
LP_SPLIT = 0.80
GP_SPLIT = 0.20
