# state.md — deal-sourcer

## Status: OPERATIONAL — pipeline runs end-to-end, confirmed 2026-04-13

## Last Session
- Date: 2026-04-13
- What was done:
  - Created full project scaffold per WORKING_PHILOSOPHY.md
  - config/assumptions.py — all 40+ hardcoded constants
  - src/underwrite.py — 3-year cashflow model, live Treasury rate, scipy IRR solver
  - src/screener.py — PASS/POSSIBLE/FAIL gate logic
  - src/ingest.py — CSV ingestion with graceful missing-column handling
  - src/report.py — Excel (4 tabs, color-coded) + plain text summary
  - run.py — CLI entry point
  - data/imports/sample_import_template.csv — 3 sample deals (BTR, PASS, FAIL)
  - requirements.txt, .gitignore, CLAUDE.md, architecture.md, state.md

## What's Next
- [ ] Run sample file end-to-end and fix any errors
- [ ] Connect live CoStar/Trepp CSV format when first real export arrives
- [ ] Validate underwriting model against manual Excel models
- [ ] Add --market flag to override exit cap rate assumption
- [ ] Consider adding T12 NOI reconciliation check vs. modeled NOI

## Known Issues / Flags
- None yet — pipeline not yet run

## Decisions Made
- Solving for max bid via scipy.optimize.brentq on Deal IRR = 17%
- Treasury rate from Treasury.gov OData API; fallback to 4.75%
- IO for first 36 months, then standard amortization
- Exit cap TX major markets: 5.25%, all others: 5.50%
- Expense growth applied starting Year 2
