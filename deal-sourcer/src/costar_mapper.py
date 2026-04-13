"""
src/costar_mapper.py
Maps a raw CoStar xlsx export to the Portico pipeline import schema.

Usage:
    python src/costar_mapper.py --file data/imports/CostarExport (26).xlsx

Output:
    data/imports/costar_mapped.csv

Column mapping, filtering, asset_type derivation, and default-filling
are all performed here so that run.py receives a clean, schema-conformant CSV.
"""

import sys
import os
import shutil
import argparse
import logging
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column mapping: CoStar header -> pipeline schema name
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
    "Vacancy %":              "vacancy_pct",          # transformed -> current_occupancy
    "For Sale Price":         "asking_price",
    "Taxes Total":            "re_tax_annual",
    "Building Class":         "building_class",
    "Secondary Type":         "secondary_type",
    "Building Status":        "building_status",
    "Avg Concessions %":      "concessions_pct",
    "Year Renovated":         "year_renovated",
    "Owner Name":             "owner_name",
    "Property Manager Name":  "property_manager",
}

# Building statuses that disqualify a property entirely
EXCLUDED_STATUSES = {"Under Construction", "Proposed", "Under Renovation"}

# Secondary type values that map to BTR
BTR_KEYWORDS = {"build-to-rent", "btr", "single family", "single-family"}

# Secondary type values that map to manufactured housing (not in buy box)
MANUFACTURED_KEYWORDS = {"manufactured", "mobile home"}


def _derive_asset_type(secondary_type: str) -> str:
    """
    Derive asset_type from CoStar Secondary Type field.

    Returns 'btr', 'manufactured', or 'conventional'.
    """
    if not secondary_type or pd.isna(secondary_type):
        return "conventional"
    val = str(secondary_type).lower().strip()
    if any(kw in val for kw in BTR_KEYWORDS):
        return "btr"
    if any(kw in val for kw in MANUFACTURED_KEYWORDS):
        return "manufactured"
    return "conventional"


def map_costar_export(input_path: str, output_path: str) -> pd.DataFrame:
    """
    Read a CoStar xlsx export, map columns, clean/filter, fill defaults,
    and write a pipeline-ready CSV.

    Args:
        input_path:  Path to the raw CoStar .xlsx file
        output_path: Path to write the cleaned .csv

    Returns:
        DataFrame of the cleaned, mapped records written to output_path
    """
    print(f"\n[Mapper] Reading: {input_path}")

    # CoStar files are sometimes locked by OneDrive sync -- copy first
    tmp_path = input_path + ".tmp.xlsx"
    shutil.copy2(input_path, tmp_path)
    try:
        raw = pd.read_excel(tmp_path, dtype=str, engine="openpyxl")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    total_raw = len(raw)
    print(f"[Mapper] {total_raw} rows loaded from CoStar export")
    print(f"[Mapper] Columns found: {list(raw.columns)}")

    # Log any expected columns that are missing
    missing_cols = [cs for cs in COLUMN_MAP if cs not in raw.columns]
    if missing_cols:
        print(f"[Mapper] WARNING: These CoStar columns not found (will default): {missing_cols}")

    # -----------------------------------------------------------------------
    # STEP 1 -- Select and rename columns
    # -----------------------------------------------------------------------
    present_map = {cs: ps for cs, ps in COLUMN_MAP.items() if cs in raw.columns}
    df = raw[list(present_map.keys())].rename(columns=present_map).copy()

    # Add empty columns for any that were missing
    for cs, ps in COLUMN_MAP.items():
        if ps not in df.columns:
            df[ps] = None
            print(f"[Mapper]   Added empty column: {ps} ('{cs}' not in export)")

    # -----------------------------------------------------------------------
    # STEP 2 -- Derive asset_type
    # -----------------------------------------------------------------------
    df["asset_type"] = df["secondary_type"].apply(_derive_asset_type)

    asset_counts = df["asset_type"].value_counts().to_dict()
    print(f"[Mapper] Asset types derived: {asset_counts}")

    # -----------------------------------------------------------------------
    # STEP 3 -- Clean and filter
    # -----------------------------------------------------------------------
    removal_log = []

    def _remove(mask, reason):
        count = mask.sum()
        if count:
            names = df.loc[mask, "property_name"].tolist()
            removal_log.append((reason, count, names[:5]))  # log up to 5 names
            print(f"[Mapper] REMOVING {count} -- {reason}")
        return ~mask

    # Convert units and vintage to numeric before null checks
    df["units"] = pd.to_numeric(df["units"], errors="coerce")
    df["vintage"] = pd.to_numeric(df["vintage"], errors="coerce")

    # Filter: building status
    status_mask = df["building_status"].isin(EXCLUDED_STATUSES)
    keep = _remove(status_mask, f"Building Status in {EXCLUDED_STATUSES}")
    df = df[keep].copy()

    # Filter: null or zero units
    null_units_mask = df["units"].isna() | (df["units"] == 0)
    keep = _remove(null_units_mask, "Units null or zero")
    df = df[keep].copy()

    # Filter: null vintage
    null_vintage_mask = df["vintage"].isna()
    keep = _remove(null_vintage_mask, "Vintage (Year Built) null")
    df = df[keep].copy()

    survived = len(df)
    removed_total = total_raw - survived
    print(f"\n[Mapper] Filtering summary:")
    print(f"  Raw rows      : {total_raw}")
    print(f"  Removed       : {removed_total}")
    print(f"  Survived      : {survived}")
    for reason, count, examples in removal_log:
        print(f"    - {count} removed: {reason}")
        if examples:
            print(f"      e.g. {examples}")

    # -----------------------------------------------------------------------
    # STEP 4 -- Convert and fill missing values
    # -----------------------------------------------------------------------
    df["notes"] = ""

    # Vacancy % -> current_occupancy (occupancy = 1 - vacancy/100)
    df["vacancy_pct"] = pd.to_numeric(df["vacancy_pct"], errors="coerce")
    missing_vac = df["vacancy_pct"].isna()
    df.loc[missing_vac, "notes"] += "occupancy assumed; "
    df["current_occupancy"] = df["vacancy_pct"].apply(
        lambda v: 0.94 if pd.isna(v) else round(1.0 - v / 100.0, 4)
    )
    print(f"[Mapper] Vacancy: {missing_vac.sum()} missing -> defaulted to 0.94 occupancy")

    # current_rent_per_unit -- flag nulls
    df["current_rent_per_unit"] = pd.to_numeric(df["current_rent_per_unit"], errors="coerce")
    missing_rent = df["current_rent_per_unit"].isna()
    df.loc[missing_rent, "notes"] += "NO RENT DATA; "
    df.loc[missing_rent, "current_rent_per_unit"] = 0
    print(f"[Mapper] Rent: {missing_rent.sum()} missing -> set to 0, flagged NO RENT DATA")

    # asking_price -- null = solve for max bid
    df["asking_price"] = pd.to_numeric(df["asking_price"], errors="coerce")
    missing_price = df["asking_price"].isna()
    df.loc[missing_price, "asking_price"] = 0
    print(f"[Mapper] Asking price: {missing_price.sum()} missing -> set to 0 (model solves max bid)")

    # re_tax_annual
    df["re_tax_annual"] = pd.to_numeric(df["re_tax_annual"], errors="coerce")
    missing_tax = df["re_tax_annual"].isna()
    df.loc[missing_tax, "notes"] += "tax estimated; "
    print(f"[Mapper] Taxes: {missing_tax.sum()} missing -> will be estimated in UW engine")

    # Ensure numeric types
    df["units"] = df["units"].astype(int)
    df["vintage"] = df["vintage"].astype(int)

    # Add required pipeline columns not in CoStar export
    df["loan_maturity_date"] = None
    df["debt_type"] = None
    df["noi_t12"] = None

    # Clean up notes column
    df["notes"] = df["notes"].str.strip("; ")

    # -----------------------------------------------------------------------
    # STEP 5 -- Final column order for pipeline
    # -----------------------------------------------------------------------
    output_cols = [
        "property_name", "address", "city", "state", "market", "submarket",
        "asset_type", "units", "vintage", "asking_price",
        "current_rent_per_unit", "current_occupancy",
        "re_tax_annual", "noi_t12", "loan_maturity_date", "debt_type",
        "building_class", "secondary_type", "building_status",
        "concessions_pct", "year_renovated", "owner_name", "property_manager",
        "notes",
    ]
    # Keep only columns that exist
    output_cols = [c for c in output_cols if c in df.columns]
    df_out = df[output_cols].copy()

    # -----------------------------------------------------------------------
    # Write CSV
    # -----------------------------------------------------------------------
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df_out.to_csv(output_path, index=False)
    print(f"\n[Mapper] Wrote {len(df_out)} rows to: {output_path}")

    # Summary by asset_type
    print("\n[Mapper] Asset type breakdown in output:")
    for atype, cnt in df_out["asset_type"].value_counts().items():
        print(f"  {atype}: {cnt}")

    no_rent = (df_out["current_rent_per_unit"] == 0).sum()
    no_price = (df_out["asking_price"] == 0).sum()
    print(f"\n[Mapper] Heads-up:")
    print(f"  {no_rent} properties have NO RENT DATA -> will be EXCLUDED by buy box filter")
    print(f"  {no_price} properties have no asking price -> model will solve for max bid")

    return df_out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Map CoStar export to Portico pipeline schema")
    parser.add_argument("--file", required=True, help="Path to CoStar .xlsx file")
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(__file__), "..", "data", "imports", "costar_mapped.csv"),
        help="Output CSV path (default: data/imports/costar_mapped.csv)",
    )
    args = parser.parse_args()

    input_path = args.file if os.path.isabs(args.file) else os.path.join(
        os.path.dirname(__file__), "..", args.file
    )
    output_path = args.output if os.path.isabs(args.output) else os.path.join(
        os.path.dirname(__file__), "..", args.output
    )

    map_costar_export(input_path, output_path)
