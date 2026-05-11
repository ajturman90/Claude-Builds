"""
src/underwrite.py
Core auto-underwriting engine for Portico deal sourcing platform.

All numeric assumptions are imported from config/assumptions.py.
No constants are defined here.

Architecture:
  Step 1 -- Live 5yr Treasury rate fetch (cached per process)
  Step 2 -- Historical screening NOI (current-owner context, not used in UW)
  Step 3 -- Forward UW NOI build (locked assumptions, 5-year horizon)
  Step 4 -- Debt structure (65% LTV, 3yr IO, then amortizing to 30yr)
  Step 5 -- Exit and returns (Year 5 exit, LP/GP waterfall)
  Step 6 -- Max bid solver (brentq targeting 19% Deal IRR)
"""

import sys
import os
import math
import logging
from datetime import datetime
from typing import Optional

import xml.etree.ElementTree as ET

import numpy as np
import numpy_financial as npf
import requests
from scipy.optimize import brentq

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.assumptions import (
    VACANCY_RATE,
    BAD_DEBT_YEAR1, BAD_DEBT_STABILIZED,
    CONCESSIONS_YEAR1, CONCESSIONS_STABILIZED,
    RENT_GROWTH, OTHER_INCOME_GROWTH, EXPENSE_GROWTH,
    ADMIN_PER_DOOR, MARKETING_PER_DOOR, PAYROLL_PER_DOOR,
    RM_PER_DOOR, CONTRACT_SERVICES_PER_DOOR, CAPEX_PER_DOOR, MGMT_FEE_PCT,
    INSURANCE_PER_UNIT_YEAR, TAX_RATE_PCT, UTILITIES_PER_UNIT_YEAR,
    RUBS_PER_DOOR_ANNUAL, OTHER_INCOME_PER_DOOR_ANNUAL,
    HIST_ECONOMIC_OCCUPANCY, HIST_OPEX_TOGGLE,
    HIST_CAP_RATE_CONV, HIST_CAP_RATE_BTR,
    HIST_OTHER_INCOME_PER_DOOR_MONTHLY, HIST_RUBS_PER_DOOR_ANNUAL,
    LTV, TREASURY_SPREAD_BPS, FALLBACK_TREASURY, FALLBACK_LOAN_RATE,
    AMORT_YEARS, IO_PERIODS_MONTHS,
    EXIT_CAP_CONV, EXIT_CAP_BTR_TX_MAJOR, EXIT_CAP_BTR_OTHER, TX_MAJOR_MARKETS,
    DISPOSITION_COST_PCT, HOLD_YEARS,
    DEAL_IRR_MIN, DEBT_YIELD_MIN_YR3,
    PREFERRED_RETURN, LP_SPLIT, GP_SPLIT, LP_EQUITY_PCT, GP_EQUITY_PCT,
    CAP_RATE_BTR_YEAR1, CAP_RATE_CONV_YEAR1,
    SOLVER_PRICE_LOW, SOLVER_PRICE_HIGH,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step 1 -- Treasury rate fetcher (5yr, cached per process)
# ---------------------------------------------------------------------------

_treasury_cache: tuple[float, str] | None = None


def fetch_treasury_5y() -> tuple[float, str]:
    """
    Fetch the current 5-year US Treasury yield from Treasury.gov OData feed.

    URL pattern:
      https://data.treasury.gov/feed.svc/DailyTreasuryYieldCurveRateData
        ?$filter=month(NEW_DATE) eq {month} and year(NEW_DATE) eq {year}
        &$select=NEW_DATE,BC_5YEAR

    Returns:
        (rate_as_decimal, source_description)
        Falls back to FALLBACK_TREASURY if API fails.
        Result is cached module-level so bulk runs call API only once.
    """
    global _treasury_cache
    if _treasury_cache is not None:
        return _treasury_cache

    now = datetime.utcnow()
    url = (
        "https://data.treasury.gov/feed.svc/DailyTreasuryYieldCurveRateData"
        f"?$filter=month(NEW_DATE)%20eq%20{now.month}%20and%20year(NEW_DATE)%20eq%20{now.year}"
        "&$select=NEW_DATE,BC_5YEAR"
    )

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        entries = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "BC_5YEAR" and elem.text and elem.text.strip():
                try:
                    entries.append(float(elem.text.strip()))
                except ValueError:
                    pass

        if not entries:
            raise ValueError("No BC_5YEAR data found in Treasury XML")

        rate_pct = entries[-1]
        rate = rate_pct / 100.0
        source = f"Treasury API 5yr {now.year}-{now.month:02d}"
        loan_rate = rate + (TREASURY_SPREAD_BPS / 10_000)
        logger.info("5yr Treasury: %.4f -> loan rate %.4f (%s)", rate, loan_rate, source)
        print(f"  [Treasury] 5yr rate from API: {rate:.4%} -> loan rate {loan_rate:.4%} ({source})")
        _treasury_cache = (rate, source)
        return _treasury_cache

    except Exception as exc:
        logger.warning("Treasury API failed (%s) -- using FALLBACK %.4f", exc, FALLBACK_TREASURY)
        loan_rate = FALLBACK_LOAN_RATE
        source = f"Fallback 5yr ({FALLBACK_TREASURY:.4%} + {TREASURY_SPREAD_BPS}bps = {loan_rate:.4%})"
        print(f"  [Treasury] API unavailable ({exc})")
        print(f"  [Treasury] Using fallback: {FALLBACK_TREASURY:.4%} + {TREASURY_SPREAD_BPS}bps = {loan_rate:.4%}")
        _treasury_cache = (FALLBACK_TREASURY, source)
        return _treasury_cache


# ---------------------------------------------------------------------------
# Loan mechanics helpers
# ---------------------------------------------------------------------------

def _monthly_io_payment(loan: float, annual_rate: float) -> float:
    """Interest-only monthly payment."""
    return loan * annual_rate / 12.0


def _monthly_amort_payment(loan: float, annual_rate: float, amort_months: int) -> float:
    """Standard level-payment amortizing monthly payment (on full loan at rate over amort_months)."""
    r = annual_rate / 12.0
    if r == 0:
        return loan / amort_months
    return loan * r * (1 + r) ** amort_months / ((1 + r) ** amort_months - 1)


def _loan_balance_at_month(loan: float, annual_rate: float,
                            amort_months: int, io_months: int,
                            elapsed_months: int) -> float:
    """
    Remaining principal after elapsed_months total, where first io_months are IO.
    Amortization starts at month io_months+1 using standard mortgage formula.
    """
    if elapsed_months <= io_months:
        return loan
    amort_elapsed = elapsed_months - io_months
    r = annual_rate / 12.0
    pmt = _monthly_amort_payment(loan, annual_rate, amort_months)
    if r == 0:
        return loan - pmt * amort_elapsed
    balance = loan * (1 + r) ** amort_elapsed - pmt * ((1 + r) ** amort_elapsed - 1) / r
    return max(balance, 0.0)


def _annual_debt_service(loan: float, annual_rate: float,
                          amort_months: int, io_months: int,
                          year: int) -> float:
    """
    Total debt service for a given year (1-indexed).
    Months within IO period use IO payment; months after use standard P&I.
    """
    total = 0.0
    io_pmt = _monthly_io_payment(loan, annual_rate)
    amort_pmt = _monthly_amort_payment(loan, annual_rate, amort_months)
    for m in range(1, 13):
        elapsed = (year - 1) * 12 + m
        total += io_pmt if elapsed <= io_months else amort_pmt
    return total


# ---------------------------------------------------------------------------
# Step 2 -- Historical screening NOI
# ---------------------------------------------------------------------------

def _build_historical_noi(units: int, monthly_rent: float, asset_type: str) -> dict:
    """
    Compute historical screening NOI using CoStar data and toggle assumptions.

    This represents what the asset is doing TODAY under current ownership.
    Used only for context/display -- NOT used in forward UW or IRR calculation.

    Toggles (from assumptions.py):
        HIST_ECONOMIC_OCCUPANCY = 0.90
        HIST_OPEX_TOGGLE = 0.50
        HIST_CAP_RATE_CONV = 0.055
        HIST_CAP_RATE_BTR = 0.050
        HIST_OTHER_INCOME_PER_DOOR_MONTHLY = 150
        HIST_RUBS_PER_DOOR_ANNUAL = 840
    """
    gsr = units * monthly_rent * 12
    egr = gsr * HIST_ECONOMIC_OCCUPANCY
    other_income = units * HIST_OTHER_INCOME_PER_DOOR_MONTHLY * 12
    rubs = units * HIST_RUBS_PER_DOOR_ANNUAL
    total_hist_revenue = egr + other_income + rubs
    total_hist_expenses = total_hist_revenue * HIST_OPEX_TOGGLE
    historical_noi = total_hist_revenue - total_hist_expenses  # = THR * 0.50

    hist_cap = HIST_CAP_RATE_BTR if asset_type == "btr" else HIST_CAP_RATE_CONV
    historical_implied_value = historical_noi / hist_cap if hist_cap > 0 else 0.0

    return {
        "historical_gsr": gsr,
        "historical_egr": egr,
        "historical_other_income": other_income,
        "historical_rubs": rubs,
        "historical_total_revenue": total_hist_revenue,
        "historical_noi": historical_noi,
        "historical_opex_pct": HIST_OPEX_TOGGLE,
        "historical_occupancy_used": HIST_ECONOMIC_OCCUPANCY,
        "historical_cap_rate_used": hist_cap,
        "historical_implied_value": historical_implied_value,
    }


# ---------------------------------------------------------------------------
# Step 3 -- Forward UW revenue and expense builders
# ---------------------------------------------------------------------------

def _build_revenue(units: int, monthly_rent: float, hold_years: int) -> list[dict]:
    """
    Build N-year forward revenue schedule using locked assumptions.

    EGI formula (multiplicative per spec):
        EGI = GSR * (1 - VACANCY_RATE) * (1 - bad_debt_rate) * (1 - conc_rate)

    Other income components:
        RUBS  = units * RUBS_PER_DOOR_ANNUAL  (grow at OTHER_INCOME_GROWTH Yr2+)
        Other = units * OTHER_INCOME_PER_DOOR_ANNUAL (grow at OTHER_INCOME_GROWTH Yr2+)

    Returns list of dicts, one per year, with keys:
        year, rent_per_unit, gsr, egi, rubs, other_income,
        total_other_income, total_income
    """
    rubs_yr1 = units * RUBS_PER_DOOR_ANNUAL
    oi_yr1 = units * OTHER_INCOME_PER_DOOR_ANNUAL

    years = []
    for yr in range(1, hold_years + 1):
        if yr == 1:
            bad_debt_rate = BAD_DEBT_YEAR1
            conc_rate = CONCESSIONS_YEAR1
            rent = monthly_rent
            rubs = rubs_yr1
            oi = oi_yr1
        else:
            bad_debt_rate = BAD_DEBT_STABILIZED
            conc_rate = CONCESSIONS_STABILIZED
            rent = monthly_rent * (1 + RENT_GROWTH) ** (yr - 1)
            rubs = rubs_yr1 * (1 + OTHER_INCOME_GROWTH) ** (yr - 1)
            oi = oi_yr1 * (1 + OTHER_INCOME_GROWTH) ** (yr - 1)

        gsr = units * rent * 12
        egi = gsr * (1 - VACANCY_RATE) * (1 - bad_debt_rate) * (1 - conc_rate)
        total_other_income = rubs + oi
        total_income = egi + total_other_income

        years.append({
            "year": yr,
            "rent_per_unit": rent,
            "gsr": gsr,
            "egi": egi,
            "rubs": rubs,
            "other_income": oi,
            "total_other_income": total_other_income,
            "total_income": total_income,
        })
    return years


def _build_expenses(units: int, rev_list: list[dict], purchase_price: float) -> list[dict]:
    """
    Build N-year expense schedule aligned to revenue list.

    Per-unit line items grow at EXPENSE_GROWTH (3%) from Year 2.
    Management fee = MGMT_FEE_PCT of actual EGI each year.
    Property taxes = purchase_price * TAX_RATE_PCT (recalculated every solver iteration).

    Returns list of dicts, one per year, with keys:
        year, admin, marketing, payroll, rm, capex, mgmt_fee,
        insurance, re_tax, utilities, total_expenses
    """
    hold_years = len(rev_list)
    re_tax_base = purchase_price * TAX_RATE_PCT

    years = []
    for yr in range(1, hold_years + 1):
        g = (1 + EXPENSE_GROWTH) ** (yr - 1)
        egi_yr = rev_list[yr - 1]["egi"]

        admin             = ADMIN_PER_DOOR * units * g
        marketing         = MARKETING_PER_DOOR * units * g
        payroll           = PAYROLL_PER_DOOR * units * g
        rm                = RM_PER_DOOR * units * g
        contract_services = CONTRACT_SERVICES_PER_DOOR * units * g
        capex             = CAPEX_PER_DOOR * units * g
        mgmt              = MGMT_FEE_PCT * egi_yr
        ins               = INSURANCE_PER_UNIT_YEAR * units * g
        re_tax            = re_tax_base * g
        util              = UTILITIES_PER_UNIT_YEAR * units * g

        total = admin + marketing + payroll + rm + contract_services + capex + mgmt + ins + re_tax + util

        years.append({
            "year": yr,
            "admin": admin,
            "marketing": marketing,
            "payroll": payroll,
            "rm": rm,
            "contract_services": contract_services,
            "capex": capex,
            "mgmt_fee": mgmt,
            "insurance": ins,
            "re_tax": re_tax,
            "utilities": util,
            "total_expenses": total,
        })
    return years


# ---------------------------------------------------------------------------
# Core underwriting function
# ---------------------------------------------------------------------------

def underwrite_deal(deal: dict, hold_years: int = None) -> dict:
    """
    Run full N-year underwriting model on a deal dictionary.

    Required deal keys:
        property_name, units, current_monthly_rent_per_unit, asset_type

    Optional deal keys:
        asking_price       -- if omitted or 0, model solves for max bid
        noi_yr1_input      -- from vintage mapper; stored as historical_noi_file (not used in UW)
        egi_yr1_input      -- from vintage mapper; stored as historical_egi_file (not used in UW)

    Args:
        hold_years: Override the hold period. If None, uses HOLD_YEARS from assumptions.py.

    Returns:
        dict with full UW results, or dict with "error" key on hard failure.
    """
    print(f"\n  [UW] Underwriting: {deal.get('property_name', 'Unknown')}")

    # Resolve hold period
    hy = int(hold_years) if hold_years is not None else HOLD_YEARS

    # --- Extract deal inputs ---
    units = int(deal["units"])
    monthly_rent = float(deal["current_monthly_rent_per_unit"])
    market = str(deal.get("market", "")).lower().strip()
    asset_type = str(deal.get("asset_type", "conventional")).lower().strip()
    asking_price_input = deal.get("asking_price")

    # File-provided historical NOI (vintage mapper only -- for display, not UW)
    _raw_hist_noi = deal.get("noi_yr1_input")
    _raw_hist_egi = deal.get("egi_yr1_input")
    historical_noi_file = None
    historical_egi_file = None
    if _raw_hist_noi is not None:
        try:
            v = float(_raw_hist_noi)
            if not math.isnan(v) and v > 0:
                historical_noi_file = v
        except (TypeError, ValueError):
            pass
    if _raw_hist_egi is not None:
        try:
            v = float(_raw_hist_egi)
            if not math.isnan(v) and v > 0:
                historical_egi_file = v
        except (TypeError, ValueError):
            pass

    # --- Step 1: Treasury rate ---
    treasury_rate, treasury_source = fetch_treasury_5y()
    loan_rate = treasury_rate + (TREASURY_SPREAD_BPS / 10_000)
    print(f"  [UW] Loan rate: {loan_rate:.4%} (5yr Treasury {treasury_rate:.4%} + {TREASURY_SPREAD_BPS}bps)")

    # --- Step 2: Historical screening NOI ---
    hist = _build_historical_noi(units, monthly_rent, asset_type)

    # --- Exit cap rate ---
    is_tx_major = any(mkt in market for mkt in TX_MAJOR_MARKETS)
    if asset_type == "btr":
        exit_cap = EXIT_CAP_BTR_TX_MAJOR if is_tx_major else EXIT_CAP_BTR_OTHER
    else:
        exit_cap = EXIT_CAP_CONV

    # --- Going-in cap threshold for PASS screen ---
    goin_cap_threshold = (
        CAP_RATE_BTR_YEAR1 if asset_type == "btr" else CAP_RATE_CONV_YEAR1
    )

    # -------------------------------------------------------------------
    # Inner function: build full cashflow model at a given purchase price
    # -------------------------------------------------------------------
    def _model_at_price(purchase_price: float) -> dict:
        """
        Full 5-year cashflow model at a specific purchase price.
        Steps 3-5 per spec.
        """
        # Step 3 -- Forward UW NOI build
        rev = _build_revenue(units, monthly_rent, hy)
        exp = _build_expenses(units, rev, purchase_price)
        noi = [rev[i]["total_income"] - exp[i]["total_expenses"] for i in range(hy)]

        # Step 4 -- Debt structure
        loan_amount = purchase_price * LTV

        # Debt yield constraint: also cap loan by Year 3 NOI / DEBT_YIELD_MIN_YR3
        # (prevents over-leveraging on thin Year 3 cash flow)
        if len(noi) >= 3:
            loan_by_dy = noi[2] / DEBT_YIELD_MIN_YR3
            loan_amount = min(loan_amount, loan_by_dy)

        ds = [
            _annual_debt_service(loan_amount, loan_rate, AMORT_YEARS * 12, IO_PERIODS_MONTHS, yr)
            for yr in range(1, hy + 1)
        ]
        dscr = [noi[i] / ds[i] if ds[i] > 0 else 0.0 for i in range(hy)]

        # Step 5 -- Exit and returns
        exit_value = noi[hy - 1] / exit_cap
        selling_costs = exit_value * DISPOSITION_COST_PCT
        loan_balance_exit = _loan_balance_at_month(
            loan_amount, loan_rate, AMORT_YEARS * 12, IO_PERIODS_MONTHS, hy * 12
        )
        net_sale_proceeds = exit_value - selling_costs - loan_balance_exit

        equity = purchase_price * (1 - LTV)
        levered_cfs = [noi[i] - ds[i] for i in range(hy)]
        levered_cfs[-1] += net_sale_proceeds

        cf_series = [-equity] + levered_cfs
        deal_irr = float(npf.irr(cf_series))

        # LP/GP waterfall
        lp_equity = equity * LP_EQUITY_PCT   # LP contributes 90%
        gp_equity = equity * GP_EQUITY_PCT   # GP contributes 10%

        lp_cfs = []
        gp_cfs = []
        total_lp_dist = 0.0
        for i, cf in enumerate(levered_cfs):
            pref = lp_equity * PREFERRED_RETURN
            if cf <= pref:
                lp_dist = cf
                gp_dist = 0.0
            else:
                lp_dist = pref + (cf - pref) * LP_SPLIT   # 80% above pref
                gp_dist = (cf - pref) * GP_SPLIT           # 20% above pref
            lp_cfs.append(lp_dist)
            gp_cfs.append(gp_dist)
            total_lp_dist += lp_dist

        lp_irr = float(npf.irr([-lp_equity] + lp_cfs))
        equity_multiple = total_lp_dist / lp_equity if lp_equity > 0 else 0.0

        # Key metrics
        going_in_cap = noi[0] / purchase_price if purchase_price > 0 else 0.0
        debt_yield_yr3 = noi[2] / loan_amount if loan_amount > 0 and len(noi) >= 3 else 0.0
        expense_ratio_yr1 = (
            exp[0]["total_expenses"] / rev[0]["total_income"]
            if rev[0]["total_income"] > 0 else 0.0
        )

        # Annual levered CFs (before reversion) for reporting
        annual_cfs_no_rev = [noi[i] - ds[i] for i in range(hy)]
        annual_cfs_no_rev[-1] += net_sale_proceeds  # include reversion in final year

        result = {
            "purchase_price": purchase_price,
            "loan_amount": loan_amount,
            "equity": equity,
            "lp_equity": lp_equity,
            "gp_equity": gp_equity,
            "loan_rate": loan_rate,
            "treasury_rate": treasury_rate,
            "treasury_source": treasury_source,
            "exit_cap": exit_cap,
            "is_tx_major_market": is_tx_major,
            # Other income breakdowns (yr1-3 always present)
            "rubs_yr1": rev[0]["rubs"],
            "rubs_yr2": rev[1]["rubs"] if hy >= 2 else 0.0,
            "rubs_yr3": rev[2]["rubs"] if hy >= 3 else 0.0,
            "other_income_yr1": rev[0]["other_income"],
            "other_income_yr2": rev[1]["other_income"] if hy >= 2 else 0.0,
            "other_income_yr3": rev[2]["other_income"] if hy >= 3 else 0.0,
            "total_other_income_yr1": rev[0]["total_other_income"],
            "rubs_per_door_yr1": rev[0]["rubs"] / units if units > 0 else 0.0,
            "other_income_per_door_yr1": rev[0]["other_income"] / units if units > 0 else 0.0,
            # Revenue/expense totals yr1
            "total_revenue_yr1": rev[0]["total_income"],
            "total_expenses_yr1": exp[0]["total_expenses"],
            "expense_ratio_yr1": expense_ratio_yr1,
            "contract_services_yr1": exp[0]["contract_services"],
            "mgmt_fee_yr1": exp[0]["mgmt_fee"],
            # NOI (all hold years, dynamic keys)
            **{f"noi_yr{i+1}": noi[i] for i in range(hy)},
            # Debt service (all hold years)
            **{f"ds_yr{i+1}": ds[i] for i in range(hy)},
            # DSCR (all hold years)
            **{f"dscr_yr{i+1}": dscr[i] for i in range(hy)},
            # Annual CFs (all hold years, includes reversion in final year)
            **{f"annual_cf_yr{i+1}": annual_cfs_no_rev[i] for i in range(hy)},
            # Hold period used
            "hold_years_used": hy,
            # Returns
            "deal_irr": deal_irr,
            "lp_irr": lp_irr,
            "equity_multiple": equity_multiple,
            "going_in_cap": going_in_cap,
            "going_in_cap_threshold": goin_cap_threshold,
            "debt_yield_yr3": debt_yield_yr3,
            "exit_value": exit_value,
            "selling_costs": selling_costs,
            "net_sale_proceeds": net_sale_proceeds,
            "loan_balance_exit": loan_balance_exit,
            "levered_cfs": cf_series,
        }

        # Pad missing year keys (in case hy < 5 for any reason)
        for i in range(hy, 5):
            result.setdefault(f"noi_yr{i+1}", None)
            result.setdefault(f"ds_yr{i+1}", None)
            result.setdefault(f"dscr_yr{i+1}", None)
            result.setdefault(f"annual_cf_yr{i+1}", None)

        return result

    # -------------------------------------------------------------------
    # Step 6 -- Solve for max bid OR use provided asking price
    # -------------------------------------------------------------------
    if asking_price_input and float(asking_price_input) > 0:
        purchase_price = float(asking_price_input)
        print(f"  [UW] Using provided asking price: ${purchase_price:,.0f}")
        result = _model_at_price(purchase_price)
        result["max_bid"] = None
        result["solved_for_max_bid"] = False
    else:
        print(f"  [UW] Solving max bid at {DEAL_IRR_MIN:.0%} Deal IRR ...")

        def _irr_diff(price):
            try:
                m = _model_at_price(price)
                return m["deal_irr"] - DEAL_IRR_MIN
            except Exception:
                return -1.0

        try:
            max_bid = brentq(_irr_diff, SOLVER_PRICE_LOW, SOLVER_PRICE_HIGH, xtol=1_000, maxiter=200)
            print(f"  [UW] Max bid solved: ${max_bid:,.0f}")
        except ValueError:
            noi_est = units * monthly_rent * 12 * (1 - VACANCY_RATE - BAD_DEBT_YEAR1 - CONCESSIONS_YEAR1)
            max_bid = noi_est * 10
            print(f"  [UW] Solver failed -- using NOI-based floor ${max_bid:,.0f}")

        result = _model_at_price(max_bid)
        result["max_bid"] = max_bid
        result["solved_for_max_bid"] = True

    # max_bid_per_unit
    bid = result.get("max_bid") or result.get("purchase_price", 0)
    result["max_bid_per_unit"] = bid / units if units > 0 else 0.0

    # Attach deal metadata
    result.update({
        "property_name": deal.get("property_name", "Unknown"),
        "address": deal.get("address", ""),
        "market": deal.get("market", ""),
        "submarket": deal.get("submarket", ""),
        "state": deal.get("state", ""),
        "asset_type": asset_type,
        "units": units,
        "vintage": deal.get("vintage"),
        "current_occupancy": deal.get("current_occupancy"),
        "monthly_rent_used": monthly_rent,
        "rent_method": deal.get("rent_method", "actual"),
        "rent_comp_count": deal.get("rent_comp_count", 0),
        # Step 2 -- Historical NOI (context only)
        **hist,
        # File-provided historical values (vintage mapper, for comparison)
        "historical_noi_file": historical_noi_file,
        "historical_egi_file": historical_egi_file,
    })

    print(f"  [UW] Done -- Deal IRR: {result['deal_irr']:.1%} | "
          f"LP IRR: {result['lp_irr']:.1%} | "
          f"DSCR Yr1: {result.get('dscr_yr1', 0):.2f}x | "
          f"Going-in Cap: {result['going_in_cap']:.2%}")

    return result
