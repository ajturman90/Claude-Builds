"""
src/ingest.py
CSV import and buy box filter for the Portico deal sourcing pipeline.

Reads raw deal exports from data/imports/, applies buy box filters,
and passes qualifying deals to the underwriting engine.
All numeric thresholds imported from config/assumptions.py.
"""

import sys
import os
import logging
from typing import Optional

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.assumptions import CONVENTIONAL, BTR
from src.underwrite import underwrite_deal
from src.screener import screen_deal

logger = logging.getLogger(__name__)

# Expected columns with default values when missing
_COLUMN_DEFAULTS = {
    "property_name": "Unknown Property",
    "address": "",
    "city": "",
    "state": "",
    "market": "",
    "asset_type": "conventional",
    "units": None,             # Required — will exclude if missing
    "vintage": None,           # Required — will exclude if missing
    "asking_price": None,      # Optional — model solves for max bid if blank
    "current_rent_per_unit": None,   # Required
    "current_occupancy": 0.94,
    "noi_t12": None,
    "loan_maturity_date": None,
    "debt_type": None,
    "occupancy": 0.94,
    # Optional UW inputs
    "other_income_annual": None,
    "insurance_annual": None,
    "re_tax_annual": None,
    "utilities_annual": None,
}

# Map CSV column names to internal deal dict keys
_COLUMN_ALIASES = {
    "current_rent_per_unit": "current_monthly_rent_per_unit",
    "occupancy": "current_occupancy",
}


def _normalize_asset_type(val: str) -> str:
    """Normalize asset_type values to 'conventional' or 'btr'."""
    val = str(val).lower().strip()
    if val in ("btr", "build to rent", "build-to-rent", "sfr"):
        return "btr"
    return "conventional"


def _get_buy_box(asset_type: str) -> Optional[dict]:
    """Return the buy box config for an asset type, or None if unrecognized."""
    if asset_type == "btr":
        return BTR
    if asset_type == "conventional":
        return CONVENTIONAL
    return None


def _apply_buy_box_filter(row: dict) -> tuple[bool, str]:
    """
    Check whether a deal row passes the buy box filter.

    Returns:
        (passes: bool, reason: str)  — reason is empty string if passes
    """
    asset_type = _normalize_asset_type(row.get("asset_type", "conventional"))
    box = _get_buy_box(asset_type)

    if box is None:
        return False, f"Unrecognized asset_type '{asset_type}'"

    units = row.get("units")
    vintage = row.get("vintage")

    if units is None or pd.isna(units):
        return False, "Missing units count"
    if vintage is None or pd.isna(vintage):
        return False, "Missing vintage year"

    units = int(units)
    vintage = int(vintage)

    if not (box["min_units"] <= units <= box["max_units"]):
        return False, (
            f"Units {units} outside buy box "
            f"[{box['min_units']}–{box['max_units']}] for {asset_type}"
        )
    if vintage < box["min_vintage"]:
        return False, (
            f"Vintage {vintage} < {box['min_vintage']} minimum for {asset_type}"
        )

    rent = row.get("current_monthly_rent_per_unit") or row.get("current_rent_per_unit")
    if rent is None or pd.isna(rent) or float(rent) <= 0:
        return False, "Missing or zero current_rent_per_unit"

    return True, ""


def _build_deal_dict(row: dict) -> dict:
    """
    Convert a normalized CSV row dict into a deal dict suitable for underwrite_deal().
    Applies column aliases and fills defaults.
    """
    deal = {}
    for csv_col, default in _COLUMN_DEFAULTS.items():
        val = row.get(csv_col, default)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            val = default
        internal_key = _COLUMN_ALIASES.get(csv_col, csv_col)
        deal[internal_key] = val

    # Normalize asset type
    deal["asset_type"] = _normalize_asset_type(deal.get("asset_type", "conventional"))

    # Market fallback to city
    if not deal.get("market") or deal["market"] == "":
        deal["market"] = row.get("city", "")

    return deal


def ingest_csv(filepath: str) -> pd.DataFrame:
    """
    Read a CSV of deals, apply buy box filter, run underwriting and screening
    on qualifying deals, and return a consolidated results DataFrame.

    Args:
        filepath: Path to the CSV file in data/imports/

    Returns:
        DataFrame with one row per deal, columns include:
        property_name, status, reasons, all UW metrics, excluded_reason
    """
    print(f"\n[Ingest] Reading: {filepath}")

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"CSV not found: {filepath}")

    raw = pd.read_csv(filepath, dtype=str)
    raw.columns = [c.lower().strip().replace(" ", "_") for c in raw.columns]
    print(f"[Ingest] {len(raw)} rows loaded, columns: {list(raw.columns)}")

    # Log any missing expected columns and what defaults will be used
    for col, default in _COLUMN_DEFAULTS.items():
        if col not in raw.columns:
            logger.warning("Column '%s' not found — defaulting to %r", col, default)
            print(f"  [Ingest] WARNING: Column '{col}' missing — using default: {default!r}")
            raw[col] = None

    # Convert numeric columns
    numeric_cols = [
        "units", "vintage", "asking_price", "current_rent_per_unit",
        "current_occupancy", "occupancy", "noi_t12",
        "other_income_annual", "insurance_annual", "re_tax_annual", "utilities_annual",
    ]
    for col in numeric_cols:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")

    results = []

    for _, row in raw.iterrows():
        row_dict = row.to_dict()
        prop_name = row_dict.get("property_name", "Unknown")

        # Map current_rent_per_unit → current_monthly_rent_per_unit for buy box check
        if "current_rent_per_unit" in row_dict and "current_monthly_rent_per_unit" not in row_dict:
            row_dict["current_monthly_rent_per_unit"] = row_dict["current_rent_per_unit"]

        passes_box, exclude_reason = _apply_buy_box_filter(row_dict)

        if not passes_box:
            print(f"  [Ingest] EXCLUDED: {prop_name} — {exclude_reason}")
            results.append({
                "property_name": prop_name,
                "market": row_dict.get("market", row_dict.get("city", "")),
                "state": row_dict.get("state", ""),
                "asset_type": row_dict.get("asset_type", ""),
                "units": row_dict.get("units"),
                "vintage": row_dict.get("vintage"),
                "asking_price": row_dict.get("asking_price"),
                "pipeline_status": "EXCLUDED",
                "excluded_reason": exclude_reason,
                "screen_reasons": "",
            })
            continue

        # Build deal dict and run UW
        deal = _build_deal_dict(row_dict)
        print(f"  [Ingest] Qualifying: {prop_name} — sending to underwriter")

        try:
            uw_result = underwrite_deal(deal)
            screen_result = screen_deal(uw_result)

            row_out = {
                "property_name": uw_result.get("property_name"),
                "address": uw_result.get("address", ""),
                "market": uw_result.get("market", ""),
                "state": uw_result.get("state", ""),
                "asset_type": uw_result.get("asset_type"),
                "units": uw_result.get("units"),
                "vintage": uw_result.get("vintage"),
                "asking_price": uw_result.get("purchase_price"),
                "max_bid": uw_result.get("max_bid"),
                "loan_amount": uw_result.get("loan_amount"),
                "loan_rate": uw_result.get("loan_rate"),
                "treasury_rate": uw_result.get("treasury_rate"),
                "treasury_source": uw_result.get("treasury_source"),
                "noi_yr1": uw_result.get("noi_yr1"),
                "noi_yr2": uw_result.get("noi_yr2"),
                "noi_yr3": uw_result.get("noi_yr3"),
                "ds_yr1": uw_result.get("ds_yr1"),
                "ds_yr2": uw_result.get("ds_yr2"),
                "ds_yr3": uw_result.get("ds_yr3"),
                "dscr_yr1": uw_result.get("dscr_yr1"),
                "dscr_yr2": uw_result.get("dscr_yr2"),
                "dscr_yr3": uw_result.get("dscr_yr3"),
                "deal_irr": uw_result.get("deal_irr"),
                "lp_irr": uw_result.get("lp_irr"),
                "equity_multiple": uw_result.get("equity_multiple"),
                "going_in_cap": uw_result.get("going_in_cap"),
                "debt_yield_yr3": uw_result.get("debt_yield_yr3"),
                "expense_ratio_yr1": uw_result.get("expense_ratio_yr1"),
                "exit_value": uw_result.get("exit_value"),
                "exit_cap": uw_result.get("exit_cap"),
                "pipeline_status": screen_result["status"],
                "screen_reasons": "; ".join(screen_result["reasons"]),
                "excluded_reason": "",
                "_uw_result": uw_result,
                "_screen_result": screen_result,
            }
        except Exception as exc:
            logger.error("UW failed for %s: %s", prop_name, exc, exc_info=True)
            print(f"  [Ingest] ERROR underwriting {prop_name}: {exc}")
            row_out = {
                "property_name": prop_name,
                "market": row_dict.get("market", ""),
                "state": row_dict.get("state", ""),
                "asset_type": row_dict.get("asset_type", ""),
                "units": row_dict.get("units"),
                "vintage": row_dict.get("vintage"),
                "asking_price": row_dict.get("asking_price"),
                "pipeline_status": "FAIL",
                "screen_reasons": f"Underwriting error: {exc}",
                "excluded_reason": "",
            }

        results.append(row_out)

    df = pd.DataFrame(results)

    pass_ct = (df["pipeline_status"] == "PASS").sum()
    possible_ct = (df["pipeline_status"] == "POSSIBLE").sum()
    fail_ct = (df["pipeline_status"] == "FAIL").sum()
    excl_ct = (df["pipeline_status"] == "EXCLUDED").sum()

    print(f"\n[Ingest] Pipeline complete: "
          f"{len(df)} total | {pass_ct} PASS | {possible_ct} POSSIBLE | "
          f"{fail_ct} FAIL | {excl_ct} EXCLUDED")

    return df
