"""
src/underwrite.py
Core auto-underwriting engine for Portico deal sourcing platform.

All numeric assumptions are imported from config/assumptions.py.
No constants are defined here.
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

# Allow running from project root or src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.assumptions import (
    VACANCY_RATE,
    BAD_DEBT_YEAR1, BAD_DEBT_STABILIZED,
    CONCESSIONS_YEAR1, CONCESSIONS_STABILIZED,
    RENT_GROWTH, OTHER_INCOME_GROWTH, EXPENSE_GROWTH,
    ADMIN_PER_DOOR, MARKETING_PER_DOOR, PAYROLL_PER_DOOR,
    RM_PER_DOOR, CAPEX_PER_DOOR, MGMT_FEE_PCT,
    OTHER_INCOME_PER_UNIT_MONTH, INSURANCE_PER_UNIT_YEAR,
    RE_TAX_PCT_OF_PRICE, UTILITIES_PER_UNIT_YEAR,
    LTV, TREASURY_SPREAD_BPS, FALLBACK_RATE,
    AMORT_YEARS, IO_PERIODS_MONTHS,
    EXIT_CAP_TX_MAJOR, EXIT_CAP_OTHER, TX_MAJOR_MARKETS,
    DISPOSITION_COST_PCT, HOLD_YEARS,
    DEAL_IRR_MIN, DEBT_YIELD_MIN_YR3,
    PREFERRED_RETURN, LP_SPLIT, GP_SPLIT,
    CAP_RATE_BTR_YEAR1, CAP_RATE_CONV_YEAR1,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Treasury rate fetcher — cached per process so bulk runs hit API only once
# ---------------------------------------------------------------------------

_treasury_cache: tuple[float, str] | None = None


def fetch_treasury_10y() -> tuple[float, str]:
    """
    Fetch the current 10-year US Treasury yield from Treasury.gov XML feed.

    Returns:
        (rate_as_decimal, source_description)
        Falls back to FALLBACK_RATE if the API is unreachable or returns no data.
        Rate is cached in-process so bulk runs only call the API once.
    """
    global _treasury_cache
    if _treasury_cache is not None:
        return _treasury_cache

    now = datetime.utcnow()
    year_month = f"{now.year}{now.month:02d}"

    url = (
        "https://home.treasury.gov/resource-center/data-chart-center/"
        f"interest-rates/pages/xml?data=daily_treasury_yield_curve"
        f"&field_tdr_date_value_month={year_month}"
    )

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)

        # Collect all BC_10YEAR values from the XML tree
        entries = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "BC_10YEAR" and elem.text and elem.text.strip():
                try:
                    entries.append(float(elem.text.strip()))
                except ValueError:
                    pass

        if not entries:
            raise ValueError("No BC_10YEAR data found in Treasury XML")

        rate_pct = entries[-1]
        rate = rate_pct / 100.0
        source = f"Treasury API {now.year}-{now.month:02d}"
        logger.info("Treasury rate: %.4f from %s", rate, source)
        print(f"  [Treasury] Rate pulled from API: {rate:.4%} ({source})")
        _treasury_cache = (rate, source)
        return _treasury_cache

    except Exception as exc:
        logger.warning("Treasury API failed (%s) -- using FALLBACK_RATE %.4f", exc, FALLBACK_RATE)
        print(f"  [Treasury] API unavailable ({exc}). Using fallback rate: {FALLBACK_RATE:.4%}")
        _treasury_cache = (FALLBACK_RATE, f"Fallback hardcoded rate ({FALLBACK_RATE:.4%})")
        return _treasury_cache


# ---------------------------------------------------------------------------
# Loan mechanics helpers
# ---------------------------------------------------------------------------

def _monthly_io_payment(loan: float, annual_rate: float) -> float:
    """Interest-only monthly payment."""
    return loan * annual_rate / 12.0


def _monthly_amort_payment(loan: float, annual_rate: float, amort_months: int) -> float:
    """Standard level-payment amortizing monthly payment."""
    r = annual_rate / 12.0
    if r == 0:
        return loan / amort_months
    return loan * r * (1 + r) ** amort_months / ((1 + r) ** amort_months - 1)


def _loan_balance_at_month(loan: float, annual_rate: float,
                            amort_months: int, io_months: int,
                            elapsed_months: int) -> float:
    """
    Remaining principal balance after `elapsed_months` total months,
    where the first `io_months` are interest-only, then amortizing.
    """
    if elapsed_months <= io_months:
        return loan  # IO period — no principal paid down

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
    Months within IO period use IO payment; amortizing months use P&I.
    """
    total = 0.0
    for m in range(1, 13):
        elapsed = (year - 1) * 12 + m
        if elapsed <= io_months:
            total += _monthly_io_payment(loan, annual_rate)
        else:
            total += _monthly_amort_payment(loan, annual_rate, amort_months)
    return total


# ---------------------------------------------------------------------------
# Revenue & expense builders
# ---------------------------------------------------------------------------

def _build_revenue(units: int, monthly_rent: float,
                    other_income_annual: Optional[float]) -> list[dict]:
    """
    Build 3-year revenue schedule.

    Returns list of dicts, one per year, with keys:
    gsr, vacancy, bad_debt, concessions, egi, other_income, total_income
    """
    years = []
    rent = monthly_rent
    oi_yr1 = other_income_annual if other_income_annual else units * OTHER_INCOME_PER_UNIT_MONTH * 12

    for yr in range(1, HOLD_YEARS + 1):
        if yr == 1:
            bad_debt_rate = BAD_DEBT_YEAR1
            conc_rate = CONCESSIONS_YEAR1
            oi = oi_yr1
        else:
            bad_debt_rate = BAD_DEBT_STABILIZED
            conc_rate = CONCESSIONS_STABILIZED
            oi = oi_yr1 * (1 + OTHER_INCOME_GROWTH) ** (yr - 1)
            rent = monthly_rent * (1 + RENT_GROWTH) ** (yr - 1)

        gsr = units * rent * 12
        vacancy = gsr * VACANCY_RATE
        bad_debt = gsr * bad_debt_rate
        concessions = gsr * conc_rate
        egi = gsr - vacancy - bad_debt - concessions
        total_income = egi + oi

        years.append({
            "year": yr,
            "rent_per_unit": rent,
            "gsr": gsr,
            "vacancy": vacancy,
            "bad_debt": bad_debt,
            "concessions": concessions,
            "egi": egi,
            "other_income": oi,
            "total_income": total_income,
        })
    return years


def _build_expenses(units: int, egi_yr1: float,
                     insurance_annual: Optional[float],
                     re_tax_annual: Optional[float],
                     utilities_annual: Optional[float],
                     purchase_price: float) -> list[dict]:
    """
    Build 3-year expense schedule.

    Returns list of dicts, one per year, with keys:
    admin, marketing, payroll, rm, capex, mgmt_fee,
    insurance, re_tax, utilities, total_expenses
    """
    ins = insurance_annual if insurance_annual else units * INSURANCE_PER_UNIT_YEAR
    ret = (re_tax_annual if re_tax_annual
           else purchase_price * RE_TAX_PCT_OF_PRICE)
    util = utilities_annual if utilities_annual else units * UTILITIES_PER_UNIT_YEAR

    years = []
    for yr in range(1, HOLD_YEARS + 1):
        g = (1 + EXPENSE_GROWTH) ** (yr - 1)
        egi_yr = egi_yr1 * (1 + EXPENSE_GROWTH) ** (yr - 1)  # proxy growth on EGI for mgmt fee

        admin = ADMIN_PER_DOOR * units * g
        marketing = MARKETING_PER_DOOR * units * g
        payroll = PAYROLL_PER_DOOR * units * g
        rm = RM_PER_DOOR * units * g
        capex = CAPEX_PER_DOOR * units * g
        mgmt = MGMT_FEE_PCT * egi_yr
        ins_yr = ins * g
        ret_yr = ret * g
        util_yr = util * g

        total = admin + marketing + payroll + rm + capex + mgmt + ins_yr + ret_yr + util_yr

        years.append({
            "year": yr,
            "admin": admin,
            "marketing": marketing,
            "payroll": payroll,
            "rm": rm,
            "capex": capex,
            "mgmt_fee": mgmt,
            "insurance": ins_yr,
            "re_tax": ret_yr,
            "utilities": util_yr,
            "total_expenses": total,
        })
    return years


# ---------------------------------------------------------------------------
# Core underwriting function
# ---------------------------------------------------------------------------

def underwrite_deal(deal: dict) -> dict:
    """
    Run full 3-year underwriting model on a deal dictionary.

    Required deal keys:
        property_name, address, market, state, asset_type,
        units, vintage, current_monthly_rent_per_unit, current_occupancy

    Optional deal keys:
        asking_price        — if omitted, model solves for max bid
        other_income_annual — annual $ of other income
        insurance_annual    — annual $ insurance
        re_tax_annual       — annual real estate taxes $
        utilities_annual    — annual utilities $

    Returns:
        dict with full UW results, or {"error": message} on hard failure.
    """
    print(f"\n  [UW] Underwriting: {deal.get('property_name', 'Unknown')}")

    # --- Extract deal inputs ---
    units = int(deal["units"])
    monthly_rent = float(deal["current_monthly_rent_per_unit"])
    market = str(deal.get("market", "")).lower().strip()
    asset_type = str(deal.get("asset_type", "conventional")).lower().strip()
    asking_price_input = deal.get("asking_price")

    other_income_annual = (
        float(deal["other_income_annual"])
        if deal.get("other_income_annual") else None
    )
    insurance_annual = (
        float(deal["insurance_annual"])
        if deal.get("insurance_annual") else None
    )
    re_tax_annual = (
        float(deal["re_tax_annual"])
        if deal.get("re_tax_annual") else None
    )
    utilities_annual = (
        float(deal["utilities_annual"])
        if deal.get("utilities_annual") else None
    )

    # --- Treasury rate ---
    treasury_rate, treasury_source = fetch_treasury_10y()
    loan_rate = treasury_rate + (TREASURY_SPREAD_BPS / 10_000)
    print(f"  [UW] Loan rate: {loan_rate:.4%} (Treasury {treasury_rate:.4%} + {TREASURY_SPREAD_BPS}bps)")

    # --- Exit cap rate ---
    is_tx_major = any(mkt in market for mkt in TX_MAJOR_MARKETS)
    exit_cap = EXIT_CAP_TX_MAJOR if is_tx_major else EXIT_CAP_OTHER

    # --- Going-in cap rate threshold ---
    goin_cap_threshold = (
        CAP_RATE_BTR_YEAR1 if asset_type == "btr" else CAP_RATE_CONV_YEAR1
    )

    # -------------------------------------------------------------------
    # Inner function: build full model at a given purchase price
    # -------------------------------------------------------------------
    def _model_at_price(purchase_price: float) -> dict:
        """Build full 3-year cashflow model at a specific purchase price."""

        # Revenue
        rev = _build_revenue(units, monthly_rent, other_income_annual)

        # Taxes default needs purchase price
        re_tax = re_tax_annual if re_tax_annual else purchase_price * RE_TAX_PCT_OF_PRICE

        # Expenses — pass Year 1 EGI as base for mgmt fee growth proxy
        exp = _build_expenses(
            units,
            egi_yr1=rev[0]["egi"],
            insurance_annual=insurance_annual,
            re_tax_annual=re_tax,
            utilities_annual=utilities_annual,
            purchase_price=purchase_price,
        )

        # NOI per year
        noi = [rev[i]["total_income"] - exp[i]["total_expenses"] for i in range(HOLD_YEARS)]

        # Debt
        # Loan = min(price × LTV, NOI_yr3 / debt_yield_min)
        loan_by_ltv = purchase_price * LTV
        loan_by_dy = noi[2] / DEBT_YIELD_MIN_YR3
        loan_amount = min(loan_by_ltv, loan_by_dy)

        # Annual debt service
        ds = [
            _annual_debt_service(loan_amount, loan_rate, AMORT_YEARS * 12, IO_PERIODS_MONTHS, yr)
            for yr in range(1, HOLD_YEARS + 1)
        ]

        # DSCR
        dscr = [noi[i] / ds[i] if ds[i] > 0 else 0.0 for i in range(HOLD_YEARS)]

        # Exit
        exit_value = noi[2] / exit_cap
        net_proceeds = exit_value * (1 - DISPOSITION_COST_PCT)
        loan_balance_exit = _loan_balance_at_month(
            loan_amount, loan_rate, AMORT_YEARS * 12, IO_PERIODS_MONTHS, IO_PERIODS_MONTHS
        )
        net_sale_to_equity = net_proceeds - loan_balance_exit

        # Levered cash flows (at deal level)
        equity = purchase_price - loan_amount
        levered_cfs = [
            noi[i] - ds[i] for i in range(HOLD_YEARS)
        ]
        levered_cfs[-1] += net_sale_to_equity  # add reversion in year 3

        cf_series = [-equity] + levered_cfs
        deal_irr = float(npf.irr(cf_series))

        # LP / GP waterfall
        lp_equity = equity * LP_SPLIT
        gp_equity = equity * GP_SPLIT

        lp_cfs = []
        gp_cfs = []
        total_lp_dist = 0.0

        for i, cf in enumerate(levered_cfs):
            pref = lp_equity * PREFERRED_RETURN
            if cf <= pref:
                lp_dist = cf
                gp_dist = 0.0
            else:
                lp_dist = pref + (cf - pref) * LP_SPLIT
                gp_dist = (cf - pref) * GP_SPLIT
            lp_cfs.append(lp_dist)
            gp_cfs.append(gp_dist)
            total_lp_dist += lp_dist

        lp_irr = float(npf.irr([-lp_equity] + lp_cfs))
        equity_multiple = total_lp_dist / lp_equity if lp_equity > 0 else 0.0

        # Metrics
        going_in_cap = noi[0] / purchase_price if purchase_price > 0 else 0.0
        debt_yield_yr3 = noi[2] / loan_amount if loan_amount > 0 else 0.0

        expense_ratio_yr1 = (
            exp[0]["total_expenses"] / rev[0]["total_income"]
            if rev[0]["total_income"] > 0 else 0.0
        )

        return {
            "purchase_price": purchase_price,
            "loan_amount": loan_amount,
            "equity": equity,
            "loan_rate": loan_rate,
            "treasury_rate": treasury_rate,
            "treasury_source": treasury_source,
            "exit_cap": exit_cap,
            "is_tx_major_market": is_tx_major,
            # Revenue
            "revenue": rev,
            # Expenses
            "expenses": exp,
            # NOI
            "noi_yr1": noi[0],
            "noi_yr2": noi[1],
            "noi_yr3": noi[2],
            # Debt service
            "ds_yr1": ds[0],
            "ds_yr2": ds[1],
            "ds_yr3": ds[2],
            # DSCR
            "dscr_yr1": dscr[0],
            "dscr_yr2": dscr[1],
            "dscr_yr3": dscr[2],
            # Returns
            "deal_irr": deal_irr,
            "lp_irr": lp_irr,
            "equity_multiple": equity_multiple,
            "going_in_cap": going_in_cap,
            "debt_yield_yr3": debt_yield_yr3,
            "expense_ratio_yr1": expense_ratio_yr1,
            "exit_value": exit_value,
            "net_sale_proceeds": net_proceeds,
            "loan_balance_exit": loan_balance_exit,
            "levered_cfs": cf_series,
            # Thresholds for reference
            "going_in_cap_threshold": goin_cap_threshold,
        }

    # -------------------------------------------------------------------
    # Solve for max bid OR use provided asking price
    # -------------------------------------------------------------------
    if asking_price_input and float(asking_price_input) > 0:
        purchase_price = float(asking_price_input)
        print(f"  [UW] Using provided asking price: ${purchase_price:,.0f}")
        result = _model_at_price(purchase_price)
        result["max_bid"] = None
        result["solved_for_max_bid"] = False
    else:
        print("  [UW] No asking price — solving for max bid at 17% deal IRR ...")
        # Objective: find price where deal IRR = DEAL_IRR_MIN
        def _irr_diff(price):
            try:
                m = _model_at_price(price)
                return m["deal_irr"] - DEAL_IRR_MIN
            except Exception:
                return -1.0  # treat as infeasible

        # Bracket: try from 1x to 30x annual NOI estimate
        noi_est = units * monthly_rent * 12 * (1 - VACANCY_RATE - BAD_DEBT_YEAR1 - CONCESSIONS_YEAR1)
        lo = max(noi_est * 1.0, 100_000)
        hi = noi_est * 30

        try:
            max_bid = brentq(_irr_diff, lo, hi, xtol=1_000, maxiter=200)
            print(f"  [UW] Max bid solved: ${max_bid:,.0f}")
        except ValueError:
            # If no root found, run at asking price = 0 (will still return metrics)
            max_bid = lo
            print(f"  [UW] Could not solve max bid — using NOI floor estimate ${max_bid:,.0f}")

        result = _model_at_price(max_bid)
        result["max_bid"] = max_bid
        result["solved_for_max_bid"] = True

    # Attach deal metadata
    result.update({
        "property_name": deal.get("property_name", "Unknown"),
        "address": deal.get("address", ""),
        "market": deal.get("market", ""),
        "state": deal.get("state", ""),
        "asset_type": asset_type,
        "units": units,
        "vintage": deal.get("vintage"),
        "current_occupancy": deal.get("current_occupancy"),
        "current_monthly_rent_per_unit": monthly_rent,
    })

    print(f"  [UW] Done — Deal IRR: {result['deal_irr']:.1%} | "
          f"LP IRR: {result['lp_irr']:.1%} | "
          f"DSCR Yr1: {result['dscr_yr1']:.2f}x | "
          f"Going-in Cap: {result['going_in_cap']:.2%}")

    return result
