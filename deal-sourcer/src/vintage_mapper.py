"""
src/vintage_mapper.py
Maps the Conventional MF 1985-2000 Vintage List xlsx export to the Portico
pipeline import schema.

Usage:
    python src/vintage_mapper.py --file "data/imports/Conventional_MF_1985-2000_Vintage_List.xlsx"

Output:
    data/imports/vintage_mapped.csv

Key differences from costar_mapper.py:
  - Purchase price sourced from "PP @ 6% Cap" column (pre-calculated at 6% cap rate).
    NOTE: User spec referenced "PP @ 5.5% Cap" — the actual column in the file is
    "PP @ 6% Cap". This mapper uses the actual column name.
  - Sets hold_years = 5 for all deals (5-year hold, no max bid solving)
  - Flags Affordable Type "Rent Subsidized" / "Rent Restricted" as
    excluded_reason_mapper so they appear in the EXCLUDED tab of the report
  - Properties with null/zero purchase price are flagged as excluded
  - Units < 75 and vintage < 1985 are removed in the mapper (buy box filter)
"""

import sys
import os
import shutil
import argparse
import logging

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.costar_mapper import estimate_missing_rents, _derive_asset_type

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column mapping: xlsx header -> pipeline schema name
# ---------------------------------------------------------------------------
COLUMN_MAP = {
    "Property Name":          "property_name",
    "Property Address":       "address",
    "City":                   "city",
    "State":                  "state",
    "Market Name":            "market",
    "Submarket Name":         "submarket",
    "Number Of Units":        "units",
    "Year Built":             "vintage",
    "Avg Asking/Unit":        "current_rent_per_unit",
    "Vacancy %":              "vacancy_pct",
    "PP @ 6% Cap":            "asking_price",       # user spec said "PP @ 5.5% Cap" -- actual col is "PP @ 6% Cap"
    "Taxes Total":            "re_tax_annual",
    "Building Class":         "building_class",
    "Secondary Type":         "secondary_type",
    "Building Status":        "building_status",
    "Year Renovated":         "year_renovated",
    "Owner Name":             "owner_name",
    "Property Manager Name":  "property_manager",
    "Maturity Date":          "loan_maturity_date",
    "Loan Type":              "debt_type",
    "Affordable Type":        "affordable_type",
    # Pre-calculated financial figures — used directly in passthrough UW mode
    "NOI @ 50% OPEX":         "noi_yr1_input",
    "Estimated Annual Rental Collection @ 90% economic Occupancy": "egi_yr1_input",
}

# Building statuses that disqualify a property entirely
EXCLUDED_STATUSES = {"Under Construction", "Proposed", "Under Renovation"}

# Affordable type values that flag as EXCLUDED in the report (kept in CSV)
AFFORDABLE_EXCLUDE = {"rent subsidized", "rent restricted"}

# Hold period override for this dataset
HOLD_YEARS_OVERRIDE = 5

# Buy box minimum for this dataset (same as CONVENTIONAL buy box)
MIN_UNITS = 75
MIN_VINTAGE = 1985


def map_vintage_export(input_path: str, output_path: str) -> pd.DataFrame:
    """
    Read a vintage-list xlsx, map columns, filter, fill defaults, run rent
    estimation, and write a pipeline-ready CSV with hold_years=5.

    Affordable-type exclusions are kept in the CSV with excluded_reason_mapper
    set so they surface in the report EXCLUDED tab.

    Args:
        input_path:  Path to the raw xlsx file
        output_path: Path to write the cleaned .csv

    Returns:
        DataFrame of all rows written to output_path (including pre-excluded rows)
    """
    print(f"\n[VintageMapper] Reading: {input_path}")

    # OneDrive lock protection — copy before read
    tmp_path = input_path + ".tmp.xlsx"
    shutil.copy2(input_path, tmp_path)
    try:
        raw = pd.read_excel(tmp_path, dtype=str, engine="openpyxl")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    total_raw = len(raw)
    print(f"[VintageMapper] {total_raw} rows loaded")
    print(f"[VintageMapper] Columns found: {list(raw.columns)}")

    # Check for the PP column — warn if user-expected name differs from actual
    expected_pp_col = "PP @ 5.5% Cap"
    actual_pp_col   = "PP @ 6% Cap"
    if expected_pp_col not in raw.columns and actual_pp_col in raw.columns:
        print(f"\n[VintageMapper] WARNING: Column '{expected_pp_col}' not found.")
        print(f"[VintageMapper]          Using '{actual_pp_col}' as purchase price column.")
        print(f"[VintageMapper]          Verify this is correct before relying on results.\n")

    # Log any expected COLUMN_MAP columns that are missing
    missing_cols = [cs for cs in COLUMN_MAP if cs not in raw.columns]
    if missing_cols:
        print(f"[VintageMapper] Missing columns (will default): {missing_cols}")

    # -----------------------------------------------------------------------
    # STEP 1 -- Select and rename columns
    # -----------------------------------------------------------------------
    present_map = {cs: ps for cs, ps in COLUMN_MAP.items() if cs in raw.columns}
    df = raw[list(present_map.keys())].rename(columns=present_map).copy()

    for cs, ps in COLUMN_MAP.items():
        if ps not in df.columns:
            df[ps] = None

    # -----------------------------------------------------------------------
    # STEP 2 -- Derive asset_type
    # -----------------------------------------------------------------------
    df["asset_type"] = df["secondary_type"].apply(_derive_asset_type)
    asset_counts = df["asset_type"].value_counts().to_dict()
    print(f"[VintageMapper] Asset types derived: {asset_counts}")

    # -----------------------------------------------------------------------
    # STEP 3 -- Convert key numerics before filtering
    # -----------------------------------------------------------------------
    df["units"]   = pd.to_numeric(df["units"],   errors="coerce")
    df["vintage"] = pd.to_numeric(df["vintage"], errors="coerce")

    # -----------------------------------------------------------------------
    # STEP 4 -- Hard filters (remove entirely; buy box violations)
    # -----------------------------------------------------------------------
    removal_log = []

    def _remove(mask, reason):
        count = int(mask.sum())
        if count:
            names = df.loc[mask, "property_name"].tolist()
            removal_log.append((reason, count, names[:5]))
            print(f"[VintageMapper] REMOVING {count} -- {reason}")
        return ~mask

    # Building status
    status_mask = df["building_status"].isin(EXCLUDED_STATUSES)
    df = df[_remove(status_mask, f"Building Status in {EXCLUDED_STATUSES}")].copy()

    # Null / zero units
    null_units_mask = df["units"].isna() | (df["units"] == 0)
    df = df[_remove(null_units_mask, "Units null or zero")].copy()

    # Units below buy box minimum
    small_units_mask = df["units"] < MIN_UNITS
    df = df[_remove(small_units_mask, f"Units < {MIN_UNITS} (conventional buy box minimum)")].copy()

    # Null vintage
    null_vintage_mask = df["vintage"].isna()
    df = df[_remove(null_vintage_mask, "Vintage (Year Built) null")].copy()

    # Vintage too old
    old_vintage_mask = df["vintage"] < MIN_VINTAGE
    df = df[_remove(old_vintage_mask, f"Vintage < {MIN_VINTAGE}")].copy()

    survived_hard = len(df)
    removed_hard  = total_raw - survived_hard
    print(f"\n[VintageMapper] Hard filter summary:")
    print(f"  Raw rows      : {total_raw}")
    print(f"  Removed       : {removed_hard}")
    print(f"  Survived      : {survived_hard}")
    for reason, count, examples in removal_log:
        print(f"    - {count} removed: {reason}")
        if examples:
            print(f"      e.g. {examples[:3]}")

    # -----------------------------------------------------------------------
    # STEP 5 -- Soft exclusions (kept in CSV, flagged for report EXCLUDED tab)
    # -----------------------------------------------------------------------
    df["notes"]                = ""
    df["excluded_reason_mapper"] = ""

    # Affordable / subsidized housing
    def _is_affordable(val):
        if pd.isna(val):
            return False
        return str(val).strip().lower() in AFFORDABLE_EXCLUDE

    affordable_mask = df["affordable_type"].apply(_is_affordable)
    affordable_ct   = int(affordable_mask.sum())
    if affordable_ct:
        df.loc[affordable_mask, "excluded_reason_mapper"] = (
            "Affordable/subsidized housing -- not in buy box"
        )
        df.loc[affordable_mask, "notes"] = df.loc[affordable_mask, "affordable_type"].apply(
            lambda v: f"Affordable Type: {v}"
        )
        print(f"[VintageMapper] FLAGGED {affordable_ct} as affordable/subsidized "
              f"(will appear as EXCLUDED in report)")

    # -----------------------------------------------------------------------
    # STEP 6 -- Convert and fill remaining numerics
    # -----------------------------------------------------------------------
    # Vacancy % -> current_occupancy
    df["vacancy_pct"] = pd.to_numeric(df["vacancy_pct"], errors="coerce")
    missing_vac = df["vacancy_pct"].isna()
    df.loc[missing_vac & (df["excluded_reason_mapper"] == ""), "notes"] += "occupancy assumed; "
    df["current_occupancy"] = df["vacancy_pct"].apply(
        lambda v: 0.94 if pd.isna(v) else round(1.0 - v / 100.0, 4)
    )
    print(f"[VintageMapper] Vacancy: {int(missing_vac.sum())} missing -> defaulted to 0.94 occupancy")

    # current_rent_per_unit
    df["current_rent_per_unit"] = pd.to_numeric(df["current_rent_per_unit"], errors="coerce")
    missing_rent_ct = int(df["current_rent_per_unit"].isna().sum())
    print(f"[VintageMapper] Rent: {missing_rent_ct} missing -> running estimate_missing_rents()")
    # Only estimate rents for non-pre-excluded rows (affordable excluded can keep 0)
    df = estimate_missing_rents(df)
    still_missing = (
        df["current_rent_per_unit"].isna() |
        ((df["current_rent_per_unit"] == 0) & (df["rent_method"] == "no_rent_data"))
    )
    df.loc[still_missing & ~df["notes"].str.contains("NO RENT DATA", na=False), "notes"] += "NO RENT DATA; "
    df.loc[df["current_rent_per_unit"].isna(), "current_rent_per_unit"] = 0

    # asking_price (PP @ 6% Cap)
    df["asking_price"] = pd.to_numeric(df["asking_price"], errors="coerce")
    no_price_mask = df["asking_price"].isna() | (df["asking_price"] == 0)
    no_price_ct   = int(no_price_mask.sum())
    if no_price_ct:
        # Only flag those not already excluded for another reason
        new_no_price = no_price_mask & (df["excluded_reason_mapper"] == "")
        df.loc[new_no_price, "excluded_reason_mapper"] = "No purchase price available"
        df.loc[no_price_mask, "asking_price"] = 0
        print(f"[VintageMapper] FLAGGED {int(new_no_price.sum())} rows with no PP as excluded")
    else:
        print(f"[VintageMapper] Asking price: all rows have PP data")

    # re_tax_annual
    df["re_tax_annual"] = pd.to_numeric(df["re_tax_annual"], errors="coerce")
    missing_tax = df["re_tax_annual"].isna()
    df.loc[missing_tax & (df["excluded_reason_mapper"] == ""), "notes"] += "tax estimated; "
    print(f"[VintageMapper] Taxes: {int(missing_tax.sum())} missing -> UW will use price * TAX_RATE_PCT")

    # noi_yr1_input and egi_yr1_input (NOI passthrough columns)
    df["noi_yr1_input"] = pd.to_numeric(df["noi_yr1_input"], errors="coerce")
    df["egi_yr1_input"] = pd.to_numeric(df["egi_yr1_input"], errors="coerce")
    noi_present = int(df["noi_yr1_input"].notna().sum())
    egi_present = int(df["egi_yr1_input"].notna().sum())
    print(f"[VintageMapper] NOI passthrough: {noi_present} rows have noi_yr1_input "
          f"({len(df) - noi_present} missing)")
    print(f"[VintageMapper] EGI passthrough: {egi_present} rows have egi_yr1_input "
          f"({len(df) - egi_present} missing)")

    # Ensure integer types for key fields
    df["units"]   = df["units"].astype(int)
    df["vintage"] = df["vintage"].astype(int)

    # -----------------------------------------------------------------------
    # STEP 7 -- Hold period override
    # -----------------------------------------------------------------------
    df["hold_years"] = HOLD_YEARS_OVERRIDE
    print(f"[VintageMapper] hold_years = {HOLD_YEARS_OVERRIDE} set for all {len(df)} rows")

    # -----------------------------------------------------------------------
    # STEP 8 -- Add noi_t12 placeholder (not in this export)
    # -----------------------------------------------------------------------
    df["noi_t12"] = None

    # Clean notes
    df["notes"] = df["notes"].str.strip("; ")

    # -----------------------------------------------------------------------
    # STEP 9 -- Final column order for pipeline
    # -----------------------------------------------------------------------
    output_cols = [
        "property_name", "address", "city", "state", "market", "submarket",
        "asset_type", "units", "vintage", "asking_price",
        "current_rent_per_unit", "current_occupancy",
        "re_tax_annual", "noi_t12", "loan_maturity_date", "debt_type",
        "building_class", "secondary_type", "building_status", "affordable_type",
        "year_renovated", "owner_name", "property_manager",
        "rent_estimated", "rent_method", "rent_comp_count",
        "noi_yr1_input", "egi_yr1_input",
        "hold_years", "excluded_reason_mapper",
        "notes",
    ]
    output_cols = [c for c in output_cols if c in df.columns]
    df_out = df[output_cols].copy()

    # -----------------------------------------------------------------------
    # Write CSV
    # -----------------------------------------------------------------------
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    df_out.to_csv(output_path, index=False)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    pre_excl   = (df_out["excluded_reason_mapper"] != "").sum()
    uw_eligible = len(df_out) - pre_excl

    print(f"\n[VintageMapper] Output summary:")
    print(f"  Total rows written    : {len(df_out)}")
    print(f"  Pre-excluded (no UW)  : {pre_excl}")
    print(f"  UW-eligible           : {uw_eligible}")
    print(f"  No rent data          : {int((df_out['current_rent_per_unit'] == 0).sum())}")
    print(f"  hold_years            : {HOLD_YEARS_OVERRIDE} (all rows)")
    print(f"\n[VintageMapper] Wrote {len(df_out)} rows to: {output_path}")

    if removal_log:
        print(f"\n[VintageMapper] Hard removal summary:")
        for reason, count, _ in removal_log:
            print(f"    {count:4d}  {reason}")

    return df_out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(
        description="Map Conventional MF 1985-2000 Vintage List to Portico pipeline schema"
    )
    parser.add_argument("--file", required=True, help="Path to xlsx file")
    parser.add_argument(
        "--output",
        default=os.path.join(
            os.path.dirname(__file__), "..", "data", "imports", "vintage_mapped.csv"
        ),
        help="Output CSV path (default: data/imports/vintage_mapped.csv)",
    )
    args = parser.parse_args()

    input_path  = args.file if os.path.isabs(args.file) else os.path.join(
        os.path.dirname(__file__), "..", args.file
    )
    output_path = args.output if os.path.isabs(args.output) else os.path.join(
        os.path.dirname(__file__), "..", args.output
    )

    map_vintage_export(input_path, output_path)
