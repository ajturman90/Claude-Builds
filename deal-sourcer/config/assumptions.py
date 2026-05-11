# config/assumptions.py
# ALL hardcoded underwriting assumptions live here.
# No src/ file may define a numeric constant -- import from here.

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
RM_PER_DOOR = 700
CONTRACT_SERVICES_PER_DOOR = 400
CAPEX_PER_DOOR = 250
MGMT_FEE_PCT = 0.025              # % of EGI

# Defaults when not provided in deal data
INSURANCE_PER_UNIT_YEAR = 550     # $/unit/year
TAX_RATE_PCT = 0.0200             # 2.00% of purchase price
UTILITIES_PER_UNIT_YEAR = 1_200   # $/unit/year

# ---------------------------------------------------------------------------
# REVENUE -- OTHER INCOME (forward UW, grow 3% Yr2+)
# ---------------------------------------------------------------------------
RUBS_PER_DOOR_ANNUAL = 840              # $840/unit/year
OTHER_INCOME_PER_DOOR_MONTHLY = 185     # $185/unit/month
OTHER_INCOME_PER_DOOR_ANNUAL = 185 * 12  # = 2220

# ---------------------------------------------------------------------------
# HISTORICAL SCREENING TOGGLES (Step 2 -- context only, not used in forward UW)
# ---------------------------------------------------------------------------
HIST_ECONOMIC_OCCUPANCY = 0.90          # 90% economic occupancy
HIST_OPEX_TOGGLE = 0.50                 # 50% OPEX ratio
HIST_CAP_RATE_CONV = 0.055              # 5.5% going-in cap for conventional
HIST_CAP_RATE_BTR = 0.050              # 5.0% going-in cap for BTR
HIST_OTHER_INCOME_PER_DOOR_MONTHLY = 150  # $150/unit/month
HIST_RUBS_PER_DOOR_ANNUAL = 840         # $840/unit/year

# ---------------------------------------------------------------------------
# EXPENSE RATIO FLAGS
# ---------------------------------------------------------------------------
EXPENSE_RATIO_GREEN = 0.60        # at or below = PASS
EXPENSE_RATIO_YELLOW = 0.65       # 60-65% = POSSIBLE
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
TREASURY_TERM = "5yr"
TREASURY_SPREAD_BPS = 175
FALLBACK_TREASURY = 0.0475
FALLBACK_LOAN_RATE = 0.0475 + 0.0175    # = 0.065
# Legacy alias kept for any remaining references
FALLBACK_RATE = FALLBACK_TREASURY
LOAN_TERM_YEARS = 5
AMORT_YEARS = 30
IO_PERIODS_MONTHS = 36

# ---------------------------------------------------------------------------
# CAP RATES -- FORWARD UW SCREEN THRESHOLDS
# ---------------------------------------------------------------------------
CAP_RATE_BTR_YEAR1 = 0.050            # BTR going-in cap minimum (PASS screen)
CAP_RATE_CONV_YEAR1 = 0.0525          # Conventional going-in cap minimum (PASS screen)
EXIT_CAP_CONV = 0.0575                # Conventional exit cap -- flat all markets
EXIT_CAP_BTR_TX_MAJOR = 0.0525        # BTR in TX major markets
EXIT_CAP_BTR_OTHER = 0.055            # BTR outside TX major markets
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
HOLD_YEARS = 5

# ---------------------------------------------------------------------------
# RETURN THRESHOLDS
# ---------------------------------------------------------------------------
IRR_EPSILON = 0.0001          # 1bp tolerance for floating-point IRR comparisons
DEAL_IRR_MIN = 0.19
LP_IRR_MIN = 0.19
DSCR_MIN = 1.25
DEBT_YIELD_MIN_YR3 = 0.075
LTV_MAX = 0.65

# ---------------------------------------------------------------------------
# EQUITY STRUCTURE
# ---------------------------------------------------------------------------
LP_EQUITY_PCT = 0.90          # LP contributes 90% of total equity
GP_EQUITY_PCT = 0.10          # GP contributes 10% of total equity
PREFERRED_RETURN = 0.08       # 8% preferred return accrues on LP equity
LP_SPLIT = 0.80               # 80% of distributions above pref go to LP
GP_SPLIT = 0.20               # 20% of distributions above pref go to GP

# ---------------------------------------------------------------------------
# MAX BID SOLVER BOUNDS
# ---------------------------------------------------------------------------
SOLVER_PRICE_LOW = 500_000
SOLVER_PRICE_HIGH = 500_000_000
