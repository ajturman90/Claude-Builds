"""
src/screener.py
Applies pass/fail/possible logic to a completed underwriting results dict.

All thresholds imported from config/assumptions.py — none defined here.
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
    CONVENTIONAL, BTR,
)

logger = logging.getLogger(__name__)

# Tolerance bands for POSSIBLE determination
_IRR_POSSIBLE_FLOOR = 0.15          # Deal IRR between 15–17% → POSSIBLE
_CAP_RATE_TOLERANCE = 0.0025        # within 25bps of threshold → POSSIBLE


def screen_deal(uw: dict) -> dict:
    """
    Evaluate a fully underwritten deal against PASS / POSSIBLE / FAIL criteria.

    Args:
        uw: Results dictionary returned by underwrite.underwrite_deal()

    Returns:
        dict with keys:
            status      — "PASS", "POSSIBLE", or "FAIL"
            reasons     — list of human-readable strings describing failures/flags
            flags       — dict of individual metric pass/fail booleans
    """
    asset_type = str(uw.get("asset_type", "conventional")).lower()
    units = int(uw.get("units", 0))
    vintage = uw.get("vintage")
    vintage = int(vintage) if vintage else 0

    # Determine buy box for this asset type
    box = BTR if asset_type == "btr" else CONVENTIONAL
    going_in_cap_threshold = (
        CAP_RATE_BTR_YEAR1 if asset_type == "btr" else CAP_RATE_CONV_YEAR1
    )

    # Pull metrics
    deal_irr = uw.get("deal_irr", 0.0)
    lp_irr = uw.get("lp_irr", 0.0)
    dscr_yr1 = uw.get("dscr_yr1", 0.0)
    dscr_yr2 = uw.get("dscr_yr2", 0.0)
    dscr_yr3 = uw.get("dscr_yr3", 0.0)
    debt_yield_yr3 = uw.get("debt_yield_yr3", 0.0)
    going_in_cap = uw.get("going_in_cap", 0.0)
    expense_ratio_yr1 = uw.get("expense_ratio_yr1", 0.0)

    # ------------------------------------------------------------------
    # Hard PASS flags (all must be True for PASS or POSSIBLE)
    # ------------------------------------------------------------------
    flags = {}

    flags["deal_irr_pass"] = deal_irr >= DEAL_IRR_MIN
    flags["lp_irr_pass"] = lp_irr >= LP_IRR_MIN
    flags["dscr_yr1_pass"] = dscr_yr1 >= DSCR_MIN
    flags["dscr_yr2_pass"] = dscr_yr2 >= DSCR_MIN
    flags["dscr_yr3_pass"] = dscr_yr3 >= DSCR_MIN
    flags["debt_yield_yr3_pass"] = debt_yield_yr3 >= DEBT_YIELD_MIN_YR3
    flags["going_in_cap_pass"] = going_in_cap >= going_in_cap_threshold
    flags["expense_ratio_green"] = expense_ratio_yr1 <= EXPENSE_RATIO_GREEN
    flags["expense_ratio_yellow"] = expense_ratio_yr1 <= EXPENSE_RATIO_YELLOW
    flags["units_in_box"] = box["min_units"] <= units <= box["max_units"]
    flags["vintage_in_box"] = vintage >= box["min_vintage"]

    # ------------------------------------------------------------------
    # Identify failure reasons
    # ------------------------------------------------------------------
    reasons = []

    if not flags["deal_irr_pass"]:
        reasons.append(
            f"Deal IRR {deal_irr:.1%} < minimum {DEAL_IRR_MIN:.0%}"
        )
    if not flags["lp_irr_pass"]:
        reasons.append(
            f"LP IRR {lp_irr:.1%} < minimum {LP_IRR_MIN:.0%}"
        )
    if not flags["dscr_yr1_pass"]:
        reasons.append(f"DSCR Yr1 {dscr_yr1:.2f}x < {DSCR_MIN:.2f}x")
    if not flags["dscr_yr2_pass"]:
        reasons.append(f"DSCR Yr2 {dscr_yr2:.2f}x < {DSCR_MIN:.2f}x")
    if not flags["dscr_yr3_pass"]:
        reasons.append(f"DSCR Yr3 {dscr_yr3:.2f}x < {DSCR_MIN:.2f}x")
    if not flags["debt_yield_yr3_pass"]:
        reasons.append(
            f"Debt Yield Yr3 {debt_yield_yr3:.2%} < {DEBT_YIELD_MIN_YR3:.1%}"
        )
    if not flags["going_in_cap_pass"]:
        reasons.append(
            f"Going-in cap {going_in_cap:.2%} < threshold {going_in_cap_threshold:.2%} "
            f"for {asset_type}"
        )
    if not flags["expense_ratio_green"]:
        if flags["expense_ratio_yellow"]:
            reasons.append(
                f"Expense ratio {expense_ratio_yr1:.1%} in yellow zone "
                f"({EXPENSE_RATIO_GREEN:.0%}–{EXPENSE_RATIO_YELLOW:.0%})"
            )
        else:
            reasons.append(
                f"Expense ratio {expense_ratio_yr1:.1%} > {EXPENSE_RATIO_YELLOW:.0%} (FAIL threshold)"
            )
    if not flags["units_in_box"]:
        reasons.append(
            f"Units {units} outside buy box [{box['min_units']}–{box['max_units']}]"
        )
    if not flags["vintage_in_box"]:
        reasons.append(
            f"Vintage {vintage} < minimum {box['min_vintage']} for {asset_type}"
        )

    # ------------------------------------------------------------------
    # Status determination
    # ------------------------------------------------------------------
    # Core hard fails (buy box + lender metrics)
    hard_fails = [
        not flags["units_in_box"],
        not flags["vintage_in_box"],
        not flags["dscr_yr1_pass"],
        not flags["dscr_yr2_pass"],
        not flags["dscr_yr3_pass"],
        not flags["debt_yield_yr3_pass"],
        not flags["expense_ratio_yellow"],   # above 65% = hard fail
    ]

    possible_triggers = []

    # Expense ratio in yellow zone
    if not flags["expense_ratio_green"] and flags["expense_ratio_yellow"]:
        possible_triggers.append("Expense ratio in yellow zone")

    # Going-in cap within 25bps of threshold
    if not flags["going_in_cap_pass"]:
        gap = going_in_cap_threshold - going_in_cap
        if 0 < gap <= _CAP_RATE_TOLERANCE:
            possible_triggers.append(
                f"Going-in cap within {gap*10000:.0f}bps of threshold"
            )

    # Deal IRR in 15–17% range
    if _IRR_POSSIBLE_FLOOR <= deal_irr < DEAL_IRR_MIN:
        possible_triggers.append(
            f"Deal IRR {deal_irr:.1%} in POSSIBLE range (15–17%)"
        )

    if any(hard_fails):
        # Any hard fail → FAIL regardless of soft triggers
        status = "FAIL"
    elif (
        flags["deal_irr_pass"]
        and flags["lp_irr_pass"]
        and flags["dscr_yr1_pass"]
        and flags["dscr_yr2_pass"]
        and flags["dscr_yr3_pass"]
        and flags["debt_yield_yr3_pass"]
        and flags["going_in_cap_pass"]
        and flags["expense_ratio_green"]
        and flags["units_in_box"]
        and flags["vintage_in_box"]
    ):
        status = "PASS"
        reasons = []  # All clear
    elif possible_triggers and not any(
        [not flags["deal_irr_pass"] and deal_irr < _IRR_POSSIBLE_FLOOR,
         not flags["lp_irr_pass"]]
    ):
        # Soft triggers only — POSSIBLE
        status = "POSSIBLE"
    else:
        status = "FAIL"

    logger.info(
        "Screened %s -> %s (%d reasons)",
        uw.get("property_name", "?"), status, len(reasons)
    )
    print(f"  [Screen] {uw.get('property_name', '?')}: {status}"
          + (f" — {'; '.join(reasons)}" if reasons else ""))

    return {
        "status": status,
        "reasons": reasons,
        "flags": flags,
        "possible_triggers": possible_triggers,
    }
