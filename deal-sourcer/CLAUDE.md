# See ../WORKING_PHILOSOPHY.md for master rules.

## This Project
- Name: deal-sourcer
- End state: Ingest CoStar/Trepp CSV exports, auto-underwrite each deal, screen PASS/POSSIBLE/FAIL, output Excel report with color-coded metrics
- Stack: Python, pandas, numpy-financial, scipy, openpyxl, requests

## Key Commands
- Run: `python run.py --file data/imports/your_file.csv`
- Test: `python run.py --file data/imports/sample_import_template.csv`
- Deploy: local CLI only (no web server)

## Hard Rules (inherited — do not remove)
- Read state.md before starting any session
- Update state.md at the end of every session
- Never overwrite files without reading first
- Surgical edits only — no rewrites unless explicitly instructed

## Project-Specific Rules
- ALL numeric constants must live in config/assumptions.py — zero hardcoded values in src/
- Every function must have a docstring
- Never crash on a missing CSV column — use defaults from assumptions.py and log the assumption
- Treasury rate pulled live from Treasury.gov API; fallback to FALLBACK_RATE in assumptions.py
- Max bid is solved via scipy.optimize.brentq targeting 17% Deal IRR
- LP waterfall: 8% pref → 80/20 split on excess
- Hold period is always 3 years per assumptions.py
- Expense ratio >= 65% is a hard FAIL; 60–65% is POSSIBLE

## File Map
- config/assumptions.py  — all constants
- src/ingest.py          — CSV read, buy box filter, orchestration
- src/underwrite.py      — 3-year cashflow model, Treasury rate, IRR solver
- src/screener.py        — PASS/POSSIBLE/FAIL logic
- src/report.py          — Excel + text output
- run.py                 — CLI entry point
- data/imports/          — raw CSV drops (gitignored)
- data/outputs/          — reports (gitignored)
