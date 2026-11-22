---
name: xva-sensitivity-report
description: XVA risk ladders by asset class - IR delta by curve and tenor, IR vega, FX delta by currency pair, EQ and CO delta, and credit CS01 by counterparty and tenor - with the largest contributors. Use for "XVA risk", "sensitivities", "CS01 ladder", "what are we exposed to".
argument-hint: Optional asset class (IR, FX, EQ, CO, CR) and date
---

# XVA sensitivity report

1. Call `get_data_status` for the date (default latest clean date).
2. For each asset class requested (all five if none), call `get_xva_sensitivities`:
   - IR: `delta` grouped by `bucket`; `vega` grouped by `bucket`.
   - FX, EQ, CO: `delta` grouped by `risk_factor`.
   - CR: `cs01` grouped by `counterparty` (top 10) and by `bucket`.
3. Present one table per asset class: rows sorted by absolute value, total at the bottom, unit from `provenance.unit` in the header.
4. Close with two or three sentences naming the largest exposures in plain words, for example "a 1bp widening in Atlas Shipping's 5Y spread costs about 30k".

Hedge discussion is optional and only when asked: label it "For discussion, not a recommendation", and keep it to which risk would be reduced, never instrument sizes or prices.
