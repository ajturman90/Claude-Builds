"""
src/report.py
Generates Excel and plain-text deal pipeline reports.

Outputs go to data/outputs/:
  - deal_pipeline_{date}.xlsx  — 4 tabs: PASS, POSSIBLE, EXCLUDED, Assumptions
  - deal_summary_{date}.txt    — plain-text summary for quick review

All assumptions snapshot imported from config/assumptions.py.
"""

import sys
import os
import logging
from datetime import datetime

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config.assumptions as A

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
_GREEN_FILL = PatternFill("solid", fgColor="C6EFCE")
_YELLOW_FILL = PatternFill("solid", fgColor="FFEB9C")
_RED_FILL = PatternFill("solid", fgColor="FFC7CE")
_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
_BOLD = Font(bold=True)

_THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)


# ---------------------------------------------------------------------------
# Column definitions for deal tabs
# ---------------------------------------------------------------------------
_DEAL_COLUMNS = [
    ("property_name", "Property Name"),
    ("market", "Market"),
    ("state", "State"),
    ("asset_type", "Asset Type"),
    ("units", "Units"),
    ("vintage", "Vintage"),
    ("asking_price", "Asking / Max Bid ($)"),
    ("deal_irr", "Deal IRR"),
    ("lp_irr", "LP IRR"),
    ("equity_multiple", "Equity Multiple"),
    ("going_in_cap", "Going-In Cap"),
    ("dscr_yr1", "DSCR Yr1"),
    ("dscr_yr2", "DSCR Yr2"),
    ("dscr_yr3", "DSCR Yr3"),
    ("debt_yield_yr3", "Debt Yield Yr3"),
    ("expense_ratio_yr1", "Expense Ratio Yr1"),
    ("noi_yr1", "NOI Yr1 ($)"),
    ("noi_yr2", "NOI Yr2 ($)"),
    ("noi_yr3", "NOI Yr3 ($)"),
    ("loan_amount", "Loan Amount ($)"),
    ("loan_rate", "Loan Rate"),
    ("treasury_rate", "Treasury Rate"),
    ("exit_value", "Exit Value ($)"),
    ("exit_cap", "Exit Cap"),
    ("screen_reasons", "Flags / Notes"),
]

_PCT_COLS = {
    "deal_irr", "lp_irr", "going_in_cap", "dscr_yr1", "dscr_yr2",
    "dscr_yr3", "debt_yield_yr3", "expense_ratio_yr1", "loan_rate",
    "treasury_rate", "exit_cap",
}
_MONEY_COLS = {
    "asking_price", "noi_yr1", "noi_yr2", "noi_yr3",
    "loan_amount", "exit_value",
}
_MULTI_COLS = {"equity_multiple"}

# Metrics where lower = better (red if high) — reversed logic
_LOWER_BETTER = {"expense_ratio_yr1"}

# Thresholds for cell-level green/yellow/red coloring
_METRIC_THRESHOLDS = {
    "deal_irr":         (A.DEAL_IRR_MIN, A.DEAL_IRR_MIN - 0.02),
    "lp_irr":           (A.LP_IRR_MIN, A.LP_IRR_MIN - 0.02),
    "dscr_yr1":         (A.DSCR_MIN, A.DSCR_MIN - 0.10),
    "dscr_yr2":         (A.DSCR_MIN, A.DSCR_MIN - 0.10),
    "dscr_yr3":         (A.DSCR_MIN, A.DSCR_MIN - 0.10),
    "debt_yield_yr3":   (A.DEBT_YIELD_MIN_YR3, A.DEBT_YIELD_MIN_YR3 - 0.01),
    "expense_ratio_yr1": (A.EXPENSE_RATIO_GREEN, A.EXPENSE_RATIO_YELLOW),
}


def _cell_fill(col_key: str, value) -> PatternFill:
    """Return the appropriate fill color for a metric cell."""
    if col_key not in _METRIC_THRESHOLDS or value is None:
        return None

    green_thresh, yellow_thresh = _METRIC_THRESHOLDS[col_key]

    if col_key in _LOWER_BETTER:
        # Lower is better: green ≤ green_thresh, yellow ≤ yellow_thresh, else red
        if value <= green_thresh:
            return _GREEN_FILL
        elif value <= yellow_thresh:
            return _YELLOW_FILL
        else:
            return _RED_FILL
    else:
        if value >= green_thresh:
            return _GREEN_FILL
        elif value >= yellow_thresh:
            return _YELLOW_FILL
        else:
            return _RED_FILL


def _format_cell(ws, row: int, col: int, value, col_key: str):
    """Write a value to a cell with formatting."""
    cell = ws.cell(row=row, column=col)

    if value is None or (isinstance(value, float) and str(value) == "nan"):
        cell.value = "—"
        return

    if col_key in _PCT_COLS and isinstance(value, (int, float)):
        cell.value = value
        cell.number_format = "0.00%"
    elif col_key in _MONEY_COLS and isinstance(value, (int, float)):
        cell.value = value
        cell.number_format = "$#,##0"
    elif col_key in _MULTI_COLS and isinstance(value, (int, float)):
        cell.value = value
        cell.number_format = "0.00x"
    else:
        cell.value = value

    fill = _cell_fill(col_key, value)
    if fill:
        cell.fill = fill

    cell.border = _THIN_BORDER
    cell.alignment = Alignment(horizontal="center")


def _write_deal_tab(ws, df: pd.DataFrame, tab_fill: PatternFill):
    """Write a deal dataframe to a worksheet with headers and cell coloring."""
    headers = [label for _, label in _DEAL_COLUMNS]

    # Header row
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        cell.border = _THIN_BORDER

    if df.empty:
        ws.cell(row=2, column=1, value="No deals in this category.")
        return

    for row_idx, (_, row) in enumerate(df.iterrows(), start=2):
        for col_idx, (col_key, _) in enumerate(_DEAL_COLUMNS, start=1):
            val = row.get(col_key)
            _format_cell(ws, row_idx, col_idx, val, col_key)

    # Auto-width
    for col_idx in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 18

    ws.column_dimensions["A"].width = 28  # property name
    ws.column_dimensions[get_column_letter(len(headers))].width = 40  # flags

    ws.freeze_panes = "A2"


def _write_assumptions_tab(ws):
    """Snapshot of all assumptions at time of report generation."""
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 20

    rows = [
        ("ASSUMPTIONS SNAPSHOT", f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}"),
        ("", ""),
        ("-- BUY BOX --", ""),
        ("Conventional Min Units", A.CONVENTIONAL["min_units"]),
        ("Conventional Max Units", A.CONVENTIONAL["max_units"]),
        ("Conventional Min Vintage", A.CONVENTIONAL["min_vintage"]),
        ("BTR Min Units", A.BTR["min_units"]),
        ("BTR Max Units", A.BTR["max_units"]),
        ("BTR Min Vintage", A.BTR["min_vintage"]),
        ("", ""),
        ("-- VACANCY & CREDIT LOSS --", ""),
        ("Vacancy Rate", f"{A.VACANCY_RATE:.0%}"),
        ("Bad Debt Year 1", f"{A.BAD_DEBT_YEAR1:.0%}"),
        ("Bad Debt Stabilized", f"{A.BAD_DEBT_STABILIZED:.1%}"),
        ("Concessions Year 1", f"{A.CONCESSIONS_YEAR1:.0%}"),
        ("Concessions Stabilized", f"{A.CONCESSIONS_STABILIZED:.0%}"),
        ("", ""),
        ("-- EXPENSE ASSUMPTIONS ($/door/year) --", ""),
        ("Admin", f"${A.ADMIN_PER_DOOR:,}"),
        ("Marketing", f"${A.MARKETING_PER_DOOR:,}"),
        ("Payroll", f"${A.PAYROLL_PER_DOOR:,}"),
        ("R&M", f"${A.RM_PER_DOOR:,}"),
        ("CapEx Reserve", f"${A.CAPEX_PER_DOOR:,}"),
        ("Mgmt Fee", f"{A.MGMT_FEE_PCT:.1%} of EGI"),
        ("", ""),
        ("-- DEBT --", ""),
        ("LTV", f"{A.LTV:.0%}"),
        ("Treasury Spread (bps)", A.TREASURY_SPREAD_BPS),
        ("Fallback Rate", f"{A.FALLBACK_RATE:.2%}"),
        ("Amort Years", A.AMORT_YEARS),
        ("IO Months", A.IO_PERIODS_MONTHS),
        ("", ""),
        ("-- RETURN THRESHOLDS --", ""),
        ("Deal IRR Min", f"{A.DEAL_IRR_MIN:.0%}"),
        ("LP IRR Min", f"{A.LP_IRR_MIN:.0%}"),
        ("DSCR Min", f"{A.DSCR_MIN:.2f}x"),
        ("Debt Yield Min Yr3", f"{A.DEBT_YIELD_MIN_YR3:.1%}"),
        ("LTV Max", f"{A.LTV_MAX:.0%}"),
        ("", ""),
        ("-- EQUITY STRUCTURE --", ""),
        ("Preferred Return", f"{A.PREFERRED_RETURN:.0%}"),
        ("LP Split", f"{A.LP_SPLIT:.0%}"),
        ("GP Split", f"{A.GP_SPLIT:.0%}"),
        ("Hold Years", A.HOLD_YEARS),
    ]

    for r_idx, (label, value) in enumerate(rows, start=1):
        cell_a = ws.cell(row=r_idx, column=1, value=label)
        cell_b = ws.cell(row=r_idx, column=2, value=value)
        if label.startswith("--"):
            cell_a.font = Font(bold=True, color="1F3864")
        if r_idx == 1:
            cell_a.font = Font(bold=True, size=12)


def generate_reports(df: pd.DataFrame, output_dir: str) -> tuple[str, str]:
    """
    Generate Excel and text reports from a completed pipeline DataFrame.

    Args:
        df:          DataFrame returned by ingest.ingest_csv()
        output_dir:  Path to data/outputs/ directory

    Returns:
        (xlsx_path, txt_path) — absolute paths to generated files
    """
    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")

    xlsx_path = os.path.join(output_dir, f"deal_pipeline_{date_str}.xlsx")
    txt_path = os.path.join(output_dir, f"deal_summary_{date_str}.txt")

    print(f"\n[Report] Generating Excel: {xlsx_path}")

    # Split by status
    df_pass = df[df["pipeline_status"] == "PASS"].copy()
    df_possible = df[df["pipeline_status"] == "POSSIBLE"].copy()
    df_excluded = df[df["pipeline_status"].isin(["EXCLUDED", "FAIL"])].copy()

    # Write Excel
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        # Write placeholder sheets so openpyxl can add them
        pd.DataFrame().to_excel(writer, sheet_name="PASS Deals", index=False)
        pd.DataFrame().to_excel(writer, sheet_name="POSSIBLE Deals", index=False)
        pd.DataFrame().to_excel(writer, sheet_name="EXCLUDED & FAIL", index=False)
        pd.DataFrame().to_excel(writer, sheet_name="Assumptions", index=False)

    wb = load_workbook(xlsx_path)

    _write_deal_tab(wb["PASS Deals"], df_pass, _GREEN_FILL)
    _write_deal_tab(wb["POSSIBLE Deals"], df_possible, _YELLOW_FILL)

    # Excluded tab — simpler columns
    ws_excl = wb["EXCLUDED & FAIL"]
    excl_cols = ["property_name", "market", "state", "asset_type",
                 "units", "vintage", "asking_price",
                 "pipeline_status", "excluded_reason", "screen_reasons"]
    excl_labels = ["Property Name", "Market", "State", "Asset Type",
                   "Units", "Vintage", "Asking Price",
                   "Status", "Excluded Reason", "Screen Flags"]
    for ci, lbl in enumerate(excl_labels, 1):
        cell = ws_excl.cell(row=1, column=ci, value=lbl)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.border = _THIN_BORDER
        cell.alignment = Alignment(horizontal="center")
    for ri, (_, row) in enumerate(df_excluded.iterrows(), 2):
        for ci, col_key in enumerate(excl_cols, 1):
            val = row.get(col_key, "")
            cell = ws_excl.cell(row=ri, column=ci, value=val)
            cell.border = _THIN_BORDER
            if col_key == "pipeline_status":
                if val == "EXCLUDED":
                    cell.fill = _YELLOW_FILL
                else:
                    cell.fill = _RED_FILL
    for ci in range(1, len(excl_cols) + 1):
        ws_excl.column_dimensions[get_column_letter(ci)].width = 20
    ws_excl.column_dimensions["A"].width = 30
    ws_excl.freeze_panes = "A2"

    _write_assumptions_tab(wb["Assumptions"])

    wb.save(xlsx_path)
    print(f"[Report] Excel saved: {xlsx_path}")

    # ---------- Plain text summary ----------
    total = len(df)
    n_pass = len(df_pass)
    n_possible = len(df_possible)
    n_fail = (df["pipeline_status"] == "FAIL").sum()
    n_excl = (df["pipeline_status"] == "EXCLUDED").sum()

    lines = [
        "=" * 70,
        "PORTICO DEAL PIPELINE SUMMARY",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 70,
        "",
        f"  Total Deals Ingested : {total}",
        f"  PASS                 : {n_pass}",
        f"  POSSIBLE             : {n_possible}",
        f"  FAIL                 : {n_fail}",
        f"  EXCLUDED (buy box)   : {n_excl}",
        "",
    ]

    if n_pass > 0:
        lines += ["-" * 70, "PASS DEALS", "-" * 70, ""]
        for _, row in df_pass.iterrows():
            bid = row.get("asking_price") or row.get("max_bid")
            bid_str = f"${bid:,.0f}" if bid and not str(bid) == "nan" else "N/A"
            lines.append(f"  {row.get('property_name', 'N/A')}")
            lines.append(f"    Market      : {row.get('market', '?')}")
            lines.append(f"    Units       : {row.get('units', '?')}")
            lines.append(f"    Max Bid     : {bid_str}")
            deal_irr = row.get("deal_irr")
            lp_irr = row.get("lp_irr")
            cap = row.get("going_in_cap")
            dscr = row.get("dscr_yr1")
            lines.append(f"    Deal IRR    : {deal_irr:.1%}" if deal_irr else "    Deal IRR    : N/A")
            lines.append(f"    LP IRR      : {lp_irr:.1%}" if lp_irr else "    LP IRR      : N/A")
            lines.append(f"    Going-In Cap: {cap:.2%}" if cap else "    Going-In Cap: N/A")
            lines.append(f"    DSCR Yr1    : {dscr:.2f}x" if dscr else "    DSCR Yr1    : N/A")
            lines.append("")

    if n_possible > 0:
        lines += ["-" * 70, "POSSIBLE DEALS", "-" * 70, ""]
        for _, row in df_possible.iterrows():
            lines.append(f"  {row.get('property_name', 'N/A')} — {row.get('screen_reasons', '')}")
        lines.append("")

    lines += ["=" * 70, "END OF REPORT", "=" * 70]

    txt_content = "\n".join(lines)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt_content)

    print(f"[Report] Text summary saved: {txt_path}")
    return xlsx_path, txt_path
