"""
src/screener.py
Applies PASS / POSSIBLE / FAIL logic to a completed underwriting results dict.

All thresholds imported from config/assumptions.py -- none defined here.

PASS (all must be true):
  Deal IRR >= 19%, LP IRR >= 19% (both with 1bp epsilon)
  DSCR >= 1.25x all 5 years
  Debt Yield Year 3 >= 7.5%
  Going-in cap >= 5.0% BTR / 5.25% conventional
  Expense Ratio Year 1 <= 60%
  Units and vintage within buy box

POSSIBLE (no hard fails, at least one soft trigger):
  Expense ratio 60-65%
  Going-in cap within 25bps of threshold
  Deal IRR 17-19%

FAIL: has a viable price but misses hard metrics
EXCLUDED: set upstream (buy box, no rent, no viable price)
"""

import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.assumptions import (
    DEAL_IRR_MIN, LP_IRR_MIN, DSCR_MIN,
    DEBT_YIELD_MIN_YR3, LTV_MAX,
    EXPENSE_RATIO_GREEN, EXPENSE_RATIO_YELLOW,
    CAP_RATE_BTR_YEAR1, CAP_RATE_CONV_YEAR1,
    CONVENTIONAL, BTR, IRR_EPSILON,
    HOLD_YEARS,
)

logger = logging.getLogger(__name__)

# Tolerance bands for POSSIBLE determination
_IRR_POSSIBLE_FLOOR = 0.17          # Deal IRR 17-19% -> POSSIBLE
_CAP_RATE_TOLERANCE = 0.0025        # within 25bps of going-in cap threshold -> POSSIBLE


def screen_deal(uw: dict) -> dict:
    """
    Evaluate a fully underwritten deal against PASS / POSSIBLE / FAIL criteria.

    Args:
        uw: Results dictionary returned by underwrite.underwrite_deal()

    Returns:
        dict with keys:
            status            -- "PASS", "POSSIBLE", or "FAIL"
            reasons           -- list of human-readable strings describing failures/flags
            flags             -- dict of individual metric pass/fail booleans
            possible_triggers -- list of soft-trigger descriptions
    """
    asset_type = str(uw.get("asset_type", "conventional")).lower()
    units = int(uw.get("units", 0))
    vintage = uw.get("vintage")
    vintage = int(vintage) if vintage else 0

    hy = int(uw.get("hold_years_used", HOLD_YEARS))

    box = BTR if asset_type == "btr" else CONVENTIONAL
    going_in_cap_threshold = (
        CAP_RATE_BTR_YEAR1 if asset_type == "btr" else CAP_RATE_CONV_YEAR1
    )

    # --- Pull metrics ---
    deal_irr         = uw.get("deal_irr", 0.0) or 0.0
    lp_irr           = uw.get("lp_irr", 0.0) or 0.0
    debt_yield_yr3   = uw.get("debt_yield_yr3", 0.0) or 0.0
    going_in_cap     = uw.get("going_in_cap", 0.0) or 0.0
    expense_ratio_yr1 = uw.get("expense_ratio_yr1", 0.0) or 0.0

    dscr = {}
    for yr in range(1, hy + 1):
        dscr[yr] = uw.get(f"dscr_yr{yr}", 0.0) or 0.0

    # ------------------------------------------------------------------
    # Hard PASS flags
    # ------------------------------------------------------------------
    flags = {}

    flags["deal_irr_pass"] = deal_irr >= (DEAL_IRR_MIN - IRR_EPSILON)
    flags["lp_irr_pass"]   = lp_irr   >= (LP_IRR_MIN - IRR_EPSILON)
    flags["debt_yield_yr3_pass"] = debt_yield_yr3 >= DEBT_YIELD_MIN_YR3
    flags["going_in_cap_pass"]   = going_in_cap   >= going_in_cap_threshold
    flags["expense_ratio_green"]  = expense_ratio_yr1 <= EXPENSE_RATIO_GREEN
    flags["expense_ratio_yellow"] = expense_ratio_yr1 <= EXPENSE_RATIO_YELLOW
    flags["units_in_box"]   = box["min_units"] <= units <= box["max_units"]
    flags["vintage_in_box"] = vintage >= box["min_vintage"]

    for yr in range(1, hy + 1):
        flags[f"dscr_yr{yr}_pass"] = dscr[yr] >= DSCR_MIN

    # ------------------------------------------------------------------
    # Failure reason descriptions
    # ------------------------------------------------------------------
    reasons = []

    if not flags["deal_irr_pass"]:
        reasons.append(f"Deal IRR {deal_irr:.1%} < minimum {DEAL_IRR_MIN:.0%}")
    if not flags["lp_irr_pass"]:
        reasons.append(f"LP IRR {lp_irr:.1%} < minimum {LP_IRR_MIN:.0%}")
    for yr in range(1, hy + 1):
        if not flags[f"dscr_yr{yr}_pass"]:
            reasons.append(f"DSCR Yr{yr} {dscr[yr]:.2f}x < {DSCR_MIN:.2f}x")
    if not flags["debt_yield_yr3_pass"]:
        reasons.append(f"Debt Yield Yr3 {debt_yield_yr3:.2%} < {DEBT_YIELD_MIN_YR3:.1%}")
    if not flags["going_in_cap_pass"]:
        reasons.append(
            f"Going-in cap {going_in_cap:.2%} < threshold {going_in_cap_threshold:.2%} "
            f"for {asset_type}"
        )
    if not flags["expense_ratio_green"]:
        if flags["expense_ratio_yellow"]:
            reasons.append(
                f"Expense ratio {expense_ratio_yr1:.1%} in yellow zone "
                f"({EXPENSE_RATIO_GREEN:.0%}-{EXPENSE_RATIO_YELLOW:.0%})"
            )
        else:
            reasons.append(
                f"Expense ratio {expense_ratio_yr1:.1%} > {EXPENSE_RATIO_YELLOW:.0%} (FAIL threshold)"
            )
    if not flags["units_in_box"]:
        reasons.append(f"Units {units} outside buy box [{box['min_units']}-{box['max_units']}]")
    if not flags["vintage_in_box"]:
        reasons.append(f"Vintage {vintage} < minimum {box['min_vintage']} for {asset_type}")

    # ------------------------------------------------------------------
    # Soft triggers for POSSIBLE
    # ------------------------------------------------------------------
    possible_triggers = []

    if not flags["expense_ratio_green"] and flags["expense_ratio_yellow"]:
        possible_triggers.append("Expense ratio in yellow zone (60-65%)")

    if not flags["going_in_cap_pass"]:
        gap = going_in_cap_threshold - going_in_cap
        if 0 < gap <= _CAP_RATE_TOLERANCE:
            possible_triggers.append(
                f"Going-in cap within {gap * 10000:.0f}bps of threshold"
            )

    if _IRR_POSSIBLE_FLOOR <= deal_irr < DEAL_IRR_MIN:
        possible_triggers.append(
            f"Deal IRR {deal_irr:.1%} in POSSIBLE range (17-19%)"
        )

    # ------------------------------------------------------------------
    # Hard fail conditions (any one -> FAIL regardless of soft triggers)
    # ------------------------------------------------------------------
    hard_fails = [
        not flags["units_in_box"],
        not flags["vintage_in_box"],
        not flags["expense_ratio_yellow"],   # above 65% = hard fail
        not flags["debt_yield_yr3_pass"],
    ]
    # DSCR hard fail: all hold-year DSCRs must pass
    for yr in range(1, hy + 1):
        hard_fails.append(not flags[f"dscr_yr{yr}_pass"])

    # ------------------------------------------------------------------
    # Status determination
    # ------------------------------------------------------------------
    if any(hard_fails):
        status = "FAIL"

    elif (
        flags["deal_irr_pass"]
        and flags["lp_irr_pass"]
        and flags["going_in_cap_pass"]
        and flags["expense_ratio_green"]
        and flags["units_in_box"]
        and flags["vintage_in_box"]
        and flags["debt_yield_yr3_pass"]
        and all(flags[f"dscr_yr{yr}_pass"] for yr in range(1, hy + 1))
    ):
        status = "PASS"
        reasons = []
        # Flag epsilon-passed IRR deals for visibility
        irr_at_margin = deal_irr < DEAL_IRR_MIN and flags["deal_irr_pass"]
        lp_irr_at_margin = lp_irr < LP_IRR_MIN and flags["lp_irr_pass"]
        if irr_at_margin or lp_irr_at_margin:
            reasons.append("IRR at margin -- priced at max bid")

    elif possible_triggers and flags["lp_irr_pass"] and not (
        not flags["deal_irr_pass"] and deal_irr < _IRR_POSSIBLE_FLOOR
    ):
        # Soft trigger(s) only, LP IRR still passes, deal IRR not below POSSIBLE floor
        status = "POSSIBLE"

    else:
        status = "FAIL"

    logger.info(
        "Screened %s -> %s (%d reasons)",
        uw.get("property_name", "?"), status, len(reasons)
    )
    print(f"  [Screen] {uw.get('property_name', '?')}: {status}"
          + (f" -- {'; '.join(reasons)}" if reasons else ""))

    return {
        "status": status,
        "reasons": reasons,
        "flags": flags,
        "possible_triggers": possible_triggers,
    }
