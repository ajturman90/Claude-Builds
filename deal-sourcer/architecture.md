# architecture.md — deal-sourcer

## End State
Ingest CoStar/Trepp CSV exports → auto-underwrite each deal → screen PASS/POSSIBLE/FAIL → output color-coded Excel report + plain text summary.

## Data Flow

```
data/imports/*.csv
        │
        ▼
src/ingest.py
  - Read CSV, normalize columns
  - Apply buy box filter (units, vintage, asset_type)
  - EXCLUDED deals → flagged with reason, skip UW
        │
        ▼ (qualifying deals only)
src/underwrite.py
  - Fetch 10yr Treasury from Treasury.gov API (fallback: 4.75%)
  - Build 3-year revenue schedule (GSR → EGI → Total Income)
  - Build 3-year expense schedule (per-door + % of EGI)
  - Calculate NOI, Debt Service, DSCR each year
  - Solve max bid via scipy.optimize.brentq @ 17% Deal IRR
  - Calculate LP IRR, Equity Multiple, Going-In Cap, Debt Yield
        │
        ▼
src/screener.py
  - Apply PASS / POSSIBLE / FAIL criteria
  - Output status + list of flagged metrics
        │
        ▼
src/report.py
  - deal_pipeline_{date}.xlsx
      Tab 1: PASS (green)
      Tab 2: POSSIBLE (yellow)
      Tab 3: EXCLUDED & FAIL (red)
      Tab 4: Assumptions snapshot
  - deal_summary_{date}.txt
        │
        ▼
data/outputs/
```

## Key Design Decisions

| Decision | Rationale |
|---|---|
| All constants in config/assumptions.py | Single source of truth; zero drift between model and docs |
| scipy.optimize.brentq for max bid | Faster and more reliable than iterative guessing |
| numpy-financial for IRR | Handles sign changes correctly, consistent with Excel XIRR |
| IO period 36 months then amortizing | Matches typical agency/CMBS structure |
| Fallback Treasury rate | Pipeline never crashes if API is down |
| Graceful CSV column handling | Real exports are always messy |

## Underwriting Model Summary

```
Year 1 GSR = units × monthly_rent × 12
  – Vacancy (6%)
  – Bad Debt (1% Yr1, 0.5% Yr2-3)
  – Concessions (1% Yr1, 0% Yr2-3)
= EGI
+ Other Income ($75/unit/month default)
= Total Income

Expenses:
  Admin $325 + Marketing $350 + Payroll $1,650 + R&M $600
  + CapEx $250 + Mgmt 2.5% EGI + Insurance $500 + RE Tax + Utilities $1,200
  (all per door/year, grow 3% Yr2+)
= Total Expenses

NOI = Total Income – Total Expenses

Loan = min(Price × 65% LTV, Yr3 NOI / 7.5% debt yield)
Rate = 10yr Treasury + 175bps
Debt Service: IO months 1-36, P&I months 37+

Deal IRR solved at max purchase price where IRR = 17%
LP IRR: 8% pref on LP equity, then 80/20 split
Exit: Yr3 NOI / exit cap rate × (1 – 1% disposition)
```

## Screening Criteria

| Metric | PASS | POSSIBLE | FAIL |
|---|---|---|---|
| Deal IRR | ≥ 17% | 15–17% | < 15% |
| LP IRR | ≥ 16% | — | < 16% |
| DSCR (all years) | ≥ 1.25x | — | < 1.25x |
| Debt Yield Yr3 | ≥ 7.5% | — | < 7.5% |
| Going-In Cap (Conv) | ≥ 5.50% | within 25bps | < 5.25% |
| Going-In Cap (BTR) | ≥ 5.00% | within 25bps | < 4.75% |
| Expense Ratio | ≤ 60% | 60–65% | > 65% |
| Units (Conv) | 75–300 | — | outside range |
| Vintage (Conv) | ≥ 1985 | — | < 1985 |
| Units (BTR) | 50–200 | — | outside range |
| Vintage (BTR) | ≥ 2015 | — | < 2015 |
