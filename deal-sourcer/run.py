"""
run.py — Main entry point for the Portico Deal Sourcing Pipeline.

Usage:
    python run.py --file data/imports/your_file.csv

Pipeline steps:
    1. Ingest CSV → buy box filter
    2. Underwrite qualifying deals
    3. Screen deals (PASS / POSSIBLE / FAIL)
    4. Generate Excel + text reports in data/outputs/
"""

import argparse
import logging
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Logging setup — print INFO to console, DEBUG to file
# ---------------------------------------------------------------------------
LOG_DIR = os.path.join(os.path.dirname(__file__), "data", "processed")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
# Quiet down noisy third-party loggers
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

logger = logging.getLogger("run")

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

from src.ingest import ingest_csv
from src.report import generate_reports


def main():
    """Run the full deal sourcing pipeline from CSV input to report output."""
    parser = argparse.ArgumentParser(
        description="Portico Multifamily Deal Sourcing & Auto-Underwriting Pipeline"
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to input CSV (e.g. data/imports/your_file.csv)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(os.path.dirname(__file__), "data", "outputs"),
        help="Directory for report outputs (default: data/outputs/)",
    )
    args = parser.parse_args()

    # Resolve paths relative to script location
    csv_path = os.path.join(os.path.dirname(__file__), args.file) \
        if not os.path.isabs(args.file) else args.file

    print("\n" + "=" * 70)
    print("  PORTICO DEAL SOURCING & AUTO-UNDERWRITING PIPELINE")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print(f"\n[Pipeline] Input file : {csv_path}")
    print(f"[Pipeline] Output dir : {args.output_dir}")
    print(f"[Pipeline] Log file   : {log_file}\n")

    # -------------------------------------------------------------------
    # Step 1 + 2 + 3: Ingest → Underwrite → Screen
    # -------------------------------------------------------------------
    print("[Pipeline] Step 1/4 — Ingesting deals and applying buy box filter...")
    print("[Pipeline] Step 2/4 — Underwriting qualifying deals...")
    print("[Pipeline] Step 3/4 — Screening deals (PASS / POSSIBLE / FAIL)...")

    df = ingest_csv(csv_path)

    # -------------------------------------------------------------------
    # Step 4: Generate reports
    # -------------------------------------------------------------------
    print("\n[Pipeline] Step 4/4 -- Generating reports...")

    # Extract live treasury info from first underwritten row
    meta = {"run_ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
    uw_rows = df[df["pipeline_status"].isin(["PASS", "POSSIBLE", "FAIL"])]
    if len(uw_rows) > 0:
        first = uw_rows.iloc[0]
        if "treasury_rate" in first and first["treasury_rate"] is not None:
            meta["treasury_rate"] = first["treasury_rate"]
        if "loan_rate" in first and first["loan_rate"] is not None:
            meta["loan_rate"] = first["loan_rate"]
        if "treasury_source" in first and first["treasury_source"] is not None:
            meta["treasury_source"] = str(first["treasury_source"])

    xlsx_path, txt_path = generate_reports(df, args.output_dir, meta=meta)

    # -------------------------------------------------------------------
    # Console summary
    # -------------------------------------------------------------------
    n_total = len(df)
    n_pass = (df["pipeline_status"] == "PASS").sum()
    n_possible = (df["pipeline_status"] == "POSSIBLE").sum()
    n_fail = (df["pipeline_status"] == "FAIL").sum()
    n_excl = (df["pipeline_status"] == "EXCLUDED").sum()

    print("\n" + "=" * 70)
    print("  PIPELINE COMPLETE")
    print("=" * 70)
    print(f"  Total deals ingested : {n_total}")
    print(f"  PASS                 : {n_pass}")
    print(f"  POSSIBLE             : {n_possible}")
    print(f"  FAIL                 : {n_fail}")
    print(f"  EXCLUDED (buy box)   : {n_excl}")
    print(f"\n  Excel report : {xlsx_path}")
    print(f"  Text summary : {txt_path}")
    print(f"  Run log      : {log_file}")

    if n_pass > 0:
        print("\n  --- PASS DEALS ---")
        pass_df = df[df["pipeline_status"] == "PASS"]
        for _, row in pass_df.iterrows():
            bid = row.get("max_bid") or row.get("asking_price")
            bpu = row.get("max_bid_per_unit")
            bid_str = f"${bid:,.0f}" if bid and str(bid) != "nan" else "N/A"
            bpu_str = f" (${bpu:,.0f}/unit)" if bpu and str(bpu) != "nan" else ""
            irr = row.get("deal_irr")
            lp  = row.get("lp_irr")
            cap = row.get("going_in_cap")
            dscr = row.get("dscr_yr1")
            hist_noi = row.get("historical_noi")
            print(f"  * {row.get('property_name')} | {row.get('market')} | "
                  f"{int(row.get('units', 0))} units | Bid: {bid_str}{bpu_str} | "
                  f"IRR: {irr:.1%} | LP IRR: {lp:.1%} | "
                  f"Cap: {cap:.2%} | DSCR: {dscr:.2f}x | "
                  f"Hist NOI: ${hist_noi:,.0f}" if hist_noi else
                  f"  * {row.get('property_name')} | {row.get('market')} | "
                  f"{int(row.get('units', 0))} units | Bid: {bid_str}{bpu_str} | "
                  f"IRR: {irr:.1%} | LP IRR: {lp:.1%} | "
                  f"Cap: {cap:.2%} | DSCR: {dscr:.2f}x")

    if n_possible > 0:
        print("\n  --- POSSIBLE DEALS ---")
        for _, row in df[df["pipeline_status"] == "POSSIBLE"].iterrows():
            print(f"  ~ {row.get('property_name')} — {row.get('screen_reasons', '')}")

    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    main()
